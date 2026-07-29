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

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = int(os.getenv('POLL_INTERVAL_MINUTES', '5'))


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
    """Start background scheduler for periodic polling."""
    if not SCHEDULER_AVAILABLE:
        logger.error("APScheduler not installed. Run: pip install APScheduler")
        sys.exit(1)

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        run_ingestion_job,
        'interval',
        minutes=POLL_INTERVAL_MINUTES,
        id='dtcc_poll',
        name='DTCC Live Poll',
        replace_existing=True
    )

    scheduler.start()
    logger.info("=" * 60)
    logger.info("Scheduler started")
    logger.info(f"  Polling every {POLL_INTERVAL_MINUTES} minute(s)")
    logger.info("=" * 60)

    return scheduler


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
        summaries = []
        for regulator, asset in backfill.TARGETS:
            summaries.append(backfill.backfill_target(regulator, asset))

        header = f"{'Regulator':<10}{'Asset':<8}{'Files':<10}{'Records':<12}{'Errors':<8}"
        print("\n" + header)
        print("-" * len(header))
        for s in summaries:
            print(
                f"{s['regulator']:<10}{s['asset']:<8}{s['files_processed']:<10}"
                f"{s['records_loaded']:<12}{s['errors']:<8}"
            )

        print("\nDecoding UPIs seen during backfill...")
        try:
            decode_stats = decode_upis.run(max_batches=10_000)
            print(f"Decode complete: {decode_stats}")
        except Exception as e:
            print(f"UPI decode pass failed (non-fatal): {e}")

    elif args.start_scheduler:
        logger.info("Starting scheduler in production mode...")
        try:
            scheduler = start_scheduler()
            # Keep scheduler running
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user.")
            scheduler.shutdown()
            sys.exit(0)

    else:
        parser.print_help()
        print("\nExamples:")
        print("  python scheduled_ingest.py --run-now          # Run a single poll now")
        print("  python scheduled_ingest.py --backfill         # Backfill full history")
        print("  python scheduled_ingest.py --start-scheduler  # Run scheduler")
