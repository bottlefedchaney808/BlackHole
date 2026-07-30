-- 003_add_instrument_identifiers.sql
-- Phase 4 (instrument identifier generalization, see .claude/EXPANSION_PLAN.md 4.2)
-- Adds columns to support cross-source instrument identifier tracking and normalization.
--
-- New columns:
--   source_identifier: Non-DTCC identifier (e.g., CME code, OTC CUSIP)
--   source_identifier_type: Type of source_identifier (e.g., CME_CONTRACT_CODE, OTC_CUSIP)
--   normalized_instrument_id: Internal unique ID across all sources (populated by resolver)
--   instrument_type: Standardized type (swap, future, option, etc.)
--
-- Existing columns still used:
--   upi: DTCC-specific UPI (remains as primary DTCC identifier)
--   data_source: Source adapter name (DTCC, CME, OTC, etc.)

ALTER TABLE swap_trades ADD COLUMN source_identifier TEXT;
ALTER TABLE swap_trades ADD COLUMN source_identifier_type TEXT;
ALTER TABLE swap_trades ADD COLUMN normalized_instrument_id TEXT;
ALTER TABLE swap_trades ADD COLUMN instrument_type TEXT;

-- Index for efficient cross-source queries
CREATE INDEX IF NOT EXISTS idx_swap_trades_source_identifier
ON swap_trades(source_identifier, source_identifier_type);

-- Index for grouped queries across sources
CREATE INDEX IF NOT EXISTS idx_swap_trades_normalized_instrument_id
ON swap_trades(normalized_instrument_id);

-- Index for instrument type filtering
CREATE INDEX IF NOT EXISTS idx_swap_trades_instrument_type
ON swap_trades(instrument_type);
