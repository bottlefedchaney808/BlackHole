"""Load swaps data into SQLite database."""
import sqlite3
import time
from datetime import date
import logging
from typing import List, Dict, Optional, Union
import os

from shared.data_source import DataSourceAdapter
from shared.logging import setup_logging, log_operation, get_metrics
from shared.connection_pool import ConnectionPool, get_pool, init_pool

# Setup structured JSON logging
logger = setup_logging(
    name='db_loader',
    level=logging.INFO,
    use_json=True,
)

# Database file path (SWAPS_DB_PATH env var overrides, e.g. for a mounted
# Docker volume; see .env.example / docker-compose.yml)
DB_PATH = os.environ.get('SWAPS_DB_PATH') or os.path.join(os.path.dirname(__file__), 'swaps.db')

# All swap_trades columns except the primary key and ingested_at (auto-managed).
# Now includes data_source to track which adapter produced each trade.
_TRADE_UPDATE_COLUMNS = [
    'original_dissemination_id', 'regulator', 'asset_class', 'action_type',
    'event_type', 'event_timestamp', 'execution_timestamp', 'effective_date',
    'expiration_date', 'cleared', 'notional_amount_leg1', 'notional_currency_leg1',
    'notional_amount_leg2', 'notional_currency_leg2', 'price', 'price_currency',
    'price_unit_of_measure', 'underlier_id_leg1', 'underlier_id_source_leg1',
    'underlying_asset_name', 'upi', 'upi_fisn', 'upi_underlier_name',
    'source_file', 'raw_json', 'data_source',
]

# Full insert column order (primary key first, then the rest).
_TRADE_INSERT_COLUMNS = ['dissemination_id'] + _TRADE_UPDATE_COLUMNS


