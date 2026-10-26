import asyncio
from contextlib import asynccontextmanager
import urllib.request
import json
import logging
import os
import sqlite3
import time
from enum import Enum

from fastapi import FastAPI, HTTPException
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
    RUNNING = None
    COMPLETED = 1
    FAILED = 0


class RunRequest(BaseModel):
    experiment: str
    model: str
    prompts: list[str]
    samples_per_prompt: int


class Run(BaseModel):
    run_id: int
    model: str
    prompts: list[tuple[int, str]]
    samples_per_prompt: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    workers = [asyncio.create_task(worker()) for _ in range(1)]
    yield
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


db = sqlite3.connect("data.db")
with open("schema.sql", "r") as f, db:
    db.executescript(f.read())


def get_or_create_prompt(content: str) -> int:
    with db:
        row = db.execute("select id from prompt where content = ?", (content,))
        row = row.fetchone()
        if row:
            return row[0]
        cur = db.execute("insert into prompt (content) values (?)", (content,))
        return cur.lastrowid


app = FastAPI(lifespan=lifespan)
queue = asyncio.Queue()


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


@app.get("/samples")
async def get_samples(run_id: int):
    with db:
        try:
            run = db.execute("select 1 from run where id = ?", (run_id,)).fetchone()
            if not run:
                raise HTTPException(status_code=404, detail="Run not found")

            return db.execute(
                "select experiment, model, content as prompt, raw from sample "
                "join prompt on sample.prompt_id = prompt.id "
                "join run on sample.run_id = run.id "
                "where run_id = ?",
                (run_id,),
            ).fetchall()
        except sqlite3.Error as e:
            logger.exception("Error getting samples for run %s", run_id)
            raise HTTPException(status_code=500, detail=f"Internal database error")


@app.get("/runs")
async def get_runs():
    with db:
        try:
            return db.execute("select * from run").fetchall()
        except sqlite3.Error as e:
            logger.exception("Error getting runs")
            raise HTTPException(status_code=500, detail=f"Internal database error")


@app.post("/runs", status_code=202)
async def start_run(req: RunRequest):
    # TODO: add run unique title/identifier?
    try:
        with db:
            cur = db.execute(
                "insert into run (experiment, model, samples_per_prompt) values (?, ?, ?)",
                (req.experiment, req.model, req.samples_per_prompt),
            )
            run_id = cur.lastrowid
            prompts = [(get_or_create_prompt(p), p) for p in req.prompts]
    except sqlite3.Error:
        logger.exception("Error starting run")
        raise HTTPException(status_code=500, detail="Internal database error")

    await queue.put(
        Run(
            run_id=run_id,
            model=req.model,
            prompts=prompts,
            samples_per_prompt=req.samples_per_prompt,
        )
    )
    return dict(run_id=run_id)


async def send_prompt(model: str, prompt: str):
    messages = [
        dict(
            role="system",
            content=f"current time: {time.monotonic_ns()}. respond only with number or a word that the user asks for",
        ),
        dict(role="user", content=prompt),
    ]
    payload = dict(
        model=model,
        messages=messages,
        temperature=1.0,
        top_p=1.0,
        max_tokens=16,
    )
    resp = await make_request("POST", "/v1/chat/completions", payload)
    if not resp:
        raise Exception("Error in API request")
    return resp.get("choices", [{}])[0].get("message", {}).get("content", "")


async def set_run_status(run_id: int, status: RunStatus):
    try:
        with db:
            db.execute("update run set completed = ? where id = ?", (status.value, run_id))
        return True
    except sqlite3.Error as e:
        logger.exception("Could not mark run %s as %s: %s", run_id, status.value, e)
        return False


async def process_run(run: Run):
    if not await is_model_loaded(run.model):
        await load_model(run.model)
    samples = []
    for prompt_id, prompt in run.prompts:  # TODO: run in parallel
        idx = 0
        while idx < run.samples_per_prompt:  # TODO: cap the retries
            raw = await send_prompt(run.model, prompt)
            raw = raw.strip()
            if not raw:
                continue
            idx += 1
            samples.append((run.run_id, prompt_id, idx, raw))
    return samples


async def worker():
    while True:
        run = await queue.get()
        try:
            samples = await process_run(run)
            with db:
                db.executemany(
                    "insert into sample (run_id, prompt_id, sample_index, raw) "
                    "values (?, ?, ?, ?)",
                    samples,
                )
            await set_run_status(run.run_id, RunStatus.COMPLETED)
        except Exception:
            logger.exception("Run %s failed", run.run_id)
            await set_run_status(run.run_id, RunStatus.FAILED)
        finally:
            queue.task_done()
            # TODO: unload model once all runs are done
