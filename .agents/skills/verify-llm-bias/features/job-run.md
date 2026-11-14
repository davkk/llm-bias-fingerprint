# Job run

## Sub-features

- Strict YAML validation: unknown keys are rejected, `models` and `prompts` need
  at least one entry, `samples_per_prompt` must be greater than zero.
- One queued run per model, all sharing the YAML `name`.
- `--dry-run` prints the plan without creating run rows.
- Duplicate run-name guard, with `--force` to override.
- Per-run generation parameters: `temperature`, `top_p`, `seed`,
  `reasoning_effort`, plus `answer_type` for analysis.

## How to get to it (user POV)

1. Launch the isolated app and source its environment (see `../SKILL.md`).
2. Write a YAML job spec, normally under `experiments/`, or use
   `experiments/smoke.yaml`.
3. Run `uv run llm-bias job run <file>` from the repo root.
4. The CLI prints the created run ids. The sampler workers in the FastAPI process
   claim them and start calling the model server. Watch with `job status` or the
   dashboard.

## Driving it with shell and HTTP

```bash
VERIFY="$(readlink -f /tmp/opencode/verify-llm-bias/latest)"
source "$VERIFY/env.sh"
cd "$ROOT"

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

# Plan first. This calls init_db(), then reads the name check, but writes no runs.
LLM_BIAS_DB="$LLM_BIAS_DB" uv run llm-bias job run "$VERIFY/job.yaml" --dry-run

# Prove the dry run wrote nothing.
"$ROOT/.venv/bin/python" - "$LLM_BIAS_DB" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print("runs before create:", conn.execute("select count(*) from run").fetchone()[0])
PY

# Create the real runs.
LLM_BIAS_DB="$LLM_BIAS_DB" uv run llm-bias job run "$VERIFY/job.yaml"
```

Expected dry-run output:

```text
name: verify-smoke
experiment: verify-number
answer_type: number
models: Qwen3.5-4B-Q4_0
prompts: 1
samples_per_prompt: 3
runs to create: 1
config: {"reasoning_effort": null, "seed": null, "system_prompt": "...", "temperature": 1.0, "top_p": 1.0}
```

Expected create output:

```text
created 1 runs: [1]
```

Then poll until the worker settles:

```bash
for _ in $(seq 1 120); do
  progress="$(curl -fsS "$WEB_URL/progress")"
  status="$(jq -r '.[0].status // empty' <<<"$progress")"
  case "$status" in done|failed) break;; esac
  sleep 2
done
jq -r '.[0] | "status=\(.status) samples=\(.n_samples)/\(.n_prompts * .samples_per_prompt)"' <<<"$progress"
```

The first sample waits for the router to load the model, so allow 20-40 seconds.
Ten consecutive API calls need about 30 seconds of wall time on this machine.

For a multi-model comparison, list several ids from `GET /models` in `models:`.
The launch uses `--models-max 5`, matching `make server`. Keep it at least 3 if
you expect the three workers to run different models concurrently; lower values
let one worker evict another worker's model.

## Gotchas

- The duplicate-name check runs before `--dry-run`. A second dry run with the same
  name exits 1 with `use --force`; it never prints the plan.
- The CLI writes runs but never validates model ids. An unknown model fails inside
  the worker after the lease and retries up to `MAX_ATTEMPTS=5`.
- `name` is not unique in the database. `--force` creates more rows with the same
  name, and `job status <name>` reports all of them.
- The CLI needs only the database, not `API_URL`. Sampling needs both the FastAPI
  workers and a reachable model server. The launch script supplies both.
- `LLM_BIAS_DB` must be exported for every CLI call. Sourcing `env.sh` does this.
- There are no timestamps on runs, so status cannot compute elapsed time or ETA.
- An empty completion is valid data and is stored as `raw=''`; it is not a
  sampling failure.
