"""quant_alert_check.py

Thin CLI entry point for Task 14 of the quant-console plan
(docs/superpowers/plans/2026-08-01-quant-console.md, Phase 3's Proactive
Layer). Meant to be invoked by a plain OS-level scheduled task (Windows
Task Scheduler), never `CronCreate` and never a live Claude Code session --
see dashboard/quant_alerts.py's module docstring for why (session-scoped
schedules silently expire; this must not).

This script does not install its own schedule -- that is a deliberate,
out-of-band, one-time step the operator runs by hand (spec Phase 3 /
Task 14's own instruction: "installing the actual scheduled task on the
user's machine is an out-of-band manual step, not something this plan's
code does for them"):

    schtasks /Create /TN "QuantConsoleAlertCheck" ^
        /TR "\"<repo>\\.venv\\Scripts\\python.exe\" \"<repo>\\scripts\\quant_alert_check.py\"" ^
        /SC MINUTE /MO 15 /RL LIMITED

Adjust /MO (minutes between runs) to taste -- the spec treats this as
best-effort and explicitly not time-sensitive (Phase 3 / Error Handling),
so anywhere from a few minutes to hourly is reasonable. Remove later with
`schtasks /Delete /TN "QuantConsoleAlertCheck" /F`.

Also updates a small `.quant_alert_status.json` flag file in
orchestrator_output/ on every run (found or not) -- not part of
check_for_alerts() itself (that function's own contract is pure detection,
list[dict] in/out), but needed so a stalled watcher is discoverable
(spec Error Handling: "the only mitigation is the alert table/file's own
last-run timestamp being visible... so a stalled watcher is discoverable
on inspection rather than failing silently forever"). Task 15 (GET /quant's
alert banner) is expected to read it; this script only writes it.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import orchestrator  # noqa: E402
from dashboard.quant_alerts import check_for_alerts  # noqa: E402

STATUS_FILENAME = ".quant_alert_status.json"


def _write_status_file(output_root: str, found_count: int) -> None:
    """Best-effort; a failure here must never turn a successful detection
    run into a script failure (same posture this whole plan uses
    everywhere else for non-critical writes)."""
    try:
        os.makedirs(output_root, exist_ok=True)
        status_path = os.path.join(output_root, STATUS_FILENAME)
        tmp_path = f"{status_path}.tmp-{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({
                "last_checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "found": found_count,
            }, f)
        os.replace(tmp_path, status_path)
    except Exception as e:
        print(f"[quant_alert_check] WARNING: could not write status file: "
              f"{type(e).__name__}: {e}", file=sys.stderr)


def main() -> int:
    found = check_for_alerts(orchestrator.DB_PATH)

    for alert in found:
        print(f"[quant_alert_check] {alert['ticker']}: {alert['condition']} -- {alert['detail']}")
    if not found:
        print("[quant_alert_check] no new alerts")

    output_root = os.path.join(os.path.dirname(orchestrator.DB_PATH), "orchestrator_output")
    _write_status_file(output_root, len(found))

    return 0


if __name__ == "__main__":
    sys.exit(main())
