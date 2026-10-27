pragma journal_mode = wal;

create table if not exists prompt (
    id integer primary key,
    content text not null,
    unique (content)
);

create table if not exists run (
    id integer primary key,
    experiment text not null,
    model text not null,
    samples_per_prompt integer not null,
    attempts integer not null default 0,
    lease_until integer,
    completed boolean -- null = pending/running, 1 = ok, 0 = failed
);

create table if not exists run_prompt (
    run_id integer not null references run(id),
    prompt_id integer not null references prompt(id),
    primary key (run_id, prompt_id)
);

create table if not exists sample (
    run_id integer not null references run(id),
    prompt_id integer not null references prompt(id),
    sample_index integer not null,
    raw text not null,
    primary key (run_id, prompt_id, sample_index)
);

-- TODO: add indexes for ids
