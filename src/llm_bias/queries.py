import textwrap

CREATE_RUN = textwrap.dedent("""\
    insert into run (experiment, model, samples_per_prompt, status, name, answer_type,
    temperature, top_p, seed, reasoning_effort)
    values (:experiment, :model, :samples_per_prompt, :status, :name, :answer_type,
    :temperature, :top_p, :seed, :reasoning_effort)
""")

INSERT_PROMPT = "insert or ignore into prompt (content) values (:prompt)"

GET_PROMPT_ID = "select id from prompt where content = :prompt"

LINK_RUN_PROMPT = "insert into run_prompt (run_id, prompt_id) values (:run_id, :prompt_id)"

CLAIM_RUN = textwrap.dedent("""\
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
""")

RENEW_LEASE = textwrap.dedent("""\
    update run set lease_until = unixepoch() + :lease_s
    where id = :run_id and attempts = :attempts and status = :pending
""")

FINISH_RUN = textwrap.dedent("""\
    update run set status = :done, lease_until = null
    where id = :run_id and attempts = :attempts and status = :pending
""")

FAIL_RUN = textwrap.dedent("""\
    update run set status = :failed, lease_until = null
    where id = :run_id and attempts = :attempts and status = :pending
""")

IS_RUN_COMPLETE = textwrap.dedent("""\
    select not exists (
        select 1
        from run_prompt
        where run_id = :run_id
            and (select count(*) from sample
                 where sample.run_id = run_prompt.run_id
                     and sample.prompt_id = run_prompt.prompt_id) < :n
    ) as complete
""")

HAS_PENDING_RUNS = "select 1 from run where status = :pending and model = :model limit 1"

DELETE_RUN = "delete from run where id = :run_id"

GET_RUN_CONFIG = textwrap.dedent("""\
    select temperature, top_p, seed, reasoning_effort
    from run where id = :run_id
""")

RUN_NAME_EXISTS = "select 1 from run where name = :name limit 1"

LIST_RUN_NAMES = textwrap.dedent("""\
    select name, count(*) as runs,
        sum(case when status = 'done' then 1 else 0 end) as done
    from run group by name order by name
""")

GET_RUNS_BY_NAME = "select id, model, samples_per_prompt, status from run where name = :name order by id"

GET_RUN_PROGRESS = textwrap.dedent("""\
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
""")

GET_RUN_WORK = textwrap.dedent("""\
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
""")

RUN_EXISTS = "select 1 from run where id = :run_id"

GET_PROMPTS = textwrap.dedent("""\
    select prompt.content
    from run_prompt
    join prompt on run_prompt.prompt_id = prompt.id
    where run_prompt.run_id = :run_id
    order by prompt.id
""")

GET_SAMPLES = textwrap.dedent("""\
    select experiment, model, content as prompt, raw from sample
    join prompt on sample.prompt_id = prompt.id
    join run on sample.run_id = run.id
    where run_id = :run_id
""")

GET_RUNS = "select id, experiment, model, samples_per_prompt, lease_until, status, name, answer_type from run"

GET_PROGRESS = textwrap.dedent("""\
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
""")

GET_ANALYSIS = textwrap.dedent("""\
    select
        run.id,
        run.experiment,
        run.model,
        run.status,
        run.answer_type,
        sample.raw
    from sample
    join run on sample.run_id = run.id
""")

GET_DISTINCT_EXPERIMENTS = "select distinct experiment from run"

GET_PROMPT_MODELS = textwrap.dedent("""\
    select run.model, prompt.content as prompt, sample.raw
    from sample
    join run on sample.run_id = run.id
    join prompt on sample.prompt_id = prompt.id
""")
