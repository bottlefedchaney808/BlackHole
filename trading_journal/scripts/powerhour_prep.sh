#!/bin/bash
# powerhour_prep.sh — daily intraday options look (power-hour prep).
# Runs the ThetaData options scan and prints the report (delivered verbatim).
# Portable across WSL + Windows-native layouts.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"          # repo root (script at <repo>/trading_journal/scripts)
cd "$REPO" || exit 1
PY=".venv/Scripts/python.exe"                    # Windows-native venv
[ -x "$PY" ] || PY="Financial_Dev_Env/bin/python3"  # WSL fallback
env -u PYTHONPATH -u VIRTUAL_ENV PYTHONPATH="Options_Suite:Vol_Suite:." \
  "$PY" trading_journal/scripts/powerhour_prep.py
