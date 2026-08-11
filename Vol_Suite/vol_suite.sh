#!/usr/bin/env bash
# Vol Suite launcher — shared .venv.
set -euo pipefail
cd "$(dirname "$0")"
if [ -x "../.venv/bin/python3" ]; then
  exec ../.venv/bin/python3 volatility_suite.py "$@"
elif [ -x "../.venv/bin/python" ]; then
  exec ../.venv/bin/python volatility_suite.py "$@"
else
  echo "Shared .venv not found at ../.venv -- run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi
