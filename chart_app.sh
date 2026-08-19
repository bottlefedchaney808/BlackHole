#!/usr/bin/env bash
# Native chart app launcher. Binds to localhost:8791 only.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"
unset PYTHONPATH
unset VIRTUAL_ENV

VENV_PYTHON=""
for candidate in "$SCRIPT_DIR/.venv/Scripts/python.exe" "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
        VENV_PYTHON="$candidate"
        break
    fi
done

if [ -z "$VENV_PYTHON" ]; then
    echo "Shared .venv not found at $SCRIPT_DIR/.venv — run: python -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

PORT=8791
URL="http://127.0.0.1:${PORT}"

open_url() {
    if command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    elif command -v start >/dev/null 2>&1; then
        start "" "$URL" >/dev/null 2>&1 &
    else
        echo "Open $URL in your browser."
    fi
}

( sleep 2; open_url ) &
exec "$VENV_PYTHON" -m uvicorn chart_app.server:app --host 127.0.0.1 --port "$PORT"
