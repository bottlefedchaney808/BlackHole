"""Backfill the full available DTCC swap history for configured (regulator, asset) targets.

Walks all available cumulative EOD files (starting after the currently
recorded ``last_cumulative_date``), loads them in ascending date order, then
tops up with any live slices published after the last cumulative day.

Supports graceful shutdown: pressing Ctrl+C will finish the current batch,
update state, and exit cleanly.
"""
import logging
import re
import time
from datetime import date
from typing import Optional

from shared.logging import setup_logging, log_operation, get_metrics

from dtcc_api_client import list_live_slices, list_cumulative, download_zip
from dtcc_parser import parse_swap_zip
from db_loader import SwapsLoader
from shutdown_signal import GracefulShutdown

# Setup structured JSON logging
logger = setup_logging(
    name='backfill',
    level=logging.INFO,
    use_json=True,
)

TARGETS = [("SEC", "EQ"), ("CFTC", "EQ")]

# Matches the trailing _YYYY_MM_DD.zip portion of a cumulative fileName, e.g.
# SEC_CUMULATIVE_EQUITIES_2026_07_28.zip
_DATE_SUFFIX_RE = re.compile(r"(\d{4})_(\d{1,2})_(\d{1,2})\.zip$", re.IGNORECASE)


def _extract_date(file_name: str) -> str:
    """Extract a YYYY-MM-DD date string from a cumulative fileName."""
    match = _DATE_SUFFIX_RE.search(file_name)
    if not match:
        raise ValueError(f"Could not extract date from fileName: {file_name}")
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _load_entry(loader, regulator, asset, zip_url, source_file, scrape_date, start_time):
    """Download, parse, and upsert a single entry. Returns (fetched, inserted, updated)."""
    with log_operation(
        'backfill_entry',
        metadata={
            'regulator': regulator,
            'asset': asset,
            'source_file': source_file,
        }
    ) as op_context:
        zip_bytes = download_zip(zip_url)
        records = parse_swap_zip(zip_bytes, regulator, asset, source_file=source_file)
        result = loader.upsert_trades(records)
        duration = int(time.time() - start_time)

        # Record metrics
        metrics = get_metrics()
        metrics.add_upsert_batch(len(records), duration / 1000.0)

        op_context.rows_affected = len(records)

        loader.log_scrape(
            scrape_date=scrape_date,
            fetched=len(records),
            inserted=result.get("inserted", 0),
            updated=result.get("updated", 0),
            errors=0,
            status="success",
            duration=duration,
        )

        logger.info(
            f"Successfully loaded {len(records)} records",
            extra={
                'regulator': regulator,
                'asset': asset,
                'source_file': source_file,
                'fetched': len(records),
                'inserted': result.get("inserted", 0),
                'updated': result.get("updated", 0),
                'duration_sec': duration / 1000.0,
            }
        )

        return len(records), result.get("inserted", 0), result.get("updated", 0)


