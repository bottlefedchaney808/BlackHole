import sys, os, json, datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
from shared.thetadata import ThetaDataController
from Options_Suite.NewtonRaphsonIV import implied_volatility_nr

TICKERS = ["MBLY", "INTU", "TSLA", "DKS"]
MIN_DTE = 21
LOOKBACK_TRADING_DAYS = 20
CAL_SLACK = 40
STRK_FACTORS = [0.85, 0.92, 1.0, 1.08, 1.15]
PARITY_TOL = 0.08   # quoted spot vs put-call-parity spot divergence that marks spot suspect

def pct_rank(val, series):
    if not series:
        return None
    return 100.0 * sum(1 for x in series if x <= val) / len(series)

def nearest_strike(strikes, target):
    return min(strikes, key=lambda k: abs(k - target))

def iso_date(created):
    if not created:
        return None
    s = str(created)
    return s[:10].replace("-", "") if len(s) >= 10 else None

def day_series(rows):
    out = {}
    for r in rows:
        d = iso_date(r.get("created"))
        if not d:
            continue
        v = None
        c = r.get("close")
        if c:
            try:
                v = float(c)
            except (ValueError, TypeError):
                v = None
        if not v or v <= 0:
            try:
                b, a = float(r.get("bid")), float(r.get("ask"))
                if b > 0 and a > 0:
                    v = (b + a) / 2.0
            except (ValueError, TypeError):
                v = None
        if v and v > 0 and d not in out:
            out[d] = v
    return out

def mid(row):
    try:
        b, a = float(row.get("bid")), float(row.get("ask"))
        if b > 0 and a > 0:
            return (b + a) / 2.0
    except (ValueError, TypeError):
        pass
    return None

def invert_iv(px, S, K, T, r, cp):
    """Invert one quote to an IV, or None if the price cannot identify one.

    The `converged` flag is honoured, not discarded. A wing strike whose
    price is saturated (far enough OTM/ITM that a whole range of vols
    reproduces it to within the solver's tolerance) comes back
    converged=False with a plausible-LOOKING number attached -- and these
    IVs are averaged into avg20, which is what the percentile rank below is
    computed against. Letting one through does not look like an error, it
    looks like a slightly different IV rank. Dropping the point leaves it
    out of the average instead, which every call site already handles: they
    all guard on `if iv:`, and a ticker with no usable ATM row reports
    "no ATM IV row" rather than inventing one.
    """
    try:
        iv, converged = implied_volatility_nr(px, S, K, T, r, cp=cp, q=0.0,
                                              tol=1e-5, max_iterations=200, seed=0.4)
        if not converged:
            return None
        return iv if iv and 0.03 < iv < 3.0 else None
    except Exception:
        return None

