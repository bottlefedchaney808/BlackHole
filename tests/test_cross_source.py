"""Tests for cross-source analytics orchestration.

Validates:
  1. Multi-source backfill from adapters
  2. Cross-source aggregations in queries
  3. Dashboard filters by source
  4. Vol_Suite context with multi-source support
"""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in __import__('sys').path:
    __import__('sys').path.insert(0, str(REPO_ROOT))

from shared.data_source import DataSourceAdapter, TradeRecord
from shared.query_builder import CrossSourceQueryBuilder, get_cross_source_summary
from db_loader import SwapsLoader


# --------------------------------------------------------------------------
# Mock adapters for testing
# --------------------------------------------------------------------------

class MockDTCCAdapter(DataSourceAdapter):
    """Mock DTCC adapter for testing."""

    def get_name(self) -> str:
        return "DTCC"

    def get_schema_version(self) -> int:
        return 1

    def fetch_trades(self, date_range=None, filters=None) -> List[TradeRecord]:
        """Return mock DTCC trades."""
        from datetime import datetime, timedelta
        today = datetime.now().date()
        return [
            TradeRecord({
                'dissemination_id': 'dtcc_001',
                'original_dissemination_id': 'dtcc_001',
                'regulator': 'SEC',
                'asset_class': 'EQ',
                'action_type': 'NEW',
                'event_type': 'TRADE',
                'event_timestamp': f'{today}T10:00:00Z',
                'execution_timestamp': f'{today}T10:00:00Z',
                'effective_date': str(today),
                'expiration_date': str((today + timedelta(days=180))),
                'cleared': False,
                'notional_amount_leg1': 1000000.0,
                'notional_currency_leg1': 'USD',
                'notional_amount_leg2': 1000000.0,
                'notional_currency_leg2': 'USD',
                'price': 100.0,
                'price_currency': 'USD',
                'price_unit_of_measure': 'BPS',
                'underlier_id_leg1': 'MSFT',
                'underlier_id_source_leg1': 'RIC',
                'underlying_asset_name': 'Microsoft Corp',
                'upi': 'MSFT_EQ_001',
                'upi_fisn': 'MSFT_FISN_001',
                'upi_underlier_name': 'MSFT Equity Swap',
                'source_file': 'mock_dtcc',
                'raw_json': '{}',
            }),
            TradeRecord({
                'dissemination_id': 'dtcc_002',
                'original_dissemination_id': 'dtcc_002',
                'regulator': 'SEC',
                'asset_class': 'IR',
                'action_type': 'NEW',
                'event_type': 'TRADE',
                'event_timestamp': f'{today}T11:00:00Z',
                'execution_timestamp': f'{today}T11:00:00Z',
                'effective_date': str(today),
                'expiration_date': str((today + timedelta(days=90))),
                'cleared': True,
                'notional_amount_leg1': 5000000.0,
                'notional_currency_leg1': 'USD',
                'notional_amount_leg2': None,
                'notional_currency_leg2': None,
                'price': 50.0,
                'price_currency': 'BPS',
                'price_unit_of_measure': 'BPS',
                'underlier_id_leg1': 'SOFR',
                'underlier_id_source_leg1': 'Bloomberg',
                'underlying_asset_name': 'SOFR Rate Swap',
                'upi': 'SOFR_IR_001',
                'upi_fisn': 'SOFR_FISN_001',
                'upi_underlier_name': 'SOFR Interest Rate Swap',
                'source_file': 'mock_dtcc',
                'raw_json': '{}',
            }),
        ]


class MockCMEAdapter(DataSourceAdapter):
    """Mock CME adapter for testing."""

    def get_name(self) -> str:
        return "CME"

    def get_schema_version(self) -> int:
        return 1

    def fetch_trades(self, date_range=None, filters=None) -> List[TradeRecord]:
        """Return mock CME trades."""
        from datetime import datetime, timedelta
        today = datetime.now().date()
        return [
            TradeRecord({
                'dissemination_id': 'cme_001',
                'original_dissemination_id': 'cme_001',
                'regulator': 'CFTC',
                'asset_class': 'EQ',
                'action_type': 'NEW',
                'event_type': 'TRADE',
                'event_timestamp': f'{today}T09:30:00Z',
                'execution_timestamp': f'{today}T09:30:00Z',
                'effective_date': str(today),
                'expiration_date': str((today + timedelta(days=150))),
                'cleared': True,
                'notional_amount_leg1': 2000000.0,
                'notional_currency_leg1': 'USD',
                'notional_amount_leg2': 2000000.0,
                'notional_currency_leg2': 'USD',
                'price': 102.0,
                'price_currency': 'USD',
                'price_unit_of_measure': 'BPS',
                'underlier_id_leg1': 'NVDA',
                'underlier_id_source_leg1': 'RIC',
                'underlying_asset_name': 'NVIDIA Corp',
                'upi': 'NVDA_EQ_001',
                'upi_fisn': 'NVDA_FISN_001',
                'upi_underlier_name': 'NVDA Equity Swap',
                'source_file': 'mock_cme',
                'raw_json': '{}',
            }),
        ]


