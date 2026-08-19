
import sqlite3
DB = r"C:\Users\bottl\FinancialDevelopment\swaps.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
total, distinct_id = cur.execute(
    "SELECT COUNT(*), COUNT(DISTINCT dissemination_id) FROM swap_trades").fetchone()
print(f"total                 = {total:,}")
print(f"distinct dissem_id    = {distinct_id:,}")
print(f"duplicate rows        = {total - distinct_id:,} ({(total - distinct_id)/total*100:.2f}%)")
print()
print("=== rows by year (effective_date) ===")
for y, n in cur.execute(
    "SELECT substr(effective_date,1,4) y, COUNT(*) FROM swap_trades GROUP BY y ORDER BY y"):
    print(f"  {y}  {n:>13,}")
print()
print("=== range ===")
lo, hi = cur.execute(
    "SELECT MIN(effective_date), MAX(effective_date) FROM swap_trades").fetchone()
print(f"  {lo} .. {hi}")
print()
print("=== data_source ===")
for v, n in cur.execute(
    "SELECT data_source, COUNT(*) FROM swap_trades GROUP BY data_source"):
    print(f"  {v}  {n:>13,}")
print()
print("=== top duplicated dissemination_id ===")
for did, n in cur.execute(
    "SELECT dissemination_id, COUNT(*) n FROM swap_trades "
    "GROUP BY dissemination_id HAVING n > 1 ORDER BY n DESC LIMIT 5"):
    print(f"  x{n:>5}  {did}")
con.close()
