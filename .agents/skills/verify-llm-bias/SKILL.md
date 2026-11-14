---
name: verify-llm-bias
description: Use when verifying the llm-bias-fingerprint app end to end: launch an isolated llama-server plus FastAPI instance, drive YAML jobs through the llm-bias CLI, watch the live dashboard, and capture API, SQLite, screenshot, and report evidence.
---

# Verify llm-bias-fingerprint

llm-bias-fingerprint is a research harness, not a typical CRUD app. A YAML job is
expanded by the `llm-bias` CLI into one run per model. FastAPI serves a live run
dashboard, JSON endpoints, and three sampler workers in the same process. The
workers claim queued runs from SQLite, ask a local `llama-server` router for
completions, and store raw text. Analysis is computed at read time; reports are
generated HTML.

Verification means: start a throwaway database and a dedicated model server,
create a real run from YAML, let the real sampler write real completions, and
prove the dashboard, API, analysis, and report reflect those rows.

The repo's normal commands are `make server` (llama-server on 8013) and `make run`
(FastAPI on 8000, repo `data.db`). Do not use them for verification: they share
the user's data and ports. The scripts below mirror them with isolated paths.

## Launch

```bash
cd /home/davkk/code/llm-bias-fingerprint
.agents/skills/verify-llm-bias/scripts/launch.sh
VERIFY="$(readlink -f /tmp/opencode/verify-llm-bias/latest)"
source "$VERIFY/env.sh"
```

`launch.sh` creates a new `run-YYYYmmdd-HHMMSS` directory under
`/tmp/opencode/verify-llm-bias`, points `latest` at it, and starts:

- `llama-server` on `127.0.0.1:8014` with `~/models`, `--models-max 5`,
  `--no-models-autoload`, and the same `--fit` flags as `make server`.
- FastAPI with `uv run fastapi dev --no-reload --port 8765`, `API_URL` pointed at
  port 8014, and `LLM_BIAS_DB` pointed at `$VERIFY/evidence/data.db`.

It waits for readiness before printing `READY`:

- `/models` must list the expected model id (`VERIFY_MODEL`, default
  `Qwen3.5-4B-Q4_0`).
- `/progress` must return a JSON array. On a fresh database it is `[]`; that
  proves schema creation ran.

The first sample is slow (20-40 seconds) because the router loads the model only
when a run claims it. `llama-server` may log errors for unrelated corrupt GGUFs
in `~/models`; those are harmless as long as the expected model appears in
`/models`.

`make server` and `make run` remain the normal developer path. Use this isolated
launch for any proof that should not contaminate `data.db` or the user's ports.

Teardown is in Cleanup. Every invocation gets a fresh database, so run names from
earlier runs do not collide.

## Doctor

```bash
# Before launch: tools, model file, ports free, repo build present.
.agents/skills/verify-llm-bias/scripts/doctor.sh

# While running: pid ownership, HTTP readiness, DB schema, launch commit.
.agents/skills/verify-llm-bias/scripts/doctor.sh "$VERIFY"
```

`doctor.sh` is read-only. It exits 1 on `FAIL`, 0 on `OK` or `WARN`. `WARN` means
usable but incomplete, for example no browser for screenshots or a working tree
that changed after launch. There is no auth in this app; all routes are local and
unauthenticated.

If the doctor reports the launch commit differs from the current `HEAD`, relaunch.
`--no-reload` is deliberate: editing a file must never kill in-flight workers,
and it also means a running process does not pick up code changes.

## Drive

```bash
VERIFY="$(readlink -f /tmp/opencode/verify-llm-bias/latest)"
source "$VERIFY/env.sh"
cd "$ROOT"
```

`env.sh` exports `WEB_URL`, `LLAMA_URL`, `LLM_BIAS_DB`, `VERIFY_MODEL`, both
ports, and both pid file paths.

### CLI: create a real run

Write a scratch job. One prompt and three samples is enough to prove sampling,
progress, analysis, and a report without a long run.

```bash
cat > "$VERIFY/job.yaml" <<'YAML'
name: verify-smoke
experiment: verify-number
answer_type: number
models:
  - Qwen3.5-4B-Q4_0
prompts:
  - random number between 1 and 100
samples_per_prompt: 3
params:
  temperature: 1.0
  top_p: 1.0
YAML

LLM_BIAS_DB="$LLM_BIAS_DB" uv run llm-bias job run "$VERIFY/job.yaml" --dry-run
LLM_BIAS_DB="$LLM_BIAS_DB" uv run llm-bias job run "$VERIFY/job.yaml"
```

