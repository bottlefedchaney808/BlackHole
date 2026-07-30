"""Unified query builder for cross-source swap trade analytics.

Provides high-level query interfaces that abstract the data_source column,
enabling aggregations across DTCC, CME, OTC and other sources transparently.

All queries return results normalized to a common schema (same as swap_trades)
with an additional 'data_source' field for filtering/auditing by origin.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


class CrossSourceQueryBuilder:
    """Builder for cross-source swap trade queries.

    Supports:
      - Filtering by source(s) in WHERE clause
      - Aggregations (SUM, COUNT, AVG) across sources
      - Time-series queries with per-source breakdown
      - Instrument resolution across sources
    """

    def __init__(self, db_path: str):
        """Initialize with database path.

        Args:
            db_path: Path to swaps.db (same as SwapsQuery)
        """
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        """Create database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def query_by_sources(
        self,
        sources: List[str],
        days_back: int = 30,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get all trades from specified sources over last N days.

        Args:
            sources: List of source names (e.g., ['DTCC', 'CME'])
            days_back: Number of days to look back (default 30)
            limit: Maximum rows to return (default 1000)

        Returns:
            List of trade dicts with 'data_source' field
        """
        if not sources:
            return []

        sources_upper = [s.upper() for s in sources]
        placeholders = ', '.join(['?' for _ in sources_upper])

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            query = f"""
                SELECT *
                FROM swap_trades
                WHERE data_source IN ({placeholders})
                  AND effective_date >= date('now', '-{days_back} days')
                ORDER BY effective_date DESC, dissemination_id DESC
                LIMIT ?;
            """
            cur.execute(query, sources_upper + [limit])
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def aggregate_notional_by_source(
        self,
        sources: List[str],
        days_back: int = 30,
    ) -> Dict[str, float]:
        """Get sum of notional_amount_leg1 by source over last N days.

        Returns dict mapping source name -> total notional.
        Treats NULL notional as 0.

        Args:
            sources: List of source names
            days_back: Number of days to look back

        Returns:
            Dict mapping source -> total notional (float)
        """
        if not sources:
            return {}

        sources_upper = [s.upper() for s in sources]
        placeholders = ', '.join(['?' for _ in sources_upper])

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            query = f"""
                SELECT data_source,
                       SUM(COALESCE(notional_amount_leg1, 0)) AS total_notional
                FROM swap_trades
                WHERE data_source IN ({placeholders})
                  AND effective_date >= date('now', '-{days_back} days')
                GROUP BY data_source
                ORDER BY total_notional DESC;
            """
            cur.execute(query, sources_upper)
            rows = cur.fetchall()
            return {dict(r)['data_source']: dict(r)['total_notional'] for r in rows}
        finally:
            conn.close()

    def aggregate_notional_cross_source(
        self,
        sources: List[str],
        days_back: int = 30,
    ) -> float:
        """Get total notional across all specified sources.

        Args:
            sources: List of source names
            days_back: Number of days to look back

        Returns:
            Total notional as float
        """
        if not sources:
            return 0.0

        sources_upper = [s.upper() for s in sources]
        placeholders = ', '.join(['?' for _ in sources_upper])

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            query = f"""
                SELECT SUM(COALESCE(notional_amount_leg1, 0)) AS total_notional
                FROM swap_trades
                WHERE data_source IN ({placeholders})
                  AND effective_date >= date('now', '-{days_back} days');
            """
            cur.execute(query, sources_upper)
            row = cur.fetchone()
            return float(dict(row)['total_notional'] or 0)
        finally:
            conn.close()

    def timeseries_by_source(
        self,
        sources: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get daily time-series data by source.

        Returns dict mapping source name -> list of daily aggregates.
        Each daily aggregate includes: effective_date, notional_sum, trade_count.

        Args:
            sources: List of source names
            start_date: Optional start date (default 90 days ago)
            end_date: Optional end date (default today)

        Returns:
            Dict mapping source -> list of daily records
        """
        if not sources:
            return {}

        if start_date is None:
            start_date = date.today() - timedelta(days=90)
        if end_date is None:
            end_date = date.today()

        sources_upper = [s.upper() for s in sources]
        placeholders = ', '.join(['?' for _ in sources_upper])

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            query = f"""
                SELECT data_source,
                       effective_date,
                       SUM(COALESCE(notional_amount_leg1, 0)) AS notional_sum,
                       COUNT(*) AS trade_count
                FROM swap_trades
                WHERE data_source IN ({placeholders})
                  AND effective_date >= ? AND effective_date <= ?
                GROUP BY data_source, effective_date
                ORDER BY data_source, effective_date;
            """
            cur.execute(query, sources_upper + [start_date, end_date])
            rows = cur.fetchall()

            result = {source: [] for source in sources_upper}
            for r in rows:
                row_dict = dict(r)
                source = row_dict['data_source']
                if source not in result:
                    result[source] = []
                result[source].append(row_dict)

            return result
        finally:
            conn.close()

    def resolve_instrument_across_sources(
        self,
        upi: str,
        sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Find all occurrences of an instrument UPI across enabled sources.

        Useful for verifying that a single instrument is tracked consistently
        across DTCC, CME, OTC feeds (same underlier_asset_name, pricing, etc).

        Args:
            upi: UPI to search for
            sources: Optional list of sources to filter; if None, searches all

        Returns:
            List of trade records for this UPI, with data_source field
        """
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            if sources:
                sources_upper = [s.upper() for s in sources]
                placeholders = ', '.join(['?' for _ in sources_upper])
                query = f"""
                    SELECT *
                    FROM swap_trades
                    WHERE upi = ? AND data_source IN ({placeholders})
                    ORDER BY data_source, effective_date DESC;
                """
                cur.execute(query, [upi] + sources_upper)
            else:
                query = """
                    SELECT *
                    FROM swap_trades
                    WHERE upi = ?
                    ORDER BY data_source, effective_date DESC;
                """
                cur.execute(query, [upi])

            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def top_products_by_source(
        self,
        sources: List[str],
        days_back: int = 30,
        limit: int = 20,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get top N products by notional for each source.

        Args:
            sources: List of source names
            days_back: Number of days to look back
            limit: Number of top products per source

        Returns:
            Dict mapping source -> list of top products (each with product, notional, trade_count)
        """
        if not sources:
            return {}

        sources_upper = [s.upper() for s in sources]
        placeholders = ', '.join(['?' for _ in sources_upper])

        conn = self.get_connection()
        try:
            cur = conn.cursor()
            # Note: 'product' is typically asset_class or underlier_id_leg1 or similar
            # Adjust this query based on your schema
            query = f"""
                SELECT data_source,
                       asset_class AS product,
                       SUM(COALESCE(notional_amount_leg1, 0)) AS notional_sum,
                       COUNT(*) AS trade_count
                FROM swap_trades
                WHERE data_source IN ({placeholders})
                  AND effective_date >= date('now', '-{days_back} days')
                GROUP BY data_source, asset_class
                ORDER BY data_source, notional_sum DESC
                LIMIT ?;
            """
            cur.execute(query, sources_upper + [limit * len(sources_upper)])
            rows = cur.fetchall()

            result = {source: [] for source in sources_upper}
            for r in rows:
                row_dict = dict(r)
                source = row_dict['data_source']
                if len(result[source]) < limit:
                    result[source].append(row_dict)

            return result
        finally:
            conn.close()


# Convenience functions for common patterns

def get_cross_source_summary(
    db_path: str,
    sources: List[str],
    days_back: int = 30,
) -> Dict[str, Any]:
    """Get a quick summary of activity across specified sources.

    Args:
        db_path: Path to swaps.db
        sources: List of source names
        days_back: Number of days to look back

    Returns:
        Dict with keys: sources, total_notional, notional_by_source, trade_counts_by_source
    """
    builder = CrossSourceQueryBuilder(db_path)

    notional_by_source = builder.aggregate_notional_by_source(sources, days_back)
    total_notional = builder.aggregate_notional_cross_source(sources, days_back)

    conn = builder.get_connection()
    try:
        cur = conn.cursor()
        sources_upper = [s.upper() for s in sources]
        placeholders = ', '.join(['?' for _ in sources_upper])
        query = f"""
            SELECT data_source, COUNT(*) AS trade_count
            FROM swap_trades
            WHERE data_source IN ({placeholders})
              AND effective_date >= date('now', '-{days_back} days')
            GROUP BY data_source;
        """
        cur.execute(query, sources_upper)
        rows = cur.fetchall()
        trade_counts = {dict(r)['data_source']: dict(r)['trade_count'] for r in rows}
    finally:
        conn.close()

    return {
        'sources_requested': sources,
        'total_notional': total_notional,
        'notional_by_source': notional_by_source,
        'trade_counts_by_source': trade_counts,
        'days_back': days_back,
    }
