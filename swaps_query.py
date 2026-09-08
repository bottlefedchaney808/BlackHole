"""Query layer for swaps database."""
import sqlite3
import threading
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
DB_PATH = os.environ.get('SWAPS_DB_PATH')
if DB_PATH is None:
    DB_PATH = os.path.join(os.path.dirname(__file__), 'swaps.db')
    logger.warning("SWAPS_DB_PATH unset; using repo-root recent-window DB")

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

# In-process caches for search_trades()'s expensive COUNT(*) and the
# swaps.html filter dropdowns (distinct regulator/asset_class lists).
# Both are process-global (not per-SwapsQuery-instance) so every request
# handler in the dashboard's threadpool shares one cache regardless of how
# many SwapsQuery objects it constructs. Guarded by a plain threading.Lock
# since FastAPI runs sync `def` routes like `/swaps` in a worker threadpool,
# not a single event-loop thread -- concurrent requests are the normal case,
# not an edge case.
#
# Counts may lag reality by up to the TTL below after a write (e.g. a
# scheduled ingest poll landing new rows). Rows returned for the current
# page are never cached -- only the total/pages figures used for pagination
# math can be stale, and only within that TTL window.
_COUNT_CACHE_LOCK = threading.Lock()
_COUNT_CACHE: dict = {}
_COUNT_CACHE_TTL_SEC = 15.0

