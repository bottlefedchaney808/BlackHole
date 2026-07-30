"""Scheduled ingestion job for DTCC swaps data."""
import logging
import os
import sys
from dotenv import load_dotenv

# Try to import APScheduler; if not available, provide fallback
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    SCHEDULER_AVAILABLE = True
except ImportError:
    SCHEDULER_AVAILABLE = False

import poll_ingest
import backfill
import decode_upis
from shutdown_signal import create_shutdown_manager

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = int(os.getenv('POLL_INTERVAL_MINUTES', '5'))

# Heartbeat file, touched on a short fixed interval independent of
# POLL_INTERVAL_MINUTES so container health checks (Dockerfile.scheduler)
# have a fast, reliable liveness signal that doesn't wait on the poll cadence.
HEARTBEAT_FILE = os.getenv(
    'SCHEDULER_HEARTBEAT_FILE',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '.scheduler_heartbeat'),
)
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv('SCHEDULER_HEARTBEAT_INTERVAL_SECONDS', '60'))


def touch_heartbeat():
    """Update the heartbeat file's mtime so external health checks can tell
    the scheduler loop is still alive (see HEALTHCHECK in Dockerfile.scheduler)."""
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE) or '.', exist_ok=True)
        with open(HEARTBEAT_FILE, 'a'):
            os.utime(HEARTBEAT_FILE, None)
    except OSError as e:
        logger.warning("Failed to update heartbeat file %s: %s", HEARTBEAT_FILE, e)


def run_ingestion_job():
    """Main job: run an incremental poll pass against DTCC's live slice feed."""
    logger.info("=" * 60)
    logger.info("Starting DTCC swaps poll")
    logger.info("=" * 60)

    summary = poll_ingest.run()

    logger.info("=" * 60)
    logger.info("Poll complete")
    for key, stats in summary.items():
        logger.info(f"  {key}: new_slices={stats['new_slices']} records={stats['records']}")
    logger.info("=" * 60)

    try:
        decode_stats = decode_upis.run()
        logger.info("UPI decode pass: %s", decode_stats)
    except Exception as e:
        # Decoding new UPIs is best-effort -- a broken OpenFIGI call or a
        # locked db must never fail the ingestion job itself.
        logger.warning("UPI decode pass failed (non-fatal): %s", e)

    return summary


def start_scheduler():
    """Start background scheduler for periodic polling with graceful shutdown."""
    if not SCHEDULER_AVAILABLE:
        logger.error("APScheduler not installed. Run: pip install APScheduler")
        sys.exit(1)

    scheduler = BackgroundScheduler()
    shutdown = create_shutdown_manager()

    scheduler.add_job(
        run_ingestion_job,
        'interval',
        minutes=POLL_INTERVAL_MINUTES,
        id='dtcc_poll',
        name='DTCC Live Poll',
        replace_existing=True
    )
    scheduler.add_job(
        touch_heartbeat,
        'interval',
        seconds=HEARTBEAT_INTERVAL_SECONDS,
        id='heartbeat',
        name='Heartbeat',
        replace_existing=True
    )

    touch_heartbeat()  # write one immediately so health checks pass before the first tick
    scheduler.start()
    logger.info("=" * 60)
    logger.info("Scheduler started")
    logger.info(f"  Polling every {POLL_INTERVAL_MINUTES} minute(s)")
    logger.info(f"  Heartbeat file: {HEARTBEAT_FILE} (every {HEARTBEAT_INTERVAL_SECONDS}s)")
    logger.info("=" * 60)

    return scheduler, shutdown


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='DTCC swaps data ingestion')
    parser.add_argument('--run-now', action='store_true',
                        help='Run a single poll pass immediately')
    parser.add_argument('--backfill', action='store_true',
                        help='Backfill full historical data for all configured targets')
    parser.add_argument('--start-scheduler', action='store_true',
                        help='Start background scheduler (production mode)')

    args = parser.parse_args()

    if args.run_now:
        logger.info("Running poll immediately...")
        try:
            result = run_ingestion_job()
            print(f"\nPoll result: {result}")
        except Exception as e:
            print(f"\nPoll failed: {e}")
            sys.exit(1)

    elif args.backfill:
        logger.info("Running backfill for all configured targets...")
        shutdown = create_shutdown_manager()

        summaries = []
        for regulator, asset in backfill.TARGETS:
            logger.info("=" * 60)
            logger.info(f"Starting backfill for {regulator}/{asset}")
            logger.info("=" * 60)
            try:
                summary = backfill.backfill_target(regulator, asset, shutdown=shutdown)
                summaries.append(summary)

                # Check if shutdown was requested
                if shutdown.is_requested():
                    logger.info("Shutdown requested: stopping backfill")
                    break
            except Exception as e:
                logger.error("Backfill for %s/%s failed: %s", regulator, asset, e)
                summaries.append({
                    "regulator": regulator,
                    "asset": asset,
                    "files_processed": 0,
                    "records_loaded": 0,
                    "errors": 1,
                })

        header = f"{'Regulator':<10}{'Asset':<8}{'Files':<10}{'Records':<12}{'Errors':<8}"
        print("\n" + header)
        print("-" * len(header))
        for s in summaries:
            print(
                f"{s['regulator']:<10}{s['asset']:<8}{s['files_processed']:<10}"
                f"{s['records_loaded']:<12}{s['errors']:<8}"
            )

        if not shutdown.is_requested():
            print("\nDecoding UPIs seen during backfill...")
            try:
                decode_stats = decode_upis.run(max_batches=10_000)
                print(f"Decode complete: {decode_stats}")
            except Exception as e:
                print(f"UPI decode pass failed (non-fatal): {e}")
        else:
            logger.info("Skipping UPI decode due to shutdown request")

        shutdown.cleanup()

    elif args.start_scheduler:
        logger.info("Starting scheduler in production mode...")
        try:
            scheduler, shutdown = start_scheduler()
            # Keep scheduler running until shutdown is requested
            import time
            while not shutdown.is_requested():
                time.sleep(1)

            # Graceful shutdown: finish current jobs and exit cleanly
            logger.info("Graceful shutdown: waiting for scheduler to complete current jobs...")
            scheduler.shutdown(wait=True)
            shutdown.cleanup()
            logger.info("Shutdown complete")
            sys.exit(0)
        except KeyboardInterrupt:
            # This shouldn't normally fire since shutdown manager handles SIGINT,
            # but keep as fallback
            logger.info("Scheduler stopped by user.")
            try:
                scheduler.shutdown(wait=True)
            except Exception as e:
                logger.warning("Error during scheduler shutdown: %s", e)
            sys.exit(0)
        except Exception as e:
            logger.error("Scheduler error: %s", e)
            try:
                scheduler.shutdown(wait=False)
            except Exception as shutdown_error:
                logger.warning("Error during emergency scheduler shutdown: %s", shutdown_error)
            sys.exit(1)

    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scheduled_ingest.py --run-now          # Run a single poll now")
        print("  python scheduled_ingest.py --backfill         # Backfill full history")
        print("  python scheduled_ingest.py --start-scheduler  # Run scheduler")