class SwapsLoader:
    """Load and upsert swap trade data into SQLite database.

    Supports connection pooling for high-throughput concurrent ingestion.
    When a pool is initialized, upsert and state operations use pooled connections.
    Backward compatible with single-connection mode when no pool is available.
    """

    def __init__(self, db_path: str = None, use_pool: bool = True):
        """
        Initialize SwapsLoader.

        Args:
            db_path: Path to SQLite database (default from DB_PATH)
            use_pool: Whether to use connection pool if available (default True)
        """
        self.db_path = db_path or DB_PATH
        self.use_pool = use_pool

    def get_connection(self) -> sqlite3.Connection:
        """
        Acquire a database connection.

        Uses connection pool if available and enabled, otherwise creates a new connection.

        Returns:
            sqlite3.Connection configured with row_factory
        """
        if self.use_pool:
            pool = get_pool()
            if pool and pool.db_path == self.db_path:
                pooled = pool.get_connection(timeout=5.0)
                return pooled.conn

        # Fallback: create new connection (no pooling)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        return conn

    def _return_connection(self, conn: Optional[sqlite3.Connection]):
        """
        Return connection to pool if pooling is enabled.

        Args:
            conn: Connection to return (can be None)
        """
        if not conn or not self.use_pool:
            return

        pool = get_pool()
        if pool and pool.db_path == self.db_path:
            # Find the pooled wrapper and return it
            # Note: We need to track which pooled connection owns this sqlite3.Connection
            # This is handled by ConnectionPool.get_connection_context()
            pass

    def upsert_trades(self, records: List[Dict], data_source: Optional[Union[str, DataSourceAdapter]] = None) -> Dict:
        """
        Upsert records into swap_trades table (atomic via transaction).
        Returns dict with inserted/updated counts.

        Args:
            records: List of trade dicts to upsert. Each record must have
                    dissemination_id; missing columns are stored as NULL.
            data_source: Optional source name or adapter instance.
                        If a DataSourceAdapter, calls get_name() to get the source name.
                        Defaults to 'DTCC' for backward compatibility.
                        This value is stored in swap_trades.data_source for every record.

        Returns:
            Dict with 'inserted', 'updated' counts and a 'note' about accuracy.

        NOTE: We rely on SQLite's ON CONFLICT clause to atomically handle duplicates,
        not separate SELECT checks which create race conditions under concurrency.
        """
        if not records:
            logger.info("No records to insert.")
            return {'inserted': 0, 'updated': 0}

        # Resolve data_source: if it's an adapter, call get_name(); otherwise use as-is
        if isinstance(data_source, DataSourceAdapter):
            source_name = data_source.get_name()
        elif data_source is None:
            source_name = 'DTCC'  # Default for backward compatibility
        else:
            source_name = str(data_source)

        start_time = time.time()
        with log_operation(
            'upsert_trades',
            metadata={
                'batch_size': len(records),
                'data_source': source_name,
            }
        ) as op_context:
            logger.info(
                f"Upserting {len(records)} records",
                extra={
                    'batch_size': len(records),
                    'data_source': source_name,
                }
            )

            columns_sql = ", ".join(_TRADE_INSERT_COLUMNS)
            placeholders_sql = ", ".join(["?"] * len(_TRADE_INSERT_COLUMNS))
            update_set_sql = ", ".join(f"{col} = excluded.{col}" for col in _TRADE_UPDATE_COLUMNS)

            query = f"""
                INSERT INTO swap_trades ({columns_sql})
                VALUES ({placeholders_sql})
                ON CONFLICT(dissemination_id)
                DO UPDATE SET {update_set_sql};
            """

            # Use pool context manager if available
            pool = get_pool() if self.use_pool else None
            use_pool_context = pool and pool.db_path == self.db_path

            try:
                if use_pool_context:
                    with pool.get_connection_context(timeout=5.0) as conn:
                        self._execute_upsert(conn, query, records, source_name, start_time, op_context)
                else:
                    conn = self.get_connection()
                    try:
                        self._execute_upsert(conn, query, records, source_name, start_time, op_context)
                    finally:
                        conn.close()

            except Exception as e:
                logger.error(
                    f"Upsert failed: {e}",
                    extra={
                        'batch_size': len(records),
                        'data_source': source_name,
                        'error_type': type(e).__name__,
                    }
                )
                metrics = get_metrics()
                metrics.add_error(type(e).__name__)
                raise

    def _execute_upsert(self, conn: sqlite3.Connection, query: str, records: List[Dict],
                        source_name: str, start_time: float, op_context) -> Dict:
        """Execute the actual upsert logic against a connection."""
        cur = conn.cursor()
        try:
            # Start explicit transaction for atomicity
            cur.execute("BEGIN EXCLUSIVE;")

            for r in records:
                # Inject data_source into every record
                r_with_source = r.copy()
                r_with_source['data_source'] = source_name
                values = [r_with_source.get(col) for col in _TRADE_INSERT_COLUMNS]
                cur.execute(query, values)

            # Use changes() to detect how many rows were affected by last batch
            # SQLite's changes() returns total rows modified in this transaction
            conn.commit()
            total_changes = conn.total_changes
            duration = time.time() - start_time

            # Record metrics
            metrics = get_metrics()
            metrics.add_upsert_batch(len(records), duration)
            op_context.rows_affected = len(records)

            # For accurate insert/update counts, we would need to use RETURNING clause (SQLite 3.35+)
            # For now, we report the total and log a warning if counting is critical
            logger.info(
                f"Upserted {len(records)} records",
                extra={
                    'batch_size': len(records),
                    'total_changes': total_changes,
                    'duration_sec': duration,
                    'data_source': source_name,
                }
            )

            # Return conservative estimate: we can't distinguish inserts from updates without RETURNING
            return {'inserted': len(records), 'updated': 0, 'note': 'counts are estimated; use RETURNING for accuracy'}

        finally:
            cur.close()

    def get_state(self, regulator: str, asset_class: str) -> Optional[Dict]:
        """Get ingestion state for a regulator/asset_class pair."""
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        if use_pool_context:
            with pool.get_connection_context(timeout=5.0) as conn:
                return self._execute_get_state(conn, regulator, asset_class)
        else:
            conn = self.get_connection()
            try:
                return self._execute_get_state(conn, regulator, asset_class)
            finally:
                conn.close()

    def _execute_get_state(self, conn: sqlite3.Connection, regulator: str, asset_class: str) -> Optional[Dict]:
        """Execute get_state query against a connection."""
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

    def set_state(self, regulator: str, asset_class: str,
                  last_cumulative_date: str = None, last_live_slice_id: int = None) -> None:
        """Upsert ingestion state, only overwriting fields that were passed."""
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        try:
            if use_pool_context:
                with pool.get_connection_context(timeout=5.0) as conn:
                    self._execute_set_state(conn, regulator, asset_class, last_cumulative_date, last_live_slice_id)
            else:
                conn = self.get_connection()
                try:
                    self._execute_set_state(conn, regulator, asset_class, last_cumulative_date, last_live_slice_id)
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to set state: {e}")
            raise

    def _execute_set_state(self, conn: sqlite3.Connection, regulator: str, asset_class: str,
                           last_cumulative_date: Optional[str], last_live_slice_id: Optional[int]) -> None:
        """Execute set_state logic against a connection."""
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
        finally:
            cur.close()

    def log_scrape(self, scrape_date: date, fetched: int, inserted: int,
                    updated: int, errors: int, status: str, error_msg: str = None,
                    duration: int = 0) -> None:
        """Log scrape operation to scrape_log table."""
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        try:
            if use_pool_context:
                with pool.get_connection_context(timeout=5.0) as conn:
                    self._execute_log_scrape(conn, scrape_date, fetched, inserted, updated, errors, status, error_msg, duration)
            else:
                conn = self.get_connection()
                try:
                    self._execute_log_scrape(conn, scrape_date, fetched, inserted, updated, errors, status, error_msg, duration)
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Failed to log scrape: {e}")

    def _execute_log_scrape(self, conn: sqlite3.Connection, scrape_date: date, fetched: int, inserted: int,
                            updated: int, errors: int, status: str, error_msg: Optional[str], duration: int) -> None:
        """Execute log_scrape logic against a connection."""
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
        finally:
            cur.close()

    def get_last_scrape_log(self) -> Optional[Dict]:
        """Get the most recent scrape log entry."""
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        if use_pool_context:
            with pool.get_connection_context(timeout=5.0) as conn:
                return self._execute_get_last_scrape_log(conn)
        else:
            conn = self.get_connection()
            try:
                return self._execute_get_last_scrape_log(conn)
            finally:
                conn.close()

    def _execute_get_last_scrape_log(self, conn: sqlite3.Connection) -> Optional[Dict]:
        """Execute get_last_scrape_log query against a connection."""
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


if __name__ == '__main__':
    loader = SwapsLoader()
    last_log = loader.get_last_scrape_log()
    if last_log:
        print("Last scrape:")
        print(dict(last_log))
    else:
        print("No previous scrapes logged.")
