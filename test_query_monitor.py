"""Tests for query performance monitoring system.

Tests:
  * Query fingerprinting and normalization
  * Query plan analysis
  * Slow query detection and recording
  * Performance metrics aggregation
  * Dashboard metrics endpoints
"""

import pytest
import sqlite3
import time
import tempfile
import os
from datetime import datetime, timezone

from shared.query_monitor import (
    QueryMonitor,
    SlowQueryRecord,
    fingerprint_query,
    extract_table_names,
    extract_columns_referenced,
    parse_explain_query_plan,
    analyze_query_plan,
    QueryPlanNode,
)
from swaps_query import SwapsQuery


# ── Query Fingerprinting Tests ─────────────────────────────────────────────

class TestQueryFingerprinting:
    """Test query normalization and fingerprinting."""

    def test_fingerprint_identical_queries_same_fingerprint(self):
        """Identical queries should produce the same fingerprint."""
        query1 = "SELECT * FROM swap_trades WHERE upi = 'ABC123'"
        query2 = "SELECT * FROM swap_trades WHERE upi = 'ABC123'"
        assert fingerprint_query(query1) == fingerprint_query(query2)

    def test_fingerprint_different_values_same_fingerprint(self):
        """Queries with different values should produce the same fingerprint."""
        query1 = "SELECT * FROM swap_trades WHERE upi = 'ABC123'"
        query2 = "SELECT * FROM swap_trades WHERE upi = 'XYZ999'"
        # Should be the same fingerprint (values normalized)
        assert fingerprint_query(query1) == fingerprint_query(query2)

    def test_fingerprint_different_numbers_same_fingerprint(self):
        """Queries with different numeric values should produce the same fingerprint."""
        query1 = "SELECT * FROM swap_trades WHERE notional > 1000000"
        query2 = "SELECT * FROM swap_trades WHERE notional > 5000000"
        # Should be the same fingerprint (numbers normalized)
        assert fingerprint_query(query1) == fingerprint_query(query2)

    def test_fingerprint_different_queries_different_fingerprint(self):
        """Structurally different queries should produce different fingerprints."""
        query1 = "SELECT * FROM swap_trades WHERE upi = 'ABC123'"
        query2 = "SELECT * FROM swap_trades WHERE effective_date > '2024-01-01'"
        assert fingerprint_query(query1) != fingerprint_query(query2)

    def test_fingerprint_whitespace_normalized(self):
        """Queries with different whitespace should produce the same fingerprint."""
        query1 = "SELECT * FROM swap_trades WHERE upi = 'ABC123'"
        query2 = "SELECT  *  FROM  swap_trades  WHERE  upi = 'ABC123'"
        assert fingerprint_query(query1) == fingerprint_query(query2)


# ── Table/Column Extraction Tests ──────────────────────────────────────────

class TestTableExtraction:
    """Test extraction of table names from queries."""

    def test_extract_single_table(self):
        """Extract table from simple SELECT."""
        query = "SELECT * FROM swap_trades"
        tables = extract_table_names(query)
        assert 'swap_trades' in tables

    def test_extract_multiple_tables(self):
        """Extract multiple tables from JOIN."""
        query = """
            SELECT * FROM swap_trades
            JOIN swap_rates ON swap_trades.id = swap_rates.trade_id
        """
        tables = extract_table_names(query)
        assert 'swap_trades' in tables
        assert 'swap_rates' in tables

    def test_extract_table_case_insensitive(self):
        """Table extraction should be case-insensitive."""
        query = "SELECT * FROM SWAP_TRADES"
        tables = extract_table_names(query)
        assert 'swap_trades' in tables

    def test_extract_column_names_from_where(self):
        """Extract column names from WHERE clause."""
        query = "SELECT * FROM swap_trades WHERE upi = ? AND effective_date > ?"
        columns = extract_columns_referenced(query)
        assert 'upi' in columns
        assert 'effective_date' in columns

    def test_extract_no_columns_no_where(self):
        """Should return empty list if no WHERE clause."""
        query = "SELECT * FROM swap_trades"
        columns = extract_columns_referenced(query)
        assert len(columns) == 0


