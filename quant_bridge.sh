#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${FINDEV_PYTHON:-$ROOT/.venv/Scripts/python.exe}"
# Prefer the project's interpreter, but fall back if its compiled dependencies
# are unusable on this host (the bridge itself has no extra dependency needs).
if [[ ! -x "$PYTHON" ]] || ! "$PYTHON" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  PYTHON="$(command -v python3 || command -v python)"
fi
exec "$PYTHON" -m uvicorn quant_bridge:app --app-dir "$ROOT" --host 127.0.0.1 --port "${QUANT_BRIDGE_PORT:-8765}"