def backfill_target(regulator: str, asset: str, shutdown: Optional[GracefulShutdown] = None) -> dict:
    """Backfill all available cumulative history plus trailing live slices for one target.

    Args:
        regulator: Regulatory body (e.g., 'SEC', 'CFTC')
        asset: Asset class (e.g., 'EQ')
        shutdown: Optional GracefulShutdown manager for checking shutdown requests

    Returns:
        Summary dict with files_processed, records_loaded, errors counts
    """
    loader = SwapsLoader()
    state = loader.get_state(regulator, asset)
    last_cum_date = state["last_cumulative_date"] if state else None

    files_processed = 0
    records_loaded = 0
    errors = 0

    logger.info(
        "Starting backfill for %s/%s (last_cumulative_date=%s)",
        regulator, asset, last_cum_date,
    )

    cumulative = list_cumulative(regulator, asset)

    dated_entries = []
    for entry in cumulative:
        try:
            entry_date = _extract_date(entry.get("fileName", ""))
        except ValueError as e:
            logger.warning("Skipping cumulative entry with unparsable fileName: %s", e)
            continue
        dated_entries.append((entry_date, entry))

    dated_entries.sort(key=lambda pair: pair[0])

    last_cumulative_day_processed = last_cum_date

    for entry_date, entry in dated_entries:
        if last_cum_date is not None and entry_date <= last_cum_date:
            continue

        # Check if shutdown has been requested; if so, finish current state and exit
        if shutdown and shutdown.is_requested():
            logger.info(
                "Shutdown requested during cumulative processing for %s/%s. "
                "Exiting at entry_date=%s (last processed=%s)",
                regulator, asset, entry_date, last_cumulative_day_processed,
            )
            break

        start_time = time.time()
        logger.info(
            "Processing cumulative day %s for %s/%s (%s)",
            entry_date, regulator, asset, entry.get("fileName"),
        )
        try:
            fetched, inserted, updated = _load_entry(
                loader, regulator, asset,
                entry["fullFilePath"], entry["fileName"],
                scrape_date=entry_date, start_time=start_time,
            )
            # CRITICAL: Update state WITHIN the try block so if set_state fails, we know about it
            # and don't silently lose state. This ensures atomicity: either both upsert and
            # set_state succeed, or both are considered failed.
            loader.set_state(regulator, asset, last_cumulative_date=entry_date)
            files_processed += 1
            records_loaded += fetched
            last_cumulative_day_processed = entry_date
            logger.info(
                "Successfully processed cumulative day %s for %s/%s",
                entry_date, regulator, asset,
            )
        except Exception as e:
            errors += 1
            duration = int(time.time() - start_time)
            logger.error(
                "Failed processing cumulative day %s for %s/%s: %s (state NOT updated; resume will retry)",
                entry_date, regulator, asset, e,
            )
            loader.log_scrape(
                scrape_date=entry_date,
                fetched=0,
                inserted=0,
                updated=0,
                errors=1,
                status="failed",
                error_msg=str(e),
                duration=duration,
            )
            # continue to the next day regardless

    # Top up with live slices published after the last cumulative day processed.
    live_slices = list_live_slices(regulator, asset)

    if last_cumulative_day_processed is not None:
        eligible_slices = [
            s for s in live_slices
            if (s.get("dissemDTM") or "")[:10] >= last_cumulative_day_processed
        ]
    else:
        eligible_slices = list(live_slices)

    eligible_slices.sort(key=lambda s: s.get("sliceId"))

    max_slice_id = None
    for slice_entry in eligible_slices:
        # Check if shutdown has been requested; if so, finish current state and exit
        if shutdown and shutdown.is_requested():
            logger.info(
                "Shutdown requested during live slice processing for %s/%s. "
                "Exiting with last_live_slice_id=%s",
                regulator, asset, max_slice_id,
            )
            break

        slice_id = slice_entry.get("sliceId")
        start_time = time.time()
        slice_date = (slice_entry.get("dissemDTM") or "")[:10] or str(date.today())
        logger.info(
            "Processing live slice %s for %s/%s (dissemDTM=%s)",
            slice_id, regulator, asset, slice_entry.get("dissemDTM"),
        )
        try:
            fetched, inserted, updated = _load_entry(
                loader, regulator, asset,
                slice_entry["fullFilePath"], slice_entry["fileName"],
                scrape_date=slice_date, start_time=start_time,
            )
            files_processed += 1
            records_loaded += fetched
            if max_slice_id is None or slice_id > max_slice_id:
                max_slice_id = slice_id
            # CRITICAL: Update state WITHIN the try block for atomicity (same as cumulative above)
            loader.set_state(regulator, asset, last_live_slice_id=slice_id)
        except Exception as e:
            errors += 1
            duration = int(time.time() - start_time)
            logger.error(
                "Failed processing live slice %s for %s/%s: %s (state NOT updated; resume will retry)",
                slice_id, regulator, asset, e,
            )
            loader.log_scrape(
                scrape_date=slice_date,
                fetched=0,
                inserted=0,
                updated=0,
                errors=1,
                status="failed",
                error_msg=str(e),
                duration=duration,
            )
            # continue to the next slice regardless

    summary = {
        "regulator": regulator,
        "asset": asset,
        "files_processed": files_processed,
        "records_loaded": records_loaded,
        "errors": errors,
    }
    logger.info("Finished backfill for %s/%s: %s", regulator, asset, summary)
    return summary


if __name__ == "__main__":
    from shutdown_signal import create_shutdown_manager

    # Create and register shutdown manager for graceful Ctrl+C handling
    shutdown = create_shutdown_manager()

    summaries = []
    for regulator, asset in TARGETS:
        logger.info("=" * 60)
        logger.info(f"Starting backfill for {regulator}/{asset}")
        logger.info("=" * 60)
        try:
            summary = backfill_target(regulator, asset, shutdown=shutdown)
            summaries.append(summary)

            # Check if shutdown was requested during this target
            if shutdown.is_requested():
                logger.info("Shutdown requested: stopping backfill after current target")
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

    # Print summary table
    header = f"{'Regulator':<10}{'Asset':<8}{'Files':<10}{'Records':<12}{'Errors':<8}"
    print("\n" + header)
    print("-" * len(header))
    for s in summaries:
        print(
            f"{s['regulator']:<10}{s['asset']:<8}{s['files_processed']:<10}"
            f"{s['records_loaded']:<12}{s['errors']:<8}"
        )

    # Cleanup shutdown manager
    shutdown.cleanup()
