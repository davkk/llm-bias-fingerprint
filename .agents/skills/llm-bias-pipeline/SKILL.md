---
name: llm-bias-pipeline
description: Test the llm-bias-fingerprint sampling pipeline end to end. Use when the user wants to run, smoke-test, or verify sampling/analysis for llm-bias-fingerprint.
---

# Test the llm-bias-fingerprint pipeline

## Prerequisites

- `.env` has `API_URL=http://127.0.0.1:8013`
- `~/models` contains the five project quants

## Flow

1. Start the project llama-server:

```sh
make server
```

Verify:

```sh
curl -s http://127.0.0.1:8013/models | jq
```

2. Start the app (web + 3 sampler workers):

```sh
make run
```

3. Create a run from YAML:

```sh
uv run llm-bias job run experiments/smoke.yaml
# or inspect first:
uv run llm-bias job run experiments/smoke.yaml --dry-run
```

4. Watch progress:

```sh
uv run llm-bias job status smoke
# dashboard: http://127.0.0.1:8000
```

5. Inspect results:

```sh
curl -s "http://127.0.0.1:8000/analysis?experiment=number" | jq
curl -s "http://127.0.0.1:8000/analysis?experiment=number&by=value" | jq
```

6. Stop:

```sh
kill "$(cat .run/llama-server.pid)"
# stop the FastAPI process with Ctrl-C
```

Logs: `.run/llama-server.log`.

## Notes

- `make server` creates `llama/models/` symlinks and backgrounds `llama-server`.
- The app starts sampler workers; if the server is down, runs stay queued and retry.
- Use `LLM_BIAS_DB=/tmp/...` for throwaway test databases.
