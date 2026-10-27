import asyncio
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, closing, contextmanager
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

N_WORKERS = 4
LEASE_DURATION_S = 120
RENEW_EVERY_S = 10
MAX_EMPTY = 5
MAX_ATTEMPTS = 3

API_URL = os.getenv("API_URL")

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


def db_conn():
    conn = sqlite3.connect("data.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


@contextmanager
def db():
    with closing(db_conn()) as conn, conn:
        yield conn


def init_db():
    schema = Path("schema.sql").read_text(encoding="utf-8")
    with db() as conn:
        conn.executescript(schema)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(init_db)
    workers = [asyncio.create_task(worker()) for _ in range(N_WORKERS)]
    yield
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


app = FastAPI(lifespan=lifespan)
queue = asyncio.Queue()


def make_request_sync(method: str, endpoint: str, payload: dict | None = None):
    assert API_URL is not None
    req = urllib.request.Request(
        API_URL + endpoint,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8") if payload else None,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.error("HTTP %s: %s", e.code, e.read().decode())
        return {}
    except Exception:
        logger.exception("request failed")
        return {}


async def make_request(method: str, endpoint: str, payload: dict | None = None):
    return await asyncio.to_thread(make_request_sync, method, endpoint, payload)


async def is_model_loaded(name: str):
    resp = await make_request("GET", "/models")
    models = resp.get("data", [])
    if not models:
        raise RuntimeError("No models found")
    cur = next((m for m in models if m["id"] == name), None)
    if not cur:
        raise RuntimeError(f'Model "{name}" not found')
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
def get_samples(run_id: int):
    try:
        with db() as conn:
            run = conn.execute(
                "select 1 from run where id = :run_id",
                {"run_id": run_id},
            ).fetchone()
            if not run:
                raise HTTPException(status_code=404, detail="Run not found")
            return conn.execute(
                """
                select experiment, model, content as prompt, raw from sample
                join prompt on sample.prompt_id = prompt.id
                join run on sample.run_id = run.id
                where run_id = :run_id
                """,
                {"run_id": run_id},
            ).fetchall()
    except sqlite3.Error:
        logger.exception("Error getting samples for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal database error")


@app.get("/runs")
def get_runs():
    try:
        with db() as conn:
            return conn.execute("select * from run").fetchall()
    except sqlite3.Error:
        logger.exception("Error getting runs")
        raise HTTPException(status_code=500, detail="Internal database error")


def create_run(req: RunRequest):
    with db() as conn:
        run_id = conn.execute(
            "insert into run (experiment, model, samples_per_prompt) values (:experiment, :model, :samples_per_prompt)",
            {
                "experiment": req.experiment,
                "model": req.model,
                "samples_per_prompt": req.samples_per_prompt,
            },
        ).lastrowid
        for prompt in req.prompts:
            conn.execute(
                "insert or ignore into prompt (content) values (:prompt)",
                {"prompt": prompt},
            )
            prompt_row = conn.execute(
                "select id from prompt where content = :prompt",
                {"prompt": prompt},
            ).fetchone()
            assert prompt_row is not None
            prompt_id = prompt_row["id"]
            conn.execute(
                "insert into run_prompt (run_id, prompt_id) values (:run_id, :prompt_id)",
                {"run_id": run_id, "prompt_id": prompt_id},
            )
    return run_id


@app.post("/runs", status_code=202)
async def start_run(req: RunRequest):
    try:
        run_id = await asyncio.to_thread(create_run, req)
    except sqlite3.Error:
        logger.exception("Error starting run")
        raise HTTPException(status_code=500, detail="Internal database error")
    return {"run_id": run_id}


async def send_prompt(model: str, prompt: str):
    messages = [
        {
            "role": "system",
            "content": "respond only with a number or a word that the user asks for",
        },
        {"role": "user", "content": prompt},
    ]
    payload = {
        "model": model,
        "messages": messages,
        # TODO: make this configurable via request
        "temperature": 1.0,
        "top_p": 1.0,
        "max_tokens": 16,
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning_effort": "none",
    }
    resp = await make_request("POST", "/v1/chat/completions", payload)
    return resp.get("choices", [{}])[0].get("message", {}).get("content", "")


def set_run_status(run_id: int, status: RunStatus):
    try:
        with db() as conn:
            conn.execute(
                "update run set completed = :completed where id = :run_id",
                {"completed": status.value, "run_id": run_id},
            )
        return True
    except sqlite3.Error:
        logger.exception("Could not mark run %s as %s", run_id, status.value)
        return False


def claim_run():
    with db() as conn:
        # expired and out of attempts
        conn.execute(
            """
            update run set completed = 0, lease_until = null
            where completed is null and lease_until < unixepoch() and attempts >= :max_attempts
            """,
            {"max_attempts": MAX_ATTEMPTS},
        )
        # select the next run
        run = conn.execute(
            """
            update run set
                attempts = attempts + 1,
                lease_until = unixepoch() + :lease_s
            where id = (
                select id from run
                where completed is null
                    and (lease_until is null or lease_until < unixepoch())
                    and attempts < :max_attempts
                order by id
                limit 1
            )
            returning id, attempts, model, samples_per_prompt
            """,
            {"max_attempts": MAX_ATTEMPTS, "lease_s": LEASE_DURATION_S},
        ).fetchone()
    return run


def renew_lease(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            """
            update run set lease_until = unixepoch() + :lease_s
            where id = :run_id and attempts = :attempts and completed is null
            """,
            {"lease_s": LEASE_DURATION_S, "run_id": run_id, "attempts": attempts},
        )
    return cur.rowcount == 1


def finish_run(run_id: int, attempts: int, status: RunStatus):
    with db() as conn:
        cur = conn.execute(
            """
            update run set completed = :completed, lease_until = null
            where id = :run_id and attempts = :attempts and completed is null
            """,
            {"completed": status.value, "run_id": run_id, "attempts": attempts},
        )
    return cur.rowcount == 1


def load_run_work(run_id: int):
    """finds next sample_index to write"""
    with db() as conn:
        return conn.execute(
            """
            select
                run_prompt.prompt_id,
                prompt.content,
                coalesce((
                    select max(sample.sample_index) from sample
                    where sample.run_id = run_prompt.run_id
                        and sample.prompt_id = run_prompt.prompt_id
                ), -1) + 1 as start
            from run_prompt
            join prompt on run_prompt.prompt_id = prompt.id
            where run_prompt.run_id = :run_id
            """,
            {"run_id": run_id},
        ).fetchall()


class Sample(BaseModel):
    run_id: int
    prompt_id: int
    sample_index: int
    raw: str


def save_sample(sample: Sample):
    with db() as conn:
        conn.execute(
            """
            insert into sample (run_id, prompt_id, sample_index, raw)
            values (:run_id, :prompt_id, :sample_index, :raw)
            """,
            dict(sample),
        )


async def renew_loop(run_id: int, attempts: int):
    while True:
        await asyncio.sleep(RENEW_EVERY_S)
        ok = await asyncio.to_thread(renew_lease, run_id, attempts)
        if not ok:
            logger.warning("Could not renew run %s with attempts %s", run_id, attempts)


async def process_run(run_id: int, model: str, samples_per_prompt: int):
    if not await is_model_loaded(model):
        await load_model(model)
    work = await asyncio.to_thread(load_run_work, run_id)
    for row in work:
        prompt_id = row["prompt_id"]
        prompt = row["content"]
        start = row["start"]
        idx = start
        empty = 0
        while idx < samples_per_prompt and empty < MAX_EMPTY:
            raw = await send_prompt(model, prompt)
            raw = raw.strip()
            if not raw:
                empty += 1
                continue
            yield Sample(run_id=run_id, prompt_id=prompt_id, sample_index=idx, raw=raw)
            idx += 1


async def worker():
    while True:
        run = await asyncio.to_thread(claim_run)
        if run is None:
            await asyncio.sleep(1)
            continue
        run_id = run["id"]
        attempts = run["attempts"]
        model = run["model"]
        n = run["samples_per_prompt"]
        hb = asyncio.create_task(renew_loop(run_id, attempts))
        try:
            async for sample in process_run(run_id, model, n):
                await asyncio.to_thread(save_sample, sample)
            await asyncio.to_thread(finish_run, run_id, attempts, RunStatus.COMPLETED)
            logger.info("Run %s completed", run_id)
        except Exception:
            logger.exception("Run %s failed on attempt %s", run_id, attempts)
            if attempts >= MAX_ATTEMPTS:
                await asyncio.to_thread(finish_run, run_id, attempts, RunStatus.FAILED)
                logger.error("Run %s failed too many times", run_id)
            # else: leave completed null - lease expires, another worker may pick it up
        finally:
            # TODO: unload model once all runs are done
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass
