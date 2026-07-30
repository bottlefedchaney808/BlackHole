"""Tests for DataSourceAdapter interface and implementations.

Validates:
  - Adapter interface compliance
  - DTCC adapter functionality
  - Database upsert with data_source tracking
  - Backward compatibility (existing code works with defaults)
"""

import pytest
import tempfile
import os
import sqlite3
from datetime import date
from unittest.mock import Mock, patch, MagicMock

from shared.data_source import DataSourceAdapter, TradeRecord
from adapters.dtcc_adapter import DTCCAdapter
from db_loader import SwapsLoader


# ──────────────────────────────────────────────────────────────────────────
# Test fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with the schema for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Create schema
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE swap_trades (
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
            data_source TEXT NOT NULL DEFAULT 'DTCC',
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def mock_adapter():
    """Create a mock adapter for testing interface compliance."""

    class MockAdapter(DataSourceAdapter):
        def get_name(self) -> str:
            return "MOCK"

        def get_schema_version(self) -> int:
            return 1

        def fetch_trades(self, date_range=None, filters=None):
            return [
                TradeRecord({
                    "dissemination_id": f"MOCK-{i}",
                    "regulator": "SEC",
                    "asset_class": "EQ",
                    "source_file": "mock.csv",
                    "raw_json": "{}",
                })
                for i in range(3)
            ]

    return MockAdapter()


# ──────────────────────────────────────────────────────────────────────────
# Test DataSourceAdapter interface
# ──────────────────────────────────────────────────────────────────────────


class TestDataSourceAdapterInterface:
    """Verify the abstract interface and its contracts."""

    def test_adapter_cannot_be_instantiated(self):
        """DataSourceAdapter is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            DataSourceAdapter()

    def test_mock_adapter_implements_interface(self, mock_adapter):
        """A proper mock adapter can be instantiated and used."""
        assert mock_adapter.get_name() == "MOCK"
        assert mock_adapter.get_schema_version() == 1
        assert len(mock_adapter.fetch_trades()) == 3

    def test_trade_record_to_dict(self):
        """TradeRecord.to_dict() exports the internal data."""
        data = {
            "dissemination_id": "TEST-001",
            "regulator": "SEC",
            "asset_class": "EQ",
        }
        record = TradeRecord(data)
        exported = record.to_dict()
        assert exported == data
        # Verify it's a copy, not the original
        exported["regulator"] = "CFTC"
        assert record.data["regulator"] == "SEC"

    def test_trade_record_repr(self):
        """TradeRecord has a useful repr."""
        record = TradeRecord({"dissemination_id": "REPR-TEST"})
        assert "REPR-TEST" in repr(record)


# ──────────────────────────────────────────────────────────────────────────
# Test DTCCAdapter
# ──────────────────────────────────────────────────────────────────────────


class TestDTCCAdapter:
    """Verify DTCC adapter implementation."""

    def test_dtcc_get_name(self):
        """DTCCAdapter.get_name() returns 'DTCC'."""
        adapter = DTCCAdapter()
        assert adapter.get_name() == "DTCC"

    def test_dtcc_get_schema_version(self):
        """DTCCAdapter.get_schema_version() returns 1."""
        adapter = DTCCAdapter()
        assert adapter.get_schema_version() == 1
        assert isinstance(adapter.get_schema_version(), int)

    def test_dtcc_fetch_trades_empty(self):
        """DTCCAdapter.fetch_trades() returns empty list when no data."""
        adapter = DTCCAdapter()

        # Mock the _get_* helper methods to return Mock functions
        # (not the data directly, but functions that return data)
        adapter._get_list_cumulative = Mock(return_value=Mock(return_value=[]))
        adapter._get_list_live_slices = Mock(return_value=Mock(return_value=[]))

        result = adapter.fetch_trades(filters={"regulator": "SEC", "asset_class": "EQ"})

        assert result == []

    def test_dtcc_fetch_trades_with_data(self):
        """DTCCAdapter.fetch_trades() returns parsed records."""
        adapter = DTCCAdapter()

        # Mock the _get_* helper methods to return callable Mock objects
        adapter._get_list_cumulative = Mock(return_value=Mock(return_value=[
            {"fullFilePath": "http://...", "fileName": "cum.zip"}
        ]))
        adapter._get_list_live_slices = Mock(return_value=Mock(return_value=[]))
        adapter._get_download_zip = Mock(return_value=Mock(return_value=b"zipdata"))
        adapter._get_parse_swap_zip = Mock(return_value=Mock(return_value=[
            {"dissemination_id": "DTCC-001", "regulator": "SEC", "asset_class": "EQ"},
            {"dissemination_id": "DTCC-002", "regulator": "SEC", "asset_class": "EQ"},
        ]))

        result = adapter.fetch_trades(filters={"regulator": "SEC"})

        assert len(result) == 2
        assert all(isinstance(r, TradeRecord) for r in result)
        assert result[0].data["dissemination_id"] == "DTCC-001"

    def test_dtcc_fetch_trades_handles_errors(self):
        """DTCCAdapter.fetch_trades() logs errors but continues with other files."""
        adapter = DTCCAdapter()

        # Mock the _get_* helper methods
        adapter._get_list_cumulative = Mock(return_value=Mock(return_value=[
            {"fullFilePath": "http://ok", "fileName": "ok.zip"},
            {"fullFilePath": "http://fail", "fileName": "fail.zip"},
        ]))
        adapter._get_list_live_slices = Mock(return_value=Mock(return_value=[]))

        # First call succeeds, second fails
        download_mock = Mock(side_effect=[
            b"zipdata",  # OK
            Exception("Network error"),  # FAIL
        ])
        adapter._get_download_zip = Mock(return_value=download_mock)
        adapter._get_parse_swap_zip = Mock(return_value=Mock(return_value=[]))

        # Should not raise; logs the error and continues
        result = adapter.fetch_trades(filters={"regulator": "SEC"})
        # Empty because parse_swap_zip is mocked to return [], but the point is it didn't crash
        assert isinstance(result, list)

    def test_dtcc_normalize_timestamp(self):
        """DTCCAdapter.normalize_timestamp() handles DTCC format."""
        adapter = DTCCAdapter()

        # Valid timestamp (pass-through)
        ts = adapter.normalize_timestamp("2026-07-29T14:30:00Z")
        assert ts == "2026-07-29T14:30:00Z"

        # None/empty
        assert adapter.normalize_timestamp(None) is None
        assert adapter.normalize_timestamp("") is None
        assert adapter.normalize_timestamp("  ") is None


# ──────────────────────────────────────────────────────────────────────────
# Test SwapsLoader with data_source
# ──────────────────────────────────────────────────────────────────────────


class TestSwapsLoaderDataSource:
    """Verify SwapsLoader.upsert_trades() handles data_source parameter."""

    def test_upsert_with_adapter_instance(self, temp_db, mock_adapter):
        """upsert_trades() stores data_source from adapter instance."""
        loader = SwapsLoader(temp_db)

        records = [
            {
                "dissemination_id": "ID-001",
                "regulator": "SEC",
                "asset_class": "EQ",
                "source_file": "test.csv",
                "raw_json": "{}",
            }
        ]

        result = loader.upsert_trades(records, data_source=mock_adapter)

        # Verify record was inserted
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT dissemination_id, data_source FROM swap_trades WHERE dissemination_id = 'ID-001'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["dissemination_id"] == "ID-001"
        assert row["data_source"] == "MOCK"  # From mock_adapter.get_name()

    def test_upsert_with_string_source(self, temp_db):
        """upsert_trades() stores data_source from string."""
        loader = SwapsLoader(temp_db)

        records = [
            {
                "dissemination_id": "ID-002",
                "regulator": "SEC",
                "asset_class": "EQ",
                "source_file": "test.csv",
                "raw_json": "{}",
            }
        ]

        loader.upsert_trades(records, data_source="CME")

        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT data_source FROM swap_trades WHERE dissemination_id = 'ID-002'"
        ).fetchone()
        conn.close()

        assert row["data_source"] == "CME"

    def test_upsert_with_default_source(self, temp_db):
        """upsert_trades() defaults to 'DTCC' when data_source not provided."""
        loader = SwapsLoader(temp_db)

        records = [
            {
                "dissemination_id": "ID-003",
                "regulator": "SEC",
                "asset_class": "EQ",
                "source_file": "test.csv",
                "raw_json": "{}",
            }
        ]

        loader.upsert_trades(records)  # No data_source param

        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT data_source FROM swap_trades WHERE dissemination_id = 'ID-003'"
        ).fetchone()
        conn.close()

        assert row["data_source"] == "DTCC"

    def test_upsert_with_multiple_records(self, temp_db):
        """upsert_trades() applies data_source to all records."""
        loader = SwapsLoader(temp_db)

        records = [
            {
                "dissemination_id": f"ID-{i:03d}",
                "regulator": "SEC",
                "asset_class": "EQ",
                "source_file": "test.csv",
                "raw_json": "{}",
            }
            for i in range(5)
        ]

        loader.upsert_trades(records, data_source="OTC")

        conn = sqlite3.connect(temp_db)
        rows = conn.execute("SELECT data_source FROM swap_trades").fetchall()
        conn.close()

        assert len(rows) == 5
        assert all(row[0] == "OTC" for row in rows)

    def test_upsert_on_conflict_updates_data_source(self, temp_db):
        """ON CONFLICT update includes data_source."""
        loader = SwapsLoader(temp_db)

        # Insert initial record
        records1 = [
            {
                "dissemination_id": "ID-CONFLICT",
                "regulator": "SEC",
                "asset_class": "EQ",
                "price": 100.0,
                "source_file": "v1.csv",
                "raw_json": "{}",
            }
        ]
        loader.upsert_trades(records1, data_source="DTCC")

        # Upsert same dissemination_id with different data
        records2 = [
            {
                "dissemination_id": "ID-CONFLICT",
                "regulator": "SEC",
                "asset_class": "EQ",
                "price": 101.0,
                "source_file": "v2.csv",
                "raw_json": "{}",
            }
        ]
        loader.upsert_trades(records2, data_source="CME")

        # Verify the record was updated and data_source changed
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT price, source_file, data_source FROM swap_trades WHERE dissemination_id = 'ID-CONFLICT'"
        ).fetchone()
        conn.close()

        assert row["price"] == 101.0
        assert row["source_file"] == "v2.csv"
        assert row["data_source"] == "CME"

    def test_upsert_empty_records_returns_zero(self, temp_db):
        """upsert_trades() with empty records list returns inserted=0."""
        loader = SwapsLoader(temp_db)
        result = loader.upsert_trades([])

        assert result["inserted"] == 0
        assert result["updated"] == 0


# ──────────────────────────────────────────────────────────────────────────
# Test backward compatibility
# ──────────────────────────────────────────────────────────────────────────


class TestBackwardCompatibility:
    """Verify existing code (backfill.py) still works without changes."""

    def test_backfill_style_upsert_still_works(self, temp_db):
        """The existing backfill.py::_load_entry pattern still works."""
        loader = SwapsLoader(temp_db)

        # Simulate backfill.py passing records (no data_source param)
        records = [
            {
                "dissemination_id": "OLD-001",
                "regulator": "SEC",
                "asset_class": "EQ",
                "source_file": "2026_07_29.csv",
                "raw_json": "{}",
            }
        ]

        # This is how backfill.py currently calls it (no data_source)
        result = loader.upsert_trades(records)

        assert result["inserted"] == 1
        # Record should be in DB with default data_source='DTCC'
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT data_source FROM swap_trades WHERE dissemination_id = 'OLD-001'"
        ).fetchone()
        conn.close()
        assert row["data_source"] == "DTCC"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
