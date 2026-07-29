"""Initialize SQLite database schema for DTCC swap trade data."""
import sqlite3
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'swaps.db')


def init_database(db_path: str = None):
    """Create tables if they don't exist."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    logger.info(f"Creating database at {db_path}...")

    logger.info("Creating swap_trades table...")
    cur.execute("""
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
    """)

    logger.info("Creating swap_trades indexes...")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_swap_trades_regulator_asset
        ON swap_trades(regulator, asset_class);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_swap_trades_effective_date
        ON swap_trades(effective_date);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_swap_trades_upi
        ON swap_trades(upi);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_swap_trades_ingested_at
        ON swap_trades(ingested_at DESC, dissemination_id DESC);
    """)

    logger.info("Creating ingestion_state table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_state (
            regulator TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            last_cumulative_date TEXT,
            last_live_slice_id INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (regulator, asset_class)
        );
    """)

    logger.info("Creating scrape_log table...")
    cur.execute("""
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
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_scrape_log_date
        ON scrape_log(scrape_date DESC);
    """)

    # orchestrator_runs is not DTCC data -- it is the audit trail for
    # orchestrator.py's cross-suite runs. It lives in swaps.db rather than a
    # second database file because orchestrator.py already opens this
    # connection to read swap activity for the context handoff, and one file
    # means one backup and one lock domain.
    logger.info("Creating orchestrator_runs table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orchestrator_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT,
            focus_json TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            status TEXT,
            results_json TEXT
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_orchestrator_runs_started
        ON orchestrator_runs(started_at DESC);
    """)

    logger.info("Creating upi_reference table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upi_reference (
            upi TEXT PRIMARY KEY,
            company_name TEXT,
            decode_tier TEXT,
            decode_detail TEXT,
            raw_underlier_name TEXT,
            decoded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    logger.info("Creating upi_decode_state table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upi_decode_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_rowid INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Database initialized at {db_path}")


if __name__ == '__main__':
    try:
        init_database()
        print(f"Database ready: {DB_PATH}")
        print("  Now run: python backfill.py")
    except Exception as e:
        logger.error(f"Database setup failed: {e}")
        raise
