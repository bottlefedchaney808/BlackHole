"""Read-only duplicate/dedup analysis for swaps.db.

Answers the key question for the compression plan:
are the 70.9M rows in swap_trades legitimate full history, or are they
duplicate ingestion (slice + cumulative overlap, double backfill)?
"""
import sqlite3
from collections import Counter

DB = r"C:\Users\bottl\FinancialDevelopment\swaps.db"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()

print("=== swap_trades column list ===")
cols = [r[1] for r in cur.execute("PRAGMA table_info(swap_trades)")]
print(cols)

date_col = None
for cand in ("trade_date", "trade_dt", "dt", "date", "as_of_date", "report_date"):
    if cand in cols:
        date_col = cand
        break
print(f"\ndate_col = {date_col}")

if date_col:
    lo, hi = cur.execute(f"SELECT MIN({date_col}), MAX({date_col}) FROM swap_trades").fetchone()
    print(f"range   = {lo} .. {hi}")
    print("\n=== rows per year ===")
    for y, n in cur.execute(
        f"SELECT substr({date_col},1,4) y, COUNT(*) FROM swap_trades GROUP BY y ORDER BY y"
    ):
        print(f"  {y}  {n:>13,}")

    src_col = "data_source" if "data_source" in cols else None
    if src_col:
        print("\n=== rows by data_source ===")
        for v, n in cur.execute(f"SELECT {src_col}, COUNT(*) FROM swap_trades GROUP BY {src_col}"):
            print(f"  {v}  {n:>13,}")

# Natural key: the upsert keys we know from db_loader (SEC, trade_date, UPI, notional...)
# Use the whole identifying column set where present.
key_cols = [c for c in ("sec", "trade_date", "product_code", "upi", "notional") if c in cols]
print(f"\n=== dup check ===\nkey cols: {key_cols}")
if len(key_cols) >= 3:
    kexp = ", ".join(key_cols)
    total, distinct = cur.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT {kexp}) FROM swap_trades"
    ).fetchone()
    print(f"total  = {total:,}")
    print(f"distinct(key) = {distinct:,}")
    print(f"possible dup rows = {total - distinct:,} ({(total - distinct) / total * 100:.1f}%)")
    if total > distinct:
        print("\ntop 10 most-duplicated keys:")
        for key, n in cur.execute(
            f"SELECT {kexp}, COUNT(*) n FROM swap_trades "
            f"GROUP BY {kexp} HAVING n > 1 ORDER BY n DESC, {kexp} LIMIT 10"
        ):
            print(f"  x{n:>4}  {key}")

con.close()
print("\nDONE")
