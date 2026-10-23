import asyncio
from contextlib import asynccontextmanager
import urllib.request
from uuid import uuid4
from collections import defaultdict
from dataclasses import dataclass
import json
import logging
import os
import sqlite3
import time
from typing import Literal
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


class JobRequestStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobRequest(BaseModel):
    model: str
    prompt: str


class Job(BaseModel):
    job: JobRequest
    status: JobRequestStatus
    result: list[str] | None


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


app = FastAPI(lifespan=lifespan)
queue = asyncio.Queue()
jobs: dict[str, Job] = {}


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


@app.get("/jobs")
async def get_jobs():
    return jobs


@app.post("/jobs", status_code=202)
async def start_job(job: JobRequest):
    job_id = str(uuid4())
    jobs[job_id] = Job(job=job, status=JobRequestStatus.RUNNING, result=None)
    await queue.put((job_id, job))
    return dict(job_id=job_id)


async def send_prompt(job: JobRequest):
    messages = [
        dict(
            role="system",
            content=f"current time: {time.monotonic_ns()}. respond only with number or a word that the user asks for",
        ),
        dict(role="user", content=job.prompt),
    ]
    payload = dict(
        model=job.model,
        messages=messages,
        temperature=1.0,
        top_p=1.0,
        max_tokens=16,
    )
    resp = await make_request("POST", "/v1/chat/completions", payload)
    if not resp:
        raise Exception("Error in API request")
    return (
        resp.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
        .lower()
    )


async def worker():
    while True:
        job_id, job = await queue.get()
        try:
            is_loaded = await is_model_loaded(job.model)
            if not is_loaded:
                await load_model(job.model)
            results = []
            while len(results) < 10:
                ans = await send_prompt(job)
                if not ans:
                    continue
                results.append(ans)
            jobs[job_id].status = JobRequestStatus.COMPLETED
            jobs[job_id].result = results
        except Exception as e:
            logger.exception("Job %s failed", job_id)
            jobs[job_id].status = JobRequestStatus.FAILED
        finally:
            queue.task_done()
            # await unload_model(job.model)  # TODO: fix unload race
