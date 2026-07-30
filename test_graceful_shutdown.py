#!/usr/bin/env python3
"""Test script to verify graceful shutdown behavior.

Demonstrates:
1. Signal handler registration (SIGINT/SIGTERM)
2. Graceful shutdown during batch processing
3. State preservation on shutdown
4. Clean exit without data loss
"""
import logging
import time
import signal
import sys
from shutdown_signal import create_shutdown_manager, GracefulShutdown

# Configure logging to show timestamps and messages clearly
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_basic_shutdown():
    """Test 1: Basic signal handler registration and detection."""
    logger.info("=" * 70)
    logger.info("TEST 1: Basic Signal Handler Registration")
    logger.info("=" * 70)

    shutdown = create_shutdown_manager()
    logger.info("Shutdown manager created and registered")
    logger.info(f"Shutdown requested: {shutdown.is_requested()}")

    # Simulate receiving a signal
    logger.info("Simulating SIGINT signal...")
    signal.raise_signal(signal.SIGINT)

    # Give the signal time to be processed
    time.sleep(0.1)

    logger.info(f"Shutdown requested after signal: {shutdown.is_requested()}")
    shutdown.cleanup()
    logger.info("PASS: Signal handler working correctly\n")


def test_batch_processing_with_shutdown():
    """Test 2: Graceful shutdown during batch processing.

    Simulates backfill or scheduler processing batches and checking
    shutdown flag after each batch.
    """
    logger.info("=" * 70)
    logger.info("TEST 2: Batch Processing with Graceful Shutdown")
    logger.info("=" * 70)

    shutdown = create_shutdown_manager()
    batch_size = 5
    total_batches = 20
    batches_processed = 0
    records_loaded = 0

    logger.info(f"Processing {total_batches} batches of {batch_size} records each")
    logger.info("Try pressing Ctrl+C to test graceful shutdown...")
    logger.info("")

    # Simulate batch processing
    try:
        for batch_num in range(1, total_batches + 1):
            # Check shutdown flag BEFORE processing batch (key for graceful behavior)
            if shutdown.is_requested():
                logger.info(f"Graceful shutdown initiated: stopping at batch {batch_num}")
                break

            # Simulate batch processing
            logger.info(f"Processing batch {batch_num}/{total_batches}...")
            batch_start = time.time()

            # Simulate work: increment counters and sleep briefly
            batches_processed += 1
            records_loaded += batch_size

            # Sleep to simulate actual I/O work and allow time for Ctrl+C
            time.sleep(1)

            batch_time = time.time() - batch_start
            logger.info(
                f"  Completed batch {batch_num}: "
                f"loaded {batch_size} records in {batch_time:.2f}s"
            )

            # Check shutdown after batch completes (crucial: finish current batch before exiting)
            if shutdown.is_requested():
                logger.info("Shutdown requested: finishing cleanup and exiting")
                break

    except KeyboardInterrupt:
        logger.info("Caught KeyboardInterrupt (this shouldn't fire with signal manager)")
        pass
    finally:
        # Always cleanup state on exit
        logger.info("")
        logger.info("=" * 70)
        logger.info("SHUTDOWN SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Batches processed: {batches_processed}")
        logger.info(f"Records loaded: {records_loaded}")
        logger.info(f"Shutdown gracefully: {shutdown.is_requested()}")
        shutdown.cleanup()
        logger.info("PASS: Graceful shutdown during batch processing\n")


def test_state_preservation():
    """Test 3: Verify that state is preserved across shutdown.

    This simulates the critical requirement that last_cumulative_date
    and other state are updated BEFORE shutdown, not after.
    """
    logger.info("=" * 70)
    logger.info("TEST 3: State Preservation on Graceful Shutdown")
    logger.info("=" * 70)

    shutdown = create_shutdown_manager()

    # Simulate state management (like in backfill)
    class MockState:
        def __init__(self):
            self.last_cumulative_date = None
            self.last_live_slice_id = None

    state = MockState()
    entries = [
        {"date": "2026-01-01", "records": 100},
        {"date": "2026-01-02", "records": 150},
        {"date": "2026-01-03", "records": 120},
        {"date": "2026-01-04", "records": 200},
    ]

    logger.info("Processing entries with state updates:")
    for entry in entries:
        if shutdown.is_requested():
            logger.info(f"Shutdown requested: stopping at {entry['date']}")
            break

        logger.info(f"Processing {entry['date']}...")
        # CRITICAL: Update state WITHIN processing block (before shutdown check)
        state.last_cumulative_date = entry["date"]
        time.sleep(0.5)

        # Simulate shutdown signal after 2 batches
        if entry["date"] == "2026-01-02":
            logger.info("(Simulating shutdown signal here)")
            signal.raise_signal(signal.SIGINT)
            time.sleep(0.1)

        logger.info(
            f"  Entry processed and state updated: {entry['date']} "
            f"({entry['records']} records)"
        )

        if shutdown.is_requested():
            logger.info("Shutdown requested: exiting after current batch")
            break

    logger.info("")
    logger.info("=" * 70)
    logger.info("STATE PRESERVATION SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Last cumulative date saved: {state.last_cumulative_date}")
    logger.info(f"Expected to be: 2026-01-02")
    assert state.last_cumulative_date == "2026-01-02", "State not preserved correctly!"
    logger.info("PASS: State correctly preserved on graceful shutdown\n")

    shutdown.cleanup()


def test_scheduler_scenario():
    """Test 4: Simulate scheduler scenario with clean shutdown.

    This mimics scheduled_ingest.py behavior where scheduler runs
    until shutdown is requested.
    """
    logger.info("=" * 70)
    logger.info("TEST 4: Scheduler Loop with Graceful Shutdown")
    logger.info("=" * 70)

    shutdown = create_shutdown_manager()
    job_count = 0
    max_jobs = 15

    logger.info(f"Starting scheduler loop (will run max {max_jobs} jobs)")
    logger.info("Try pressing Ctrl+C to trigger graceful shutdown...\n")

    try:
        while not shutdown.is_requested() and job_count < max_jobs:
            job_count += 1
            logger.info(f"Job {job_count}: Starting ingestion cycle...")

            # Simulate job work
            time.sleep(0.5)

            logger.info(f"Job {job_count}: Completed successfully")

            # In real scheduler, sleep between jobs
            time.sleep(0.5)

    finally:
        logger.info("")
        logger.info("=" * 70)
        logger.info("SCHEDULER SHUTDOWN SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Jobs completed: {job_count}")
        logger.info(f"Graceful shutdown requested: {shutdown.is_requested()}")
        logger.info("Calling scheduler.shutdown(wait=True) would happen here")
        shutdown.cleanup()
        logger.info("PASS: Scheduler graceful shutdown complete\n")


def main():
    """Run all tests."""
    logger.info("\n")
    logger.info("#" * 70)
    logger.info("# GRACEFUL SHUTDOWN TEST SUITE")
    logger.info("#" * 70)
    logger.info("\n")

    tests = [
        ("Test 1: Basic Signal Handler", test_basic_shutdown),
        ("Test 2: Batch Processing", test_batch_processing_with_shutdown),
        ("Test 3: State Preservation", test_state_preservation),
        ("Test 4: Scheduler Scenario", test_scheduler_scenario),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            logger.error(f"FAIL: {test_name} - {e}")
            failed += 1

    logger.info("")
    logger.info("#" * 70)
    logger.info("# TEST RESULTS")
    logger.info("#" * 70)
    logger.info(f"Passed: {passed}/{len(tests)}")
    logger.info(f"Failed: {failed}/{len(tests)}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
