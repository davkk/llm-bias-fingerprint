from llm_bias import config
from llm_bias.db import db
from llm_bias.models import RunRequest, RunStatus


def create_run(req: RunRequest):
    with db() as conn:
        run_id = conn.execute(
            "insert into run (experiment, model, samples_per_prompt, status, name, answer_type, "
            "temperature, top_p, reasoning_effort) "
            "values (:experiment, :model, :samples_per_prompt, :status, :name, :answer_type, "
            ":temperature, :top_p, :reasoning_effort)",
            {
                "experiment": req.experiment,
                "model": req.model,
                "samples_per_prompt": req.samples_per_prompt,
                "status": RunStatus.PENDING,
                "name": req.name,
                "answer_type": req.answer_type.value if req.answer_type is not None else None,
                "temperature": req.temperature,
                "top_p": req.top_p,
                "reasoning_effort": req.reasoning_effort,
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


def claim_run():
    with db() as conn:
        run = conn.execute(
            """
            update run set
                attempts = attempts + 1,
                lease_until = unixepoch() + :lease_s
            where id = (
                select id from run
                where status = :pending
                    and (lease_until is null or lease_until < unixepoch())
                order by attempts, id
                limit 1
            )
            returning id, attempts, model, samples_per_prompt
            """,
            {"lease_s": config.LEASE_DURATION_S, "pending": RunStatus.PENDING},
        ).fetchone()
    return run


def renew_lease(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            """
            update run set lease_until = unixepoch() + :lease_s
            where id = :run_id and attempts = :attempts and status = :pending
            """,
            {"lease_s": config.LEASE_DURATION_S, "run_id": run_id, "attempts": attempts, "pending": RunStatus.PENDING},
        )
    return cur.rowcount == 1


def finish_run(run_id: int, attempts: int):
    with db() as conn:
        cur = conn.execute(
            """
            update run set status = :done, lease_until = null
            where id = :run_id and attempts = :attempts and status = :pending
            """,
            {"run_id": run_id, "attempts": attempts, "done": RunStatus.DONE, "pending": RunStatus.PENDING},
        )
    return cur.rowcount == 1


def fail_run(run_id: int, attempts: int):
    """mark a run that exhausted its attempts as permanently failed"""
    with db() as conn:
        cur = conn.execute(
            """
            update run set status = :failed, lease_until = null
            where id = :run_id and attempts = :attempts and status = :pending
            """,
            {"run_id": run_id, "attempts": attempts, "failed": RunStatus.FAILED, "pending": RunStatus.PENDING},
        )
    return cur.rowcount == 1


def run_is_complete(run_id: int, samples_per_prompt: int):
    """whether every prompt in the run has all its samples"""
    with db() as conn:
        row = conn.execute(
            """
            select not exists (
                select 1
                from run_prompt
                where run_id = :run_id
                    and (select count(*) from sample
                         where sample.run_id = run_prompt.run_id
                             and sample.prompt_id = run_prompt.prompt_id) < :n
            ) as complete
            """,
            {"run_id": run_id, "n": samples_per_prompt},
        ).fetchone()
    return bool(row["complete"])


def has_pending_runs(model: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "select 1 from run where status = :pending and model = :model limit 1",
            {"pending": RunStatus.PENDING, "model": model},
        ).fetchone()
    return row is not None


def load_run_config(run_id: int) -> dict:
    with db() as conn:
        row = conn.execute(
            """
            select temperature, top_p, reasoning_effort
            from run where id = :run_id
            """,
            {"run_id": run_id},
        ).fetchone()
    if row is None:
        return {}
    return {
        "temperature": row["temperature"],
        "top_p": row["top_p"],
        "reasoning_effort": row["reasoning_effort"],
    }


def run_name_exists(name: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "select 1 from run where name = :name limit 1",
            {"name": name},
        ).fetchone()
    return row is not None


def list_run_names():
    with db() as conn:
        return conn.execute(
            "select name, count(*) as runs, "
            "sum(case when status = 'done' then 1 else 0 end) as done "
            "from run group by name order by name"
        ).fetchall()


def get_runs_by_name(name: str):
    with db() as conn:
        return conn.execute(
            "select id, model, samples_per_prompt, status from run where name = :name order by id",
            {"name": name},
        ).fetchall()


def get_run_progress(name: str):
    with db() as conn:
        return conn.execute(
            """
            select
                run.id,
                run.model,
                run.samples_per_prompt,
                run.status,
                (select count(*) from run_prompt where run_id = run.id) as n_prompts,
                (select count(*) from sample where run_id = run.id) as n_samples
            from run
            where name = :name
            order by id
            """,
            {"name": name},
        ).fetchall()


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
