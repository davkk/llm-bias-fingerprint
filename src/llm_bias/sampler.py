import asyncio
import logging
from collections.abc import AsyncIterator

from llm_bias import config
from llm_bias.db import db
from llm_bias.model_manager import is_model_loaded, load_model, unload_model
from llm_bias.models import Sample
from llm_bias.runs import (
    claim_run,
    fail_run,
    finish_run,
    has_pending_runs,
    load_run_config,
    load_run_work,
    renew_lease,
    run_is_complete,
)
from llm_bias.sampling import send_prompt

logger = logging.getLogger(__name__)

_model_lock = asyncio.Lock()
_model_users: dict[str, int] = {}


def save_sample(sample: Sample):
    with db() as conn:
        conn.execute(
            """
            insert into sample (run_id, prompt_id, sample_index, raw)
            values (:run_id, :prompt_id, :sample_index, :raw)
            """,
            dict(sample),
        )


async def acquire_model(model: str):
    """Make sure the model is loaded and record one active user."""
    async with _model_lock:
        if not await is_model_loaded(model):
            await load_model(model)
        _model_users[model] = _model_users.get(model, 0) + 1


async def release_model(model: str):
    """Drop one user; unload when this model has no users and no pending runs."""
    async with _model_lock:
        users = _model_users.get(model, 0)
        if users > 1:
            _model_users[model] = users - 1
            return
        if users == 1:
            del _model_users[model]
        else:
            logger.warning("release_model called for %s with no active users", model)

        try:
            if await asyncio.to_thread(has_pending_runs, model):
                return
            await unload_model(model)
            logger.info("Unloaded model %s", model)
        except Exception:
            logger.exception("Could not unload model %s", model)


async def renew_loop(run_id: int, attempts: int):
    while True:
        await asyncio.sleep(config.RENEW_EVERY_S)
        ok = await asyncio.to_thread(renew_lease, run_id, attempts)
        if not ok:
            logger.warning("Could not renew run %s with attempts %s", run_id, attempts)


async def process_run(
    run_id: int, model: str, samples_per_prompt: int, params: dict | None = None
) -> AsyncIterator[Sample]:
    work = await asyncio.to_thread(load_run_work, run_id)
    for row in work:
        prompt_id = row["prompt_id"]
        prompt = row["content"]
        for idx in range(row["start"], samples_per_prompt):
            raw = await send_prompt(model, prompt, params)
            yield Sample(run_id=run_id, prompt_id=prompt_id, sample_index=idx, raw=raw.strip())


async def handle_incomplete_run(run_id: int, attempts: int):
    """give up permanently once the attempt budget is spent, otherwise leave it queued"""
    if attempts >= config.MAX_ATTEMPTS:
        await asyncio.to_thread(fail_run, run_id, attempts)
        logger.error("Run %s failed permanently after %s attempts", run_id, attempts)
    else:
        logger.warning("Run %s incomplete after attempt %s; will retry", run_id, attempts)


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
        acquired = False
        try:
            await acquire_model(model)
            acquired = True
            params = await asyncio.to_thread(load_run_config, run_id)
            async for sample in process_run(run_id, model, n, params):
                await asyncio.to_thread(save_sample, sample)
            if await asyncio.to_thread(run_is_complete, run_id, n):
                await asyncio.to_thread(finish_run, run_id, attempts)
                logger.info("Run %s completed", run_id)
            else:
                await handle_incomplete_run(run_id, attempts)
        except Exception:
            logger.exception("Run %s failed on attempt %s", run_id, attempts)
            await handle_incomplete_run(run_id, attempts)
        finally:
            if acquired:
                await release_model(model)
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass
