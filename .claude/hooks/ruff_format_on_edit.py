"""PostToolUse hook: ruff-fix and format a .py file right after Edit/Write.

pre-commit already runs ruff at commit time, but that means lint drift is
only caught after several files have already been touched. This gives
immediate feedback on the file that was just written, using the same repo
venv pre-commit uses so results match.
"""
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe")

inp = json.load(sys.stdin)
tool_input = inp.get("tool_input") or {}
file_path = tool_input.get("file_path") or tool_input.get("path") or ""

if not file_path.endswith(".py") or not os.path.isfile(file_path):
    sys.exit(0)

python = VENV_PYTHON if os.path.isfile(VENV_PYTHON) else sys.executable
subprocess.run([python, "-m", "ruff", "check", "--fix", "--quiet", file_path])
subprocess.run([python, "-m", "ruff", "format", "--quiet", file_path])
