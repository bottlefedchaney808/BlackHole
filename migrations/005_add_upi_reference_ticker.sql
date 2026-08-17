-- 005_add_upi_reference_ticker.sql
-- Adds the OpenFIGI-resolved ticker to upi_reference, alongside the
-- company_name that upi_decoder.py/decode_upis.py already write. Additive
-- only: nullable column, no backfill of historical rows here (see
-- decode_upis.py::run_ticker_backfill for the bounded, resumable pass that
-- fills it in for previously-decoded 'openfigi'-tier rows).
--
-- Indexes support SwapsQuery.search_trades's free-text search (ticker,
-- company_name) and its sort allowlist (ticker, company_name), which join
-- swap_trades to upi_reference on upi.

ALTER TABLE upi_reference ADD COLUMN ticker TEXT;

CREATE INDEX IF NOT EXISTS idx_upi_reference_ticker
ON upi_reference(ticker);

CREATE INDEX IF NOT EXISTS idx_upi_reference_company_name
ON upi_reference(company_name);