The second command prints `created 1 runs: [<id>]`. The CLI only writes rows; the
sampler workers in the FastAPI process pick them up. To compare quants, list more
models in the YAML. Each model becomes a separate run with the same `name`.

### Watch until the run settles

```bash
for _ in $(seq 1 120); do
  progress="$(curl -fsS "$WEB_URL/progress")"
  status="$(jq -r '.[0].status // empty' <<<"$progress")"
  case "$status" in done|failed) break;; esac
  sleep 2
done
jq -r '.[0] | "status=\(.status) samples=\(.n_samples)/\(.n_prompts * .samples_per_prompt) raw=\(.all_values)"' <<<"$progress"
```

A finished numeric run looks like
`status=done samples=3/3 raw=42, 73, 42`. `all_values` is insertion order, not
sample index order.

### HTTP: inspect the same rows

```bash
RUN_ID="$(curl -fsS "$WEB_URL/runs" | jq -r '.[0].id')"
curl -fsS "$WEB_URL/runs" | jq .
curl -fsS "$WEB_URL/prompts?run_id=$RUN_ID" | jq .
curl -fsS "$WEB_URL/samples?run_id=$RUN_ID" | jq .
curl -fsS "$WEB_URL/analysis?experiment=verify-number" | jq .
curl -fsS "$WEB_URL/analysis?experiment=verify-number&by=value" | jq .
```

`/analysis` without `experiment` mixes every experiment in the database. Always
filter when proving one job.

### Browser: prove the dashboard rendered

```bash
"$ROOT/.venv/bin/python" .agents/skills/verify-llm-bias/scripts/screenshot.py \
  "$WEB_URL/" "$VERIFY/evidence/dashboard.png" 3500
```

The helper starts its own headless Brave on a free CDP port, loads the page,
waits for the first 2-second poll, captures a 1440x900 PNG, prints the page title,
connection status, row count, and a text snippet, then kills only the browser it
started. Add `--eval "EXPRESSION"` to evaluate DOM state and `--click SELECTOR` to
exercise a control before the screenshot.

Stable handles in the dashboard:

- `#status` becomes `live` and `#dot` gains class `ok` when `/progress` answers.
- `#body tr` holds grouped run rows; each row has id, experiment, model, status,
  progress bar, `done/total`, lease, and recent raw values.
- `#pause` toggles to `resume`.
- `#empty` is shown when `/progress` returns `[]`. Its text stays `connecting...`
  even when the connection is live, so do not use it as a readiness signal.

Do not use `firefox --headless --screenshot` for this page. It captures before the
first asynchronous fetch and the proof shows `connecting...` instead of rows.

### SQLite: verify the side effect, not only the screen

```bash
"$ROOT/.venv/bin/python" - "$LLM_BIAS_DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for row in conn.execute("select id, name, model, status, attempts from run"):
    print(row)
print("samples", conn.execute("select count(*) from sample").fetchone()[0])
PY
```

Expect one `done` run, `attempts=1`, and `samples=3` for the scratch job.

### Reports

```bash
LLM_BIAS_DB="$LLM_BIAS_DB" uv run llm-bias report "$VERIFY/job.yaml" \
  --out-dir "$VERIFY/evidence/reports"
rg -c 'llm-bias report: verify-number|Label share by model' \
  "$VERIFY/evidence/reports/verify-number.html"
```

Use the evidence output directory, never the default `static/reports`, so the
repo stays clean. The HTML references Plotly from a CDN; the file structure is
the proof, not an offline render.

## Evidence

Write every artifact under `$VERIFY/evidence/`. It survives cleanup. The launch
already created:

- `manifest.txt`: timestamp, git commit, branch, dirty-file count, tool versions,
  model, ports, paths.
- `git-status.txt`: full working-tree status at launch.
- `llama-server.log`, `web.log`: real sampler and server logs.
- `data.db`: the side effect, including raw `sample.raw` rows.

For a proof, capture the action and the resulting state:

