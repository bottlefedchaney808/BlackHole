#!/usr/bin/env bash
# Grand orchestrator launcher -- runs orchestrator.py on the shared .venv.
# Linux/Mac equivalent of orchestrator.bat.
# Examples:
#   bash orchestrator.sh --unified --ticker NVDA --expiry 2026-10-16
#   bash orchestrator.sh --suite options --ticker AAPL --target-years 0.25
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"
unset PYTHONPATH
unset PYTHONHOME

# The consolidated venv puts the interpreter at .venv/bin/python (with a
# python3 symlink alongside it on most distros) -- unlike Windows, where it
# lives at .venv\Scripts\python.exe. Detect it instead of hardcoding one name.
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

if [ "$#" -eq 0 ]; then
    echo "No arguments given -- this script is normally run from a shell with flags:"
    echo "  bash orchestrator.sh --unified --ticker TICKER [--expiry YYYY-MM-DD | --target-years N] [--strike N] [--option-type call|put]"
    echo "  bash orchestrator.sh --suite options|vol|var|sentiment --ticker TICKER [...]"
    echo
    echo "Or answer these prompts for a quick unified run:"
    read -r -p "Ticker (blank to cancel): " ORCH_TICKER
    if [ -z "$ORCH_TICKER" ]; then
        echo "Cancelled."
        exit 0
    fi
    read -r -p "Expiry YYYY-MM-DD (blank = 0.25yr default): " ORCH_EXPIRY
    if [ -z "$ORCH_EXPIRY" ]; then
        "$VENV_PYTHON" orchestrator.py --unified --ticker "$ORCH_TICKER"
    else
        "$VENV_PYTHON" orchestrator.py --unified --ticker "$ORCH_TICKER" --expiry "$ORCH_EXPIRY"
    fi
    exit 0
fi

"$VENV_PYTHON" orchestrator.py "$@"
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
    echo
    echo "orchestrator.py exited with an error (see above)."
fi
exit "$EXIT_CODE"