# --------------------------------------------------------------------------
# Test cases
# --------------------------------------------------------------------------

class TestMultiSourceBackfill(unittest.TestCase):
    """Test multi-source data backfill and ingestion."""

    def setUp(self):
        """Create a temporary test database."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.db_path = self.temp_db.name
        self.temp_db.close()

        # Initialize the schema
        self._init_schema()

    def tearDown(self):
        """Clean up temporary database."""
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def _init_schema(self):
        """Create minimal swap_trades schema for testing."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS swap_trades (
                    dissemination_id TEXT PRIMARY KEY,
                    original_dissemination_id TEXT,
                    regulator TEXT,
                    asset_class TEXT,
                    action_type TEXT,
                    event_type TEXT,
                    event_timestamp TEXT,
                    execution_timestamp TEXT,
                    effective_date TEXT,
                    expiration_date TEXT,
                    cleared INTEGER,
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
                    source_file TEXT,
                    raw_json TEXT,
                    data_source TEXT,
                    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def test_ingest_from_mock_adapters(self):
        """Test ingesting trades from mock DTCC and CME adapters."""
        loader = SwapsLoader(self.db_path)

        # Ingest from DTCC
        dtcc_adapter = MockDTCCAdapter()
        dtcc_trades = dtcc_adapter.fetch_trades()
        dtcc_records = [t.to_dict() for t in dtcc_trades]
        dtcc_result = loader.upsert_trades(dtcc_records, data_source=dtcc_adapter)

        self.assertEqual(len(dtcc_records), 2)
        self.assertGreater(dtcc_result['inserted'], 0)

        # Ingest from CME
        cme_adapter = MockCMEAdapter()
        cme_trades = cme_adapter.fetch_trades()
        cme_records = [t.to_dict() for t in cme_trades]
        cme_result = loader.upsert_trades(cme_records, data_source=cme_adapter)

        self.assertEqual(len(cme_records), 1)
        self.assertGreater(cme_result['inserted'], 0)

        # Verify data_source column is set correctly
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute('SELECT data_source, COUNT(*) AS cnt FROM swap_trades GROUP BY data_source;')
            by_source = {row['data_source']: row['cnt'] for row in cur.fetchall()}

            self.assertIn('DTCC', by_source)
            self.assertIn('CME', by_source)
            self.assertEqual(by_source['DTCC'], 2)
            self.assertEqual(by_source['CME'], 1)
        finally:
            conn.close()

    def test_cross_source_aggregation_notional(self):
        """Test aggregating notional across sources."""
        # Ingest mock data
        loader = SwapsLoader(self.db_path)
        for adapter in [MockDTCCAdapter(), MockCMEAdapter()]:
            trades = adapter.fetch_trades()
            records = [t.to_dict() for t in trades]
            loader.upsert_trades(records, data_source=adapter)

        # Query aggregations
        builder = CrossSourceQueryBuilder(self.db_path)
        total_notional = builder.aggregate_notional_cross_source(['DTCC', 'CME'])
        by_source = builder.aggregate_notional_by_source(['DTCC', 'CME'])

        # DTCC: 1M + 5M = 6M
        # CME: 2M
        # Total: 8M
        expected_total = 8000000.0
        self.assertEqual(total_notional, expected_total)
        self.assertEqual(len(by_source), 2)
        self.assertAlmostEqual(by_source['DTCC'], 6000000.0)
        self.assertAlmostEqual(by_source['CME'], 2000000.0)

    def test_query_by_sources_filter(self):
        """Test querying trades filtered by source."""
        # Ingest mock data
        loader = SwapsLoader(self.db_path)
        for adapter in [MockDTCCAdapter(), MockCMEAdapter()]:
            trades = adapter.fetch_trades()
            records = [t.to_dict() for t in trades]
            loader.upsert_trades(records, data_source=adapter)

        # Query only CME trades
        builder = CrossSourceQueryBuilder(self.db_path)
        cme_trades = builder.query_by_sources(['CME'], days_back=30, limit=100)

        self.assertEqual(len(cme_trades), 1)
        self.assertEqual(cme_trades[0]['dissemination_id'], 'cme_001')
        self.assertEqual(cme_trades[0]['data_source'], 'CME')

        # Query both sources
        all_trades = builder.query_by_sources(['DTCC', 'CME'], days_back=30, limit=100)
        self.assertEqual(len(all_trades), 3)

    def test_instrument_resolution_across_sources(self):
        """Test resolving the same instrument UPI across sources."""
        # Ingest mock data
        loader = SwapsLoader(self.db_path)
        for adapter in [MockDTCCAdapter(), MockCMEAdapter()]:
            trades = adapter.fetch_trades()
            records = [t.to_dict() for t in trades]
            loader.upsert_trades(records, data_source=adapter)

        # Both DTCC trades have different UPIs (MSFT_EQ_001, SOFR_IR_001)
        # CME has NVDA_EQ_001
        # Let's test finding MSFT_EQ_001 (only in DTCC)
        builder = CrossSourceQueryBuilder(self.db_path)
        msft_trades = builder.resolve_instrument_across_sources('MSFT_EQ_001')

        self.assertEqual(len(msft_trades), 1)
        self.assertEqual(msft_trades[0]['data_source'], 'DTCC')
        self.assertEqual(msft_trades[0]['underlying_asset_name'], 'Microsoft Corp')

    def test_cross_source_summary(self):
        """Test the convenience function for cross-source summary."""
        # Ingest mock data
        loader = SwapsLoader(self.db_path)
        for adapter in [MockDTCCAdapter(), MockCMEAdapter()]:
            trades = adapter.fetch_trades()
            records = [t.to_dict() for t in trades]
            loader.upsert_trades(records, data_source=adapter)

        # Get summary
        summary = get_cross_source_summary(self.db_path, ['DTCC', 'CME'], days_back=30)

        self.assertEqual(summary['total_notional'], 8000000.0)
        self.assertIn('DTCC', summary['notional_by_source'])
        self.assertIn('CME', summary['notional_by_source'])
        self.assertEqual(summary['trade_counts_by_source']['DTCC'], 2)
        self.assertEqual(summary['trade_counts_by_source']['CME'], 1)


