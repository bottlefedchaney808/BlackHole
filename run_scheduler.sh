#!/usr/bin/env bash
# Starts the DTCC live poller. Leave this window open -- it polls DTCC's
# public API every POLL_INTERVAL_MINUTES (default 5) and loads new swap
# trades automatically. Ctrl+C to stop.
# Linux/Mac equivalent of run_scheduler.bat.
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

# Only one scheduler may write to swaps.db at a time -- a second instance
# causes "database is locked" errors that silently stall ingestion progress.
# mkdir is atomic, so this is a safe single-instance lock.
LOCK_DIR="$SCRIPT_DIR/.scheduler.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "A scheduler already appears to be running (lock folder .scheduler.lock exists)."
    echo "Only run ONE instance of run_scheduler.sh at a time -- a second one will"
    echo "silently corrupt ingestion progress by racing the first for database writes."
    echo
    echo "If you're sure nothing is actually running (e.g. it crashed without"
    echo "cleaning up), delete the .scheduler.lock folder and try again."
    exit 1
fi

# Release the lock on any exit path (normal, error, or Ctrl+C).
cleanup() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

"$VENV_PYTHON" scheduled_ingest.py --start-scheduler
SCHED_EXIT=$?
if [ "$SCHED_EXIT" -ne 0 ]; then
    echo
    echo "scheduled_ingest.py exited with an error (see above)."
fi
exit "$SCHED_EXIT"