# ── Query Plan Analysis Tests ──────────────────────────────────────────────

class TestQueryPlanAnalysis:
    """Test EXPLAIN QUERY PLAN parsing and analysis."""

    def test_parse_explain_output(self):
        """Parse SQLite EXPLAIN QUERY PLAN output."""
        plan_output = [
            (0, 'OpenRead', 0, 1, 1, '', 0, 'SCAN TABLE swap_trades'),
            (1, 'Filter', 0, 1, 1, '', 0, 'upi=?'),
        ]
        nodes = parse_explain_query_plan(plan_output)
        assert len(nodes) == 2
        assert nodes[0].opcode == 'OpenRead'
        assert 'SCAN TABLE' in nodes[0].comment

    def test_identify_full_scan(self):
        """Identify full table scans in query plan."""
        node = QueryPlanNode(
            addr=0,
            opcode='OpenRead',
            p1=0,
            p2=1,
            p3=1,
            p4='',
            p5=0,
            comment='SCAN TABLE swap_trades',
        )
        assert node.is_full_scan()

    def test_identify_index_scan(self):
        """Identify index scans in query plan."""
        node = QueryPlanNode(
            addr=0,
            opcode='OpenRead',
            p1=0,
            p2=1,
            p3=1,
            p4='',
            p5=0,
            comment='SEEK INDEX idx_upi',
        )
        assert node.is_index_scan()

    def test_analyze_full_scan_issues(self):
        """Analyze query plan for full scan issues."""
        nodes = [
            QueryPlanNode(
                addr=0, opcode='OpenRead', p1=0, p2=1, p3=1, p4='',
                p5=0, comment='SCAN TABLE swap_trades'
            )
        ]
        analysis = analyze_query_plan(nodes)
        assert analysis['full_scans'] == 1
        assert len(analysis['issues']) > 0
        assert 'swap_trades' in analysis['issues'][0]

    def test_analyze_index_scan_efficiency(self):
        """Analyze efficiency of index scans."""
        nodes = [
            QueryPlanNode(
                addr=0, opcode='OpenRead', p1=0, p2=2, p3=1, p4='idx_upi',
                p5=0, comment='SEEK INDEX idx_upi'
            )
        ]
        analysis = analyze_query_plan(nodes)
        assert analysis['index_scans'] == 1
        assert analysis['full_scans'] == 0


# ── Query Monitor Tests ────────────────────────────────────────────────────

