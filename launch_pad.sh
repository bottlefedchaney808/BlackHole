#!/usr/bin/env bash
# The launch pad: one card per trading sleeve, with Launch / Stop buttons.
# Starts the dock on 127.0.0.1:8792 and opens it.
#
# This is NOT the chart (chart_app.sh, :8791). Separate processes; starting
# one does not start the other.
set -euo pipefail
cd "$(dirname "$0")"

# Hermes sessions leak PYTHONPATH/PYTHONHOME into the project venv.
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV || true

PY=".venv/bin/python"
[ -x "$PY" ] || PY=".venv/Scripts/python.exe"
if [ ! -x "$PY" ]; then
  echo "[launch_pad] .venv not found. Run: python -m venv .venv" >&2
  echo "              then: $PY -m pip install -r requirements.txt" >&2
  exit 1
fi

_up() { "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',8792))==0 else 1)" 2>/dev/null; }

# Refuse to double-launch: two docks would race artifacts/launch_dock.json.
if _up; then
  echo "[launch_pad] already running on 127.0.0.1:8792 - opening it."
else
  echo "[launch_pad] starting dock on http://127.0.0.1:8792 ..."
  nohup "$PY" -m launch_dock.server >/dev/null 2>&1 &
  for _ in $(seq 1 20); do _up && break; sleep 0.5; done
fi

URL="http://127.0.0.1:8792/"
if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
elif command -v open >/dev/null 2>&1; then open "$URL"
elif command -v start >/dev/null 2>&1; then start "$URL"
else echo "[launch_pad] open $URL"; fi

echo "[launch_pad] open. Stopping the dock does NOT stop a running sleeve."
