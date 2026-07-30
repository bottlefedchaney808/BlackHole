"""Load testing for connection pool performance under high-throughput ingestion.

Simulates:
  - Parallel bulk upserts via backfill
  - Concurrent read queries
  - WAL mode correctness under contention
  - Throughput measurement (rows/sec)
  - Pool utilization monitoring
"""
import sqlite3
import threading
import time
import statistics
import tempfile
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from pathlib import Path

from shared.connection_pool import ConnectionPool, init_pool, close_pool, get_pool
from db_loader import SwapsLoader
from swaps_query import SwapsQuery
from setup_db import migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def generate_test_records(batch_id: int, count: int) -> List[Dict]:
    """Generate test swap records for bulk insert."""
    records = []
    for i in range(count):
        records.append({
            'dissemination_id': f'test_batch_{batch_id}_record_{i}',
            'regulator': 'SEC',
            'asset_class': 'EQ',
            'action_type': 'NEW',
            'event_type': 'TRADE',
            'event_timestamp': datetime.now().isoformat(),
            'execution_timestamp': datetime.now().isoformat(),
            'effective_date': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
            'expiration_date': (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d'),
            'cleared': 1,
            'notional_amount_leg1': 1000000.0 * (i + 1),
            'notional_currency_leg1': 'USD',
            'notional_amount_leg2': 2000000.0 * (i + 1),
            'notional_currency_leg2': 'EUR',
            'price': 1.5 + (i * 0.01),
            'price_currency': 'USD',
            'price_unit_of_measure': 'bps',
            'underlier_id_leg1': f'UNDERLYING_{i}',
            'underlier_id_source_leg1': 'CUSIP',
            'underlying_asset_name': f'Asset_{i}',
            'upi': f'UPI_{batch_id}_{i}',
            'upi_fisn': f'FISN_{i}',
            'upi_underlier_name': f'Underlier_{i}',
            'source_file': f'batch_{batch_id}.csv',
            'raw_json': '{}',
            'data_source': 'TEST',
        })
    return records


class LoadTestRunner:
    """Orchestrates load testing with and without connection pooling."""

    def __init__(self, db_path: str, results_file: str = 'load_test_results.json'):
        self.db_path = db_path
        self.results_file = results_file
        self.results: Dict = {
            'timestamp': datetime.now().isoformat(),
            'tests': {},
        }

    def run_upsert_throughput_test(self, pool_size: int = 5, num_threads: int = 5,
                                    records_per_thread: int = 1000, use_pool: bool = True) -> Dict:
        """Test parallel upsert throughput."""
        test_name = f"upsert_throughput_pool={use_pool}_threads={num_threads}_records={records_per_thread}"
        logger.info(f"Starting: {test_name}")

        if use_pool:
            pool = init_pool(self.db_path, pool_size=pool_size, max_pool_size=pool_size * 2)
        else:
            pool = None

        start_time = time.time()
        errors = []
        lock = threading.Lock()

        def upsert_worker(worker_id: int, batch_size: int):
            try:
                loader = SwapsLoader(db_path=self.db_path, use_pool=use_pool)
                for batch_idx in range(num_threads):
                    records = generate_test_records(worker_id * 100 + batch_idx, batch_size)
                    loader.upsert_trades(records)
            except Exception as e:
                with lock:
                    errors.append(f"Worker {worker_id}: {str(e)}")

        threads = [
            threading.Thread(target=upsert_worker, args=(i, records_per_thread))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        duration = time.time() - start_time
        total_records = num_threads * records_per_thread * num_threads
        throughput = total_records / duration if duration > 0 else 0

        result = {
            'test_name': test_name,
            'use_pool': use_pool,
            'pool_size': pool_size if use_pool else 0,
            'num_threads': num_threads,
            'records_per_thread': records_per_thread,
            'total_records': total_records,
            'duration_sec': round(duration, 2),
            'throughput_rows_per_sec': round(throughput, 2),
            'errors': errors,
        }

        if use_pool:
            result['pool_stats'] = pool.get_stats()
            close_pool()

        logger.info(f"Completed: {test_name} - {throughput:.0f} rows/sec")
        return result

    def run_read_throughput_test(self, pool_size: int = 5, num_readers: int = 5,
                                  queries_per_reader: int = 50, use_pool: bool = True) -> Dict:
        """Test concurrent read query throughput."""
        test_name = f"read_throughput_pool={use_pool}_readers={num_readers}_queries={queries_per_reader}"
        logger.info(f"Starting: {test_name}")

        if use_pool:
            pool = init_pool(self.db_path, pool_size=pool_size, max_pool_size=pool_size * 2)
        else:
            pool = None

        start_time = time.time()
        errors = []
        query_times = []
        lock = threading.Lock()

        def read_worker(reader_id: int):
            try:
                query = SwapsQuery(db_path=self.db_path, use_pool=use_pool)
                for _ in range(queries_per_reader):
                    query_start = time.time()
                    stats = query.get_database_stats()
                    query_duration = time.time() - query_start
                    with lock:
                        query_times.append(query_duration)
            except Exception as e:
                with lock:
                    errors.append(f"Reader {reader_id}: {str(e)}")

        threads = [
            threading.Thread(target=read_worker, args=(i,))
            for i in range(num_readers)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        duration = time.time() - start_time
        total_queries = num_readers * queries_per_reader
        throughput = total_queries / duration if duration > 0 else 0

        result = {
            'test_name': test_name,
            'use_pool': use_pool,
            'pool_size': pool_size if use_pool else 0,
            'num_readers': num_readers,
            'queries_per_reader': queries_per_reader,
            'total_queries': total_queries,
            'duration_sec': round(duration, 2),
            'throughput_queries_per_sec': round(throughput, 2),
            'query_latency_avg_ms': round(statistics.mean(query_times) * 1000, 2) if query_times else 0,
            'query_latency_p95_ms': round(statistics.quantiles(query_times, n=20)[18] * 1000, 2) if len(query_times) > 20 else 0,
            'errors': errors,
        }

        if use_pool:
            result['pool_stats'] = pool.get_stats()
            close_pool()

        logger.info(f"Completed: {test_name} - {throughput:.0f} queries/sec")
        return result

    def run_mixed_workload_test(self, pool_size: int = 5, duration_sec: int = 30,
                                 num_writers: int = 3, num_readers: int = 5, use_pool: bool = True) -> Dict:
        """Test concurrent readers and writers (simulating real workload)."""
        test_name = f"mixed_workload_pool={use_pool}_writers={num_writers}_readers={num_readers}_duration={duration_sec}s"
        logger.info(f"Starting: {test_name}")

        if use_pool:
            pool = init_pool(self.db_path, pool_size=pool_size, max_pool_size=pool_size * 2)
        else:
            pool = None

        start_time = time.time()
        stop_event = threading.Event()
        errors = []
        writes_count = [0]
        reads_count = [0]
        lock = threading.Lock()

        def writer_worker(writer_id: int):
            try:
                loader = SwapsLoader(db_path=self.db_path, use_pool=use_pool)
                while not stop_event.is_set():
                    records = generate_test_records(writer_id, 100)
                    loader.upsert_trades(records)
                    with lock:
                        writes_count[0] += len(records)
            except Exception as e:
                with lock:
                    errors.append(f"Writer {writer_id}: {str(e)}")

        def reader_worker(reader_id: int):
            try:
                query = SwapsQuery(db_path=self.db_path, use_pool=use_pool)
                while not stop_event.is_set():
                    query.get_database_stats()
                    with lock:
                        reads_count[0] += 1
            except Exception as e:
                with lock:
                    errors.append(f"Reader {reader_id}: {str(e)}")

        threads = []
        for i in range(num_writers):
            threads.append(threading.Thread(target=writer_worker, args=(i,)))
        for i in range(num_readers):
            threads.append(threading.Thread(target=reader_worker, args=(i,)))

        for t in threads:
            t.start()

        time.sleep(duration_sec)
        stop_event.set()

        for t in threads:
            t.join()

        total_duration = time.time() - start_time
        write_throughput = writes_count[0] / total_duration if total_duration > 0 else 0
        read_throughput = reads_count[0] / total_duration if total_duration > 0 else 0

        result = {
            'test_name': test_name,
            'use_pool': use_pool,
            'pool_size': pool_size if use_pool else 0,
            'num_writers': num_writers,
            'num_readers': num_readers,
            'duration_sec': round(total_duration, 2),
            'total_writes': writes_count[0],
            'write_throughput_rows_per_sec': round(write_throughput, 2),
            'total_reads': reads_count[0],
            'read_throughput_queries_per_sec': round(read_throughput, 2),
            'errors': errors,
        }

        if use_pool:
            result['pool_stats'] = pool.get_stats()
            close_pool()

        logger.info(f"Completed: {test_name} - Writes: {write_throughput:.0f}/s, Reads: {read_throughput:.0f}/s")
        return result

    def run_all_tests(self) -> Dict:
        """Run comprehensive load test suite."""
        logger.info("=" * 80)
        logger.info("LOAD TEST SUITE: Connection Pool Performance")
        logger.info("=" * 80)

        # Test 1: Upsert throughput without pool (baseline)
        logger.info("\n[1/6] Testing baseline upsert throughput (no pool)...")
        result1 = self.run_upsert_throughput_test(
            num_threads=3, records_per_thread=500, use_pool=False
        )
        self.results['tests']['upsert_no_pool'] = result1

        # Test 2: Upsert throughput with pool
        logger.info("\n[2/6] Testing pooled upsert throughput...")
        result2 = self.run_upsert_throughput_test(
            pool_size=5, num_threads=3, records_per_thread=500, use_pool=True
        )
        self.results['tests']['upsert_with_pool'] = result2

        # Calculate upsert improvement
        if result1['duration_sec'] > 0 and result2['duration_sec'] > 0:
            speedup = result1['duration_sec'] / result2['duration_sec']
            self.results['upsert_speedup'] = round(speedup, 2)
            logger.info(f"Upsert speedup: {speedup:.2f}x")

        # Test 3: Read throughput without pool (baseline)
        logger.info("\n[3/6] Testing baseline read throughput (no pool)...")
        result3 = self.run_read_throughput_test(
            num_readers=3, queries_per_reader=50, use_pool=False
        )
        self.results['tests']['read_no_pool'] = result3

        # Test 4: Read throughput with pool
        logger.info("\n[4/6] Testing pooled read throughput...")
        result4 = self.run_read_throughput_test(
            pool_size=5, num_readers=3, queries_per_reader=50, use_pool=True
        )
        self.results['tests']['read_with_pool'] = result4

        # Calculate read improvement
        if result3['duration_sec'] > 0 and result4['duration_sec'] > 0:
            speedup = result3['duration_sec'] / result4['duration_sec']
            self.results['read_speedup'] = round(speedup, 2)
            logger.info(f"Read speedup: {speedup:.2f}x")

        # Test 5: Mixed workload without pool
        logger.info("\n[5/6] Testing baseline mixed workload (no pool)...")
        result5 = self.run_mixed_workload_test(
            duration_sec=20, num_writers=2, num_readers=3, use_pool=False
        )
        self.results['tests']['mixed_no_pool'] = result5

        # Test 6: Mixed workload with pool
        logger.info("\n[6/6] Testing pooled mixed workload...")
        result6 = self.run_mixed_workload_test(
            pool_size=5, duration_sec=20, num_writers=2, num_readers=3, use_pool=True
        )
        self.results['tests']['mixed_with_pool'] = result6

        # Print summary
        self._print_summary()
        self._save_results()

        return self.results

    def _print_summary(self):
        """Print test summary to console."""
        logger.info("\n" + "=" * 80)
        logger.info("LOAD TEST SUMMARY")
        logger.info("=" * 80)

        if 'upsert_speedup' in self.results:
            logger.info(f"Upsert Speedup (with pool): {self.results['upsert_speedup']}x")
        if 'read_speedup' in self.results:
            logger.info(f"Read Speedup (with pool): {self.results['read_speedup']}x")

        logger.info("\nUpsert Throughput:")
        if 'upsert_no_pool' in self.results['tests']:
            logger.info(f"  Without pool: {self.results['tests']['upsert_no_pool']['throughput_rows_per_sec']} rows/sec")
        if 'upsert_with_pool' in self.results['tests']:
            logger.info(f"  With pool (5 conn): {self.results['tests']['upsert_with_pool']['throughput_rows_per_sec']} rows/sec")

        logger.info("\nRead Throughput:")
        if 'read_no_pool' in self.results['tests']:
            logger.info(f"  Without pool: {self.results['tests']['read_no_pool']['throughput_queries_per_sec']} queries/sec")
        if 'read_with_pool' in self.results['tests']:
            logger.info(f"  With pool (5 conn): {self.results['tests']['read_with_pool']['throughput_queries_per_sec']} queries/sec")

        logger.info("\nMixed Workload (Readers + Writers):")
        if 'mixed_no_pool' in self.results['tests']:
            m1 = self.results['tests']['mixed_no_pool']
            logger.info(f"  Without pool - Writes: {m1['write_throughput_rows_per_sec']} rows/sec, Reads: {m1['read_throughput_queries_per_sec']} queries/sec")
        if 'mixed_with_pool' in self.results['tests']:
            m2 = self.results['tests']['mixed_with_pool']
            logger.info(f"  With pool - Writes: {m2['write_throughput_rows_per_sec']} rows/sec, Reads: {m2['read_throughput_queries_per_sec']} queries/sec")

    def _save_results(self):
        """Save results to JSON file."""
        with open(self.results_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"\nResults saved to: {self.results_file}")


def main():
    """Run the load test suite."""
    # Create temporary database for testing
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        # Initialize schema
        logger.info(f"Initializing test database: {db_path}")
        migrate(db_path)

        # Run load tests
        runner = LoadTestRunner(db_path)
        results = runner.run_all_tests()

        return 0
    except Exception as e:
        logger.error(f"Load test failed: {e}", exc_info=True)
        return 1
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)
        for suffix in ['-wal', '-shm']:
            wal_path = db_path + suffix
            if os.path.exists(wal_path):
                os.unlink(wal_path)


if __name__ == '__main__':
    import sys
    sys.exit(main())
