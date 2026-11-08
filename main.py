import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

N_WORKERS = 4
LEASE_DURATION_S = 120
RENEW_EVERY_S = 10
MAX_EMPTY = 5

API_URL = os.getenv("API_URL")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    experiment: str
    model: str
    prompts: list[str]
    samples_per_prompt: int


def db_conn():
    conn = sqlite3.connect("data.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn


def normalize_label(raw: str) -> str:
    return " ".join(raw.split()).lower()


def parse_number(text: str) -> int | None:
    m = re.search(r"\d+", text)
    n = int(m.group()) if m else None
    return n if n is not None and 1 <= n <= 100 else None


def parse_open_number(text: str) -> int | None:
    m = re.search(r"-?\d[\d,]*", text)
    if not m:
        return None
    try:
        return int(m.group().replace(",", ""))
    except ValueError:
        return None


def parse_value(experiment: str, text: str) -> int | None:
    if experiment == "number":
        return parse_number(text)
    if experiment == "number-open":
        return parse_open_number(text)
    return None


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
    for _ in range(10):
        resp = await make_request("POST", "/models/load", {"model": name})
        if resp.get("success"):
            await wait_for_model(name, timeout=120)
            return
        # a concurrent load in flight makes the router refuse; back off and retry
        logger.warning("model %s load refused, retrying", name)
        await asyncio.sleep(3)
    raise RuntimeError(f"could not load model {name}")


async def unload_model(name: str):
    resp = await make_request("POST", "/models/unload", {"model": name})
    assert resp.get("success")


@app.get("/prompts")
def get_prompts(run_id: int):
    try:
        with db() as conn:
            run = conn.execute("select 1 from run where id = :run_id", {"run_id": run_id}).fetchone()
            if not run:
                raise HTTPException(status_code=404, detail="Run not found")
            rows = conn.execute(
                """
                select prompt.content
                from run_prompt
                join prompt on run_prompt.prompt_id = prompt.id
                where run_prompt.run_id = :run_id
                order by prompt.id
                """,
                {"run_id": run_id},
            ).fetchall()
            return [row["content"] for row in rows]
    except sqlite3.Error:
        logger.exception("Error getting prompts for run %s", run_id)
        raise HTTPException(status_code=500, detail="Internal database error")


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
            return conn.execute(
                "select id, experiment, model, samples_per_prompt, lease_until, completed from run"
            ).fetchall()
    except sqlite3.Error:
        logger.exception("Error getting runs")
        raise HTTPException(status_code=500, detail="Internal database error")


@app.get("/progress")
def get_progress():
    """Per-run progress: samples written vs samples expected."""
    try:
        with db() as conn:
            return conn.execute(
                """
                select
                    run.id,
                    run.experiment,
                    run.model,
                    run.samples_per_prompt,
                    run.completed,
                    run.lease_until,
                    (select count(*) from run_prompt where run_prompt.run_id = run.id) as n_prompts,
                    (select count(*) from sample where sample.run_id = run.id) as n_samples
                from run
                order by run.id desc
                """
            ).fetchall()
    except sqlite3.Error:
        logger.exception("Error getting run progress")
        raise HTTPException(status_code=500, detail="Internal database error")


@app.get("/analysis")
def get_analysis(experiment: str | None = None, by: str | None = None):
    """Label distribution per run, per model, and overall."""
    by_value = by == "value"
    try:
        with db() as conn:
            query = """
                select
                    run.id,
                    run.experiment,
                    run.model,
                    run.completed,
                    sample.raw
                from sample
                join run on sample.run_id = run.id
            """
            params: dict[str, str] = {}
            if experiment is not None:
                query += " where run.experiment = :experiment"
                params["experiment"] = experiment
            query += " order by run.id desc"
            rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        logger.exception("Error getting analysis")
        raise HTTPException(status_code=500, detail="Internal database error")

    def sorted_dist(dist: dict[int | str, int]) -> dict[int | str, int]:
        return {k: dist[k] for k in sorted(dist, key=lambda k: (-dist[k], k))}

    run_meta: dict[int, tuple[str, str, int | None]] = {}
    run_dist: dict[int, dict[int | str, int]] = {}
    run_skipped: dict[int, int] = {}
    by_model_raw: dict[str, dict[int | str, int]] = {}
    by_model_skipped: dict[str, int] = {}
    overall_raw: dict[int | str, int] = {}
    overall_skipped = 0
    for row in rows:
        run_id = row["id"]
        run_meta.setdefault(run_id, (row["experiment"], row["model"], row["completed"]))
        label = normalize_label(row["raw"])
        value = parse_value(row["experiment"], row["raw"])
        if by_value and value is None:
            run_dist.setdefault(run_id, {})
            run_skipped[run_id] = run_skipped.get(run_id, 0) + 1
            by_model_raw.setdefault(row["model"], {})
            by_model_skipped[row["model"]] = by_model_skipped.get(row["model"], 0) + 1
            overall_skipped += 1
            continue
        key: int | str = value if by_value else label
        dist = run_dist.setdefault(run_id, {})
        dist[key] = dist.get(key, 0) + 1
        model_dist = by_model_raw.setdefault(row["model"], {})
        model_dist[key] = model_dist.get(key, 0) + 1
        overall_raw[key] = overall_raw.get(key, 0) + 1

    by_model = [(sum(dist.values()), model, sorted_dist(dist)) for model, dist in by_model_raw.items()]
    by_model.sort(key=lambda item: (-item[0], item[1]))

    runs = []
    for run_id in sorted(run_dist, reverse=True):
        entry = {
            "run_id": run_id,
            "experiment": run_meta[run_id][0],
            "model": run_meta[run_id][1],
            "completed": run_meta[run_id][2],
            "n": sum(run_dist[run_id].values()),
            "dist": sorted_dist(run_dist[run_id]),
        }
        if by_value:
            entry["skipped"] = run_skipped.get(run_id, 0)
        runs.append(entry)

    by_model_out = []
    for n, model, dist in by_model:
        entry = {"model": model, "n": n, "dist": dist}
        if by_value:
            entry["skipped"] = by_model_skipped.get(model, 0)
        by_model_out.append(entry)

    overall = {"n": sum(overall_raw.values()), "dist": sorted_dist(overall_raw)}
    if by_value:
        overall["skipped"] = overall_skipped

    return {
        "runs": runs,
        "by_model": by_model_out,
        "overall": overall,
    }


def create_run(req: RunRequest):
    with db() as conn:
        run_id = conn.execute(
            "insert into run (experiment, model, samples_per_prompt, completed) values (:experiment, :model, :samples_per_prompt, 0)",
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


def claim_run():
    with db() as conn:
        # retries indefinitely: no attempt cap, no auto-fail
        # order by attempts so a repeatedly failing run does not block later ones
        run = conn.execute(
            """
            update run set
                attempts = attempts + 1,
                lease_until = unixepoch() + :lease_s
            where id = (
                select id from run
                where completed = 0
                    and (lease_until is null or lease_until < unixepoch())
                order by attempts, id
                limit 1
            )
            returning id, attempts, model, samples_per_prompt
            """,
            {"lease_s": LEASE_DURATION_S},
        ).fetchone()
    return run


def renew_lease(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            """
            update run set lease_until = unixepoch() + :lease_s
            where id = :run_id and attempts = :attempts and completed = 0
            """,
            {"lease_s": LEASE_DURATION_S, "run_id": run_id, "attempts": attempts},
        )
    return cur.rowcount == 1


def finish_run(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            """
            update run set completed = 1, lease_until = null
            where id = :run_id and attempts = :attempts and completed = 0
            """,
            {"run_id": run_id, "attempts": attempts},
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


async def process_run(run_id: int, model: str, samples_per_prompt: int, status: dict):
    if not await is_model_loaded(model):
        await load_model(model)
    work = await asyncio.to_thread(load_run_work, run_id)
    status["completed"] = True
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
        if empty >= MAX_EMPTY and idx < samples_per_prompt:
            # hit the empty cap before finishing this prompt -> not a complete run
            status["completed"] = False


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
        status = {"completed": True}
        try:
            async for sample in process_run(run_id, model, n, status):
                await asyncio.to_thread(save_sample, sample)
            if status["completed"]:
                await asyncio.to_thread(finish_run, run_id, attempts)
                logger.info("Run %s completed", run_id)
            else:
                logger.warning("Run %s hit empty cap; leaving incomplete for retry", run_id)
        except Exception:
            logger.exception("Run %s failed on attempt %s", run_id, attempts)
            # leave completed = 0 so lease expiry re-queues the run (retry indefinitely)
        finally:
            # TODO: unload model once all runs are done
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass


app.mount("/", StaticFiles(directory="static", html=True), name="static_root")