class TestVolSuiteContextWithSources(unittest.TestCase):
    """Test Vol_Suite context building with data_sources field."""

    def setUp(self):
        """Set up test environment."""
        if str(REPO_ROOT) not in __import__('sys').path:
            __import__('sys').path.insert(0, str(REPO_ROOT))

        # Verify we can import suite_context
        vol_root = os.path.join(REPO_ROOT, 'Vol_Suite')
        if vol_root not in __import__('sys').path:
            __import__('sys').path.insert(0, vol_root)

    def test_context_with_sources(self):
        """Test building a context with data_sources parameter."""
        try:
            import suite_context
        except ImportError:
            self.skipTest("Vol_Suite/suite_context not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            context = suite_context.build_suite_context(
                output_dir=tmpdir,
                run_id='test_run_001',
                ticker='MSFT',
                option_type='call',
                strike=None,
                target_years=0.25,
                expiration_date='2026-04-15',
                index_ticker='SPY',
                basket_tickers=['MSFT', 'NVDA', 'AAPL'],
                basket_weights=[0.4, 0.3, 0.3],
                sentiment_manifest_path='/tmp/manifest.json',
                data_sources=['DTCC', 'CME'],  # Multi-source
            )

            # Verify the context includes data_sources
            self.assertIn('data_sources', context)
            self.assertEqual(context['data_sources'], ['DTCC', 'CME'])

            # Verify schema_version still passes
            self.assertEqual(context['schema_version'], 1)

            # Verify context is valid
            suite_context.validate_suite_context(context)

    def test_context_without_sources(self):
        """Test building a context without data_sources (defaults to empty list)."""
        try:
            import suite_context
        except ImportError:
            self.skipTest("Vol_Suite/suite_context not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            context = suite_context.build_suite_context(
                output_dir=tmpdir,
                run_id='test_run_002',
                ticker='AAPL',
                option_type='put',
                strike=150.0,
                target_years=0.5,
                expiration_date='2026-06-15',
                index_ticker='QQQ',
                basket_tickers=['AAPL'],
                basket_weights=[1.0],
                sentiment_manifest_path='/tmp/manifest.json',
                # No data_sources parameter
            )

            # data_sources should default to empty list
            self.assertIn('data_sources', context)
            self.assertEqual(context['data_sources'], [])

            # Verify context is still valid
            suite_context.validate_suite_context(context)


if __name__ == '__main__':
    unittest.main()