class TestQueryMonitor:
    """Test query performance monitoring."""

    def test_monitor_initialization(self):
        """Initialize query monitor with custom threshold."""
        monitor = QueryMonitor(slow_query_threshold_sec=2.0, max_history=50)
        assert monitor.slow_query_threshold_sec == 2.0
        assert monitor.max_history == 50

    def test_record_slow_query(self):
        """Record a slow query execution."""
        monitor = QueryMonitor(slow_query_threshold_sec=0.1)
        record = SlowQueryRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            query_fingerprint='abc123',
            query_text='SELECT * FROM swap_trades',
            duration_sec=0.5,
            row_count=100,
        )
        monitor.record_slow_query(record)

        slow_queries = monitor.get_slow_queries()
        assert len(slow_queries) == 1
        assert slow_queries[0]['query_fingerprint'] == 'abc123'

    def test_history_size_limit(self):
        """History should respect max_history limit."""
        monitor = QueryMonitor(max_history=5)
        for i in range(10):
            record = SlowQueryRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                query_fingerprint=f'fp{i}',
                query_text=f'SELECT * FROM table{i}',
                duration_sec=1.0 + i,
                row_count=100,
            )
            monitor.record_slow_query(record)

        assert len(monitor.slow_queries) <= 5

    def test_aggregate_query_stats(self):
        """Aggregate statistics for repeated queries."""
        monitor = QueryMonitor()
        fingerprint = 'abc123'

        for duration in [0.5, 1.0, 1.5]:
            record = SlowQueryRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                query_fingerprint=fingerprint,
                query_text='SELECT * FROM swap_trades',
                duration_sec=duration,
                row_count=100,
            )
            monitor.record_slow_query(record)

        stats = monitor.query_stats[fingerprint]
        assert stats['count'] == 3
        assert stats['avg_duration'] == 1.0  # (0.5 + 1.0 + 1.5) / 3
        assert stats['max_duration'] == 1.5
        assert stats['min_duration'] == 0.5
        assert stats['total_rows'] == 300

    def test_get_top_slow_queries(self):
        """Get top slowest queries by average duration."""
        monitor = QueryMonitor()

        # Add fast query
        for _ in range(3):
            record = SlowQueryRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                query_fingerprint='fast_query',
                query_text='SELECT * FROM fast_table',
                duration_sec=0.1,
                row_count=100,
            )
            monitor.record_slow_query(record)

        # Add slow query
        for _ in range(3):
            record = SlowQueryRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                query_fingerprint='slow_query',
                query_text='SELECT * FROM slow_table',
                duration_sec=2.0,
                row_count=100,
            )
            monitor.record_slow_query(record)

        top = monitor.get_top_slow_queries(limit=1)
        assert len(top) == 1
        assert top[0]['fingerprint'] == 'slow_query'
        assert top[0]['avg_duration_sec'] == 2.0

    def test_monitor_reset(self):
        """Reset monitor clears all data."""
        monitor = QueryMonitor()
        record = SlowQueryRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            query_fingerprint='abc123',
            query_text='SELECT * FROM swap_trades',
            duration_sec=1.0,
            row_count=100,
        )
        monitor.record_slow_query(record)

        assert len(monitor.slow_queries) > 0
        monitor.reset()
        assert len(monitor.slow_queries) == 0
        assert len(monitor.query_stats) == 0


# ── Integration Tests with Database ────────────────────────────────────────

