#!/usr/bin/env bash
# VaR Tools launcher — shared .venv. Linux/Mac equivalent of var.bat.
set -euo pipefail
cd "$(dirname "$0")"
if [ -x "../.venv/bin/python3" ]; then
  exec ../.venv/bin/python3 main.py "$@"
elif [ -x "../.venv/bin/python" ]; then
  exec ../.venv/bin/python main.py "$@"
else
  echo "Shared .venv not found at ../.venv -- run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi
