"""Query layer for swaps database."""
import sqlite3
import time
from datetime import datetime, timedelta, date
import logging
import os

from shared.logging import setup_logging, log_operation, get_metrics
from shared.query_monitor import monitor_query, get_query_monitor
from shared.connection_pool import get_pool

# Setup structured JSON logging
logger = setup_logging(
    name='swaps_query',
    level=logging.INFO,
    use_json=True,
)

# Database file path (SWAPS_DB_PATH env var overrides, e.g. for a mounted
# Docker volume; see .env.example / docker-compose.yml)
DB_PATH = os.environ.get('SWAPS_DB_PATH') or os.path.join(os.path.dirname(__file__), 'swaps.db')

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


class SwapsQuery:
    """Query interface for swaps database.

    Supports connection pooling for high-throughput concurrent reads.
    Queries are non-blocking under WAL mode; readers and writers contend minimally.
    """

    def __init__(self, db_path: str = None, use_pool: bool = True):
        """
        Initialize SwapsQuery.

        Args:
            db_path: Path to SQLite database (default from DB_PATH)
            use_pool: Whether to use connection pool if available (default True)
        """
        self.db_path = db_path or DB_PATH
        self.use_pool = use_pool
        self.query_monitor = get_query_monitor()

    def get_connection(self):
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

    def _monitor_query(self, query: str, params: tuple = None, duration: float = 0.0,
                      row_count: int = 0, error: str = None):
        """Monitor a query execution for performance tracking."""
        from shared.query_monitor import fingerprint_query, extract_table_names, SlowQueryRecord, parse_explain_query_plan, analyze_query_plan
        from datetime import timezone

        # Check if slow
        if duration >= self.query_monitor.slow_query_threshold_sec or error:
            fingerprint = fingerprint_query(query)
            tables = extract_table_names(query)

            # Capture EXPLAIN QUERY PLAN if slow and no error
            explain_plan = None
            plan_analysis = None
            if duration >= self.query_monitor.slow_query_threshold_sec and not error:
                try:
                    pool = get_pool() if self.use_pool else None
                    use_pool_context = pool and pool.db_path == self.db_path

                    if use_pool_context:
                        with pool.get_connection_context() as conn:
                            cur = conn.cursor()
                            explain_query = f'EXPLAIN QUERY PLAN {query}'
                            cur.execute(explain_query, params or ())
                            plan_output = cur.fetchall()
                            explain_plan = parse_explain_query_plan(plan_output)
                            plan_analysis = analyze_query_plan(explain_plan)
                            cur.close()
                    else:
                        conn = self.get_connection()
                        try:
                            cur = conn.cursor()
                            explain_query = f'EXPLAIN QUERY PLAN {query}'
                            cur.execute(explain_query, params or ())
                            plan_output = cur.fetchall()
                            explain_plan = parse_explain_query_plan(plan_output)
                            plan_analysis = analyze_query_plan(explain_plan)
                            cur.close()
                        finally:
                            conn.close()
                except Exception as e:
                    logger.warning(
                        'Failed to capture EXPLAIN plan',
                        extra={'error': str(e)}
                    )

            # Create record
            record = SlowQueryRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                query_fingerprint=fingerprint,
                query_text=query[:500],
                duration_sec=duration,
                row_count=row_count,
                error=error,
                explain_plan=explain_plan,
                plan_analysis=plan_analysis,
                tables=tables,
                params=params,
            )
            self.query_monitor.record_slow_query(record)

    def query_by_upi(self, upi: str, days_back: int = 30):
        """Get all swap trades for a UPI over last N days."""
        start_time = time.time()
        query = f"""
            SELECT dissemination_id, regulator, asset_class, action_type, event_type,
                   effective_date, expiration_date, cleared,
                   notional_amount_leg1, notional_currency_leg1,
                   notional_amount_leg2, notional_currency_leg2,
                   price, price_currency, price_unit_of_measure,
                   underlier_id_leg1, underlying_asset_name,
                   upi, upi_fisn, upi_underlier_name
            FROM swap_trades
            WHERE upi = ? AND effective_date >= date('now', '-{days_back} days')
            ORDER BY effective_date DESC;
        """
        params = (upi,)
        try:
            if not PANDAS_AVAILABLE:
                result = self._query_by_upi_raw(upi, days_back)
                duration = time.time() - start_time
                metrics = get_metrics()
                result_len = len(result) if isinstance(result, list) else len(result)
                metrics.add_query_execution('query_by_upi', duration)
                self._monitor_query(query, params, duration, result_len)
                logger.info(
                    "Query by UPI completed",
                    extra={
                        'upi': upi,
                        'days_back': days_back,
                        'result_count': result_len,
                        'duration_sec': duration,
                    }
                )
                return result

            import pandas as pd
            conn = self.get_connection()
            try:
                df = pd.read_sql(query, conn, params=params)
                duration = time.time() - start_time
                metrics = get_metrics()
                metrics.add_query_execution('query_by_upi', duration)
                self._monitor_query(query, params, duration, len(df))
                logger.info(
                    "Query by UPI completed",
                    extra={
                        'upi': upi,
                        'days_back': days_back,
                        'result_count': len(df),
                        'duration_sec': duration,
                    }
                )
                return df
            finally:
                conn.close()
        except Exception as e:
            duration = time.time() - start_time
            self._monitor_query(query, params, duration, 0, error=str(e))
            logger.error(
                f"Query by UPI failed: {e}",
                extra={
                    'upi': upi,
                    'days_back': days_back,
                    'error_type': type(e).__name__,
                    'duration_sec': duration,
                }
            )
            metrics = get_metrics()
            metrics.add_error(type(e).__name__)
            raise

    def _query_by_upi_raw(self, upi: str, days_back: int = 30):
        """Get swap trades for a UPI without pandas (fallback)."""
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        if use_pool_context:
            with pool.get_connection_context() as conn:
                return self._execute_query_by_upi_raw(conn, upi, days_back)
        else:
            conn = self.get_connection()
            try:
                return self._execute_query_by_upi_raw(conn, upi, days_back)
            finally:
                conn.close()

    def _execute_query_by_upi_raw(self, conn: sqlite3.Connection, upi: str, days_back: int) -> list:
        """Execute query against a connection."""
        cur = conn.cursor()
        try:
            query = f"""
                SELECT dissemination_id, regulator, asset_class, action_type, event_type,
                       effective_date, expiration_date, cleared,
                       notional_amount_leg1, notional_currency_leg1,
                       notional_amount_leg2, notional_currency_leg2,
                       price, price_currency, price_unit_of_measure,
                       underlier_id_leg1, underlying_asset_name,
                       upi, upi_fisn, upi_underlier_name
                FROM swap_trades
                WHERE upi = ? AND effective_date >= date('now', '-{days_back} days')
                ORDER BY effective_date DESC;
            """
            cur.execute(query, (upi,))
            results = cur.fetchall()
            return [dict(r) for r in results]
        finally:
            cur.close()

    def query_by_date(self, query_date: date):
        """Get all swap trades for a specific effective date."""
        start_time = time.time()
        query = """
            SELECT dissemination_id, regulator, asset_class, action_type, event_type,
                   effective_date, expiration_date, cleared,
                   notional_amount_leg1, notional_currency_leg1,
                   notional_amount_leg2, notional_currency_leg2,
                   price, price_currency, price_unit_of_measure,
                   underlier_id_leg1, underlying_asset_name,
                   upi, upi_fisn, upi_underlier_name
            FROM swap_trades
            WHERE effective_date = ?
            ORDER BY notional_amount_leg1 DESC;
        """
        params = (str(query_date),)
        try:
            if not PANDAS_AVAILABLE:
                result = self._query_by_date_raw(query_date)
                duration = time.time() - start_time
                self._monitor_query(query, params, duration, len(result) if isinstance(result, list) else 0)
                return result

            import pandas as pd
            conn = self.get_connection()
            try:
                df = pd.read_sql(query, conn, params=params)
                duration = time.time() - start_time
                self._monitor_query(query, params, duration, len(df))
                return df
            finally:
                conn.close()
        except Exception as e:
            duration = time.time() - start_time
            self._monitor_query(query, params, duration, 0, error=str(e))
            raise

    def _query_by_date_raw(self, query_date: date):
        """Get swap trades by effective date without pandas (fallback)."""
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        if use_pool_context:
            with pool.get_connection_context() as conn:
                return self._execute_query_by_date_raw(conn, query_date)
        else:
            conn = self.get_connection()
            try:
                return self._execute_query_by_date_raw(conn, query_date)
            finally:
                conn.close()

    def _execute_query_by_date_raw(self, conn: sqlite3.Connection, query_date: date) -> list:
        """Execute query against a connection."""
        cur = conn.cursor()
        try:
            query = """
                SELECT dissemination_id, regulator, asset_class, action_type, event_type,
                       effective_date, expiration_date, cleared,
                       notional_amount_leg1, notional_currency_leg1,
                       notional_amount_leg2, notional_currency_leg2,
                       price, price_currency, price_unit_of_measure,
                       underlier_id_leg1, underlying_asset_name,
                       upi, upi_fisn, upi_underlier_name
                FROM swap_trades
                WHERE effective_date = ?
                ORDER BY notional_amount_leg1 DESC;
            """
            cur.execute(query, (str(query_date),))
            results = cur.fetchall()
            return [dict(r) for r in results]
        finally:
            cur.close()

    def get_upi_summary(self, upi: str, days_back: int = 30) -> dict:
        """Get summary stats for a UPI."""
        start_time = time.time()
        query = f"""
            SELECT
                COUNT(*) as record_count,
                SUM(notional_amount_leg1) as total_notional,
                AVG(price) as avg_price,
                COUNT(DISTINCT effective_date) as trading_days,
                MIN(effective_date) as first_date,
                MAX(effective_date) as last_date
            FROM swap_trades
            WHERE upi = ? AND effective_date >= date('now', '-{days_back} days');
        """
        params = (upi,)
        try:
            conn = self.get_connection()
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                result = cur.fetchone()
                duration = time.time() - start_time
                self._monitor_query(query, params, duration, 1 if result else 0)
                return dict(result) if result else {}
            finally:
                cur.close()
                conn.close()
        except Exception as e:
            duration = time.time() - start_time
            self._monitor_query(query, params, duration, 0, error=str(e))
            raise

    def top_notional_products(self, query_date: date = None, limit: int = 50):
        """Get top products by total notional for a given effective date."""
        start_time = time.time()
        if not query_date:
            query_date = date.today() - timedelta(days=1)

        query = """
            SELECT COALESCE(upi_underlier_name, underlying_asset_name) as product,
                   SUM(notional_amount_leg1) as total_notional,
                   COUNT(*) as trade_count
            FROM swap_trades
            WHERE effective_date = ?
            GROUP BY product
            ORDER BY total_notional DESC
            LIMIT ?;
        """
        params = (str(query_date), limit)
        try:
            if not PANDAS_AVAILABLE:
                result = self._top_notional_products_raw(query_date, limit)
                duration = time.time() - start_time
                self._monitor_query(query, params, duration, len(result) if isinstance(result, list) else 0)
                return result

            import pandas as pd
            conn = self.get_connection()
            try:
                df = pd.read_sql(query, conn, params=params)
                duration = time.time() - start_time
                self._monitor_query(query, params, duration, len(df))
                return df
            finally:
                conn.close()
        except Exception as e:
            duration = time.time() - start_time
            self._monitor_query(query, params, duration, 0, error=str(e))
            raise

    def _top_notional_products_raw(self, query_date: date = None, limit: int = 50):
        """Get top notional products without pandas (fallback)."""
        if not query_date:
            query_date = date.today() - timedelta(days=1)

        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        if use_pool_context:
            with pool.get_connection_context() as conn:
                return self._execute_top_notional_products_raw(conn, query_date, limit)
        else:
            conn = self.get_connection()
            try:
                return self._execute_top_notional_products_raw(conn, query_date, limit)
            finally:
                conn.close()

    def _execute_top_notional_products_raw(self, conn: sqlite3.Connection, query_date: date, limit: int) -> list:
        """Execute query against a connection."""
        cur = conn.cursor()
        try:
            query = """
                SELECT COALESCE(upi_underlier_name, underlying_asset_name) as product,
                       SUM(notional_amount_leg1) as total_notional,
                       COUNT(*) as trade_count
                FROM swap_trades
                WHERE effective_date = ?
                GROUP BY product
                ORDER BY total_notional DESC
                LIMIT ?;
            """
            cur.execute(query, (str(query_date), limit))
            results = cur.fetchall()
            return [dict(r) for r in results]
        finally:
            cur.close()

    def get_database_stats(self) -> dict:
        """Get overall database statistics."""
        start_time = time.time()
        pool = get_pool() if self.use_pool else None
        use_pool_context = pool and pool.db_path == self.db_path

        if use_pool_context:
            with pool.get_connection_context() as conn:
                return self._execute_get_database_stats(conn, start_time)
        else:
            conn = self.get_connection()
            try:
                return self._execute_get_database_stats(conn, start_time)
            finally:
                conn.close()

    def _execute_get_database_stats(self, conn: sqlite3.Connection, start_time: float) -> dict:
        """Execute database stats query against a connection."""
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) as total_records FROM swap_trades;")
            total_records = cur.fetchone()['total_records']

            cur.execute("SELECT COUNT(DISTINCT upi) as unique_upis FROM swap_trades;")
            unique_upis = cur.fetchone()['unique_upis']

            cur.execute("""
                SELECT regulator, asset_class, COUNT(*) as record_count
                FROM swap_trades
                GROUP BY regulator, asset_class
                ORDER BY record_count DESC;
            """)
            by_regulator_asset_class = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT MIN(effective_date) as earliest_date, MAX(effective_date) as latest_date FROM swap_trades;")
            date_range = cur.fetchone()

            # Handle empty table case (R1-F7: date_range values may be None)
            earliest_date = date_range['earliest_date'] if date_range else None
            latest_date = date_range['latest_date'] if date_range else None

            duration = time.time() - start_time
            # Log monitoring for stats queries
            self._monitor_query("GET_DATABASE_STATS", None, duration, 0)

            return {
                'total_records': total_records,
                'unique_upis': unique_upis,
                'by_regulator_asset_class': by_regulator_asset_class,
                'earliest_date': earliest_date,
                'latest_date': latest_date,
            }
        finally:
            cur.close()

    def export_to_csv(self, upi: str, output_file: str, days_back: int = 30):
        """Export UPI data to CSV."""
        if not PANDAS_AVAILABLE:
            logger.error("Pandas required for CSV export. Run: pip install pandas")
            return

        df = self.query_by_upi(upi, days_back)
        df.to_csv(output_file, index=False)
        logger.info(f"Exported {len(df)} rows to {output_file}")


