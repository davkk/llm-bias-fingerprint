.PHONY: run backend server check test clean

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

server: ## launch the project llama-server in the background
	mkdir -p .run llama/models
	for model in Qwen3.5-4B-Q3_K_M Qwen3.5-4B-Q4_0 Qwen3.5-4B-Q5_K_M Qwen3.5-4B-Q6_K Qwen3.5-4B-Q8_0; do \
		ln -sfn "$(HOME)/models/$$model.gguf" "llama/models/$$model.gguf"; \
	done
	nohup llama-server --host 127.0.0.1 --port 8013 --models-dir llama/models --models-max 3 --parallel 3 --ctx-size 8192 --threads 8 --fit on --fit-target 3200 --fit-ctx 4096 --no-models-autoload > .run/llama-server.log 2>&1 &
	echo $$! > .run/llama-server.pid

clean: ## remove caches
	rm -rf __pycache__ .ruff_cache
