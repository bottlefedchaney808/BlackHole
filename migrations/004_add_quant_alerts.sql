-- 004_add_quant_alerts.sql
-- Quant Console Task 14 (Phase 3, Proactive Layer). Durable store for
-- alerts detected by dashboard/quant_alerts.py::check_for_alerts(), run by
-- an OS-level scheduled task (Windows Task Scheduler), not CronCreate --
-- see that module's docstring for why. Detection is read-only against
-- orchestrator_output/*/quant_summary.json history; this table is its only
-- write target.
--
-- Columns match the plan's own spec exactly (docs/superpowers/plans/
-- 2026-08-01-quant-console.md, Task 14):
--   id, run_id, ticker, condition, detail, created_at_utc, acknowledged
--
-- run_id is TEXT, not INTEGER/FK to orchestrator_runs.id: it is copied
-- verbatim from the triggering quant_summary.json's own `run_id` field
-- (itself a string per shared/schemas.py::QUANT_SUMMARY_REQUIRED_KEYS),
-- which is the only identifier detection has -- a suite-kind run's DB row
-- does not durably carry its output_dir (see _execute_run in
-- dashboard/app.py), so detection reads quant_summary.json files directly
-- rather than joining through orchestrator_runs.
--
-- condition encodes which module it's about (e.g. "status_degraded:vol",
-- "warnings_growing:sentiment") rather than adding a separate module
-- column, so (run_id, ticker, condition) alone is enough to dedupe -- two
-- different modules flipping in the same run are two distinct rows, not a
-- collision.

CREATE TABLE IF NOT EXISTS quant_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    condition TEXT NOT NULL,
    detail TEXT,
    created_at_utc TIMESTAMP NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

-- Idempotent-detection lookups (`does an alert for this run_id/ticker/
-- condition already exist?`) and GET /quant's "pending alerts" query
-- (Task 15) both filter on these columns.
CREATE UNIQUE INDEX IF NOT EXISTS idx_quant_alerts_dedupe
ON quant_alerts(run_id, ticker, condition);

CREATE INDEX IF NOT EXISTS idx_quant_alerts_acknowledged
ON quant_alerts(acknowledged, created_at_utc DESC);
