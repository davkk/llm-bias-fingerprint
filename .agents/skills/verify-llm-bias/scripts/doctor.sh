#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
BASE="${VERIFY_BASE:-/tmp/opencode/verify-llm-bias}"

fail=0
warn=0
ok() { printf 'OK   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=$((fail + 1)); }
maybe() { printf 'WARN %s\n' "$*"; warn=$((warn + 1)); }

port_busy() {
    ss -H -ltn "sport = :$1" 2>/dev/null | grep -q .
}

port_pid() {
    ss -H -ltnp "sport = :$1" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -1
}

pid_alive() {
    [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

read_pid() {
    [ -f "$1" ] && tr -dc '0-9' <"$1"
}

for cmd in uv llama-server curl jq ss; do
    if command -v "$cmd" >/dev/null; then
        ok "command $cmd"
    else
        bad "missing command $cmd"
    fi
done

if [ -x "$ROOT/.venv/bin/python" ]; then
    ok "virtualenv $ROOT/.venv"
else
    bad "missing $ROOT/.venv (run: uv sync)"
fi

if [ -x "$ROOT/.venv/bin/llm-bias" ] && "$ROOT/.venv/bin/llm-bias" --help >/dev/null 2>&1; then
    ok "llm-bias CLI"
else
    bad "llm-bias CLI does not run from $ROOT/.venv"
fi

if command -v llama-server >/dev/null; then
    ok "llama-server: $(llama-server --version 2>&1 | head -1)"
fi

MODEL="${VERIFY_MODEL:-Qwen3.5-4B-Q4_0}"
MODELS_DIR="${LLAMA_MODELS_DIR:-$HOME/models}"
if [ -f "$MODELS_DIR/$MODEL.gguf" ]; then
    ok "model file $MODELS_DIR/$MODEL.gguf"
else
    bad "missing model file $MODELS_DIR/$MODEL.gguf"
fi

if command -v brave-browser >/dev/null || [ -x /opt/brave.com/brave/brave ]; then
    ok "headless Brave available for screenshots"
else
    maybe "no Brave found; screenshots will not work, CLI and HTTP proofs still will"
fi

RUN_DIR="${1:-${VERIFY_DIR:-}}"
if [ -z "$RUN_DIR" ] && [ -L "$BASE/latest" ]; then
    RUN_DIR="$(readlink -f "$BASE/latest")"
fi

if [ -n "$RUN_DIR" ] && [ -f "$RUN_DIR/env.sh" ]; then
    # shellcheck disable=SC1090
    . "$RUN_DIR/env.sh" || true
fi

LLAMA_PORT="${LLAMA_PORT:-${VERIFY_LLAMA_PORT:-8014}}"
WEB_PORT="${WEB_PORT:-${VERIFY_WEB_PORT:-8765}}"
LLAMA_URL="${LLAMA_URL:-http://127.0.0.1:$LLAMA_PORT}"
WEB_URL="${WEB_URL:-http://127.0.0.1:$WEB_PORT}"

if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
    echo "doctor: no run directory yet"
    for port in "$LLAMA_PORT" "$WEB_PORT"; do
        if port_busy "$port"; then
            bad "port $port is already in use (not ours)"
        else
            ok "port $port free"
        fi
    done
    echo "doctor: not launched; run scripts/launch.sh"
    if [ "$fail" -gt 0 ]; then
        exit 1
    fi
    exit 0
fi

echo "doctor: run dir $RUN_DIR"

llama_pid="$(read_pid "$RUN_DIR/llama-server.pid")"
web_pid="$(read_pid "$RUN_DIR/web.pid")"

if pid_alive "$llama_pid"; then
    ok "llama-server pid $llama_pid alive"
else
    bad "llama-server pid ${llama_pid:-missing} is not alive"
fi
if pid_alive "$web_pid"; then
    ok "FastAPI pid $web_pid alive"
else
    bad "FastAPI pid ${web_pid:-missing} is not alive"
fi

for entry in "llama:$LLAMA_PORT:$llama_pid" "web:$WEB_PORT:$web_pid"; do
    name="${entry%%:*}"
    rest="${entry#*:}"
    port="${rest%%:*}"
    expected="${rest#*:}"
    actual="$(port_pid "$port")"
    if [ -z "$actual" ]; then
        bad "$name port $port has no listening process"
    elif [ -z "$expected" ]; then
        bad "$name port $port held by pid $actual but no pid file exists"
    elif [ "$actual" = "$expected" ]; then
        ok "$name port $port owned by pid $actual"
    elif [ "$(ps -o pgid= -p "$actual" 2>/dev/null | tr -d ' ')" = "$expected" ]; then
        ok "$name port $port owned by process group of pid $expected (listener $actual)"
    else
        bad "$name port $port held by pid $actual, expected pid or process group $expected"
    fi
done

if curl -fsS --max-time 3 "$LLAMA_URL/models" >/dev/null 2>&1; then
    if curl -fsS --max-time 3 "$LLAMA_URL/models" |
        jq -e --arg model "$MODEL" 'any(.data[]?; .id == $model)' >/dev/null 2>&1; then
        ok "llama-server lists $MODEL"
    else
        bad "llama-server does not list $MODEL"
    fi
else
    bad "llama-server HTTP is not answering on $LLAMA_URL"
fi

if curl -fsS --max-time 3 "$WEB_URL/progress" |
    jq -e 'type == "array"' >/dev/null 2>&1; then
    ok "FastAPI /progress returns a JSON array"
else
    bad "FastAPI /progress is not answering on $WEB_URL"
fi

DB="$RUN_DIR/evidence/data.db"
if [ -f "$DB" ]; then
    if "$ROOT/.venv/bin/python" - "$DB" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
required = {"prompt", "run", "run_prompt", "sample"}
missing = required - tables
if missing:
    raise SystemExit(f"missing tables: {sorted(missing)}")
runs = conn.execute("select count(*) from run").fetchone()[0]
samples = conn.execute("select count(*) from sample").fetchone()[0]
print(f"runs={runs} samples={samples} tables={','.join(sorted(tables))}")
PY
    then
        ok "sqlite schema readable at $DB"
    else
        bad "sqlite schema check failed at $DB"
    fi
else
    bad "missing database $DB"
fi

manifest="$RUN_DIR/evidence/manifest.txt"
launched_commit="$(sed -n 's/^git_commit=//p' "$manifest" 2>/dev/null | head -1)"
current_commit="$(git -C "$ROOT" rev-parse --short HEAD)"
if [ -n "$launched_commit" ] && [ "$launched_commit" != "$current_commit" ]; then
    maybe "instance launched at $launched_commit but HEAD is $current_commit; relaunch"
elif [ -n "$launched_commit" ]; then
    ok "instance commit matches HEAD ($current_commit)"
fi

ok "auth: none (local-only app)"

if [ "$fail" -gt 0 ]; then
    echo "doctor: $fail failure(s), $warn warning(s)"
    exit 1
fi
echo "doctor: healthy ($warn warning(s))"
exit 0
