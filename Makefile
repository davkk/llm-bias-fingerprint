.PHONY: run dev backend server check test clean

run: ## sampler + API, reload off so saving a file cannot kill in-flight workers
	uv run fastapi dev --no-reload

dev: ## same server with auto-reload; every save restarts workers mid-run
	uv run fastapi dev

backend:
	llama-server --models-dir $(HOME)/models --port 8012 --host 127.0.0.1 --models-max 1

format:
	ruff check --select I,W291,W293 --fix .
	ruff format --line-length=120 .

check:
	ty check --output-format=concise .
	ruff check --output-format=concise .

server: ## launch the project llama-server in the background
	mkdir -p .run
	nohup llama-server --host 127.0.0.1 --port 8013 --models-dir ~/models --models-max 5 --parallel 3 --ctx-size 8192 --threads 8 --fit on --fit-target 3200 --fit-ctx 4096 --no-models-autoload > .run/llama-server.log 2>&1 &
	echo $$! > .run/llama-server.pid

clean: ## remove caches
	rm -rf __pycache__ .ruff_cache

reports: ## regenerate all plotly HTML reports
	uv run llm-bias report --all
