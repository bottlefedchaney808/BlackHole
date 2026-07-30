#!/usr/bin/env bash
# Sentiment Scanner launcher — sets up PYTHONPATH for Vol_Suite imports,
# uses the shared Financial_Dev_Env venv.
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(dirname "$0")/../Vol_Suite:$(dirname "$0")"
exec "$(dirname "$0")/../Financial_Dev_Env/bin/python3" main.py "$@"