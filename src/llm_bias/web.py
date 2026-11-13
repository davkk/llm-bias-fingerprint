import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from llm_bias import analysis, config
from llm_bias.db import db, init_db
from llm_bias.models import RunRequest
from llm_bias.runs import create_run, delete_run
from llm_bias.sampler import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(start_workers: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(init_db)
        workers = [asyncio.create_task(worker()) for _ in range(config.N_WORKERS)] if start_workers else []
        yield
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)

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
                    "select id, experiment, model, samples_per_prompt, lease_until, status, name, answer_type from run"
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
                    run.name,
                    run.samples_per_prompt,
                    run.status,
                    run.lease_until,
                    (select count(*) from run_prompt where run_prompt.run_id = run.id) as n_prompts,
                    (select count(*) from sample where sample.run_id = run.id) as n_samples,
                    (select group_concat(raw, ', ') from (select raw from sample where sample.run_id = run.id order by id)) as all_values
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
        try:
            return analysis.get_analysis(experiment=experiment, by=by)
        except sqlite3.Error:
            logger.exception("Error getting analysis")
            raise HTTPException(status_code=500, detail="Internal database error")

    @app.post("/runs", status_code=202)
    async def start_run(req: RunRequest):
        try:
            run_id = await asyncio.to_thread(create_run, req)
        except sqlite3.Error:
            logger.exception("Error starting run")
            raise HTTPException(status_code=500, detail="Internal database error")
        return {"run_id": run_id}

    @app.delete("/runs/{run_id}", status_code=204)
    async def remove_run(run_id: int):
        try:
            ok = await asyncio.to_thread(delete_run, run_id)
        except sqlite3.Error:
            logger.exception("Error removing run %s", run_id)
            raise HTTPException(status_code=500, detail="Internal database error")
        if not ok:
            raise HTTPException(status_code=404, detail="Run not found")

    app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static_root")

    return app


app = create_app()
