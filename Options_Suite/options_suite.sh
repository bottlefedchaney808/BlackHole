#!/usr/bin/env bash
# Options Suite launcher — shared Financial_Dev_Env venv.
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(dirname "$0")"
exec "$(dirname "$0")/Financial_Dev_Env/bin/python3" main.py "$@"