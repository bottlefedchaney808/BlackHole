"""decode_upis.py -- incrementally decode swap_trades.upi into company names.

Walks swap_trades in rowid order starting from the watermark recorded in
upi_decode_state, collects UPIs not yet in upi_reference, decodes each via
upi_decoder.decode_one(), and upserts the result. Safe to re-run: it never
rescans rows it has already looked at, so cost scales with new trades
ingested since the last run, not with the size of swap_trades (~386GB and
growing).
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from typing import Optional

from upi_decoder import OpenFigiClient, decode_one

# Reuse db_loader's connection settings and single-writer cross-process lock.
# decode_upis.py writes to upi_decode_state/upi_reference in the same swaps.db
# file that db_loader.py writes swap_trades/ingestion_state/scrape_log to, and
# it runs right after both the poller (scheduled_ingest.run_ingestion_job) and
# backfill (scheduled_ingest.py --backfill). Without sharing db_loader's lock
# and pragmas, this path could race a concurrent db_loader write on the same
# file even though the lock in db_loader.py alone would otherwise serialize
# writers.
from db_loader import _configure_connection, _write_lock

logger = logging.getLogger(__name__)

# SWAPS_DB_PATH env var overrides, e.g. for a mounted Docker volume; see
# .env.example / docker-compose.yml
DB_PATH = os.environ.get('SWAPS_DB_PATH') or os.path.join(os.path.dirname(__file__), 'swaps.db')
DEFAULT_BATCH_SIZE = 5000
DEFAULT_MAX_BATCHES = 20


def _connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    return conn


def _get_watermark(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT last_rowid FROM upi_decode_state WHERE id = 1;").fetchone()
    return row["last_rowid"] if row else 0


def _set_watermark(conn: sqlite3.Connection, rowid: int) -> None:
    conn.execute(
        """
        INSERT INTO upi_decode_state (id, last_rowid, updated_at)
        VALUES (1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            last_rowid = excluded.last_rowid,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (rowid,),
    )


def run_batch(conn: sqlite3.Connection, batch_size: int, figi_client: Optional[OpenFigiClient]) -> dict:
    """Process one batch of up to batch_size new swap_trades rows.

    Returns {"rows_scanned", "new_upis", "decoded", "max_rowid"}.
    """
    watermark = _get_watermark(conn)

    rows = conn.execute(
        """
        SELECT rowid, upi, underlier_id_leg1, underlier_id_source_leg1, upi_underlier_name
        FROM swap_trades
        WHERE rowid > ? AND upi IS NOT NULL AND upi != ''
        ORDER BY rowid
        LIMIT ?;
        """,
        (watermark, batch_size),
    ).fetchall()

    if not rows:
        return {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "max_rowid": watermark}

    max_rowid = rows[-1]["rowid"]
    seen_upis = set()
    new_rows = []
    for r in rows:
        upi = r["upi"]
        if upi in seen_upis:
            continue
        seen_upis.add(upi)
        already_decoded = conn.execute(
            "SELECT 1 FROM upi_reference WHERE upi = ?;", (upi,)
        ).fetchone()
        if already_decoded is None:
            new_rows.append(r)

    decoded = 0
    for r in new_rows:
        result = decode_one(
            r["upi_underlier_name"], r["underlier_id_leg1"], r["underlier_id_source_leg1"],
            figi_client=figi_client,
        )
        conn.execute(
            """
            INSERT INTO upi_reference (upi, company_name, decode_tier, decode_detail, raw_underlier_name, ticker, decoded_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(upi) DO UPDATE SET
                company_name = excluded.company_name,
                decode_tier = excluded.decode_tier,
                decode_detail = excluded.decode_detail,
                raw_underlier_name = excluded.raw_underlier_name,
                ticker = excluded.ticker,
                decoded_at = CURRENT_TIMESTAMP;
            """,
            (r["upi"], result.company_name, result.tier, result.detail,
             r["upi_underlier_name"], result.ticker),
        )
        if result.company_name:
            decoded += 1

    _set_watermark(conn, max_rowid)
    conn.commit()

    return {"rows_scanned": len(rows), "new_upis": len(new_rows), "decoded": decoded, "max_rowid": max_rowid}


