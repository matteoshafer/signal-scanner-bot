#!/bin/bash
# Keepalive wrapper — restarts scanner.py if it crashes.
# Usage: nohup bash run.sh > scanner.log 2>&1 &

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/python3"

echo "[run.sh] Starting signal scanner bot — $(date)"

while true; do
    "$VENV" -u "$DIR/scanner.py"
    EXIT=$?
    if [ $EXIT -eq 0 ]; then
        echo "[run.sh] Bot exited cleanly (Ctrl+C). Stopping."
        break
    fi
    echo "[run.sh] Bot crashed (exit $EXIT) — restarting in 10s… $(date)"
    sleep 10
done
