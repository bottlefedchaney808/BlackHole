"""quant_alerts.py

Task 14 of docs/superpowers/plans/2026-08-01-quant-console.md (Phase 3,
Proactive Layer): durable, no-LLM detection of two named conditions across
consecutive runs for the same ticker --

  - a module's `warnings` list growing (strictly longer than the previous
    run's for that module)
  - a module's `status` flipping from `ok` to `degraded` or `error`

`check_for_alerts()` is the whole detection surface. It is meant to run
from a plain OS-level scheduled task (Windows Task Scheduler -- see
scripts/quant_alert_check.py), not `CronCreate`: scheduled jobs made with
that tool are session-only (in-memory, gone when the originating Claude
Code session ends) and hard-expire after 7 days regardless of recurrence,
so a watchlist built on it would silently stop working with no error
surfaced anywhere (spec Phase 3 / CARL finding R1-F1). A Task Scheduler
entry has no such lifetime and needs no active session.

Design note -- why this reads quant_summary.json files directly rather
than joining through orchestrator_runs: a suite-kind run's `orchestrator_runs`
row does not durably carry its `output_dir` (only a 'unified' run's own
result dict does; see dashboard/app.py::_execute_run). Since
quant_summary.json is self-describing (run_id, ticker, created_at_utc,
modules[]), scanning `orchestrator_output/*/quant_summary.json` directly is
both simpler and correct for every run kind, cold, with no dependency on
dashboard/app.py's in-memory state.

Global Constraints (plan): this module is pure read (quant_summary.json
files, the quant_alerts table for dedup) and pure write (new quant_alerts
rows) -- it must NEVER call anything in orchestrator.py that could trigger
a new suite/orchestrator run, under any circumstance, no matter what a
detected condition looks like.
"""

import glob
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from shared.schemas import validate_quant_summary

QUANT_SUMMARY_FILENAME = "quant_summary.json"


def _default_output_root(db_path: str) -> str:
    """`orchestrator_output/` lives as a sibling of swaps.db at the repo
    root in every deployment this plan targets -- see CLAUDE.md's
    Architecture section. Kept as a *default*, not hardcoded into the
    function body, so a test (or an unusual deployment) can override it via
    the `output_root` parameter instead of monkeypatching a module
    constant.
    """
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "orchestrator_output")


def _load_summaries(output_root: str) -> List[Dict[str, Any]]:
    """Every schema-valid quant_summary.json under *output_root* (one level
    of subdirectory, matching `_write_quant_summary`'s own
    `<output_dir>/quant_summary.json` layout). Malformed/invalid files are
    skipped, never fatal -- matches this plan's established "degraded,
    never raises" posture (Task 3's extractors, Task 12's report parsing).
    """
    if not os.path.isdir(output_root):
        return []

    summaries: List[Dict[str, Any]] = []
    pattern = os.path.join(output_root, "*", QUANT_SUMMARY_FILENAME)
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            validate_quant_summary(data)
        except Exception:
            continue
        summaries.append(data)
    return summaries


def _group_by_ticker(summaries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group by ticker, each ticker's list sorted oldest-first by
    `created_at_utc` (ISO 8601 strings sort correctly as plain strings).
    """
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for summary in summaries:
        by_ticker.setdefault(summary["ticker"], []).append(summary)
    for entries in by_ticker.values():
        entries.sort(key=lambda s: s.get("created_at_utc") or "")
    return by_ticker


def _detect_pair(prev: Dict[str, Any], curr: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The two named conditions (spec Phase 3), compared module-by-module
    between one run and the immediately preceding run for the same ticker.
    `condition` bakes the module name in (e.g. "status_degraded:vol")
    rather than using a separate column, matching the quant_alerts schema
    the plan specifies (migrations/004_add_quant_alerts.sql) -- this also
    makes (run_id, ticker, condition) sufficient to dedupe per-module.
    """
    prev_by_module = {m["module"]: m for m in prev.get("modules", [])}
    findings: List[Dict[str, Any]] = []

    for mod in curr.get("modules", []):
        name = mod["module"]
        prev_mod = prev_by_module.get(name)
        if prev_mod is None:
            continue

        if len(mod.get("warnings", [])) > len(prev_mod.get("warnings", [])):
            findings.append({
                "condition": f"warnings_growing:{name}",
                "detail": (
                    f"{name}: warnings grew from "
                    f"{len(prev_mod.get('warnings', []))} to "
                    f"{len(mod.get('warnings', []))} (run {prev['run_id']} -> {curr['run_id']})"
                ),
            })

        if prev_mod.get("status") == "ok" and mod.get("status") in ("degraded", "error"):
            findings.append({
                "condition": f"status_degraded:{name}",
                "detail": (
                    f"{name}: status flipped ok -> {mod.get('status')} "
                    f"(run {prev['run_id']} -> {curr['run_id']})"
                ),
            })

    return findings


def check_for_alerts(db_path: str, output_root: Optional[str] = None) -> List[Dict[str, Any]]:
    """Scan quant_summary.json history for the two named conditions, write
    any newly-detected ones to `quant_alerts`, and return the rows just
    inserted (empty list if nothing new).

    Idempotent by construction: `migrations/004_add_quant_alerts.sql`
    defines a UNIQUE index on `(run_id, ticker, condition)`, and this
    function uses `INSERT OR IGNORE` against it -- rerunning against the
    same history is always safe, whether that's a scheduled task catching
    up after a gap or two overlapping invocations.

    Walks the *entire* sorted history for every ticker on every call
    (not just the newest pair) so a missed scheduled-task run is
    self-healing on the next one, relying on the same idempotent insert to
    avoid re-alerting on pairs already seen.
    """
    root = output_root if output_root is not None else _default_output_root(db_path)
    summaries = _load_summaries(root)
    by_ticker = _group_by_ticker(summaries)

    candidates: List[Dict[str, Any]] = []
    for ticker, entries in by_ticker.items():
        for prev, curr in zip(entries, entries[1:]):
            for finding in _detect_pair(prev, curr):
                candidates.append({
                    "run_id": curr["run_id"],
                    "ticker": ticker,
                    "condition": finding["condition"],
                    "detail": finding["detail"],
                    "created_at_utc": curr["created_at_utc"],
                })

    if not candidates:
        return []

    inserted: List[Dict[str, Any]] = []
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        cur = conn.cursor()
        for candidate in candidates:
            cur.execute(
                "INSERT OR IGNORE INTO quant_alerts "
                "(run_id, ticker, condition, detail, created_at_utc, acknowledged) "
                "VALUES (?, ?, ?, ?, ?, 0);",
                (candidate["run_id"], candidate["ticker"], candidate["condition"],
                 candidate["detail"], candidate["created_at_utc"]),
            )
            if cur.rowcount:
                inserted.append({
                    "id": cur.lastrowid,
                    "run_id": candidate["run_id"],
                    "ticker": candidate["ticker"],
                    "condition": candidate["condition"],
                    "detail": candidate["detail"],
                    "created_at_utc": candidate["created_at_utc"],
                    "acknowledged": 0,
                })
        conn.commit()
    finally:
        conn.close()

    return inserted
