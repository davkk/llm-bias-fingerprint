import asyncio
from contextlib import asynccontextmanager
import urllib.request
import json
import logging
import os
import sqlite3
import time
from enum import Enum

from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv


load_dotenv()

API_URL = os.getenv("API_URL")
assert API_URL
# API_TOKEN = os.getenv("API_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class RunStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunRequest(BaseModel):
    model: str
    prompt: str


class Run(BaseModel):
    request: RunRequest
    status: RunStatus


@asynccontextmanager
async def lifespan(app: FastAPI):
    workers = [asyncio.create_task(worker()) for _ in range(1)]
    yield
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


db = sqlite3.connect("data.db")
with open("schema.sql", "r") as f:
    db.executescript(f.read())

db.execute("insert or ignore into language (name) values ('en')")
db.commit()


def get_or_create_experiment(prompt: str) -> int:
    row = db.execute("select id from experiment where initial_prompt = ?", (prompt))
    row = row.fetchone()
    if row:
        return row[0]
    row = db.execute("insert into experiment (initial_prompt) values (?)", (prompt))
    return cur.lastrowid


def get_or_create_prompt(experiment_id: int, content: str) -> int:
    row = db.execute(
        "select id from prompt where experiment_id = ? and language_id = ? and content = ?",
        (experiment_id, 1, content),
    )
    row = row.fetchone()
    if row:
        return row[0]
    cur = db.execute(
        "insert into prompt (experiment_id, language_id, content) values (?, ?, ?)",
        (experiment_id, 1, content),
    )
    return cur.lastrowid


app = FastAPI(lifespan=lifespan)
queue = asyncio.Queue()
runs: dict[int, Run] = {}


def make_request_sync(method: str, endpoint: str, payload: dict | None = None):
    req = urllib.request.Request(
        API_URL + endpoint,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8") if payload else None,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error("failed to make request: %s, %s", e.code, e.read().decode())
        return {}


async def make_request(method: str, endpoint: str, payload: dict | None = None):
    return await asyncio.to_thread(make_request_sync, method, endpoint, payload)


async def is_model_loaded(name: str):
    resp = await make_request("GET", "/models")
    models = resp.get("data", [])
    assert models
    cur = None
    for m in models:
        if m["id"] == name:
            cur = m
            break
    assert cur
    return cur.get("status", {}).get("value", "unloaded") == "loaded"


async def wait_for_model(name: str, every: int = 1, timeout: int = 30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await is_model_loaded(name):
            return
        await asyncio.sleep(every)
    raise TimeoutError(f"Model {name} not loaded after {timeout} seconds")


async def load_model(name: str):
    resp = await make_request("POST", "/models/load", {"model": name})
    assert resp.get("success")
    await wait_for_model(name)


async def unload_model(name: str):
    resp = await make_request("POST", "/models/unload", {"model": name})
    assert resp.get("success")


@app.get("/runs")
async def get_runs():
    return runs


@app.post("/runs", status_code=202)
async def start_run(req: RunRequest):
    with db:
        experiment_id = get_or_create_experiment(req.prompt)
        prompt_id = get_or_create_prompt(experiment_id, req.prompt)
        cur = db.execute(
            "insert into run (experiment_id, model) values (?, ?)",
            (experiment_id, req.model),
        )
        run_id = cur.lastrowid

    runs[run_id] = Run(request=req, status=RunStatus.RUNNING)
    await queue.put((run_id, req, prompt_id))
    return dict(run_id=run_id)


async def send_prompt(req: RunRequest):
    messages = [
        dict(
            role="system",
            content=f"current time: {time.monotonic_ns()}. respond only with number or a word that the user asks for",
        ),
        dict(role="user", content=req.prompt),
    ]
    payload = dict(
        model=req.model,
        messages=messages,
        temperature=1.0,
        top_p=1.0,
        max_tokens=16,
    )
    resp = await make_request("POST", "/v1/chat/completions", payload)
    if not resp:
        raise Exception("Error in API request")
    return resp.get("choices", [{}])[0].get("message", {}).get("content", "")


async def worker():
    while True:
        run_id, req, prompt_id = await queue.get()
        try:
            is_loaded = await is_model_loaded(req.model)
            if not is_loaded:
                await load_model(req.model)
            idx = 0
            while idx < 3:
                raw = await send_prompt(req)
                raw = raw.strip()
                if not raw:
                    continue
                db.execute(
                    "insert into sample (run_id, prompt_id, sample_index, raw) values (?, ?, ?, ?)",
                    (run_id, prompt_id, idx, raw),
                )
                db.commit()
                idx += 1
            db.execute("update run set completed = 1 where id = ?", (run_id))
            db.commit()
            runs[run_id].status = RunStatus.COMPLETED
        except Exception:
            logger.exception("Run %s failed", run_id)
            runs[run_id].status = RunStatus.FAILED
        finally:
            queue.task_done()
            # await unload_model(req.model)  # TODO: fix unload race