if __name__ == '__main__':
    try:
        q = SwapsQuery()

        # Check if database has data
        stats = q.get_database_stats()
        print("\n=== Database Statistics ===")
        print(f"Total records: {stats['total_records']}")
        print(f"Unique UPIs: {stats['unique_upis']}")
        if stats['by_regulator_asset_class']:
            print("By regulator / asset class:")
            for row in stats['by_regulator_asset_class']:
                print(f"  {row['regulator']} / {row['asset_class']}: {row['record_count']}")
        if stats['earliest_date']:
            print(f"Date range: {stats['earliest_date']} to {stats['latest_date']}")

        if stats['total_records'] > 0:
            print("\n=== Top 10 Notional Products (Most Recent Date) ===")
            top = q.top_notional_products(limit=10)
            if PANDAS_AVAILABLE:
                print(top)
            else:
                for row in top:
                    print(f"{row['product']}: ${row['total_notional']:,.0f}")

            # Pick a sample UPI from the top notional products (if any) rather
            # than hardcoding a ticker, since UPIs are dataset-specific.
            sample_upi = None
            conn = q.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT upi FROM swap_trades
                WHERE upi IS NOT NULL
                ORDER BY notional_amount_leg1 DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                sample_upi = row['upi']
            conn.close()

            if sample_upi:
                print(f"\n=== Sample UPI: {sample_upi} (Last 30 days) ===")
                sample = q.query_by_upi(sample_upi, days_back=30)
                if PANDAS_AVAILABLE:
                    print(sample.head())
                else:
                    if sample:
                        print(f"Found {len(sample)} records for {sample_upi}")
                    else:
                        print(f"No data for {sample_upi} yet")

                print(f"\n=== {sample_upi} Summary ===")
                summary = q.get_upi_summary(sample_upi, days_back=30)
                if summary['record_count']:
                    print(summary)
                else:
                    print(f"No data for {sample_upi} yet")
        else:
            print("\n⚠ Database is empty. Run: python backfill.py")

    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
