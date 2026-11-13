# TODO

Remaining work for llm-bias-fingerprint. Completed items and dropped decisions
removed; see git history for what was done.

## Principles

1. `sample.raw` is the contract; never store derived data.
2. The sampler must never import analysis or web code.
3. Memory safety is server-side (`--fit`, `--models-max`); OS-side cgroup is
   optional (manual scope). The app only avoids triggering bad states.
4. Import direction is documented only (not enforced by tooling):
   `config, models` leaves; `db`; `runs, sampling`; `model_manager`; `analysis`
   (read-only); `sampler`; `jobspec, cli`; `web`.

## Target layout

```
llm-bias-fingerprint/
├── pyproject.toml            # deps + [project.scripts] llm-bias
├── .env                      # API_URL, llama host/port
├── Makefile                  # web / sampler / dev / server
├── data.db                   # gitignored
├── static/                   # index.html, dashboard.js, reports/
├── experiments/              # YAML job specs
├── llama/models/             # generated, gitignored symlinks to ~/models
└── src/llm_bias/
    ├── config.py             # SYSTEM_PROMPT, paths, limits, env
    ├── db.py                 # db(), db_ro(), init_db()
    ├── models.py             # Sample, RunRequest, JobSpec, RunParams
    ├── runs.py               # create/claim/renew/finish/load_work
    ├── sampling.py           # llama HTTP client only
    ├── sampler.py            # worker loop, save_sample
    ├── model_manager.py      # load/unload lock, idle unload
    ├── analysis.py           # normalize/parse/aggregate, read-only
    ├── jobspec.py            # YAML load + strict validation
    ├── web.py                # create_app(), routes, static mount
    ├── cli.py                # job commands
    └── report.py             # plotly report generation
```

## Remaining work

### A. Process split

- [ ] `sampler.py` becomes its own entrypoint (`__main__`/`main`).
- [ ] `web.create_app(start_workers=False)` by default; single-process dev mode
      behind a flag.
- [ ] Makefile targets `web` and `sampler` (keep `dev` and `server`).

Done when: restarting the web server does not interrupt sampling; restarting
the sampler resumes via lease expiry.

### B. Health

- [ ] Add `/health` to web and sampler.

Done when: the dashboard can degrade to "offline" when the API is down.

### C. Observability

- [ ] Configure logging in the sampler process (currently none).
- [ ] Log rotation for server/sampler/web.

### D. Operations and verification

- [ ] Backup policy: `sqlite3 .backup` or a WAL-checkpointed file copy.
- [ ] Manual verification checklist (updated for current reality: 5 model slots,
      no systemd, no `/proc` gate):
      - fresh DB vs. recreated DB
      - kill -9 the sampler mid-run: run resumes
      - server down: runs stay queued, no crash
      - re-running the same YAML: dedupe works
- [ ] README/runbook: confirm ports, start/stop, and YAML location are accurate.

### E. Optional / future

- [ ] Dashboard shows server status and effective model limits.
- [ ] Analysis caching only if request cost becomes visible.
- [ ] Save top-k logits/probabilities per prompt instead of (or in addition to)
      many samples, so one completion yields a distribution over labels and
      reduces sampling cost. Requires sampler + schema changes; analysis would
      weight by probability.

## Dropped / decided (not doing)

- systemd units for the project server (user decision: no systemd).
- `/proc/meminfo` admission gate (server `--fit`/`--models-max` is the control).
- `models-max 3` matching `N_WORKERS 3` (now 5 slots for 5 quants; 3 workers).
- Enforced import-direction tooling (documentation only).
- Lease/backoff code change (decided no change; stalls traced to reload killing
  workers and model eviction).
- Attempt cap removal (the `failed` state after `MAX_ATTEMPTS` is intentional).
- `presets.ini` (generation params are per request; server configured by CLI flags).
- Legacy fallback for `answer_type` (no backward compat; current schema is the base;
  old DBs are recreated).
