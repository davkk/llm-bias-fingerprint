#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
BASE="${VERIFY_BASE:-/tmp/opencode/verify-llm-bias}"

if [ "$#" -gt 1 ]; then
    echo "usage: $0 [run-dir]" >&2
    exit 2
fi

if [ "$#" -eq 1 ]; then
    RUN_DIR="$1"
else
    mkdir -p "$BASE"
    stamp="$(date +%Y%m%d-%H%M%S)"
    RUN_DIR="$BASE/run-$stamp"
    n=1
    while [ -e "$RUN_DIR" ]; do
        RUN_DIR="$BASE/run-$stamp-$n"
        n=$((n + 1))
    done
fi

LLAMA_PORT="${VERIFY_LLAMA_PORT:-8014}"
WEB_PORT="${VERIFY_WEB_PORT:-8765}"
MODEL="${VERIFY_MODEL:-Qwen3.5-4B-Q4_0}"
MODELS_DIR="${LLAMA_MODELS_DIR:-$HOME/models}"
MODELS_MAX="${VERIFY_LLAMA_MODELS_MAX:-5}"
LLAMA_URL="http://127.0.0.1:$LLAMA_PORT"
WEB_URL="http://127.0.0.1:$WEB_PORT"

port_busy() {
    ss -H -ltn "sport = :$1" 2>/dev/null | grep -q .
}

die() {
    echo "launch: $*" >&2
    exit 1
}

for cmd in uv llama-server curl jq ss; do
    command -v "$cmd" >/dev/null || die "missing command: $cmd"
done
[ -x "$ROOT/.venv/bin/python" ] || die "missing $ROOT/.venv; run: uv sync"
[ -f "$MODELS_DIR/$MODEL.gguf" ] || die "missing model file: $MODELS_DIR/$MODEL.gguf"
[ ! -e "$RUN_DIR" ] || die "run dir already exists: $RUN_DIR"

for port in "$LLAMA_PORT" "$WEB_PORT"; do
    if port_busy "$port"; then
        echo "launch: refusing to launch, port $port is already in use (not ours)" >&2
        ss -ltnp "sport = :$port" >&2 || true
        exit 1
    fi
done

mkdir -p "$RUN_DIR/evidence"
mkdir -p "$BASE"
ln -sfn "$RUN_DIR" "$BASE/latest"

cleanup_on_error() {
    echo "launch: failed, stopping anything this run started" >&2
    "$SCRIPT_DIR/stop.sh" "$RUN_DIR" >/dev/null 2>&1 || true
}
trap cleanup_on_error ERR INT TERM

cd "$ROOT"

setsid nohup llama-server \
    --host 127.0.0.1 --port "$LLAMA_PORT" \
    --models-dir "$MODELS_DIR" --models-max "$MODELS_MAX" \
    --parallel 3 --ctx-size 8192 --threads 8 \
    --fit on --fit-target 3200 --fit-ctx 4096 \
    --no-models-autoload \
    >"$RUN_DIR/evidence/llama-server.log" 2>&1 &
echo $! >"$RUN_DIR/llama-server.pid"

API_URL="$LLAMA_URL" LLM_BIAS_DB="$RUN_DIR/evidence/data.db" \
    setsid nohup uv run fastapi dev --no-reload --port "$WEB_PORT" \
    >"$RUN_DIR/evidence/web.log" 2>&1 &
echo $! >"$RUN_DIR/web.pid"

llama_pid="$(cat "$RUN_DIR/llama-server.pid")"
web_pid="$(cat "$RUN_DIR/web.pid")"

ready=0
for _ in $(seq 1 120); do
    if curl -fsS --max-time 2 "$LLAMA_URL/models" 2>/dev/null |
        jq -e --arg model "$MODEL" 'any(.data[]?; .id == $model)' >/dev/null 2>&1; then
        ready=1
        break
    fi
    if ! kill -0 "$llama_pid" 2>/dev/null; then
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "launch: llama-server did not list model $MODEL on $LLAMA_URL" >&2
    tail -30 "$RUN_DIR/evidence/llama-server.log" >&2 || true
    exit 1
fi

ready=0
for _ in $(seq 1 90); do
    if curl -fsS --max-time 2 "$WEB_URL/progress" 2>/dev/null |
        jq -e 'type == "array"' >/dev/null 2>&1; then
        ready=1
        break
    fi
    if ! kill -0 "$web_pid" 2>/dev/null; then
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "launch: FastAPI did not answer /progress on $WEB_URL" >&2
    tail -30 "$RUN_DIR/evidence/web.log" >&2 || true
    exit 1
fi

{
    echo "date=$(date -Is)"
    echo "run_dir=$RUN_DIR"
    echo "root=$ROOT"
    echo "git_branch=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
    echo "git_commit=$(git -C "$ROOT" rev-parse --short HEAD)"
    echo "llama_url=$LLAMA_URL"
    echo "web_url=$WEB_URL"
    echo "db=$RUN_DIR/evidence/data.db"
    echo "model=$MODEL"
    echo "models_dir=$MODELS_DIR"
    echo "models_max=$MODELS_MAX"
    echo "llama_server=$(llama-server --version 2>&1 | head -1)"
    echo "python=$("$ROOT/.venv/bin/python" --version 2>&1)"
    echo "uv=$(uv --version)"
} >"$RUN_DIR/evidence/manifest.txt"
git -C "$ROOT" status --porcelain >"$RUN_DIR/evidence/git-status.txt" || true
echo "git_dirty_files=$(wc -l <"$RUN_DIR/evidence/git-status.txt")" >>"$RUN_DIR/evidence/manifest.txt"

cat >"$RUN_DIR/env.sh" <<EOF
export ROOT=$(printf '%q' "$ROOT")
export VERIFY_DIR=$(printf '%q' "$RUN_DIR")
export LLAMA_PORT=$(printf '%q' "$LLAMA_PORT")
export WEB_PORT=$(printf '%q' "$WEB_PORT")
export LLAMA_URL=$(printf '%q' "$LLAMA_URL")
export WEB_URL=$(printf '%q' "$WEB_URL")
export API_URL=$(printf '%q' "$LLAMA_URL")
export LLM_BIAS_DB=$(printf '%q' "$RUN_DIR/evidence/data.db")
export VERIFY_MODEL=$(printf '%q' "$MODEL")
export VERIFY_LLAMA_PID_FILE=$(printf '%q' "$RUN_DIR/llama-server.pid")
export VERIFY_WEB_PID_FILE=$(printf '%q' "$RUN_DIR/web.pid")
EOF

trap - ERR INT TERM

runs="$(curl -fsS "$WEB_URL/progress" 2>/dev/null | jq 'length')"
if [ "$runs" != "0" ]; then
    echo "launch: warning: database already has $runs run(s); evidence may mix runs" >&2
fi

echo "READY"
echo "RUN_DIR=$RUN_DIR"
echo "WEB_URL=$WEB_URL"
echo "LLAMA_URL=$LLAMA_URL"
echo "LLM_BIAS_DB=$RUN_DIR/evidence/data.db"
echo "source $RUN_DIR/env.sh"
