-- 002_add_data_source.sql
-- Phase 4 (multi-source architecture, see .claude/EXPANSION_PLAN.md 4.1)
-- tags every trade row with the adapter that produced it. DTCC is the only
-- source today, so existing rows backfill to 'DTCC' via the column default;
-- future adapters (CME, OTC, ...) will write their own source name.
ALTER TABLE swap_trades ADD COLUMN data_source TEXT NOT NULL DEFAULT 'DTCC';

CREATE INDEX IF NOT EXISTS idx_swap_trades_data_source
ON swap_trades(data_source);
