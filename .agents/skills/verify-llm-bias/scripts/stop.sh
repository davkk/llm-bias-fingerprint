#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="${VERIFY_BASE:-/tmp/opencode/verify-llm-bias}"

if [ "$#" -gt 1 ]; then
    echo "usage: $0 [run-dir]" >&2
    exit 2
fi

if [ "$#" -eq 1 ]; then
    RUN_DIR="$1"
elif [ -n "${VERIFY_DIR:-}" ]; then
    RUN_DIR="$VERIFY_DIR"
elif [ -L "$BASE/latest" ]; then
    RUN_DIR="$(readlink -f "$BASE/latest")"
else
    echo "stop: no run directory; nothing to do"
    exit 0
fi

if [ ! -d "$RUN_DIR" ]; then
    echo "stop: no such run directory: $RUN_DIR" >&2
    exit 1
fi

if [ -f "$RUN_DIR/env.sh" ]; then
    # shellcheck disable=SC1090
    . "$RUN_DIR/env.sh" || true
fi

LLAMA_PORT="${LLAMA_PORT:-${VERIFY_LLAMA_PORT:-8014}}"
WEB_PORT="${WEB_PORT:-${VERIFY_WEB_PORT:-8765}}"

port_busy() {
    ss -H -ltn "sport = :$1" 2>/dev/null | grep -q .
}

stop_pid_file() {
    local pidfile="$1"
    local name="$2"
    local pid pgid

    if [ ! -f "$pidfile" ]; then
        return 0
    fi
    pid="$(tr -dc '0-9' <"$pidfile")"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$pidfile"
        return 0
    fi

    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    echo "stop: stopping $name pid $pid"
    if [ "$pgid" = "$pid" ]; then
        kill -TERM -- "-$pid" 2>/dev/null || true
    else
        kill -TERM "$pid" 2>/dev/null || true
    fi
    for _ in $(seq 1 20); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
        if [ "$pgid" = "$pid" ]; then
            kill -KILL -- "-$pid" 2>/dev/null || true
        else
            kill -KILL "$pid" 2>/dev/null || true
        fi
    fi
    rm -f "$pidfile"
}

stop_pid_file "$RUN_DIR/web.pid" FastAPI
stop_pid_file "$RUN_DIR/llama-server.pid" llama-server

for port in "$WEB_PORT" "$LLAMA_PORT"; do
    for _ in $(seq 1 20); do
        port_busy "$port" || break
        sleep 0.25
    done
    if port_busy "$port"; then
        echo "stop: warning: port $port is still in use; not killing a process this run did not start" >&2
    fi
done

echo "stop: stopped; evidence kept at $RUN_DIR/evidence"
