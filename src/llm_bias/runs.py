from llm_bias import config, queries
from llm_bias.db import db
from llm_bias.models import RunRequest, RunStatus


def create_run(req: RunRequest):
    with db() as conn:
        run_id = conn.execute(
            queries.CREATE_RUN,
            {
                "experiment": req.experiment,
                "model": req.model,
                "samples_per_prompt": req.samples_per_prompt,
                "status": RunStatus.PENDING,
                "name": req.name,
                "answer_type": req.answer_type.value if req.answer_type is not None else None,
                "temperature": req.temperature,
                "top_p": req.top_p,
                "seed": req.seed,
                "reasoning_effort": req.reasoning_effort,
            },
        ).lastrowid
        for prompt in req.prompts:
            conn.execute(
                queries.INSERT_PROMPT,
                {"prompt": prompt},
            )
            prompt_row = conn.execute(
                queries.GET_PROMPT_ID,
                {"prompt": prompt},
            ).fetchone()
            assert prompt_row is not None
            prompt_id = prompt_row["id"]
            conn.execute(
                queries.LINK_RUN_PROMPT,
                {"run_id": run_id, "prompt_id": prompt_id},
            )
    return run_id


def claim_run():
    with db() as conn:
        run = conn.execute(
            queries.CLAIM_RUN,
            {"lease_s": config.LEASE_DURATION_S, "pending": RunStatus.PENDING},
        ).fetchone()
    return run


def renew_lease(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            queries.RENEW_LEASE,
            {"lease_s": config.LEASE_DURATION_S, "run_id": run_id, "attempts": attempts, "pending": RunStatus.PENDING},
        )
    return cur.rowcount == 1


def finish_run(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            queries.FINISH_RUN,
            {"run_id": run_id, "attempts": attempts, "done": RunStatus.DONE, "pending": RunStatus.PENDING},
        )
    return cur.rowcount == 1


def fail_run(run_id: int, attempts: int):
    """mark a run that exhausted its attempts as permanently failed"""
    with db() as conn:
        cur = conn.execute(
            queries.FAIL_RUN,
            {"run_id": run_id, "attempts": attempts, "failed": RunStatus.FAILED, "pending": RunStatus.PENDING},
        )
    return cur.rowcount == 1


def run_is_complete(run_id: int, samples_per_prompt: int):
    """whether every prompt in the run has all its samples"""
    with db() as conn:
        row = conn.execute(
            queries.IS_RUN_COMPLETE,
            {"run_id": run_id, "n": samples_per_prompt},
        ).fetchone()
    return bool(row["complete"])


def has_pending_runs(model: str) -> bool:
    with db() as conn:
        row = conn.execute(
            queries.HAS_PENDING_RUNS,
            {"pending": RunStatus.PENDING, "model": model},
        ).fetchone()
    return row is not None


def delete_run(run_id: int) -> bool:
    with db() as conn:
        cur = conn.execute(queries.DELETE_RUN, {"run_id": run_id})
    return cur.rowcount == 1


def load_run_config(run_id: int) -> dict:
    with db() as conn:
        row = conn.execute(
            queries.GET_RUN_CONFIG,
            {"run_id": run_id},
        ).fetchone()
    if row is None:
        return {}
    return {
        "temperature": row["temperature"],
        "top_p": row["top_p"],
        "seed": row["seed"],
        "reasoning_effort": row["reasoning_effort"],
    }


def run_name_exists(name: str) -> bool:
    with db() as conn:
        row = conn.execute(
            queries.RUN_NAME_EXISTS,
            {"name": name},
        ).fetchone()
    return row is not None


def list_run_names():
    with db() as conn:
        return conn.execute(queries.LIST_RUN_NAMES).fetchall()


def get_runs_by_name(name: str):
    with db() as conn:
        return conn.execute(
            queries.GET_RUNS_BY_NAME,
            {"name": name},
        ).fetchall()


def get_run_progress(name: str):
    with db() as conn:
        return conn.execute(
            queries.GET_RUN_PROGRESS,
            {"name": name},
        ).fetchall()


def load_run_work(run_id: int):
    """finds next sample_index to write"""
    with db() as conn:
        return conn.execute(
            queries.GET_RUN_WORK,
            {"run_id": run_id},
        ).fetchall()
