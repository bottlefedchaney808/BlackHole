"""Load swaps data into SQLite database."""
import sqlite3
from datetime import date
import logging
from typing import List, Dict, Optional
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database file path
DB_PATH = os.path.join(os.path.dirname(__file__), 'swaps.db')

# All swap_trades columns except the primary key and ingested_at (auto-managed).
_TRADE_UPDATE_COLUMNS = [
    'original_dissemination_id', 'regulator', 'asset_class', 'action_type',
    'event_type', 'event_timestamp', 'execution_timestamp', 'effective_date',
    'expiration_date', 'cleared', 'notional_amount_leg1', 'notional_currency_leg1',
    'notional_amount_leg2', 'notional_currency_leg2', 'price', 'price_currency',
    'price_unit_of_measure', 'underlier_id_leg1', 'underlier_id_source_leg1',
    'underlying_asset_name', 'upi', 'upi_fisn', 'upi_underlier_name',
    'source_file', 'raw_json',
]

# Full insert column order (primary key first, then the rest).
_TRADE_INSERT_COLUMNS = ['dissemination_id'] + _TRADE_UPDATE_COLUMNS


class SwapsLoader:
    """Load and upsert swap trade data into SQLite database."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH

    def get_connection(self) -> sqlite3.Connection:
        """Create database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        return conn

    def upsert_trades(self, records: List[Dict]) -> Dict:
        """
        Upsert records into swap_trades table.
        Returns dict with inserted/updated counts.
        """
        if not records:
            logger.info("No records to insert.")
            return {'inserted': 0, 'updated': 0}

        conn = self.get_connection()
        cur = conn.cursor()
        inserted = 0
        updated = 0

        columns_sql = ", ".join(_TRADE_INSERT_COLUMNS)
        placeholders_sql = ", ".join(["?"] * len(_TRADE_INSERT_COLUMNS))
        update_set_sql = ", ".join(f"{col} = excluded.{col}" for col in _TRADE_UPDATE_COLUMNS)

        query = f"""
            INSERT INTO swap_trades ({columns_sql})
            VALUES ({placeholders_sql})
            ON CONFLICT(dissemination_id)
            DO UPDATE SET {update_set_sql};
        """

        try:
            for r in records:
                cur.execute(
                    "SELECT 1 FROM swap_trades WHERE dissemination_id = ?",
                    (r.get('dissemination_id'),)
                )
                exists = cur.fetchone() is not None

                values = [r.get(col) for col in _TRADE_INSERT_COLUMNS]
                cur.execute(query, values)

                if exists:
                    updated += 1
                else:
                    inserted += 1

            conn.commit()
            logger.info(f"Upserted trades: inserted={inserted}, updated={updated}.")
            return {'inserted': inserted, 'updated': updated}

        except Exception as e:
            conn.rollback()
            logger.error(f"Upsert failed: {e}")
            raise
        finally:
            cur.close()
            conn.close()

    def get_state(self, regulator: str, asset_class: str) -> Optional[Dict]:
        """Get ingestion state for a regulator/asset_class pair."""
        conn = self.get_connection()
        cur = conn.cursor()

        try:
            cur.execute(
                "SELECT * FROM ingestion_state WHERE regulator = ? AND asset_class = ?",
                (regulator, asset_class)
            )
            result = cur.fetchone()
            return dict(result) if result else None
        finally:
            cur.close()
            conn.close()

    def set_state(self, regulator: str, asset_class: str,
                  last_cumulative_date: str = None, last_live_slice_id: int = None) -> None:
        """Upsert ingestion state, only overwriting fields that were passed."""
        conn = self.get_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO ingestion_state (
                    regulator, asset_class, last_cumulative_date, last_live_slice_id, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(regulator, asset_class)
                DO UPDATE SET
                    last_cumulative_date = COALESCE(?, last_cumulative_date),
                    last_live_slice_id = COALESCE(?, last_live_slice_id),
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                regulator, asset_class, last_cumulative_date, last_live_slice_id,
                last_cumulative_date, last_live_slice_id
            ))

            conn.commit()
            logger.info(f"Set ingestion state for {regulator}/{asset_class}.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to set state: {e}")
            raise
        finally:
            cur.close()
            conn.close()

    def log_scrape(self, scrape_date: date, fetched: int, inserted: int,
                    updated: int, errors: int, status: str, error_msg: str = None,
                    duration: int = 0) -> None:
        """Log scrape operation to scrape_log table."""
        conn = self.get_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO scrape_log (
                    scrape_date, rows_fetched, rows_inserted, rows_updated,
                    parse_errors, status, error_message, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (str(scrape_date), fetched, inserted, updated, errors, status, error_msg, duration))

            conn.commit()
            logger.info(f"Logged scrape for {scrape_date}: status={status}")
        except Exception as e:
            logger.error(f"Failed to log scrape: {e}")
        finally:
            cur.close()
            conn.close()

    def get_last_scrape_log(self) -> Optional[Dict]:
        """Get the most recent scrape log entry."""
        conn = self.get_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                SELECT * FROM scrape_log
                ORDER BY created_at DESC
                LIMIT 1;
            """)
            result = cur.fetchone()
            return dict(result) if result else None
        finally:
            cur.close()
            conn.close()


if __name__ == '__main__':
    loader = SwapsLoader()
    last_log = loader.get_last_scrape_log()
    if last_log:
        print("Last scrape:")
        print(dict(last_log))
    else:
        print("No previous scrapes logged.")