| Artifact | Proves |
| --- | --- |
| `job-dry-run.txt` | the plan and that no run rows were written |
| `job-run.txt` | the real CLI accepted the YAML and created run ids |
| `progress.json` | run moved from pending to done with the expected sample count |
| `runs.json`, `samples.json`, `prompts.json` | API reads the same rows that SQLite holds |
| `analysis-label.json`, `analysis-value.json` | distributions sum to the sample count |
| `job-status.txt`, `job-list.txt` | CLI status and grouping agree with the API |
| `dashboard.png` | the actual browser rendered the live row, not just JSON |
| `reports/<experiment>.html` | the report generator produced the expected charts |
| `db-rows.txt` | run and sample side effects at the SQL level |

Proof standards:

- Exercise the real user path: YAML through `llm-bias job run`, then the HTTP and
  browser surfaces. Do not insert runs or samples directly to make a screen pass.
- Capture the action and the result. A final screenshot alone does not prove the
  sampler ran; include the log line `Run <id> completed` and the sample rows.
- Verify side effects alongside what is visible. The dashboard reads `/progress`;
  SQLite and `/samples` must agree with it.
- Use the real local `llama-server`, not a stub. `API_URL` is the production
  boundary, so pointing it at another real router is valid; faking completions is
  not. Empty completions are valid data and are stored as `raw=''`.
- A dry run must be checked, not trusted: query `run` before and after and confirm
  it stayed at zero. The CLI calls `init_db()` even for a dry run, so the schema
  exists, but no run rows should.
- Do not delete the only proof run during inspection testing. Create a second
  disposable run for the delete journey, or capture evidence first.

## Cleanup

```bash
.agents/skills/verify-llm-bias/scripts/stop.sh "$VERIFY"
```

`stop.sh` reads only the pid files in that run directory, stops each process
group, waits for ports 8014 and 8765 to free, and leaves `$VERIFY/evidence/`
untouched. It never kills by process name, so the user's own `llama-server` on
8013 or 8012 is safe.

After cleanup:

```bash
ls -l "$VERIFY/evidence"
ss -ltn | grep -E ':(8014|8765)\b' || true
```

The evidence list must still be there and both ports must be free. If a failed
launch left a process, run `stop.sh "$VERIFY"` again; the pid files are still the
source of truth. If a port is held by a process this skill did not start, do not
kill it. Report it and pick different ports with `VERIFY_LLAMA_PORT` and
`VERIFY_WEB_PORT`.

## Helpers

| Helper | Invocation | Purpose |
| --- | --- | --- |
| `scripts/launch.sh` | `scripts/launch.sh [run-dir]` | Start isolated llama-server and FastAPI, wait for readiness, write `env.sh` and manifest. |
| `scripts/doctor.sh` | `scripts/doctor.sh [run-dir]` | Read-only prerequisites and live-instance health check. |
| `scripts/stop.sh` | `scripts/stop.sh [run-dir]` | Stop only the processes recorded in that run directory. |
| `scripts/screenshot.py` | `.venv/bin/python scripts/screenshot.py URL OUT [WAIT_MS]` | Headless Brave screenshot over CDP with optional `--eval` and `--click`. |

All are executable. `screenshot.py` needs the `websockets` package, which the
project virtualenv already has through FastAPI.

## Global gotchas

- No auth. The app is for local use only.
- No migrations. `schema.sql` is create-if-not-exists; a database from an older
  schema fails at runtime. Always use the fresh scratch database that `launch.sh`
  creates.
- `config.py` raises if the `LLAMA_MODELS_MAX` environment variable is below
  `N_WORKERS` (3). Do not export `LLAMA_MODELS_MAX=1` for the app. The separate
  `--models-max` server flag defaults to 5 here; keep it at 3 or more for
  multi-model jobs, or workers will evict each other's models.
- If the model server is down, runs stay queued, increment `attempts`, and only
  fail after `MAX_ATTEMPTS=5` lease-length retries (over 8 minutes). Do not wait
  on a broken run; run the doctor first.
- `job run --dry-run` checks the duplicate name before printing the plan. Use a
  fresh database or a new name; `--force` also works but creates a duplicate name.
- The CLI does not validate that a model id exists. An unknown model fails inside
  the worker and retries; check `/models` first.
- The dashboard has no create or delete controls. Run creation is CLI or
  `POST /runs`; deletion is `DELETE /runs/{id}`.
- Reports use `db()` (read-write), not `db_ro()`, because they call `init_db()`.
  That is another reason to point them at the scratch database.
