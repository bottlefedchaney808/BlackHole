"""PreToolUse guard for credential and database files.

.env holds plaintext ThetaData credentials (per CLAUDE.md) and swaps.db is a
WAL-mode SQLite file normally only touched through shared/connection_pool.py.
Direct Edit/Write against either is rare and worth a confirmation rather than
a silent Auto Mode edit.
"""
import json
import os
import sys

PROTECTED_BASENAMES = {".env", "swaps.db", "swaps.db-wal", "swaps.db-shm"}

inp = json.load(sys.stdin)
tool_input = inp.get("tool_input") or {}
file_path = tool_input.get("file_path") or tool_input.get("path") or ""

if os.path.basename(file_path) not in PROTECTED_BASENAMES:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": (
            f"'{os.path.basename(file_path)}' is a protected file "
            "(credentials or a live WAL-mode SQLite database). Confirm this "
            "edit is intentional before continuing."
        ),
    }
}))
