# TODO

Roadmap for separating data gathering from data analysis, and for turning
research jobs into declarative YAML.

## Current state

- [x] `completed` is a boolean, default `0`; unfinished runs stay queued and retry.
- [x] Runs are claimed in `(attempts, id)` order, so a bad run cannot starve later ones.
- [x] `sample` stores `raw` only; labels/values are computed at read time in `/analysis`.
- [x] Dashboard is served by FastAPI at `/`; dashboard and analysis endpoints exist.
- [x] No attempt cap, no persistent failed state.

## Principles

1. `sample.raw` is the contract; never store derived data.
2. The sampler must never import analysis or web code.
3. Memory safety is server-side (`--fit`, `--models-max`) plus OS-side (cgroup);
   the app only avoids triggering bad states.

## Target layout

```
llm-bias-fingerprint/
├── pyproject.toml            # deps + [project.scripts] llm-bias
├── .env                      # API_URL, llama host/port
├── Makefile                  # web / sampler / dev / server
├── data.db                   # gitignored
├── static/                   # index.html, dashboard.js
├── experiments/              # YAML job specs
├── llama/presets.ini         # project-only model list
└── src/llm_bias/
    ├── config.py             # SYSTEM_PROMPT, paths, limits, env
    ├── db.py                 # db(), db_ro(), init_db(), migrate()
    ├── models.py             # Sample, RunRequest, JobSpec, RunParams
    ├── runs.py               # create/claim/renew/finish/load_work
    ├── sampling.py           # llama HTTP client only
    ├── sampler.py            # worker loop, model gate, save_sample
    ├── model_manager.py      # load/unload lock, health, idle unload
    ├── server.py             # project llama-server start/stop/status
    ├── analysis.py           # normalize/parse/aggregate, read-only
    ├── jobspec.py            # YAML load + strict validation
    ├── web.py                # create_app(), routes, static mount
    └── cli.py                # job / server commands
```

Import direction:

```
config, models                 leaves
db                             <- config
runs, sampling                 <- db, models, config
model_manager                  <- config, sampling
analysis                       <- db_ro, models
sampler                        <- runs, sampling, model_manager
jobspec, cli                   <- runs, jobspec, models
web                            <- runs, analysis, static
```

`sampler` must never import `analysis` or `web`; `analysis` must never import
FastAPI or the sampler.

## Phase 1 - Package extraction (no behavior change)

- [ ] Create `src/llm_bias/` and move code into the modules above.
- [ ] Move `schema.sql` into the package; resolve all paths from `Path(__file__)` /
      `config.ROOT`, not CWD.
- [ ] Root `main.py` becomes a thin `from llm_bias.web import app`.
- [ ] Add `[project.scripts] llm-bias = "llm_bias.cli:main"`.
- [ ] Enforce the import direction above.

Done when: the app starts from any CWD and behaves identically.

## Phase 2 - Schema and migrations

- [ ] Add `run.config` (JSON snapshot of resolved params) and `run.name` (batch identity).
- [ ] Keep `completed` boolean default `0`; no failed state.
- [ ] Add `db.migrate()` with idempotent `ALTER TABLE ADD COLUMN` guarded by
      `pragma table_info`.
- [ ] Optional indexes only if measured: `(completed, lease_until)` for claiming.

Done when: fresh DB and migrated DB have the same shape; existing rows preserved.

## Phase 3 - Project-owned llama-server

- [ ] `llama/presets.ini` listing only the project's quants.
- [ ] `server.py` builds the command from `config.py`:
      dedicated host/port, `--fit on`, `--fit-target` if supported,
      `--models-max 1` (or `2`), `--parallel` matched to sampler concurrency,
      base `ctx` 4096-8192, dedicated log/PID paths.
- [ ] CLI: `llm-bias server start|stop|status|logs`.
- [ ] systemd user unit with `MemoryHigh`, `MemoryMax`, `OOMScoreAdjust=500`;
      optionally `systemd-oomd` / `earlyoom`.
