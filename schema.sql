pragma foreign_keys = on;
pragma journal_mode = wal;

create table if not exists prompt (
    id integer primary key,
    content text not null
);

create table if not exists run (
    id integer primary key,
    experiment text not null,
    model text not null,
    samples_per_prompt integer not null,
    temperature real not null,
    top_p real not null,
    completed boolean
);

create table if not exists sample (
    id integer primary key,
    run_id integer not null,
    prompt_id integer not null,
    sample_index integer not null,
    raw text not null,
    unique (run_id, prompt_id, sample_index),
    constraint fk_run_id foreign key (run_id) references run(id),
    constraint fk_prompt_id foreign key (prompt_id) references prompt(id)
);

-- TODO: add indexes for ids
