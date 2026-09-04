"""Integration tests for decode_upis.py against a temp SQLite file."""
import os
import sqlite3
import tempfile

import pytest

import setup_db
from decode_upis import run, run_batch


@pytest.fixture
def db_path(tmp_path):
    # tmp_path (pytest-managed) + retry: a pooled sqlite connection elsewhere
    # can hold the file momentarily open on Windows (WinError 32), so removal
    # is best-effort with short retries instead of a hard os.remove.
    import time

    path = str(tmp_path / "upi_test.db")
    setup_db.init_database(path)
    yield path
    for _ in range(5):
        try:
            if os.path.exists(path):
                os.remove(path)
            return
        except PermissionError:
            time.sleep(0.2)


def _insert_trade(conn, dissemination_id, upi, underlier_id, underlier_source, underlier_name):
    conn.execute(
        """
        INSERT INTO swap_trades (
            dissemination_id, regulator, asset_class, source_file, raw_json,
            upi, underlier_id_leg1, underlier_id_source_leg1, upi_underlier_name
        ) VALUES (?, 'CFTC', 'EQ', 'test.csv', '{}', ?, ?, ?, ?);
        """,
        (dissemination_id, upi, underlier_id, underlier_source, underlier_name),
    )
    conn.commit()


class TestRunBatch:
    def test_decodes_new_upi_via_tier1(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats == {"rows_scanned": 1, "new_upis": 1, "decoded": 1, "max_rowid": 1}
        row = conn.execute("SELECT company_name, decode_tier FROM upi_reference WHERE upi = 'UPI-A';").fetchone()
        assert row["company_name"] == "LIVZON GROUP"
        assert row["decode_tier"] == "local"

    def test_skips_upi_already_in_reference_table(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")
        conn.execute(
            "INSERT INTO upi_reference (upi, company_name, decode_tier, decode_detail) "
            "VALUES ('UPI-A', 'ALREADY DECODED', 'local', 'test-seed');"
        )
        conn.commit()

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats["new_upis"] == 0
        row = conn.execute("SELECT company_name FROM upi_reference WHERE upi = 'UPI-A';").fetchone()
        assert row["company_name"] == "ALREADY DECODED"

    def test_deduplicates_repeated_upi_within_one_batch(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")
        _insert_trade(conn, "D2", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats["new_upis"] == 1
        assert conn.execute("SELECT COUNT(*) FROM upi_reference;").fetchone()[0] == 1

    def test_advances_watermark_so_second_call_sees_no_new_rows(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")

        run_batch(conn, batch_size=100, figi_client=None)
        stats_second = run_batch(conn, batch_size=100, figi_client=None)

        assert stats_second == {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "max_rowid": 1}

    def test_unresolvable_upi_stored_with_null_company_name(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-B", "1234.XX", "RIC", "COM STK")

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats["decoded"] == 0
        row = conn.execute("SELECT company_name, decode_tier FROM upi_reference WHERE upi = 'UPI-B';").fetchone()
        assert row["company_name"] is None
        assert row["decode_tier"] == "unresolved"

    def test_null_upi_rows_are_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", None, None, None, None)

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats == {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "max_rowid": 0}


class TestRun:
    def test_run_processes_multiple_batches_until_caught_up(self, db_path):
        conn = sqlite3.connect(db_path)
        for i in range(5):
            _insert_trade(conn, f"D{i}", f"UPI-{i}", "000513.ZK", "RIC", f"COMPANY{i}\\REGISTERED\\SHARES A\\000513")
        conn.close()

        totals = run(db_path=db_path, batch_size=2, max_batches=10, use_openfigi=False)

        assert totals["batches"] == 3  # 2 + 2 + 1(partial, stops the loop)
        assert totals["new_upis"] == 5
        assert totals["decoded"] == 5

    def test_run_is_a_no_op_when_already_caught_up(self, db_path):
        conn = sqlite3.connect(db_path)
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")
        conn.close()

        run(db_path=db_path, batch_size=100, max_batches=10, use_openfigi=False)
        totals_second = run(db_path=db_path, batch_size=100, max_batches=10, use_openfigi=False)

        assert totals_second["new_upis"] == 0
        assert totals_second["batches"] == 1