- [ ] `.env` `API_URL` points at the project port.
- [ ] Document coexistence with the global `llamad`: keep quants exclusive or
      stop the global server during experiments.

Done when: `server status` shows resident models, effective `/props`, and
`MemAvailable`, and the cgroup limit is enforced.

## Phase 4 - Sampler model gate

- [ ] One `asyncio.Lock` around load/unload, so duplicate load requests cannot race.
- [ ] One active model at a time: claim only runs matching the loaded model;
      otherwise load the next group. Avoids `models-max` evict/reload churn.
- [ ] Do not add a `/proc/meminfo` admission gate; server fit/models-max is the
      primary control. Log `MemAvailable` before load as a diagnostic only.
- [ ] Unload when the queue is empty (fulfils the existing TODO and frees RAM for
      the global server).
- [ ] Fail a run with a clear message if `API_URL` is unreachable; leave it queued.

Done when: four workers never request four different models, and server-down or
low-memory leaves runs queued rather than crashing.

## Phase 5 - YAML job specs and CLI

- [ ] `jobspec.py`: strict Pydantic schema (`extra="forbid"`).
- [ ] Global `SYSTEM_PROMPT` in `config.py` (common to all experiments; not in YAML).
- [ ] Snapshot the resolved config plus system prompt into `run.config`.
- [ ] CLI: `job run FILE [--dry-run] [--force]`, `job list`, `job status`.
- [ ] Dedupe by `run.name`: refuse if it already exists unless `--force`.
- [ ] Replace `qwen35.sh` with YAML files.

YAML shape (one file = one batch, params shared by all models):

```yaml
name: qwen35-number-t1
experiment: number
models:
  - Qwen3.5-4B-Q3_K_M
  - Qwen3.5-4B-Q4_0
  - Qwen3.5-4B-Q5_K_M
  - Qwen3.5-4B-Q6_K
  - Qwen3.5-4B-Q8_0
prompts:
  - random number between 1 and 100
  - give me a number
samples_per_prompt: 100
params:
  temperature: 1.0
  top_p: 1.0
  max_tokens: 16
  seed: 0
  chat_template_kwargs:
    enable_thinking: false
  reasoning_effort: none
```

Done when: one command expands a file into runs, `--dry-run` prints the plan,
and re-running refuses without `--force`.

## Phase 6 - Process split

- [ ] `sampler.py` becomes its own entrypoint; `web.create_app(start_workers=False)`
      by default.
- [ ] Keep a single-process dev mode behind a flag.
- [ ] Makefile targets (`web`, `sampler`, `dev`, `server`) and systemd units for
      web + sampler.

Done when: restarting web does not interrupt sampling; restarting the sampler
resumes via lease expiry.

## Phase 7 - Read-only analysis and dashboard

- [ ] `db_ro()` (`file:data.db?mode=ro`) for GET endpoints and `/analysis`.
- [ ] Dashboard shows server status and effective model limits (optional).
- [ ] Add `/health` for web and sampler.
- [ ] Add analysis caching only if request cost becomes visible; do not
      reintroduce derived columns.

Done when: analysis physically cannot write, and the dashboard degrades to
"offline" when the API is down.

## Phase 8 - Operations and verification

- [ ] Log files for server/sampler/web; rotation.
- [ ] Backup policy: `sqlite3 .backup` or a file copy while WAL-checkpointed.
- [ ] Manual verification:
      - fresh DB vs migrated DB
      - kill -9 the sampler mid-run: run resumes
      - server down: runs stay queued, no crash
      - memory pressure with `models-max 1` + cgroup: server is killed, system
        stays responsive
      - re-running the same YAML: dedupe works
- [ ] README/runbook: ports, units, start/stop, where YAML lives.

## Decisions to lock

- [ ] `models-max`: `1` (safest, reload between quants) or `2` (overlap, more memory).
- [ ] Dedupe: refuse by default, `--force` creates another batch.
- [ ] Idle unload: unload when the queue empties, or keep the last model warm for
      a grace period.
- [ ] `run.name` first; add a `job` table only if per-batch metadata is needed.
