"""Query layer for swaps database."""
import sqlite3
from datetime import datetime, timedelta, date
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database file path
DB_PATH = os.path.join(os.path.dirname(__file__), 'swaps.db')

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


class SwapsQuery:
    """Query interface for swaps database."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH

    def get_connection(self):
        """Create database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        return conn

    def query_by_upi(self, upi: str, days_back: int = 30):
        """Get all swap trades for a UPI over last N days."""
        if not PANDAS_AVAILABLE:
            return self._query_by_upi_raw(upi, days_back)

        import pandas as pd
        conn = self.get_connection()
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
        df = pd.read_sql(query, conn, params=(upi,))
        conn.close()
        return df

    def _query_by_upi_raw(self, upi: str, days_back: int = 30):
        """Get swap trades for a UPI without pandas (fallback)."""
        conn = self.get_connection()
        cur = conn.cursor()
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
        conn.close()
        return [dict(r) for r in results]

    def query_by_date(self, query_date: date):
        """Get all swap trades for a specific effective date."""
        if not PANDAS_AVAILABLE:
            return self._query_by_date_raw(query_date)

        import pandas as pd
        conn = self.get_connection()
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
        df = pd.read_sql(query, conn, params=(str(query_date),))
        conn.close()
        return df

    def _query_by_date_raw(self, query_date: date):
        """Get swap trades by effective date without pandas (fallback)."""
        conn = self.get_connection()
        cur = conn.cursor()
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
        conn.close()
        return [dict(r) for r in results]

    def get_upi_summary(self, upi: str, days_back: int = 30) -> dict:
        """Get summary stats for a UPI."""
        conn = self.get_connection()
        cur = conn.cursor()
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
        cur.execute(query, (upi,))
        result = cur.fetchone()
        conn.close()
        return dict(result) if result else {}

    def top_notional_products(self, query_date: date = None, limit: int = 50):
        """Get top products by total notional for a given effective date."""
        if not PANDAS_AVAILABLE:
            return self._top_notional_products_raw(query_date, limit)

        import pandas as pd
        conn = self.get_connection()
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
        df = pd.read_sql(query, conn, params=(str(query_date), limit))
        conn.close()
        return df

    def _top_notional_products_raw(self, query_date: date = None, limit: int = 50):
        """Get top notional products without pandas (fallback)."""
        if not query_date:
            query_date = date.today() - timedelta(days=1)

        conn = self.get_connection()
        cur = conn.cursor()
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
        conn.close()
        return [dict(r) for r in results]

    def get_database_stats(self) -> dict:
        """Get overall database statistics."""
        conn = self.get_connection()
        cur = conn.cursor()

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

        conn.close()

        return {
            'total_records': total_records,
            'unique_upis': unique_upis,
            'by_regulator_asset_class': by_regulator_asset_class,
            'earliest_date': date_range['earliest_date'],
            'latest_date': date_range['latest_date'],
        }

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
