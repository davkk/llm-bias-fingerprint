EXPERIMENT ?= smoke
MODEL   ?= Qwen3.5-4B-Q4_0
PROMPT  ?= random number from 1 to 100
SAMPLES ?= 10

.PHONY: run backend check test measure clean

run:
	uv run fastapi dev

backend:
	llama-server --models-dir $(HOME)/models --port 8012 --host 127.0.0.1 --models-max 1

format:
	ruff check --select I,W291,W293 --fix .
	ruff format --line-length=120 .

check:
	ty check --output-format=concise .
	ruff check --output-format=concise .

measure: ## start a run, e.g. make measure PROMPT="random color" SAMPLES=50
	curl -sf -X POST localhost:8000/runs -H 'content-type: application/json' \
		-d '{"experiment":"$(EXPERIMENT)","model":"$(MODEL)","prompts":["$(PROMPT)"],"samples_per_prompt":$(SAMPLES)}' \
		2>/dev/null \
	| jq


clean: ## remove caches
	rm -rf __pycache__ .ruff_cache
