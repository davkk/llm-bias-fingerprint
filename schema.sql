pragma foreign_keys = on;

create table if not exists experiment (
    id integer primary key,
    model text not null,
    initial_prompt text not null,
    num_prompts integer,
    samples_per_prompt integer
);

create table if not exists language (
    id integer primary key,
    name text not null,
    unique (name)
);

create table if not exists prompt (
    id integer primary key,
    experiment_id integer not null,
    language_id integer not null,
    content text,
    constraint fk_experiment_id foreign key (experiment_id) references experiment(id),
    constraint fk_language_id foreign key (language_id) references language(id)
);

create table if not exists run (
    id integer primary key,
    experiment_id integer not null,
    completed boolean,
    constraint fk_experiment_id foreign key (experiment_id) references experiment(id)
);

create table if not exists sample (
    id integer primary key,
    run_id integer not null,
    prompt_id integer not null,
    sample_index integer not null,
    value text not null,
    unique (run_id, prompt_id, sample_index),
    constraint fk_run_id foreign key (run_id) references run(id),
    constraint fk_prompt_id foreign key (prompt_id) references prompt(id)
);