def run(
    db_path: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    use_openfigi: bool = True,
) -> dict:
    """Run up to max_batches batches, stopping early once caught up."""
    conn = _connection(db_path or DB_PATH)
    figi_client = OpenFigiClient(api_key=os.getenv("OPENFIGI_API_KEY")) if use_openfigi else None

    totals = {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "batches": 0}
    try:
        for _ in range(max_batches):
            # Hold the same cross-process write lock db_loader.py uses for the
            # full read+decode+write batch, not just the final commit, so this
            # batch can't interleave with a concurrent db_loader write txn.
            with _write_lock:
                stats = run_batch(conn, batch_size, figi_client)
            totals["rows_scanned"] += stats["rows_scanned"]
            totals["new_upis"] += stats["new_upis"]
            totals["decoded"] += stats["decoded"]
            totals["batches"] += 1
            if stats["rows_scanned"] < batch_size:
                break
        return totals
    finally:
        conn.close()


def _ticker_backfill_batch(conn: sqlite3.Connection, batch_size: int,
                           figi_client: Optional[OpenFigiClient]) -> dict:
    """One bounded batch of the historical ticker-enrichment pass.

    Targets upi_reference rows already decoded via the OpenFIGI tier
    *before* migration 005 added the `ticker` column -- those rows have
    company_name/decode_tier/decode_detail set but ticker still NULL.
    'local'/'unresolved' tier rows are untouched: decode_local() never
    produces a ticker, so there is nothing to backfill for them.

    Resumable with no extra state table: this always selects rows still
    missing `ticker`, and successfully backfilled rows drop out of that
    WHERE clause immediately, so re-running (e.g. after an OpenFIGI
    timeout, or across many bounded runs) picks up exactly where the last
    call left off. Cost scales with how many old rows are left to backfill,
    not with the size of upi_reference.
    """
    rows = conn.execute(
        """
        SELECT ur.upi, st.underlier_id_leg1, st.underlier_id_source_leg1
        FROM upi_reference ur
        JOIN swap_trades st ON st.upi = ur.upi
        WHERE ur.decode_tier = 'openfigi' AND ur.ticker IS NULL
        GROUP BY ur.upi
        LIMIT ?;
        """,
        (batch_size,),
    ).fetchall()

    if not rows:
        return {"rows_scanned": 0, "updated": 0}

    updated = 0
    for r in rows:
        result = decode_one(
            None, r["underlier_id_leg1"], r["underlier_id_source_leg1"],
            figi_client=figi_client,
        )
        if result.ticker:
            conn.execute(
                "UPDATE upi_reference SET ticker = ? WHERE upi = ?;",
                (result.ticker, r["upi"]),
            )
            updated += 1

    conn.commit()
    return {"rows_scanned": len(rows), "updated": updated}


def run_ticker_backfill(
    db_path: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    use_openfigi: bool = True,
) -> dict:
    """Bounded, resumable historical enrichment pass: fills in `ticker` for
    upi_reference rows decoded via OpenFIGI before that column existed.

    Safe to interrupt and re-run -- see `_ticker_backfill_batch`'s
    docstring. Run this once after applying migration 005 to catch up
    existing data; `run()`'s normal incremental decode already writes
    `ticker` for every new row going forward, so this is a one-time (or
    occasional) catch-up, not something the scheduler needs to call.
    """
    conn = _connection(db_path or DB_PATH)
    figi_client = OpenFigiClient(api_key=os.getenv("OPENFIGI_API_KEY")) if use_openfigi else None

    totals = {"rows_scanned": 0, "updated": 0, "batches": 0}
    try:
        for _ in range(max_batches):
            with _write_lock:
                stats = _ticker_backfill_batch(conn, batch_size, figi_client)
            totals["rows_scanned"] += stats["rows_scanned"]
            totals["updated"] += stats["updated"]
            totals["batches"] += 1
            if stats["rows_scanned"] < batch_size:
                break
        return totals
    finally:
        conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description='Decode UPI codes into company names.')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--max-batches', type=int, default=DEFAULT_MAX_BATCHES)
    parser.add_argument('--no-openfigi', action='store_true', help='Skip tier-2 OpenFIGI lookups (local decode only).')
    parser.add_argument('--backfill-tickers', action='store_true',
                        help='Instead of the normal incremental decode, run the bounded '
                             'historical ticker-enrichment pass (see run_ticker_backfill).')
    args = parser.parse_args()

    if args.backfill_tickers:
        result = run_ticker_backfill(batch_size=args.batch_size, max_batches=args.max_batches,
                                     use_openfigi=not args.no_openfigi)
        logger.info("Ticker backfill complete: %s", result)
        print(result)
    else:
        result = run(batch_size=args.batch_size, max_batches=args.max_batches, use_openfigi=not args.no_openfigi)
        logger.info("Decode run complete: %s", result)
        print(result)
