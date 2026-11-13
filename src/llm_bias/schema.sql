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
    status text not null default 'pending' check (status in ('pending', 'done', 'failed')),
    name text,
    answer_type text,
    temperature real not null default 1.0,
    top_p real not null default 1.0,
    max_tokens integer not null default 16,
    seed integer,
    enable_thinking integer not null default 0,
    reasoning_effort text
);

create table if not exists run_prompt (
    run_id integer not null references run(id) on delete cascade,
    prompt_id integer not null references prompt(id),
    primary key (run_id, prompt_id)
);

create table if not exists sample (
    run_id integer not null references run(id) on delete cascade,
    prompt_id integer not null references prompt(id),
    sample_index integer not null,
    raw text not null,
    primary key (run_id, prompt_id, sample_index)
);
