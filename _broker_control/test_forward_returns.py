"""Forward-return accuracy test for the convention-free broker-book control nets.

AGGREGATION NOTE (2026-08-16): ongoing -- re-run after build_broker_book.py picks up new
chain scans so the corpus gains independent (ticker, day) units. Current verdict is an
exploratory NULL but UNDER-POWERED (~18 unique dates); do not trust any r until unique-date
effective-n is large (hundreds). See _broker_control/README.md.

For each chain-scan snapshot (ticker, expiry, scan date) we fetch daily EOD closes from
ThetaData and compute forward returns from the scan-date close (ref) to +1/+2/+5 trading days.
Then we measure how well each control net predicts the forward move (Pearson r) and how
sign-consistent it is.  This is an ASSOCIATIONAL, daily-frequency, low-power exploratory
check -- NOT a pre-registered falsifier.  Report nominal n vs unique (ticker, date) clusters
and per-ticker sign consistency before any interpretation.
"""
import sys
import datetime as dt
from collections import defaultdict

import pandas as pd

sys.path.insert(0, r"C:/Users/bottl/FinancialDevelopment")
from shared.thetadata import ThetaDataController  # noqa: E402

CONTROL = r"C:/Users/bottl/FinancialDevelopment/_broker_control/broker_book_control.csv"
NETS = ["net_delta", "net_vanna", "net_charm", "net_gamma_gex"]


def to_date(ts: str):
    return dt.datetime.strptime(ts, "%Y-%m-%d %H:%M").date()


def main():
    ctl = pd.read_csv(CONTROL)
    ctl["date"] = ctl["scan_dt"].map(to_date)

    tickers = sorted(ctl["ticker"].unique())
    print(f"{len(ctl)} snapshots, {len(tickers)} tickers, dates {ctl['date'].min()}..{ctl['date'].max()}")

    start = (ctl["date"].min() - dt.timedelta(days=2)).strftime("%Y%m%d")
    end = dt.date.today().strftime("%Y%m%d")  # cap at today: no forward data exists past now

    td = ThetaDataController()
    closes = {}
    for t in tickers:
        try:
            rows = td.hist_stock_eod(t, start, end)
            s = {}
            for r in rows:
                dv = r.get("created") or r.get("last_trade") or r.get("date")
                if not dv:
                    continue
                d = pd.to_datetime(str(dv)).date()
                v = r.get("close")
                if v is not None and float(v) > 0:
                    s[d] = float(v)
            closes[t] = s
            print(f"  {t}: {len(s)} EOD closes ({min(s) if s else '-'}..{max(s) if s else '-'})")
        except Exception as e:  # noqa: BLE001
            print(f"  {t}: ERROR {type(e).__name__}: {e}")
            closes[t] = {}
    td.close()

    # per-row forward returns
    rows = []
    for _, r in ctl.iterrows():
        t, d = r["ticker"], r["date"]
        s = closes.get(t, {})
        dates = sorted(s.keys())
        rec = {"ticker": t, "expiry": r["expiry"], "date": d}
        for n in NETS:
            rec[n] = r[n]
        if d in s:
            ref_idx = dates.index(d)
            ref = s[d]
            for h in (1, 2, 5):
                f = dates[ref_idx + h] if ref_idx + h < len(dates) else None
                rec[f"fwd_{h}"] = (s[f] / ref - 1) if f else None
        rows.append(rec)
    df = pd.DataFrame(rows)

    out_path = r"C:/Users/bottl/FinancialDevelopment/_broker_control/forward_return_test.csv"
    df.to_csv(out_path, index=False)

    print("\n=== Pooled Pearson r(net, forward return) ===")
    for h in (1, 2, 5):
        for n in NETS:
            sub = df[[n, f"fwd_{h}"]].dropna()
            if len(sub) < 4:
                print(f"  fwd_{h}d {n:16s} n={len(sub):3d}  (too few)")
                continue
            r = sub[n].corr(sub[f"fwd_{h}"])
            print(f"  fwd_{h}d {n:16s} n={len(sub):3d}  r={r:+.3f}")

    # unique (ticker,date) clusters and per-ticker sign consistency
    print("\n=== Independence ===")
    print(f"  nominal snapshots: {len(df)}")
    print(f"  unique (ticker,date): {df[['ticker','date']].drop_duplicates().shape[0]}")
    print(f"  unique dates: {df['date'].nunique()}")

    print("\n=== Per-ticker fwd-1d sign of net_delta vs sign(fwd) agreement ===")
    sub = df[["ticker", "net_delta", "fwd_1"]].dropna()
    per = {}
    for t, g in sub.groupby("ticker"):
        agree = ((g["net_delta"] > 0) == (g["fwd_1"] > 0)).sum()
        per[t] = (agree, len(g))
    for t in sorted(per):
        a, n = per[t]
        print(f"  {t:6s} agree {a}/{n} ({100*a/n:5.1f}%)")


if __name__ == "__main__":
    sys.exit(main())