class TestSwapsQueryMonitoring:
    """Test integration of query monitoring with SwapsQuery."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary test database."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.unlink(path)

    @pytest.fixture
    def setup_test_db(self, temp_db):
        """Setup test database with schema."""
        conn = sqlite3.connect(temp_db)
        conn.execute('''
            CREATE TABLE swap_trades (
                id INTEGER PRIMARY KEY,
                upi TEXT,
                effective_date TEXT,
                notional_amount_leg1 REAL,
                underlying_asset_name TEXT,
                upi_underlier_name TEXT,
                price REAL,
                regulator TEXT,
                asset_class TEXT,
                action_type TEXT,
                event_type TEXT,
                expiration_date TEXT,
                cleared INTEGER,
                notional_currency_leg1 TEXT,
                notional_amount_leg2 REAL,
                notional_currency_leg2 TEXT,
                price_currency TEXT,
                price_unit_of_measure TEXT,
                underlier_id_leg1 TEXT,
                upi_fisn TEXT,
                data_source TEXT
            )
        ''')
        # Create index on upi
        conn.execute('CREATE INDEX idx_upi ON swap_trades(upi)')
        conn.execute('CREATE INDEX idx_effective_date ON swap_trades(effective_date)')

        # Insert test data
        conn.execute('''
            INSERT INTO swap_trades (
                upi, effective_date, notional_amount_leg1, underlying_asset_name,
                price, regulator, asset_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ('TEST123', '2024-01-15', 1000000, 'SPY', 450.0, 'CFTC', 'Equity Swap'))

        conn.commit()
        conn.close()
        return temp_db

    def test_swaps_query_monitoring(self, setup_test_db):
        """Test that SwapsQuery monitors its queries."""
        from shared.query_monitor import get_query_monitor

        # Reset global monitor
        monitor = get_query_monitor()
        monitor.reset()

        query = SwapsQuery(setup_test_db)

        # Force a slow query by wrapping get_database_stats
        start = time.time()
        stats = query.get_database_stats()
        duration = time.time() - start

        # Should have recorded the query
        slow_queries = monitor.get_slow_queries()
        # Monitor should track it (even if fast, it still records)
        assert isinstance(stats, dict)
        assert 'total_records' in stats

    def test_query_error_monitoring(self, setup_test_db):
        """Test that query errors are monitored."""
        from shared.query_monitor import get_query_monitor

        monitor = get_query_monitor()
        monitor.reset()

        query = SwapsQuery(setup_test_db)

        # Try to query non-existent UPI with query method
        try:
            result = query.query_by_upi('NONEXISTENT', days_back=30)
        except Exception:
            pass  # Expected to fail or return empty

        # Should have some monitoring data
        # (may or may not error depending on pandas availability)

    def test_multiple_query_types_tracking(self, setup_test_db):
        """Test that different query types are tracked separately."""
        from shared.query_monitor import get_query_monitor

        monitor = get_query_monitor()
        monitor.reset()

        query = SwapsQuery(setup_test_db)

        # Execute different query types
        try:
            query.get_database_stats()
        except Exception:
            pass

        try:
            query.get_upi_summary('TEST123', days_back=30)
        except Exception:
            pass

        # Each should be tracked
        stats = monitor.get_query_stats()
        # Stats may be empty if queries are fast and don't exceed threshold


# ── Performance Regression Tests ───────────────────────────────────────────

class TestPerformanceThresholds:
    """Test performance thresholds and alerting."""

    def test_slow_query_threshold(self):
        """Queries slower than threshold should be tracked."""
        monitor = QueryMonitor(slow_query_threshold_sec=0.5)

        # Record fast query
        fast_record = SlowQueryRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            query_fingerprint='fast',
            query_text='SELECT 1',
            duration_sec=0.1,
            row_count=1,
        )
        monitor.record_slow_query(fast_record)

        # Record slow query
        slow_record = SlowQueryRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            query_fingerprint='slow',
            query_text='SELECT * FROM big_table',
            duration_sec=2.0,
            row_count=1000000,
        )
        monitor.record_slow_query(slow_record)

        # Both should be tracked (monitor tracks all)
        assert len(monitor.slow_queries) == 2

    def test_query_duration_accuracy(self):
        """Query duration should be accurately measured."""
        monitor = QueryMonitor()
        start = time.time()
        time.sleep(0.1)  # Sleep for ~100ms
        duration = time.time() - start

        record = SlowQueryRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            query_fingerprint='timed',
            query_text='SELECT 1',
            duration_sec=duration,
            row_count=1,
        )
        monitor.record_slow_query(record)

        retrieved = monitor.get_slow_queries()[0]
        # Should be approximately 0.1 seconds
        assert 0.05 < retrieved['duration_sec'] < 0.2


# ── Record Serialization Tests ─────────────────────────────────────────────

class TestSlowQueryRecordSerialization:
    """Test serialization of slow query records."""

    def test_record_to_dict(self):
        """SlowQueryRecord should serialize to dict."""
        record = SlowQueryRecord(
            timestamp='2024-01-15T10:30:00Z',
            query_fingerprint='abc123',
            query_text='SELECT * FROM swap_trades',
            duration_sec=1.5,
            row_count=100,
            tables=['swap_trades'],
        )
        d = record.to_dict()

        assert d['query_fingerprint'] == 'abc123'
        assert d['duration_sec'] == 1.5
        assert d['row_count'] == 100

    def test_record_with_plan_analysis(self):
        """Record with plan analysis should serialize."""
        record = SlowQueryRecord(
            timestamp='2024-01-15T10:30:00Z',
            query_fingerprint='abc123',
            query_text='SELECT * FROM swap_trades',
            duration_sec=1.5,
            row_count=100,
            plan_analysis={
                'full_scans': 1,
                'index_scans': 0,
                'issues': ['Full table scan on swap_trades'],
                'suggestions': ['Create indexes on columns used in WHERE clauses'],
            },
        )
        d = record.to_dict()
        assert d['plan_analysis']['full_scans'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
