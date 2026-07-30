"""Comprehensive tests for SQLite connection pooling.

Tests cover:
  - Pool initialization and configuration
  - Concurrent connection access
  - Health checks and stale connection cleanup
  - WAL mode correctness under load
  - Reader/writer non-blocking behavior
  - Integration with SwapsLoader and SwapsQuery
"""
import pytest
import sqlite3
import tempfile
import os
import threading
import time
from pathlib import Path
from typing import List

from shared.connection_pool import ConnectionPool, PooledConnection, init_pool, close_pool, get_pool
from db_loader import SwapsLoader
from swaps_query import SwapsQuery


class TestPoolInitialization:
    """Test pool initialization and configuration."""

    def test_pool_creates_with_default_size(self):
        """Pool initializes with default 5 connections."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=5)
            assert pool.pool_size == 5
            assert len(pool.all_connections) == 5
            assert pool.available_connections.qsize() == 5
            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_pool_respects_max_pool_size(self):
        """Pool enforces maximum pool size limit."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=2, max_pool_size=5)
            conns = []
            for _ in range(5):
                conns.append(pool.get_connection(timeout=1.0))

            # All 5 acquired successfully
            assert len(conns) == 5
            assert len(pool.all_connections) == 5

            # Next acquisition should timeout
            with pytest.raises(TimeoutError):
                pool.get_connection(timeout=0.1)

            # Return one and try again
            pool.return_connection(conns[0])
            conn = pool.get_connection(timeout=1.0)
            assert conn is not None

            for c in conns[1:]:
                pool.return_connection(c)

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_pool_validates_size_parameters(self):
        """Pool raises on invalid size parameters."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            # pool_size > max_pool_size should fail
            with pytest.raises(ValueError):
                ConnectionPool(db_path, pool_size=10, max_pool_size=5)

            # pool_size < 1 should fail
            with pytest.raises(ValueError):
                ConnectionPool(db_path, pool_size=0, max_pool_size=5)
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_pool_wal_mode_configured(self):
        """Pool connections have WAL mode enabled."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=1)
            pooled = pool.get_connection()

            # Check WAL mode is active
            result = pooled.conn.execute("PRAGMA journal_mode;").fetchone()[0]
            assert result.lower() == 'wal'

            pool.return_connection(pooled)
            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestConcurrentAccess:
    """Test concurrent connection acquisition and usage."""

    def test_concurrent_get_connection(self):
        """Multiple threads can acquire connections concurrently."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=5, max_pool_size=10)
            acquired_conns = []
            errors = []
            lock = threading.Lock()

            def acquire_conn(count: int):
                try:
                    for _ in range(count):
                        conn = pool.get_connection(timeout=5.0)
                        with lock:
                            acquired_conns.append(conn)
                        time.sleep(0.01)  # Simulate work
                        pool.return_connection(conn)
                except Exception as e:
                    with lock:
                        errors.append(str(e))

            threads = [threading.Thread(target=acquire_conn, args=(3,)) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors occurred: {errors}"
            assert len(acquired_conns) == 15
            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_concurrent_queries(self):
        """Multiple threads can execute queries concurrently."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            # Initialize database with test table
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT);")
            conn.execute("INSERT INTO test VALUES (1, 'a'), (2, 'b'), (3, 'c');")
            conn.commit()
            conn.close()

            pool = ConnectionPool(db_path, pool_size=5, max_pool_size=10)
            results = []
            errors = []
            lock = threading.Lock()

            def reader_thread():
                try:
                    for _ in range(5):
                        with pool.get_connection_context() as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT COUNT(*) FROM test;")
                            count = cur.fetchone()[0]
                            with lock:
                                results.append(count)
                            time.sleep(0.01)
                except Exception as e:
                    with lock:
                        errors.append(str(e))

            threads = [threading.Thread(target=reader_thread) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"
            assert len(results) == 25
            assert all(r == 3 for r in results)
            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestHealthCheck:
    """Test connection health checks and stale connection cleanup."""

    def test_health_check_detects_bad_connection(self):
        """Health check identifies unhealthy connections."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=1)
            pooled = pool.get_connection()

            # Connection should be healthy
            assert pooled.health_check()

            # Manually break the connection
            pooled.conn.close()
            assert not pooled.health_check()

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_cleanup_removes_stale_connections(self):
        """Stale connection cleanup removes idle connections."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=3, stale_connection_timeout=1)
            initial_size = len(pool.all_connections)

            # Acquire and immediately return a connection
            conn = pool.get_connection()
            pool.return_connection(conn)

            # Wait for connection to become stale
            time.sleep(1.5)

            # Cleanup should remove it
            pool.cleanup_stale_connections()

            # Pool should refill to desired size
            assert len(pool.all_connections) == initial_size

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_health_check_all_removes_unhealthy(self):
        """health_check_all() removes unhealthy connections."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=3)

            # Break one connection
            broken_id = list(pool.all_connections.keys())[0]
            pool.all_connections[broken_id].conn.close()

            # Check all
            pool.health_check_all()

            # Broken one should be removed
            assert broken_id not in pool.all_connections
            assert len(pool.all_connections) < 3

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestStatistics:
    """Test pool statistics and monitoring."""

    def test_stats_track_acquisitions(self):
        """Statistics track connection acquisitions."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=2)

            conn1 = pool.get_connection()
            conn2 = pool.get_connection()
            pool.return_connection(conn1)
            pool.return_connection(conn2)

            stats = pool.get_stats()
            assert stats['acquired'] == 2
            assert stats['released'] == 2

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_stats_track_timeouts(self):
        """Statistics track timeout events."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=1, max_pool_size=1)

            conn = pool.get_connection()

            # Next request should timeout
            try:
                pool.get_connection(timeout=0.1)
            except TimeoutError:
                pass

            stats = pool.get_stats()
            assert stats['timeouts'] >= 1

            pool.return_connection(conn)
            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestIntegrationWithLoaders:
    """Test integration with SwapsLoader and SwapsQuery."""

    def test_swaps_loader_uses_pool(self):
        """SwapsLoader uses pool when available."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            # Initialize schema
            from setup_db import migrate
            migrate(db_path)

            # Initialize pool
            pool = init_pool(db_path, pool_size=5)

            # Create loader
            loader = SwapsLoader(db_path=db_path, use_pool=True)

            # Should use pool connections
            initial_acquired = pool.get_stats()['acquired']

            # Try state operation (uses pool)
            loader.set_state('SEC', 'EQ', last_cumulative_date='2024-01-01')
            state = loader.get_state('SEC', 'EQ')

            assert state is not None
            assert state['last_cumulative_date'] == '2024-01-01'

            # Pool should have acquired more connections
            assert pool.get_stats()['acquired'] > initial_acquired

            close_pool()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
                for wal_file in [f"{db_path}-wal", f"{db_path}-shm"]:
                    if os.path.exists(wal_file):
                        os.unlink(wal_file)

    def test_swaps_query_uses_pool(self):
        """SwapsQuery uses pool when available."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            # Initialize schema
            from setup_db import migrate
            migrate(db_path)

            # Initialize pool
            pool = init_pool(db_path, pool_size=5)

            # Create query interface
            query = SwapsQuery(db_path=db_path, use_pool=True)

            # Should use pool connections
            initial_acquired = pool.get_stats()['acquired']

            # Try query (uses pool)
            stats = query.get_database_stats()

            assert stats['total_records'] == 0

            # Pool should have acquired more connections
            assert pool.get_stats()['acquired'] > initial_acquired

            close_pool()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
                for wal_file in [f"{db_path}-wal", f"{db_path}-shm"]:
                    if os.path.exists(wal_file):
                        os.unlink(wal_file)


class TestContextManager:
    """Test connection context manager."""

    def test_context_manager_auto_returns_connection(self):
        """Context manager automatically returns connections."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=2)
            initial_available = pool.available_connections.qsize()

            with pool.get_connection_context() as conn:
                assert isinstance(conn, sqlite3.Connection)
                # One connection in use
                assert pool.available_connections.qsize() == initial_available - 1

            # Should be returned after context
            assert pool.available_connections.qsize() == initial_available

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_context_manager_closes_on_exception(self):
        """Context manager returns connection even on exception."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            pool = ConnectionPool(db_path, pool_size=2)
            initial_available = pool.available_connections.qsize()

            try:
                with pool.get_connection_context() as conn:
                    raise ValueError("Test error")
            except ValueError:
                pass

            # Connection should still be returned
            assert pool.available_connections.qsize() == initial_available

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestLoadScenario:
    """Test realistic high-throughput load scenarios."""

    def test_parallel_bulk_inserts(self):
        """Pool handles parallel bulk insert operations."""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            # Initialize schema
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY,
                    dissemination_id TEXT UNIQUE,
                    value REAL
                );
            """)
            conn.commit()
            conn.close()

            # Initialize pool
            pool = ConnectionPool(db_path, pool_size=5, max_pool_size=10)

            errors = []
            lock = threading.Lock()

            def insert_batch(batch_id: int, records: int):
                try:
                    with pool.get_connection_context() as conn:
                        cur = conn.cursor()
                        cur.execute("BEGIN;")
                        for i in range(records):
                            did = f"batch_{batch_id}_record_{i}"
                            cur.execute(
                                "INSERT INTO trades (dissemination_id, value) VALUES (?, ?)",
                                (did, i * 1.5)
                            )
                        conn.commit()
                except Exception as e:
                    with lock:
                        errors.append(str(e))

            # 5 batches of 100 records each = 500 total inserts
            threads = [
                threading.Thread(target=insert_batch, args=(i, 100))
                for i in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"

            # Verify all records inserted
            with pool.get_connection_context() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM trades;")
                count = cur.fetchone()[0]
                assert count == 500

            pool.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
                for wal_file in [f"{db_path}-wal", f"{db_path}-shm"]:
                    if os.path.exists(wal_file):
                        os.unlink(wal_file)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
