#!/usr/bin/env bash
# Launches the standalone swap-data browser and opens it in your default
# browser. Binds to localhost only. Linux/Mac equivalent of
# swaps_dashboard.bat. Split out of dashboard.sh 2026-08-27 -- see
# docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"
unset PYTHONPATH
unset PYTHONHOME

VENV_PYTHON=""
for candidate in "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
        VENV_PYTHON="$candidate"
        break
    fi
done

if [ -z "$VENV_PYTHON" ]; then
    echo "Shared .venv not found at $SCRIPT_DIR/.venv -- run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
    echo
    exit 1
fi

PORT=8788
URL="http://127.0.0.1:${PORT}/swaps"

open_url() {
    if command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    else
        echo "Open $URL in your browser."
    fi
}

if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
    exec 3<&- 2>/dev/null || true
    exec 3>&- 2>/dev/null || true
    echo "A swaps dashboard already appears to be running on port ${PORT}."
    echo "Only run ONE instance of swaps_dashboard.sh at a time."
    echo "Opening your existing swaps dashboard in the browser instead..."
    open_url
    exit 0
fi

( sleep 2; open_url ) &

"$VENV_PYTHON" -m uvicorn swaps_dashboard.app:app --host 127.0.0.1 --port "$PORT"
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
    echo
    echo "Swaps dashboard exited with an error (see above). If it says the port is"
    echo "already in use, another process already owns port ${PORT} -- edit"
    echo "swaps_dashboard.sh and pick a different --port number (and update the URL"
    echo "above it)."
fi
exit "$EXIT_CODE"
