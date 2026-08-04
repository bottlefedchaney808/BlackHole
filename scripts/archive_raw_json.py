"""archive_raw_json.py

Enforces the retention policy in docs/SWAPS_DB_RETENTION_POLICY.md: rows in
swap_trades older than RETENTION_DAYS (by ingested_at, not effective_date --
this is about how long ago *we* wrote the payload, not the trade's own date)
get their raw_json exported to a gzip-compressed, date-partitioned file under
raw_json_archive/, verified by reading the file back, and only then NULLed in
the database. The structured columns (notional, price, dates, upi, etc.) are
never touched -- this script only ever affects the raw_json column's value,
never deletes a row.

Dry-run by default. Pass --execute to actually write archive files and NULL
rows; without it, this only reports what it would do.

Resumable and idempotent by construction: it only ever selects rows where
raw_json IS NOT NULL, so a row already archived (raw_json already NULL) is
silently skipped on a later run -- interrupting with Ctrl+C mid-run just means
some batches finished and some didn't, safe to resume by re-running.

Meant to run as a plain OS-level scheduled task (Windows Task Scheduler),
monthly is more than sufficient given the 400-day window -- same reasoning as
scripts/quant_alert_check.py's own docstring for why not CronCreate (session-
scoped schedules aren't durable).
"""
import argparse
import gzip
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db_loader  # noqa: E402  (DB_PATH, _write_lock, _configure_connection)
from shutdown_signal import create_shutdown_manager  # noqa: E402

RETENTION_DAYS = 90
BATCH_SIZE = 5000
ARCHIVE_DIR = os.path.join(ROOT, "raw_json_archive")


def _cutoff_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_batch(conn: sqlite3.Connection, cutoff: str, batch_size: int):
    """One batch of (dissemination_id, raw_json) for rows older than cutoff
    that still have raw_json set. Ordered by ingested_at so archive files
    land in roughly chronological order, easier for a human to sanity-check.
    """
    cur = conn.execute(
        "SELECT dissemination_id, raw_json, ingested_at FROM swap_trades "
        "WHERE ingested_at < ? AND raw_json IS NOT NULL "
        "ORDER BY ingested_at LIMIT ?",
        (cutoff, batch_size),
    )
    return cur.fetchall()


def _count_eligible(conn: sqlite3.Connection, cutoff: str) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) FROM swap_trades WHERE ingested_at < ? AND raw_json IS NOT NULL",
        (cutoff,),
    )
    return cur.fetchone()[0]


def _estimate_bytes(conn: sqlite3.Connection, cutoff: str, sample_size: int = 2000) -> float:
    """Average raw_json length over a bounded sample (never a full-table
    scan -- that times out at this row count, see the retention policy doc's
    own note about why a sampled estimate was used instead)."""
    cur = conn.execute(
        "SELECT AVG(LENGTH(raw_json)) FROM ("
        "  SELECT raw_json FROM swap_trades "
        "  WHERE ingested_at < ? AND raw_json IS NOT NULL LIMIT ?"
        ")",
        (cutoff, sample_size),
    )
    avg_len = cur.fetchone()[0]
    return float(avg_len) if avg_len is not None else 0.0


def _archive_file_path(batch_index: int) -> str:
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(ARCHIVE_DIR, f"raw_json_archive_{stamp}_batch{batch_index:05d}.jsonl.gz")


def _write_archive_file(path: str, rows) -> None:
    tmp_path = f"{path}.tmp"
    with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        for dissemination_id, raw_json, ingested_at in rows:
            f.write(json.dumps({
                "dissemination_id": dissemination_id,
                "ingested_at": ingested_at,
                "raw_json": raw_json,
            }))
            f.write("\n")
    os.replace(tmp_path, path)


def _verify_archive_file(path: str, expected_ids) -> bool:
    """Read the just-written file back and confirm every expected
    dissemination_id round-tripped, before the caller is allowed to NULL
    anything in the database. Never trust a write without reading it back
    for an operation this hard to reverse.
    """
    found_ids = set()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            found_ids.add(record["dissemination_id"])
    return found_ids == set(expected_ids)


def run(execute: bool) -> int:
    cutoff = _cutoff_iso()
    conn = sqlite3.connect(db_loader.DB_PATH, timeout=30)
    db_loader._configure_connection(conn)

    try:
        eligible = _count_eligible(conn, cutoff)
        if eligible == 0:
            print(f"[archive_raw_json] no rows older than {cutoff} with raw_json still set -- nothing to do.")
            return 0

        avg_len = _estimate_bytes(conn, cutoff)
        est_bytes = avg_len * eligible
        print(f"[archive_raw_json] cutoff (ingested_at < ): {cutoff}")
        print(f"[archive_raw_json] eligible rows: {eligible:,}")
        print(f"[archive_raw_json] sampled avg raw_json length: {avg_len:,.0f} bytes")
        print(f"[archive_raw_json] estimated bytes reclaimable (pre-compression): {est_bytes / 1e9:,.2f} GB")

        if not execute:
            print("[archive_raw_json] DRY RUN -- no files written, no rows changed. Pass --execute to run for real.")
            return 0

        shutdown = create_shutdown_manager()
        batch_index = 0
        total_archived = 0
        try:
            while not shutdown.is_requested():
                rows = _fetch_batch(conn, cutoff, BATCH_SIZE)
                if not rows:
                    break

                path = _archive_file_path(batch_index)
                _write_archive_file(path, rows)

                ids = [r[0] for r in rows]
                if not _verify_archive_file(path, ids):
                    print(f"[archive_raw_json] ABORT: archive file {path} failed round-trip "
                          f"verification -- NOT nulling this batch. Investigate before rerunning.",
                          file=sys.stderr)
                    return 1

                with db_loader._write_lock:
                    placeholders = ",".join("?" for _ in ids)
                    with conn:
                        conn.execute(
                            f"UPDATE swap_trades SET raw_json = NULL "
                            f"WHERE dissemination_id IN ({placeholders})",
                            ids,
                        )

                total_archived += len(rows)
                batch_index += 1
                print(f"[archive_raw_json] batch {batch_index}: archived + nulled {len(rows):,} rows "
                      f"-> {os.path.basename(path)} (total so far: {total_archived:,}/{eligible:,})")

            if shutdown.is_requested():
                print(f"[archive_raw_json] shutdown requested -- stopped cleanly after "
                      f"{total_archived:,} rows. Safe to resume by rerunning.")
            else:
                print(f"[archive_raw_json] done. {total_archived:,} rows archived and nulled.")
        finally:
            shutdown.cleanup()

        return 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Actually write archive files and NULL rows. Without this, dry-run only.")
    args = parser.parse_args()
    return run(execute=args.execute)


if __name__ == "__main__":
    sys.exit(main())
