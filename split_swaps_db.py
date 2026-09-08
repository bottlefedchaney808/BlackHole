#!/usr/bin/env python3
"""Split the 346 GB OneDrive swaps.db into a small recent-window primary DB
plus gzip-compressed archives of everything older, built in disk-safe chunks.

Layout after this runs:
  C:/Users/bottl/FinancialDevelopment/swaps.db          <- schema + effective_date >= cutoff (apps read here)
  C:/Users/bottl/OneDrive/Stocks/Swaps/archive/*.db.gz  <- all older rows, compressed per chunk
  C:/Users/bottl/OneDrive/Stocks/Swaps/swaps.db         <- original source, untouched

Safety:
- Source is opened read-only (uri mode=ro) and never modified.
- Primary is built in a .tmp file next to the destination and atomically
  os.replace()'d into place only after row-count verification passes.
- Each archive chunk's raw DB is built on C:, gzipped straight to OneDrive,
  then deleted -- peak extra disk = one month of data (~130 GB), not all of it.

Usage: python split_swaps_db.py [--cutoff YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

SOURCE = r"C:\Users\bottl\OneDrive\Stocks\Swaps\swaps.db"
PRIMARY_DEST = r"C:/Users/bottl/FinancialDevelopment/swaps.db"
ARCHIVE_DIR = Path(r"C:\Users\bottl\OneDrive\Stocks\Swaps\archive")
STAGE_DIR = Path(os.environ.get("TEMP", "C:/Windows/Temp")) / "swaps_split_stage"

# (label, start_inclusive, end_exclusive) -- covers everything before cutoff.
CHUNKS = [
    ("pre-2025-06", "2009-01-01", "2025-06-01"),   # everything before June 2025 (~3.4M rows)
    ("2025-06", "2025-06-01", "2025-07-01"),     # 2.8M rows
    ("2025-07", "2025-07-01", "2025-08-01"),     # 7.2M rows
    ("2025-08", "2025-08-01", "2025-09-01"),     # 28.4M rows (biggest)
    ("2025-09-onward", "2025-09-01", "2026-01-01"),  # 22.7M + Oct-Dec tail
]

COLUMNS = (
    "dissemination_id, original_dissemination_id, regulator, asset_class,"
    " action_type, event_type, event_timestamp, execution_timestamp,"
    " effective_date, expiration_date, cleared, notional_amount_leg1,"
    " notional_currency_leg1, notional_amount_leg2, notional_currency_leg2,"
    " price, price_currency, price_unit_of_measure, underlier_id_leg1,"
    " underlier_id_source_leg1, underlying_asset_name, upi, upi_fisn,"
    " upi_underlier_name, source_file, raw_json, ingested_at,"
    " data_source, source_identifier, source_identifier_type,"
    " normalized_instrument_id, instrument_type"
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def open_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)


def copy_schema(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    """Copy everything EXCEPT swap_trades (table + its indexes), in dependency order."""
    rows = src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type IN ('table','index') AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    tables, rest = [], []
    for (sql,) in rows:
        if "swap_trades" in sql.split("(")[0]:
            continue  # deferred until after bulk load
        (tables if sql.startswith("CREATE TABLE") else rest).append(sql)
    for sql in tables + rest:
        dst.execute(sql)
    dst.commit()


def create_swap_table(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    """Create the empty swap_trades table (call before bulk load; indexes come after)."""
    rows = src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type='table' AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    for (sql,) in rows:
        if "swap_trades" in sql.split("(")[0]:
            dst.execute(sql)
    dst.commit()


def create_swap_indexes(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    """Create all swap_trades indexes (call after bulk load)."""
    rows = src.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND type='index' AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    for (sql,) in rows:
        if "swap_trades" in sql.split("(")[0]:
            dst.execute(sql)
    dst.commit()


def extract(src: sqlite3.Connection, start: str, end: str, dest_path: Path) -> int:
    """Copy schema + one date window of rows into a fresh DB via native SQL copy.

    Uses ATTACH (read-only on the source) + INSERT INTO ... SELECT so SQLite
    does the whole move in-process -- no Python row marshalling. Returns count.
    """
    if dest_path.exists():
        dest_path.unlink()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-journal", "-wal", "-shm"):
        p = Path(str(dest_path) + suffix)
        if p.exists():
            os.remove(p)

    # Build clean forward-slash paths so URI parsing is unambiguous (backslashes
    # are escape chars in a SQLite URI). dst opened with uri=True; source attached read-only.
    def _fwd(p):  # C:\a\b -> C:/a/b
        return str(Path(p)).replace("\\", "/")

    dst_uri = "file:" + _fwd(dest_path)
    src_uri = "file:" + _fwd(SOURCE).replace(" ", "%20") + "?mode=ro"

    # Plain connection (no URI parsing); the source is attached by bare path below.
    dst = sqlite3.connect(dst_uri, timeout=60, uri=True)
    try:
        # Bulk-load tuning: no journal, async fsync. Safe -- this is a scratch file.
        dst.execute("PRAGMA journal_mode=OFF;")
        dst.execute("PRAGMA synchronous=OFF;")
        copy_schema(src, dst)  # everything except swap_trades (table + indexes)
        create_swap_table(src, dst)

        # Attach the source read-only (URI mode is on for this connection) and do a native SQL bulk copy.
        dst.execute(f"ATTACH DATABASE '{src_uri}' AS src")
        try:
            t0 = time.time()
            cur = dst.execute(
                f"""INSERT INTO swap_trades SELECT {COLUMNS} FROM src.swap_trades
                    WHERE effective_date >= ? AND effective_date < ?""",
                (start, end),
            )
            n = cur.rowcount if cur.rowcount is not None else -1
            dst.commit()
        finally:
            dst.execute("DETACH DATABASE src")

        # Build indexes once, after all rows are in.
        create_swap_indexes(src, dst)
    finally:
        dst.close()

    if n < 0:
        chk = sqlite3.connect(str(dest_path), timeout=60)
        try:
            n = chk.execute("SELECT COUNT(*) FROM swap_trades").fetchone()[0]
        finally:
            chk.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cutoff", default="2026-01-01", help="effective_date >= cutoff stays in primary")
    ap.add_argument("--dry-run", action="store_true", help="report counts only; write nothing")
    args = ap.parse_args()

    log(f"Source: {SOURCE}")
    log(f"Cutoff: effective_date >= {args.cutoff} -> primary; older -> archives")

    src = open_ro(SOURCE)
    try:
        total = src.execute("SELECT COUNT(*) FROM swap_trades").fetchone()[0]
        recent_n = src.execute(
            "SELECT COUNT(*) FROM swap_trades WHERE effective_date >= ?", (args.cutoff,)
        ).fetchone()[0]
        old_n = total - recent_n
        log(f"Total: {total:,} | primary window: {recent_n:,} | to archive: {old_n:,}")

        if args.dry_run:
            return 0

        # ---------- Phase A: small primary, atomic swap ----------
        STAGE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_primary = Path(PRIMARY_DEST + ".tmp")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(PRIMARY_DEST) + suffix)
            if p.exists():
                os.remove(p)
        if tmp_primary.exists():
            tmp_primary.unlink()

        dst = sqlite3.connect(str(tmp_primary), timeout=60)
        try:
            log("Phase A: copying schema into primary...")
            copy_schema(src, dst)
            create_swap_table(src, dst)
            t0 = time.time()
            cur = src.execute(f"SELECT {COLUMNS} FROM swap_trades WHERE effective_date >= ?", (args.cutoff,))
            n = 0
            while True:
                batch = cur.fetchmany(50_000)
                if not batch:
                    break
                dst.executemany("INSERT INTO swap_trades VALUES (" + ",".join("?" * len(batch[0])) + ")", batch)
                n += len(batch)
            create_swap_indexes(src, dst)  # build indexes once after load
        finally:
            dst.close()

        chk = sqlite3.connect(str(tmp_primary), timeout=60)
        got = chk.execute("SELECT COUNT(*) FROM swap_trades").fetchone()[0]
        fk = len(chk.execute("PRAGMA foreign_key_check;").fetchall())
        chk.close()
        if got != recent_n or fk:
            log(f"Phase A VERIFY FAILED (got {got:,}, want {recent_n:,}; FK violations={fk}); primary untouched")
            return 1
        os.replace(str(tmp_primary), PRIMARY_DEST)
        log(f"Phase A done in {(time.time()-t0)/60:.1f} min: {PRIMARY_DEST} "
            f"({os.path.getsize(PRIMARY_DEST)/1e6:.2f} MB, {got:,} rows)")

        # ---------- Phase B: chunked archives to OneDrive ----------
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        archived = 0
        for label, start, end in CHUNKS:
            if start >= args.cutoff:  # chunk entirely inside primary window
                continue
            raw = STAGE_DIR / f"swaps_{label}.db"
            gz_path = ARCHIVE_DIR / f"swaps_{label}.db.gz"
            t0 = time.time()
            m = extract(src, start, min(end, args.cutoff), raw)
            log(f"[{label}] extracted {m:,} rows in {(time.time()-t0)/60:.1f} min "
                f"(raw {raw.stat().st_size/1e9:.2f} GB)")

            t0 = time.time()
            with open(raw, "rb") as fin, gzip.open(str(gz_path) + ".tmp", "wb", compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout, length=1 << 24)
            os.replace(str(gz_path) + ".tmp", str(gz_path))
            raw.unlink()
            archived += m
            log(f"[{label}] gzipped in {(time.time()-t0)/60:.1f} min -> {gz_path.name} "
                f"({gz_path.stat().st_size/1e9:.2f} GB)")

        if archived != old_n:
            log(f"WARN: archived {archived:,} rows but expected {old_n:,}; check coverage")
            return 1
    finally:
        src.close()

    log("DONE")
    log(f"  primary : {PRIMARY_DEST} ({os.path.getsize(PRIMARY_DEST)/1e6:.2f} MB, {recent_n:,} rows)")
    for f in sorted(ARCHIVE_DIR.glob("*.gz")):
        log(f"  archive : {f.name} ({f.stat().st_size/1e9:.2f} GB gzipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
