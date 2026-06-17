#!/usr/bin/env bash
#
# dev_server.sh — debug launcher for the robot control server.
#
# Starts the web/control server with UNBUFFERED, live logs (so a crash or
# traceback flushes immediately instead of being swallowed by buffering) and
# tees everything to logs/server-<timestamp>.log. Runs in the foreground:
# Ctrl-C stops the server. logs/server.log always points at the latest run.
#
# Usage:
#   tools/dev_server.sh                      # Piper TTS (fast + crash-free)
#   TTS_BACKEND=say tools/dev_server.sh      # instant macOS voice
#   TTS_BACKEND=qwen3-mlx tools/dev_server.sh # quality voice (slow; may segfault)
#   SERVER_TLS=0 tools/dev_server.sh         # plain HTTP (localhost only)
#
set -uo pipefail
cd "$(dirname "$0")/.."   # repo root

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/server-$TS.log"

# Default to HTTPS so the phone (LAN IP) gets a secure context; override with
# SERVER_TLS=0. Faithful to .env otherwise so crashes reproduce as-is.
export SERVER_TLS="${SERVER_TLS:-1}"
export PYTHONUNBUFFERED=1
export PYTHONFAULTHANDLER=1   # dump a Python traceback even on hard faults

# Qwen3-TTS (MLX/Metal) segfaults when driven from the orchestrator's per-turn
# worker threads, and is slow. For a stable+snappy debug server, default to
# Piper (a subprocess: thread-safe + fast). Override with e.g. TTS_BACKEND=say,
# or TTS_BACKEND=qwen3-mlx to reproduce the native crash.
export TTS_BACKEND="${TTS_BACKEND:-piper}"

# Stop any previous instance.
if pgrep -f "src.server.app" >/dev/null 2>&1; then
  echo "stopping previous server..."
  pkill -f "src.server.app" 2>/dev/null || true
  sleep 1
fi

ln -sf "server-$TS.log" "$LOG_DIR/server.log"   # logs/server.log -> latest

echo "============================================================"
echo " robot dev server   (Ctrl-C to stop)"
echo "   python   : $PY"
echo "   SERVER_TLS=$SERVER_TLS   TTS_BACKEND=${TTS_BACKEND:-<from .env>}"
echo "   log file : $LOG   (also: $LOG_DIR/server.log)"
echo "============================================================"

# -u unbuffered; 2>&1 merges stderr; tee streams live AND saves to file.
exec "$PY" -u -m src.server.app 2>&1 | tee "$LOG"
