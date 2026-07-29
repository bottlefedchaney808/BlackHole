"""Backfill the full available DTCC swap history for configured (regulator, asset) targets.

Walks all available cumulative EOD files (starting after the currently
recorded ``last_cumulative_date``), loads them in ascending date order, then
tops up with any live slices published after the last cumulative day.
"""
import logging
import re
import time
from datetime import date

from dtcc_api_client import list_live_slices, list_cumulative, download_zip
from dtcc_parser import parse_swap_zip
from db_loader import SwapsLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

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
    zip_bytes = download_zip(zip_url)
    records = parse_swap_zip(zip_bytes, regulator, asset, source_file=source_file)
    result = loader.upsert_trades(records)
    duration = int(time.time() - start_time)
    loader.log_scrape(
        scrape_date=scrape_date,
        fetched=len(records),
        inserted=result.get("inserted", 0),
        updated=result.get("updated", 0),
        errors=0,
        status="success",
        duration=duration,
    )
    return len(records), result.get("inserted", 0), result.get("updated", 0)


def backfill_target(regulator: str, asset: str) -> dict:
    """Backfill all available cumulative history plus trailing live slices for one target."""
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
            loader.set_state(regulator, asset, last_cumulative_date=entry_date)
            files_processed += 1
            records_loaded += fetched
            last_cumulative_day_processed = entry_date
        except Exception as e:
            errors += 1
            duration = int(time.time() - start_time)
            logger.error(
                "Failed processing cumulative day %s for %s/%s: %s",
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
        except Exception as e:
            errors += 1
            duration = int(time.time() - start_time)
            logger.error(
                "Failed processing live slice %s for %s/%s: %s",
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

    if max_slice_id is not None:
        loader.set_state(regulator, asset, last_live_slice_id=max_slice_id)

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
    summaries = []
    for regulator, asset in TARGETS:
        summaries.append(backfill_target(regulator, asset))

    header = f"{'Regulator':<10}{'Asset':<8}{'Files':<10}{'Records':<12}{'Errors':<8}"
    print("\n" + header)
    print("-" * len(header))
    for s in summaries:
        print(
            f"{s['regulator']:<10}{s['asset']:<8}{s['files_processed']:<10}"
            f"{s['records_loaded']:<12}{s['errors']:<8}"
        )
