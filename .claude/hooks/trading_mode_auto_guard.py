"""PreToolUse guard for the trading-mode skill.

Trading mode + Auto Mode together throws a lot of errors (Jason's observation,
2026-08-20). Hooks cannot detect whether Auto Mode is currently active (the
PreToolUse hook payload carries no permission-mode field), so this can't
silently flip Auto Mode off. Instead it forces a manual confirmation prompt
on every trading-mode invocation, asking Jason to make sure Auto Mode is off
before proceeding.
"""
import json
import sys

inp = json.load(sys.stdin)
skill = (inp.get("tool_input") or {}).get("skill")

if skill != "trading-mode":
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": (
            "Trading mode + Auto Mode together throws a lot of errors. "
            "Confirm Auto Mode is off (toggle it off, or /config -> Permission "
            "Mode) before continuing."
        ),
    }
}))
