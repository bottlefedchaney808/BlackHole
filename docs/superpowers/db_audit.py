#!/usr/bin/env python
"""swaps.db audit — READ-ONLY (mode=ro), safe with the scheduler running.

Runs a sequence of diagnostics against swaps.db and appends human-readable
results to db_audit_20260819.log (also prints to stdout). Steps:
  1. fast PRAGMAs (size, pages, freelist, journal mode)
  2. schema listing (tables, indexes, triggers, views, views size)
  3. per-table row counts (full table scans)
  4. PRAGMA quick_check
  5. PRAGMA integrity_check   (slow on hundreds of GB — last)
  6. per-table size via dbstat (full scan)

Run:  .venv\\Scripts\\python.exe docs/superpowers/db_audit.py
"""
import os
import sqlite3
import sys
import time

DB = r"C:\Users\bottl\FinancialDevelopment\swaps.db"
LOG = r"C:\Users\bottl\FinancialDevelopment\docs\superpowers\db_audit_20260819.log"


def log(msg: str = "") -> None:
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.2f} {unit}"
        n /= 1024
    return f"{n:,.2f} TB"


def main() -> None:
    t0 = time.time()
    log(f"=== swaps.db audit started {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    disk_size = os.path.getsize(DB)
    log(f"disk file size : {human(disk_size)} ({disk_size} bytes)")
    for extra in ("swaps.db-wal", "swaps.db-shm", "swaps.db-journal"):
        p = os.path.join(os.path.dirname(DB), os.path.basename(extra))
        if os.path.exists(p):
            log(f"sidecar      : {extra} = {human(os.path.getsize(p))}")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    cur = con.cursor()

    # ---- 1. fast PRAGMAs ----
    log("\n--- PRAGMAs ---")
    for pragma in (
        "page_size", "page_count", "freelist_count", "journal_mode",
        "auto_vacuum", "synchronous", "user_version", "application_id",
        "cache_size", "page_size", "wal_autocheckpoint",
    ):
        cur.execute(f"PRAGMA {pragma}")
        val = cur.fetchone()
        log(f"{pragma:20s} = {val[0]}")

    page_size, page_count, freelist = (
        cur.execute("PRAGMA page_size").fetchone()[0],
        cur.execute("PRAGMA page_count").fetchone()[0],
        cur.execute("PRAGMA freelist_count").fetchone()[0],
    )
    log(f"usable size    = {human(page_size * page_count)}  (page_size * page_count)")
    log(f"free (deleted) = {human(page_size * freelist)} ({100*freelist/max(1,page_count):.1f}% of pages)")

    # ---- 2. schema ----
    log("\n--- schema objects ---")
    for kind in ("table", "index", "view", "trigger"):
        rows = cur.execute(
            "SELECT name, COALESCE(sql,'') FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%' ORDER BY name",
            (kind,),
        ).fetchall()
        log(f"{kind} x{len(rows)}")
        if kind == "table":
            for name, _sql in rows:
                log(f"  table: {name}")

    # ---- 3. row counts ----
    log("\n--- row counts (full scan) ---")
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        ts = time.time()
        n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        log(f"  {t:45s} {n:>15,} rows  ({time.time()-ts:.1f}s)")

    # ---- 4. quick_check ----
    log("\n--- PRAGMA quick_check ---")
    ts = time.time()
    rows = cur.execute("PRAGMA quick_check").fetchall()
    log(f"  result: {[r[0] for r in rows]}  ({time.time()-ts:.1f}s)")

    # ---- 5. integrity_check (slow) ----
    log("\n--- PRAGMA integrity_check (may take a long time) ---")
    ts = time.time()
    rows = cur.execute("PRAGMA integrity_check").fetchall()
    log(f"  result: {[r[0] for r in rows]}  ({time.time()-ts:.1f}s)")

    con.close()
    log(f"\n=== audit finished in {time.time()-t0:.0f}s, {time.strftime('%Y-%m-%d %H:%M:%S')} ===")


if __name__ == "__main__":
    # fresh log for this run
    if os.path.exists(LOG) and time.time() - os.path.getmtime(LOG) < 14400:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\nRESTART {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n")
    else:
        open(LOG, "w").close()
    try:
        main()
    except Exception as e:  # noqa: BLE001 - report any failure
        log(f"FATAL: {type(e).__name__}: {e}")
        sys.exit(1)