def main():
    td = ThetaDataController()
    results = []
    today = datetime.date.today()
    end_dt = today.strftime("%Y%m%d")
    start_dt = (today - datetime.timedelta(days=CAL_SLACK)).strftime("%Y%m%d")

    for t in TICKERS:
        rec = {"ticker": t}
        try:
            spot = td.fetch_spot_price(t)
            if not spot or spot <= 0:
                rec["error"] = f"no usable spot ({spot})"
                results.append(rec); print(json.dumps(rec), flush=True); continue
            rec["spot_quoted"] = round(spot, 2)

            exps = td.list_expirations(t)
            if not exps:
                rec["error"] = "no expirations"
                results.append(rec); print(json.dumps(rec), flush=True); continue
            target = today + datetime.timedelta(days=MIN_DTE)
            parsed = [(datetime.datetime.strptime(e, "%Y%m%d").date(), e) for e in exps]
            cands = [p for p in parsed if p[0] >= target] or parsed
            exp_d, exp = min(cands, key=lambda p: abs((p[0] - target).days))
            rec["exp"] = exp

            eods = td.hist_stock_eod(t, start_dt, end_dt) or []
            closes = day_series(eods)
            if closes:
                last_d = max(closes.keys())
                rec["last_eod_close"] = round(closes[last_d], 2)
                rec["last_eod_date"] = last_d

            chain = td.option_bulk_greeks(t, exp) or []
            strikes = sorted({float(r.get("strike", 0)) / 1000.0 for r in chain if r.get("strike")})
            if not strikes:
                rec["error"] = "empty chain"
                results.append(rec); print(json.dumps(rec), flush=True); continue

            # ---- put-call parity spot check at strike nearest quoted spot ----
            k0 = nearest_strike(strikes, spot)
            c_rows = [r for r in chain if abs(float(r.get("strike", 0)) / 1000.0 - k0) < 1e-9 and str(r.get("right", ""))[:1].upper() == "C"]
            p_rows = [r for r in chain if abs(float(r.get("strike", 0)) / 1000.0 - k0) < 1e-9 and str(r.get("right", ""))[:1].upper() == "P"]
            c_m = mid(c_rows[0]) if c_rows else None
            p_m = mid(p_rows[0]) if p_rows else None
            T_y = (exp_d - today).days / 365.0
            try:
                r = float(td.fetch_risk_free_rate(T=T_y)) if td.fetch_risk_free_rate(T=T_y) else 0.04
            except Exception:
                r = 0.04
            parity = None
            if c_m and p_m:
                parity = c_m - p_m + k0 * 2.718281828 ** (-r * T_y)
                rec["parity_spot"] = round(parity, 2)
            if parity and abs(parity - spot) / spot > PARITY_TOL:
                rec["spot_suspect"] = True
                ref = parity   # options price off parity level, not the quoted spot
                rec["ref"] = round(ref, 2)
            else:
                rec["spot_suspect"] = False
                ref = spot
                rec["ref"] = round(ref, 2)

            atm_k = nearest_strike(strikes, ref)
            rec["atm_strike"] = atm_k
            atm_c_rows = [r for r in chain if abs(float(r.get("strike", 0)) / 1000.0 - atm_k) < 1e-9 and str(r.get("right", ""))[:1].upper() == "C"]
            atm_p_rows = [r for r in chain if abs(float(r.get("strike", 0)) / 1000.0 - atm_k) < 1e-9 and str(r.get("right", ""))[:1].upper() == "P"]

            # current ATM IV: min-side of call/put IV, inverted at ref level
            cur_ivs = []
            if atm_c_rows:
                pm = mid(atm_c_rows[0])
                if pm:
                    iv = invert_iv(pm, ref, atm_k, T_y, r, cp=True)
                    if iv:
                        cur_ivs.append(iv); rec["atm_c_iv"] = round(iv * 100, 2)
            if atm_p_rows:
                pm = mid(atm_p_rows[0])
                if pm:
                    iv = invert_iv(pm, ref, atm_k, T_y, r, cp=False)
                    if iv:
                        cur_ivs.append(iv); rec["atm_p_iv"] = round(iv * 100, 2)
            if not cur_ivs:
                rec["error"] = "no ATM IV row"
                results.append(rec); print(json.dumps(rec), flush=True); continue
            # current ATM IV: average of C/P unless one side is an extreme
            # outlier (>1.5x divergence -> stale side), then take the min.
            if len(cur_ivs) >= 2:
                hi, lo = max(cur_ivs), min(cur_ivs)
                cur_iv = min(cur_ivs) if (lo > 0 and hi / lo > 1.5) else sum(cur_ivs) / len(cur_ivs)
            else:
                cur_iv = cur_ivs[0]
            rec["cur_iv"] = round(cur_iv * 100, 2)

            brack = sorted({nearest_strike(strikes, ref * f) for f in STRK_FACTORS})
            rec["bracket"] = brack

            opt = {"C": {}, "P": {}}
            for k in brack:
                for right in ("C", "P"):
                    rows = td.option_hist_eod_single(t, exp, int(round(k * 1000)), right, start_dt, end_dt) or []
                    opt[right][k] = day_series(rows)

            daily_ivs = []
            for d in sorted(closes.keys()):
                if d > end_dt:
                    continue
                S = closes[d]
                k = nearest_strike(brack, S)
                T = (exp_d - datetime.datetime.strptime(d, "%Y%m%d").date()).days / 365.0
                if T <= 0.02:
                    continue
                ivs = []
                for right in ("C", "P"):
                    px = opt[right].get(k, {}).get(d)
                    if px:
                        iv = invert_iv(px, S, k, T, r, cp=(right == "C"))
                        if iv:
                            ivs.append(iv)
                if ivs:
                    daily_ivs.append(min(ivs) if len(ivs) > 1 else ivs[0])
                if len(daily_ivs) >= LOOKBACK_TRADING_DAYS:
                    break

            if len(daily_ivs) >= 10:
                avg20 = sum(daily_ivs) / len(daily_ivs)
                rec["avg20"] = round(avg20 * 100, 2)
                rec["n_days"] = len(daily_ivs)
                rec["ratio"] = round(cur_iv / avg20, 3)
                rec["pct"] = round(pct_rank(cur_iv, daily_ivs), 1)
            else:
                rec["avg20"] = None
                rec["n_days"] = len(daily_ivs)
                rec["ratio"] = None
                rec["pct"] = None
                rec["error"] = f"only {len(daily_ivs)} days of IV history"
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        results.append(rec)
        print(json.dumps(rec), flush=True)
    td.close()

    print("=== SUMMARY ===", flush=True)
    alerts, notifies, suspect = [], [], []
    for rec in results:
        t = rec["ticker"]
        if rec.get("error"):
            print(f"{t}: ERROR {rec['error']}", flush=True)
            continue
        if rec.get("ratio") is None:
            print(f"{t}: spot={rec.get('ref')} cur_iv={rec.get('cur_iv')}% avg20=n/a ({rec.get('n_days')}d)", flush=True)
            continue
        sus = " SPOT-SUSPECT" if rec.get("spot_suspect") else ""
        print(f"{t}: spot={rec.get('ref')} (quoted {rec.get('spot_quoted')}, parity {rec.get('parity_spot')}){sus} "
              f"exp={rec.get('exp')} cur_iv={rec.get('cur_iv')}% [C={rec.get('atm_c_iv')} P={rec.get('atm_p_iv')}] "
              f"avg20={rec.get('avg20')}% ratio={rec.get('ratio')} pct={rec.get('pct')}th ({rec.get('n_days')}d)", flush=True)
        if rec.get("spot_suspect"):
            suspect.append(rec)
            continue
        if rec["ratio"] > 1.5 or rec["pct"] >= 90.0:
            alerts.append(rec)
        elif rec["pct"] <= 10.0:
            notifies.append(rec)
    print("=== ALERTS ===", flush=True)
    for r in alerts:
        print(json.dumps(r), flush=True)
    print("=== NOTIFIES ===", flush=True)
    for r in notifies:
        print(json.dumps(r), flush=True)
    print("=== SPOT-SUSPECT ===", flush=True)
    for r in suspect:
        print(json.dumps(r), flush=True)
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
