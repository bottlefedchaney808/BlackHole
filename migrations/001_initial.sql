-- 001_initial.sql
-- Baseline schema, as it existed before the migration framework was
-- introduced. Every statement is IF-NOT-EXISTS so this migration is safe to
-- run against a database that already has these objects (created by the
-- pre-migration setup_db.py) as well as against a brand-new empty file --
-- both end up recorded as schema v1.

-- Tracks which migrations have been applied to this database. Created here
-- for documentation purposes; the migration runner (setup_db.py) also
-- creates it directly before checking the current version, since it has to
-- exist before version 1 can even be recorded.
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL,
    migration_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS swap_trades (
    dissemination_id TEXT PRIMARY KEY,
    original_dissemination_id TEXT,
    regulator TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    action_type TEXT,
    event_type TEXT,
    event_timestamp TEXT,
    execution_timestamp TEXT,
    effective_date TEXT,
    expiration_date TEXT,
    cleared TEXT,
    notional_amount_leg1 REAL,
    notional_currency_leg1 TEXT,
    notional_amount_leg2 REAL,
    notional_currency_leg2 TEXT,
    price REAL,
    price_currency TEXT,
    price_unit_of_measure TEXT,
    underlier_id_leg1 TEXT,
    underlier_id_source_leg1 TEXT,
    underlying_asset_name TEXT,
    upi TEXT,
    upi_fisn TEXT,
    upi_underlier_name TEXT,
    source_file TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_swap_trades_regulator_asset
ON swap_trades(regulator, asset_class);

CREATE INDEX IF NOT EXISTS idx_swap_trades_effective_date
ON swap_trades(effective_date);

CREATE INDEX IF NOT EXISTS idx_swap_trades_upi
ON swap_trades(upi);

CREATE INDEX IF NOT EXISTS idx_swap_trades_ingested_at
ON swap_trades(ingested_at DESC, dissemination_id DESC);

CREATE TABLE IF NOT EXISTS ingestion_state (
    regulator TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    last_cumulative_date TEXT,
    last_live_slice_id INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (regulator, asset_class)
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scrape_date TEXT NOT NULL,
    rows_fetched INTEGER,
    rows_inserted INTEGER,
    rows_updated INTEGER,
    parse_errors INTEGER,
    status TEXT,
    error_message TEXT,
    duration_seconds INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scrape_log_date
ON scrape_log(scrape_date DESC);

-- orchestrator_runs is not DTCC data -- it is the audit trail for
-- orchestrator.py's cross-suite runs. It lives in swaps.db rather than a
-- second database file because orchestrator.py already opens this
-- connection to read swap activity for the context handoff, and one file
-- means one backup and one lock domain.
CREATE TABLE IF NOT EXISTS orchestrator_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT,
    focus_json TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status TEXT,
    results_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_started
ON orchestrator_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS upi_reference (
    upi TEXT PRIMARY KEY,
    company_name TEXT,
    decode_tier TEXT,
    decode_detail TEXT,
    raw_underlier_name TEXT,
    decoded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS upi_decode_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_rowid INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
