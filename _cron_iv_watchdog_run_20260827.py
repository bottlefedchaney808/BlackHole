"""IV alert watchdog - cron run. Watchlist from latest manifest pack.
For each ticker: current ATM IV vs 20-day ATM IV history (percentile) from
option_bulk_hist_eod_greeks. Flags: >90th pct ALERT, <10th pct NOTIFY,
or IV > 1.5x 20d mean IV. Every call wrapped in try/except."""
import json, sys, time
from datetime import datetime, timedelta
sys.path.insert(0, '.')
from shared.thetadata import ThetaDataController

MANIFEST = "sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json"
DEFAULT = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU"]

def load_watchlist():
    try:
        with open(MANIFEST) as f:
            m = json.load(f)
        packs = m.get("packs", [])
        # newest pack first; watchlist = today's latest pack only
        for p in packs:
            tk = p.get("tickers", [])
            if tk:
                return tk
    except Exception as e:
        print(f"WARN manifest: {e}")
    return DEFAULT

def nearest_expiry(td, ticker, target_days=30):
    exps = td.list_expirations(ticker)
    if not exps:
        return None
    target = datetime.now() + timedelta(days=target_days)
    best = None; best_d = 1e9
    for e in exps:
        try:
            d = abs((datetime.strptime(e, "%Y%m%d") - target).days)
        except Exception:
            continue
        if d < best_d:
            best_d = d; best = e
    return best

def atm_iv_live(td, ticker, exp, spot):
    rows = td.option_bulk_greeks(ticker, exp)
    window = max(5.0, float(spot) * 0.02)
    best = None
    for r in rows or []:
        k = float(r.get('strike', 0)) / 1000.0
        if abs(k - float(spot)) > window:
            continue
        iv = r.get('implied_vol')
        if iv and float(iv) > 0:
            d = abs(k - float(spot))
            if best is None or d < best[0]:
                best = (d, float(iv))
    return best[1] * 100.0 if best else None

def atm_iv_history(td, ticker, exp, spot, days=20):
    """Per-trading-day ATM IV over trailing window, one request."""
    end = datetime.now()
    start = end - timedelta(days=int(days * 1.8) + 8)  # cover trading days
    rows = td.option_bulk_hist_eod_greeks(
        ticker, exp, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    window = max(5.0, float(spot) * 0.02)
    by_day = {}
    for r in rows or []:
        if r.get("right") != "C":
            continue
        iv = r.get("implied_vol")
        if not iv or float(iv) <= 0:
            continue
        k = float(r.get("strike", 0)) / 1000.0
        if abs(k - float(spot)) > window:
            continue
        d = str(r.get("date"))
        best = by_day.get(d)
        if best is None or abs(k - float(spot)) < best[0]:
            by_day[d] = (abs(k - float(spot)), float(iv))
    vals = sorted(v[1] for v in by_day.values())
    return vals[-days:]

def pct_rank(v, hist):
    if not hist:
        return None
    below = sum(1 for h in hist if h <= v)
    return below / len(hist) * 100.0

def main():
    tickers = load_watchlist()
    td = ThetaDataController()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for t in tickers:
        try:
            spot = td.fetch_spot_price(t)
            if not spot:
                out.append({"ticker": t, "skip": "no spot"})
                continue
            exp = nearest_expiry(td, t)
            if not exp:
                out.append({"ticker": t, "skip": "no options"})
                continue
            cur = atm_iv_live(td, t, exp, spot)
            if cur is None:
                out.append({"ticker": t, "skip": "no live ATM IV"})
                continue
            hist = atm_iv_history(td, t, exp, spot)
            avg = sum(hist) / len(hist) if hist else None
            pct = pct_rank(cur, hist) if hist else None
            rec = {"ticker": t, "spot": round(float(spot), 2), "iv": round(cur, 1),
                   "exp": exp, "iv_20d_avg": round(avg, 1) if avg else None,
                   "pctile": round(pct, 0) if pct is not None else None,
                   "n_hist": len(hist)}
            flags = []
            if pct is not None and pct > 90:
                flags.append("ALERT")
            if pct is not None and pct < 10:
                flags.append("NOTIFY")
            if avg and cur > 1.5 * avg:
                flags.append("ALERT")
            rec["flags"] = flags
            out.append(rec)
            print(f"{t:6s} spot={rec['spot']:>9.2f} IV={rec['iv']:>6.1f}% "
                  f"20d_avg={rec['iv_20d_avg']}% pct={rec['pctile']}% n={rec['n_hist']} "
                  f"{flags or 'ok'}")
        except Exception as e:
            out.append({"ticker": t, "error": str(e)[:80]})
            print(f"{t:6s} ERROR: {str(e)[:80]}")
        time.sleep(0.4)
    td.close()

    alerts = [r for r in out if r.get("flags")]
    with open("artifacts/iv_watchdog_results.json", "w") as f:
        json.dump({"timestamp": now, "watchlist": tickers, "results": out}, f, indent=2)

    print("\n=== SUMMARY ===")
    if alerts:
        print(f"IV ALERT — {now}")
        for r in alerts:
            print(f"  {r['ticker']}: IV={r['iv']}% (vs {r['iv_20d_avg']}% 20d avg) "
                  f"— {r['pctile']}th percentile  {r['flags']}")
    else:
        print(f"IV check: all clear — {now}")

if __name__ == "__main__":
    main()
