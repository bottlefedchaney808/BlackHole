#!/usr/bin/env bash
# Launches the live local dashboard (swap data + orchestrator control panel)
# and opens it in your default browser. Binds to localhost only.
# Linux/Mac equivalent of dashboard.bat.
#
# Note: this pins port 8787 to match dashboard.bat exactly (the dashboard's
# own README documents the uvicorn default of 8000 for a bare `uvicorn
# app:app` invocation -- dashboard.bat/.sh both override that to 8787 so the
# "already running" check below has a fixed port to probe).
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

PORT=8787
URL="http://127.0.0.1:${PORT}"

open_url() {
    if command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    else
        echo "Open $URL in your browser."
    fi
}

# Refuse to start a second dashboard on the same port -- besides just
# failing to bind, a duplicate process also doubles up writes against
# swaps.db and can cause "database is locked" errors for the scheduler.
# Bash's /dev/tcp pseudo-device lets us probe the port without depending on
# netstat/lsof/nc being installed.
if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
    exec 3<&- 2>/dev/null || true
    exec 3>&- 2>/dev/null || true
    echo "A dashboard already appears to be running on port ${PORT}."
    echo "Only run ONE instance of dashboard.sh at a time."
    echo "Opening your existing dashboard in the browser instead..."
    open_url
    exit 0
fi

# Also make sure the native chart app (chart_app, :8791) is up, since the
# Chart tab iframes it -- headless (no browser tab of its own; the
# dashboard's Chart tab is the tab that shows it). chart_app.sh run directly
# still opens its own tab exactly as it does today.
if ! (exec 3<>"/dev/tcp/127.0.0.1/8791") 2>/dev/null; then
    "$VENV_PYTHON" -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791 >/dev/null 2>&1 &
else
    exec 3<&- 2>/dev/null || true
    exec 3>&- 2>/dev/null || true
fi

# Give uvicorn a couple seconds to bind before opening the browser tab,
# so it doesn't load before anything is listening.
( sleep 2; open_url ) &

cd dashboard
"$VENV_PYTHON" -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
    echo
    echo "Dashboard exited with an error (see above). If it says the port is"
    echo "already in use or \"access forbidden\", another process or an unrelated"
    echo "service already owns port ${PORT} -- edit dashboard.sh and pick"
    echo "a different --port number (and update the URL above it)."
fi
exit "$EXIT_CODE"
