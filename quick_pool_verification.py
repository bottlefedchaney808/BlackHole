"""Quick verification that connection pooling is working."""
import tempfile
import os
import sys
import traceback
from shared.connection_pool import ConnectionPool

def verify_pool():
    """Quick pool functionality verification."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    pool = None
    try:
        print("Testing Connection Pool Implementation...")
        print("=" * 60)

        # Test 1: Pool initialization
        print("\n[1] Initializing pool with default settings...")
        pool = ConnectionPool(db_path, pool_size=5)
        print(f"    [OK] Pool created: {len(pool.all_connections)} connections")

        # Test 2: Get connection
        print("\n[2] Acquiring connection from pool...")
        conn = pool.get_connection(timeout=5.0)
        print(f"    [OK] Connection acquired: {conn.connection_id}")

        # Test 3: WAL mode
        print("\n[3] Verifying WAL mode configuration...")
        result = conn.conn.execute("PRAGMA journal_mode;").fetchone()[0]
        print(f"    [OK] Journal mode: {result}")
        assert result.lower() == 'wal', f"Expected WAL, got {result}"

        # Test 4: Health check
        print("\n[4] Performing health check...")
        is_healthy = conn.health_check()
        print(f"    [OK] Connection healthy: {is_healthy}")
        assert is_healthy, "Connection should be healthy"

        # Test 5: Return connection
        print("\n[5] Returning connection to pool...")
        pool.return_connection(conn)
        print(f"    [OK] Connection returned")

        # Test 6: Stats
        print("\n[6] Checking pool statistics...")
        stats = pool.get_stats()
        print(f"    [OK] Pool stats:")
        print(f"      - Acquired: {stats['acquired']}")
        print(f"      - Released: {stats['released']}")
        print(f"      - Available: {stats['available']}")
        print(f"      - Total: {stats['pool_size']}")

        # Test 7: Context manager
        print("\n[7] Testing context manager...")
        with pool.get_connection_context() as pooled_conn:
            result = pooled_conn.execute("SELECT 1").fetchone()[0]
            print(f"    [OK] Context manager works: SELECT 1 = {result}")

        # Test 8: Cleanup
        print("\n[8] Closing pool...")
        pool.close()
        pool = None
        print(f"    [OK] Pool closed gracefully")

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n[FAILED] TEST FAILED: {e}")
        traceback.print_exc()
        return 1
    finally:
        if pool:
            try:
                pool.close()
            except:
                pass

        # Wait a moment for file locks to release
        import time
        time.sleep(0.5)

        # Try to cleanup files
        try:
            if os.path.exists(db_path):
                os.unlink(db_path)
        except:
            pass

        for suffix in ['-wal', '-shm']:
            wal_path = db_path + suffix
            try:
                if os.path.exists(wal_path):
                    os.unlink(wal_path)
            except:
                pass


if __name__ == '__main__':
    sys.exit(verify_pool())