_FILTER_OPTIONS_CACHE_LOCK = threading.Lock()
_FILTER_OPTIONS_CACHE: dict = {}
_FILTER_OPTIONS_CACHE_TTL_SEC = 60.0


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

    #: Columns callers may sort search_trades() results by, mapped to their
    #: real qualified SQL column. This allowlist is what makes sort_by safe
    #: to take directly from a query string -- anything not a key here
    #: silently falls back to the default rather than being interpolated.
    SEARCH_SORT_COLUMNS = {
        'effective_date': 'st.effective_date',
        'ingested_at': 'st.ingested_at',
        'notional_amount_leg1': 'st.notional_amount_leg1',
        'price': 'st.price',
        'dissemination_id': 'st.dissemination_id',
        'regulator': 'st.regulator',
        'asset_class': 'st.asset_class',
        'company_name': 'ur.company_name',
        'ticker': 'ur.ticker',
    }

    #: swap_trades columns returned by search_trades(), before the
    #: upi_reference join columns (company_name, ticker) and the computed
    #: match_field are appended.
    SEARCH_COLUMNS = (
        'dissemination_id', 'regulator', 'asset_class', 'action_type', 'event_type',
        'effective_date', 'expiration_date', 'cleared', 'notional_amount_leg1',
        'notional_currency_leg1', 'price', 'underlying_asset_name', 'upi',
        'upi_underlier_name', 'ingested_at',
    )

    #: Cap on how many matching rows search_trades() will actually count.
    #: A plain `COUNT(*)` over a broad filter (e.g. regulator=SEC on a
    #: 71M-row table) has to walk every matching index entry -- tens of
    #: millions of rows -- which is what made cold /swaps requests exceed
    #: 120s even with the (recency-limited) result cache below. Counting is
    #: now done via `SELECT 1 ... LIMIT cap+1` wrapped in COUNT(*): SQLite
    #: stops scanning as soon as cap+1 rows are found, so the count is
    #: bounded no matter how large the true match set is. When the bounded
    #: count comes back at cap+1, the true total is unknown -- callers get
    #: `count_is_exact=False` and `total` set to the cap, not a fabricated
    #: exact number. Overridable via SWAPS_SEARCH_COUNT_CAP for tests/tuning.
    SEARCH_COUNT_CAP = int(os.environ.get('SWAPS_SEARCH_COUNT_CAP', '10000'))

    #: sort_by keys with no usable swap_trades-side index. `notional_amount_leg1`
    #: and `price` have no index at all; `company_name`/`ticker` are only
    #: indexed on upi_reference, not on the swap_trades side of the join, so
    #: ordering the *joined* result by them still can't be satisfied by that
    #: index. On a 71M-row table, `ORDER BY <these> LIMIT ? OFFSET ?` makes
    #: SQLite pull every filter-matching row into a temp B-tree to sort
    #: before LIMIT applies -- unbounded work regardless of page size, the
    #: same failure mode migration 006 fixed for the regulator+ingested_at
    #: combo. A free-text `query` has the identical problem: the 5-column
    #: LIKE (including two joined columns) can't use an index either. Both
    #: cases fall back to the bounded-window pipeline below instead of a
    #: plain filtered scan.
    BOUNDED_SORT_COLUMNS = frozenset({'notional_amount_leg1', 'price', 'company_name', 'ticker'})

    #: Row cap for the bounded-window pipeline used by free-text search and
    #: by BOUNDED_SORT_COLUMNS sorts. Rather than let SQLite scan-and-sort
    #: the full table (unbounded, and unbounded-fast-when-sparse -- a rare
    #: search term can force nearly a full 71M-row scan just to confirm
    #: there aren't enough matches to hit SEARCH_COUNT_CAP), these paths
    #: first pull the most recent SEARCH_WINDOW_CAP rows via the existing
    #: (ingested_at DESC, dissemination_id DESC) index -- an O(cap) indexed
    #: LIMIT scan, not a table scan -- and only then apply the unindexed
    #: LIKE filter / ORDER BY to that bounded set. This trades full-history
    #: completeness for a hard bound on worst-case work: search/unindexed
    #: sort results only cover the most recent SEARCH_WINDOW_CAP ingested
    #: rows (see `windowed` in the returned dict). Benchmarked against the
    #: production 342GB/71M-row swaps.db: the inner windowed scan alone
    #: takes ~0.4-1.2s for cap 20k-100k, and the outer LIKE/sort over that
    #: bounded set is sub-100ms. Overridable via SWAPS_SEARCH_WINDOW_CAP.
    SEARCH_WINDOW_CAP = int(os.environ.get('SWAPS_SEARCH_WINDOW_CAP', '100000'))

    def search_trades(self, query: str = None, regulator: str = None,
                      asset_class: str = None, cleared: bool = None,
                      effective_date_from: str = None, effective_date_to: str = None,
                      sort_by: str = 'ingested_at', sort_dir: str = 'desc',
                      page: int = 1, per_page: int = 50) -> dict:
        """Parameterized free-text + filtered search over swap_trades, joined
        to upi_reference (via upi) for company_name/ticker.

        `query`, if given, is matched (case-insensitive substring) against
        upi, ur.ticker, ur.company_name, upi_underlier_name (the raw DTCC
        underlier name) and underlying_asset_name -- a single search box can
        therefore hit a UPI code, a resolved ticker/company, or the raw
        underlier text DTCC actually sent. Each returned row carries
        `match_field`, naming which of those columns matched first (in that
        priority order) -- without it, a company-name hit and a raw-text hit
        that happens to contain the same substring are visually
        indistinguishable, which is exactly the kind of ambiguity a search
        UI needs to surface rather than hide.

        `sort_by` must be a key of SEARCH_SORT_COLUMNS; anything else falls
        back to 'ingested_at'. Every value (query, filters, sort selection,
        limit/offset) is bound as a parameter or resolved through the
        allowlist above -- none of it is ever interpolated into the SQL
        text -- so this is safe against SQL injection despite taking
        free-form input.

        Pagination is deterministic: `dissemination_id DESC` is always
        appended as a tiebreaker after the primary sort column (unless that
        *is* the primary column), so rows with equal sort values don't
        reorder between pages.

        Returns {'rows': [...], 'total', 'count_is_exact', 'has_more',
        'count_cap', 'page', 'per_page', 'pages', 'sort_by', 'sort_dir'}.

        `total` is always safe to render, but its meaning depends on
        `count_is_exact`: when True it's the real row count; when False the
        real count is >= SEARCH_COUNT_CAP and `total`/`pages` reflect the cap,
        not reality -- callers (the dashboard template) must not present it
        as an exact total in that case, and should treat `has_more` (i.e.
        `not count_is_exact`) as "there may be additional pages beyond
        `pages`" rather than disabling further pagination.
        """
        start_time = time.time()
        page = max(1, int(page or 1))
        per_page = min(500, max(10, int(per_page or 50)))
        sort_by = sort_by if sort_by in self.SEARCH_SORT_COLUMNS else 'ingested_at'
        sort_col = self.SEARCH_SORT_COLUMNS[sort_by]
        sort_dir = 'ASC' if str(sort_dir).lower() == 'asc' else 'DESC'

        # Only the indexed filters (regulator/asset_class/cleared/date) --
        # kept separate from the free-text `query` clause so both the
        # bounded-window inner query below and the plain (non-windowed)
        # path can reuse exactly this WHERE.
        filter_clauses = []
        filter_params: list = []
        if regulator:
            filter_clauses.append('st.regulator = ?')
            filter_params.append(regulator)
        if asset_class:
            filter_clauses.append('st.asset_class = ?')
            filter_params.append(asset_class)
        if cleared is not None:
            filter_clauses.append('st.cleared = ?')
            filter_params.append(1 if cleared else 0)
        if effective_date_from:
            filter_clauses.append('st.effective_date >= ?')
            filter_params.append(str(effective_date_from))
        if effective_date_to:
            filter_clauses.append('st.effective_date <= ?')
            filter_params.append(str(effective_date_to))

        query = (query or '').strip()
        query_clause = None
        query_params: list = []
        match_field_sql = 'NULL'
        if query:
            like = f'%{query}%'
            query_clause = (
                '(st.upi LIKE ? OR ur.ticker LIKE ? OR ur.company_name LIKE ? '
                'OR st.upi_underlier_name LIKE ? OR st.underlying_asset_name LIKE ?)'
            )
            query_params = [like, like, like, like, like]
            match_field_sql = (
                "CASE "
                "WHEN st.upi LIKE ? THEN 'upi' "
                "WHEN ur.ticker LIKE ? THEN 'ticker' "
                "WHEN ur.company_name LIKE ? THEN 'company_name' "
                "WHEN st.upi_underlier_name LIKE ? THEN 'raw_underlier_name' "
                "WHEN st.underlying_asset_name LIKE ? THEN 'underlying_asset_name' "
                "ELSE NULL END"
            )
        # select_params binds the match_field CASE's own placeholders (in
        # the SELECT list); query_params binds the WHERE clause's -- same
        # values, two distinct sets of `?`s in the SQL text.
        select_params: list = list(query_params)

        filter_where_sql = ('WHERE ' + ' AND '.join(filter_clauses)) if filter_clauses else ''
        tiebreak = '' if sort_col == 'st.dissemination_id' else ', st.dissemination_id DESC'
        join_sql = 'LEFT JOIN upi_reference ur ON ur.upi = st.upi'
        select_cols = ', '.join(f'st.{c}' for c in self.SEARCH_COLUMNS) + ', ur.company_name, ur.ticker'
        window_cap = self.SEARCH_WINDOW_CAP
        inner_cols = ', '.join(f'st.{c}' for c in self.SEARCH_COLUMNS)
        # Bounded-window inner query: the most recent `window_cap` rows
        # matching the indexed filters, via the same (ingested_at DESC,
        # dissemination_id DESC) index the default listing already uses --
        # an O(window_cap) indexed LIMIT scan, never O(table). See
        # BOUNDED_SORT_COLUMNS/SEARCH_WINDOW_CAP docstrings above.
        window_inner_sql = (
            f'SELECT {inner_cols} FROM swap_trades st {filter_where_sql} '
            'ORDER BY st.ingested_at DESC, st.dissemination_id DESC LIMIT ?'
        )

        # A free-text `query` forces windowing for both the count and the
        # row select (its LIKE can't use an index either way). A
        # BOUNDED_SORT_COLUMNS sort_by only affects the row select -- COUNT
        # has no ORDER BY, so an unindexed sort doesn't make counting any
        # slower and can still use the plain (fast, existing) count path.
        select_windowed = bool(query) or sort_by in self.BOUNDED_SORT_COLUMNS
        count_windowed = bool(query)

        if select_windowed:
            base_sql = f'FROM ({window_inner_sql}) st {join_sql}'
            base_params = list(filter_params) + [window_cap]
        else:
            base_sql = f'FROM swap_trades st {join_sql} {filter_where_sql}'
            base_params = list(filter_params)
        select_where_sql = f'WHERE {query_clause}' if query else ''
        select_sql = (
            f'SELECT {select_cols}, {match_field_sql} AS match_field {base_sql} {select_where_sql} '
            f'ORDER BY {sort_col} {sort_dir}{tiebreak} LIMIT ? OFFSET ?;'
        )
        select_full_params = select_params + base_params + (list(query_params) if query else [])

        # COUNT(*) only needs the upi_reference join when the free-text
        # `query` filter is active -- that's the only thing that references
        # ur.*. regulator/asset_class/cleared/date filters and pagination
        # never touch ur.*, so for a plain filtered listing (the common
        # case), skipping the join lets SQLite satisfy COUNT(*) from
        # swap_trades' own indexes alone instead of probing upi_reference
        # once per matching row -- on a 1.9M-row bench table this cut a
        # regulator-filtered count from ~540ms to ~30ms.
        if count_windowed:
            count_base_sql = f'FROM ({window_inner_sql}) st {join_sql}'
            count_base_params = list(filter_params) + [window_cap]
        else:
            count_base_sql = f'FROM swap_trades st {filter_where_sql}'
            count_base_params = list(filter_params)
        count_where_sql = f'WHERE {query_clause}' if query else ''
        count_cap = self.SEARCH_COUNT_CAP
        # Bounded count: cap the number of matching rows SQLite will ever
        # walk to produce a count. The inner query stops at cap+1 rows (the
        # "+1" is how we distinguish "exactly cap matches" from "more than
        # cap matches" without an extra query), and the outer COUNT(*) just
        # counts whatever the subquery returned -- never O(true match count).
        # When count_windowed, that "matching rows" universe is itself
        # already bounded to window_cap rows, not the full table -- see
        # count_is_exact handling below, which is forced False in that case.
        count_sql = (
            f'SELECT COUNT(*) AS n FROM (SELECT 1 {count_base_sql} {count_where_sql} LIMIT ?) AS capped;'
        )
        count_full_params = count_base_params + (list(query_params) if query else [])

        # Cache key covers db_path plus every filter value that changes the
        # WHERE clause -- query text, regulator/asset_class/cleared/date
        # range -- but deliberately excludes sort_by/sort_dir/page/per_page,
        # since none of those affect the row count. Repeated requests that
        # only page or re-sort through the same filtered result set reuse
        # the cached total instead of re-running COUNT(*).
        count_cache_key = (
            self.db_path, query, regulator, asset_class, cleared,
            effective_date_from, effective_date_to,
        )

        try:
            conn = self.get_connection()
            try:
                cur = conn.cursor()

                counted = None  # (total, count_is_exact)
                now = time.time()
                with _COUNT_CACHE_LOCK:
                    cached = _COUNT_CACHE.get(count_cache_key)
                    if cached is not None and (now - cached[2]) < _COUNT_CACHE_TTL_SEC:
                        counted = (cached[0], cached[1])

                if counted is None:
                    cur.execute(count_sql, count_full_params + [count_cap + 1])
                    n = cur.fetchone()['n']
                    if n > count_cap:
                        counted = (count_cap, False)
                    else:
                        counted = (n, True)
                    with _COUNT_CACHE_LOCK:
                        _COUNT_CACHE[count_cache_key] = (counted[0], counted[1], now)

                total, count_is_exact = counted

                offset = (page - 1) * per_page
                cur.execute(select_sql, select_full_params + [per_page, offset])
                rows = [dict(r) for r in cur.fetchall()]
                cur.close()
            finally:
                conn.close()

            duration = time.time() - start_time
            self._monitor_query(select_sql, tuple(select_full_params + [per_page, offset]),
                                duration, len(rows))
            pages = max(1, (total + per_page - 1) // per_page) if total else 1
            return {
                'rows': rows, 'total': total, 'count_is_exact': count_is_exact,
                'has_more': not count_is_exact, 'count_cap': count_cap,
                'page': page, 'per_page': per_page,
                'pages': pages, 'sort_by': sort_by, 'sort_dir': sort_dir.lower(),
            }
        except Exception as e:
            duration = time.time() - start_time
            self._monitor_query(select_sql, None, duration, 0, error=str(e))
            raise

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
