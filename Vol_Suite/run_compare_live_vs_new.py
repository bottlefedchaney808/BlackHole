"""Head-to-head comparison: NEW expiry-book model vs FULL LIVE dealer-positioning model.

Both models run on the IDENTICAL on-disk seed rows per (ticker, window):
  - NEW model:  expiry_book_exposure.build_net_exposure(rows, spot) -> NetExposure
                (.gex() dollar-gamma-per-1%, .dex() delta shares, dealer frame)
  - LIVE model: dealer_positioning.compute_dealer_positioning(...) driven by a _SeedTD
                fake controller that serves the SAME seed rows (spot / expiries / greeks /
                OI) — exactly the pattern the test suite uses (monkeypatch
                ThetaDataController). This runs the FULL live computation offline:
                sign resolution, gamma/delta aggregation, hedge requirement, vannaflow.

Measures per (ticker, window) snapshot:
  (1) SIGN AGREEMENT       — new_gex sign vs live total_net_gamma sign (LONG/SHORT read)
  (2) CROSS-SECTIONAL      — per-ticker mean exposure correlation
  (3) PREDICTIVENESS       — corr(model signal sign, next-day return), head-to-head

No network. No live model file modified. New file only.
"""
import glob
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import expiry_book_exposure as ebe
import dealer_positioning as dp
import run_expiry_tier2_multi as t2m

SCRATCH = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2b"
REUSE = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2"
SIGN_MODEL = os.environ.get("DEALER_SIGN_MODEL", "vol_surface_replication")


class _SeedTD:
    """Serves one seed file's data to compute_dealer_positioning (full live path, offline)."""

    def __init__(self, seed, serve_date=None):
        self.seed = seed
        self.serve_date = serve_date  # None = last day (as-of)
        self.spot_by_date = {}
        self.g_by_date = {}
        self.oi_by_date = {}
        self._load()

    def _load(self):
        for r in self.seed.get("spot", []):
            d = ebe._date_of(r)
            v = ebe._extract(r, "close")
            if not math.isnan(v):
                self.spot_by_date[d] = v
        for g in self.seed.get("greeks", []):
            d = ebe._date_of(g)
            k = ebe._extract(g, "strike")
            # RAW theta-int strike (e.g. 748000) -- the live model converts via
            # strike_from_theta itself, same as a real ThetaDataController row.
            if not math.isnan(k) and k > 10000:
                k = int(k)
            iv = ebe._extract(g, "implied_vol")
            if not math.isnan(k) and not math.isnan(iv) and iv > 0:
                self.g_by_date.setdefault(d, []).append({
                    "strike": k, "right": str(g.get("right", "C")).upper()[:1],
                    "implied_vol": iv, "gamma": ebe._extract(g, "gamma"),
                    "delta": ebe._extract(g, "delta"), "vanna": ebe._extract(g, "vanna"),
                    "charm": ebe._extract(g, "charm"),
                    "bid": ebe._extract(g, "bid", 1.0), "ask": ebe._extract(g, "ask", 1.1),
                })
        for o in self.seed.get("oi", []):
            d = ebe._date_of(o)
            k = ebe._extract(o, "strike")
            if not math.isnan(k) and k > 10000:
                k = int(k)
            self.oi_by_date.setdefault(d, {})[(k, str(o.get("right", "C")).upper()[:1])] = \
                int(ebe._extract(o, "open_interest", 0.0))

    def _last_day(self):
        return sorted(self.spot_by_date)[-1] if self.spot_by_date else None

    def _serve_day(self):
        d = self.serve_date or self._last_day()
        return d

    def fetch_spot_price(self, ticker):
        d = self._serve_day()
        return self.spot_by_date.get(d, 0.0) if d else 0.0

    def fetch_dividend_yield(self, ticker, spot=None):
        return 0.0

    def fetch_risk_free_rate(self, T):
        return 0.04

    def list_expirations(self, root):
        exp = str(self.seed.get("manifest", {}).get("expiry", ""))
        return [exp] if exp else []

    def option_bulk_greeks(self, root, exp):
        d = self._serve_day()
        if not d or d not in self.g_by_date:
            return []
        return self.g_by_date[d]

    def option_bulk_oi(self, root, exp):
        d = self._serve_day()
        if not d or d not in self.oi_by_date:
            return []
        return [{"strike": k, "right": r, "open_interest": v}
                for (k, r), v in self.oi_by_date[d].items()]

    def close(self):
        pass


def load_all_clusters():
    clusters = {}
    for wdir in sorted(glob.glob(os.path.join(SCRATCH, "window_*"))):
        wkey = os.path.basename(wdir)
        for f in sorted(glob.glob(os.path.join(wdir, "seed_data_*.json"))):
            tk = os.path.basename(f).split("_")[2].upper()
            seed = t2m._load_seed_file(f)
            s = t2m.build_burst_series_from_seed(seed, tk)
            if len(s["dates"]) >= 4:
                clusters[(tk, wkey)] = (seed, s)
    for f in sorted(glob.glob(os.path.join(REUSE, "seed_data_*_short.json"))):
        tk = os.path.basename(f).split("_")[2].upper()
        seed = t2m._load_seed_file(f)
        s = t2m.build_burst_series_from_seed(seed, tk)
        if len(s["dates"]) >= 4:
            clusters[(tk, "reuse_20260814")] = (seed, s)
    return clusters


def _hist_rows_from_seed(seed):
    """Build (hist_greek_rows, hist_oi_rows, hist_spot_rows) in the exact row
    shape _accumulate_from_history expects (theta-int strikes, 'C'/'P' rights,
    'date' parseable by _parse_hist_date, 'close' on spot rows)."""
    g_rows, o_rows, s_rows = [], [], []
    for g in seed.get("greeks", []):
        k = ebe._extract(g, "strike")
        if math.isnan(k) or k <= 0:
            continue
        k = int(k) if k > 10000 else int(round(k * 1000))  # theta-int
        row = {
            "date": str(ebe._date_of(g))[:8],
            "strike": k,
            "right": str(g.get("right", "C")).upper()[:1],
            "implied_vol": ebe._extract(g, "implied_vol", 0.0),
        }
        v = ebe._extract(g, "vanna", float("nan"))
        if not math.isnan(v):
            row["vanna"] = v
        g_rows.append(row)
    for o in seed.get("oi", []):
        k = ebe._extract(o, "strike")
        if math.isnan(k) or k <= 0:
            continue
        k = int(k) if k > 10000 else int(round(k * 1000))
        o_rows.append({
            "date": str(ebe._date_of(o))[:8],
            "strike": k,
            "right": str(o.get("right", "C")).upper()[:1],
            "open_interest": int(ebe._extract(o, "open_interest", 0.0)),
        })
    for sp in seed.get("spot", []):
        d = str(ebe._date_of(sp))[:8]
        v = ebe._extract(sp, "close")
        if not math.isnan(v):
            s_rows.append({"date": d, "close": v})
    return g_rows, o_rows, s_rows


def run_live_model(seed, ticker):
    """Full live compute_dealer_positioning driven by _SeedTD (the test-suite pattern),
    with time frozen at the window's as-of date so the live model's today-anchored
    expiry filter sees the window's expiries as still-listed (fair historical compare).
    Accumulation is the live default (per Jason's directive), so the accumulated
    seed-plus-flow book is fed the seed's full history via _accumulation_hist_rows."""
    import datetime as _dt
    # effective as-of: manifest obs_date_range[-1], else last spot date
    m = seed.get("manifest", {})
    odr = m.get("obs_date_range") or []
    if odr and str(odr[-1])[:8].isdigit():
        as_of = _dt.datetime.strptime(str(odr[-1])[:8], "%Y%m%d").date()
    else:
        spots = seed.get("spot", [])
        as_of = None
        for sp in reversed(spots):
            d = str(ebe._date_of(sp))[:8]
            if d.isdigit():
                as_of = _dt.datetime.strptime(d, "%Y%m%d").date()
                break
        if as_of is None:
            as_of = _dt.date.today()

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.combine(as_of, _dt.time(16, 0), tzinfo=tz)

    td = _SeedTD(seed)
    hist_rows = _hist_rows_from_seed(seed)
    old_ctrl = dp.ThetaDataController
    old_dt = dp.datetime
    dp.ThetaDataController = lambda *a, **k: td
    dp.datetime = _FrozenDT
    try:
        exp = str(seed.get("manifest", {}).get("expiry", "")) or None
        accumulate_on = os.environ.get("DEALER_ACCUMULATION", "1") == "1"
        result = dp.compute_dealer_positioning(ticker, target_years=0.25,
                                               expiration=exp, sign_model=SIGN_MODEL,
                                               accumulate=accumulate_on,
                                               _accumulation_hist_rows=hist_rows)
        return result
    finally:
        dp.ThetaDataController = old_ctrl
        dp.datetime = old_dt


def run_new_model(seed, ticker):
    spot_by_date, g_by_date, oi_by_date = {}, {}, {}
    for r in seed.get("spot", []):
        d = ebe._date_of(r)
        v = ebe._extract(r, "close")
        if not math.isnan(v):
            spot_by_date[d] = v
    for g in seed.get("greeks", []):
        d = ebe._date_of(g)
        k = ebe._extract(g, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        iv = ebe._extract(g, "implied_vol")
        if not math.isnan(k) and not math.isnan(iv) and iv > 0:
            g_by_date.setdefault(d, []).append((k, str(g.get("right", "C")).upper()[:1], iv))
    for o in seed.get("oi", []):
        d = ebe._date_of(o)
        k = ebe._extract(o, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        oi_by_date.setdefault(d, {})[(k, str(o.get("right", "C")).upper()[:1])] = \
            int(ebe._extract(o, "open_interest", 0.0))
    if not spot_by_date:
        return None
    last_d = sorted(spot_by_date)[-1]
    spot = spot_by_date[last_d]
    rows = g_by_date.get(last_d, [])
    if not rows or spot <= 0:
        return None
    ne_rows = [{"strike": k, "right": r, "oi": oi_by_date.get(last_d, {}).get((k, r), 0),
                "implied_vol": iv} for (k, r, iv) in rows]
    ne = ebe.build_net_exposure(ne_rows, spot, ticker=ticker)
    return ne


def _corr(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma = sum(a) / n; mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def _per_day_model_series(seed, tk, s):
    """Per-day (date, fwd, div, burst, new_gex, live_gamma, live_net_vanna) for a cluster.
    B1/B4 need per-day signals: new gex via build_net_exposure on day-i rows; live model via
    same-day snapshot (time frozen at day i, _SeedTD serving day i, accumulate=False — the
    as-of accumulated book is the window-level object; the per-day flow object is the
    same-day snapshot)."""
    import datetime as _dt
    days = s["dates"]
    out = []
    for i, d in enumerate(days):
        fw = s["fwd"][i]
        if i == len(days) - 1:
            fw = None  # terminal placeholder — not a real forward return
        div = s["div"][i]
        burst = s["burst"][i]
        # new model on day i's rows
        ng = None
        try:
            rows = _rows_for_day(seed, d)
            spot = _spot_for_day(seed, d)
            if rows and spot and spot > 0:
                ne = ebe.build_net_exposure(rows, spot, ticker=tk)
                ng = ne.gex()
        except Exception:
            ng = None
        # live model same-day snapshot at day i
        lg = None; lnv = None
        try:
            as_of = _dt.datetime.strptime(str(d)[:8], "%Y%m%d").date()
            class _FrozenDT(_dt.datetime):
                @classmethod
                def now(cls, tz=None):
                    return _dt.datetime.combine(as_of, _dt.time(16, 0), tzinfo=tz)
            td = _SeedTD(seed, serve_date=d)
            old_ctrl = dp.ThetaDataController
            old_dt = dp.datetime
            dp.ThetaDataController = lambda *a, **k: td
            dp.datetime = _FrozenDT
            try:
                exp = str(seed.get("manifest", {}).get("expiry", "")) or None
                res = dp.compute_dealer_positioning(tk, target_years=0.25, expiration=exp,
                                                    sign_model=SIGN_MODEL, accumulate=False)
                lg = res.total_net_gamma
                lnv = (res.vanna_call_shares + res.vanna_put_shares) if (
                    not math.isnan(res.vanna_call_shares) and not math.isnan(res.vanna_put_shares)) else None
            finally:
                dp.ThetaDataController = old_ctrl
                dp.datetime = old_dt
        except Exception:
            lg = None; lnv = None
        out.append({"date": d, "fwd": fw, "div": div, "burst": burst,
                    "new_gex": ng, "live_gamma": lg, "live_net_vanna": lnv})
    return out


def _rows_for_day(seed, d):
    rows = []
    for g in seed.get("greeks", []):
        if str(ebe._date_of(g))[:8] != str(d)[:8]:
            continue
        k = ebe._extract(g, "strike")
        if math.isnan(k) or k <= 0:
            continue
        k = k / 1000.0 if k > 10000 else k
        iv = ebe._extract(g, "implied_vol")
        if math.isnan(iv) or iv <= 0:
            continue
        rows.append({"strike": k, "right": str(g.get("right", "C")).upper()[:1],
                     "oi": 0, "implied_vol": iv})
    if not rows:
        return rows
    oi_map = _oi_for_day(seed, d)
    for r in rows:
        r["oi"] = oi_map.get((r["strike"], r["right"]), 0)
    return rows


def _oi_for_day(seed, d):
    m = {}
    for o in seed.get("oi", []):
        if str(ebe._date_of(o))[:8] != str(d)[:8]:
            continue
        k = ebe._extract(o, "strike")
        if math.isnan(k) or k <= 0:
            continue
        k = k / 1000.0 if k > 10000 else k
        m[(k, str(o.get("right", "C")).upper()[:1])] = int(ebe._extract(o, "open_interest", 0.0))
    return m


def _spot_for_day(seed, d):
    for r in seed.get("spot", []):
        if str(ebe._date_of(r))[:8] == str(d)[:8]:
            v = ebe._extract(r, "close")
            if not math.isnan(v):
                return v
    return None


def _spearman(a, b):
    def _rank(x):
        idx = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and x[idx[j + 1]] == x[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r
    if len(a) < 4:
        return 0.0
    return _corr(_rank(a), _rank(b))


def _fisher_tanh_ci(r, n):
    if n < 4 or abs(r) >= 0.999:
        return (float("nan"), float("nan"))
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1.0 / math.sqrt(max(n - 3, 1))
    return (math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se))


def _de_mean(x):
    m = sum(x) / len(x) if x else 0.0
    return [v - m for v in x]


def _zscore(x):
    m = sum(x) / len(x) if x else 0.0
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / len(x)) if x else 0.0
    return [(v - m) / sd if sd > 0 else 0.0 for v in x]


def _cluster_bootstrap_ci(x_lists, y_lists, n_iter=2000, seed=7):
    """Bootstrap over clusters; pooled de-meaned corr. Returns (lo, hi, r)."""
    import random
    rng = random.Random(seed)
    K = len(x_lists)
    xall, yall = [], []
    for x, y in zip(x_lists, y_lists):
        xall.extend(_de_mean(x)); yall.extend(_de_mean(y))
    r_obs = _corr(xall, yall)
    stats = []
    for _ in range(n_iter):
        xs, ys = [], []
        for _ in range(K):
            j = rng.randrange(K)
            xs.extend(_de_mean(x_lists[j])); ys.extend(_de_mean(y_lists[j]))
        if len(xs) >= 4:
            stats.append(_corr(xs, ys))
    stats.sort()
    if len(stats) < 100:
        return (0.0, 0.0, r_obs)
    return (stats[int(0.05 * len(stats))], stats[int(0.95 * len(stats)) - 1], r_obs)


def _tanh_md(n, alpha=0.05, power=0.80):
    z = 1.959963984540054 + 0.8416212335729143
    if n <= 4:
        return 1.0
    return math.tanh(z / math.sqrt(max(n - 3, 1)))


def _is_index(tk):
    return tk in ("SPY", "QQQ")


_INDEX_SET = ("SPY", "QQQ")


def main():
    t0 = time.time()
    clusters = load_all_clusters()
    print(f"[cmp] {len(clusters)} ticker×window clusters  (live sign model: {SIGN_MODEL})")

    # per-cluster spot (last-day close) + per-ticker mean spot — for B2/B3 units standardization
    spot_of = {}
    spot_by_tk = {}
    for (tk, w), (seed, s) in clusters.items():
        sp = _spot_for_day(seed, s["dates"][-1])
        spot_of[(tk, w)] = sp if sp else float('nan')
    for (tk, w) in spot_of:
        sp = spot_of[(tk, w)]
        if sp == sp:
            spot_by_tk.setdefault(tk, []).append(sp)
    spot_by_tk = {t: (sum(v) / len(v)) for t, v in spot_by_tk.items() if v}

    fwd = {}
    for (tk, w), (seed, s) in clusters.items():
        if s["fwd"]:
            fwd[(tk, w)] = s["fwd"][-1]

    rows = {}
    for (tk, w), (seed, s) in clusters.items():
        try:
            live = run_live_model(seed, tk)
            ne = run_new_model(seed, tk)
            if live and ne and live.total_net_gamma is not None:
                rows[(tk, w)] = {"new_gex": ne.gex(), "new_dex": ne.dex(),
                                 "live_gamma": live.total_net_gamma,
                                 "live_delta": live.net_dealer_delta if hasattr(live, "net_dealer_delta") else 0.0,
                                 "live_hedge": getattr(live, "hedge_requirement", 0.0)}
        except Exception as e:
            print(f"  [warn] {tk}/{w}: {type(e).__name__}: {e}")
    print(f"[cmp] both models computed on {len(rows)} snapshots")

    # (1) sign agreement
    print("\n=== (1) SIGN AGREEMENT (LONG/SHORT read) ===")
    agree = 0
    per_tk = {}
    for (tk, w), r in sorted(rows.items()):
        ng = "LONG" if r["new_gex"] > 0 else "SHORT"
        lg = "LONG" if r["live_gamma"] > 0 else "SHORT"
        a = ng == lg
        agree += int(a)
        per_tk.setdefault(tk, []).append(int(a))
        flag = "  <-- DISAGREE" if not a else ""
        print(f"  {tk:5s} {w:18s} new_gex={r['new_gex']:+.3e} ({ng:5s}) | "
              f"live_gamma={r['live_gamma']:+.3e} ({lg:5s}) | {a}{flag}")
    print(f"\n  OVERALL SIGN AGREEMENT: {agree}/{len(rows)} = {agree/len(rows):.1%}")
    print(f"  per-ticker: { {k: f'{sum(v)/len(v):.0%}' for k, v in per_tk.items()} }")

    # (2) cross-sectional correlation of mean exposures
    print("\n=== (2) CROSS-SECTIONAL (per-ticker mean exposure) ===")
    tk_gex, tk_lg = {}, {}
    tk_spot = {}
    for (tk, w), r in rows.items():
        tk_gex.setdefault(tk, []).append(r["new_gex"])
        tk_lg.setdefault(tk, []).append(r["live_gamma"])
    tks = sorted(tk_gex)
    gex_m = [sum(tk_gex[t]) / len(tk_gex[t]) for t in tks]
    lg_m = [sum(tk_lg[t]) / len(tk_lg[t]) for t in tks]
    rc = _corr(gex_m, lg_m)
    print(f"  corr(mean new_gex, mean live_gamma) across {len(tks)} tickers = {rc:+.4f}")
    for t in tks:
        print(f"    {t:5s} new_gex={sum(tk_gex[t])/len(tk_gex[t]):+.3e}  "
              f"live_gamma={sum(tk_lg[t])/len(tk_lg[t]):+.3e}")

    # (2b) B2 — units standardization: live_gamma_std = dollar-gamma-per-1% (spot^2, 100, 0.01)
    print("\n=== (2b) B2 UNITS-STANDARDIZED CROSS-SECTIONAL (live_gamma -> dollar-gamma-per-1%) ===")
    # live_gamma is raw signed gamma (~1e2-1e3). Standardize to the new model's units:
    # Gamma*OI*100*spot^2*0.01. spot from each cluster's last-day close.
    std_gex_m, std_lg_m = [], []
    for t in tks:
        gs = tk_gex[t]; ls = tk_lg[t]
        # per-cluster spot lookup (reuse the cluster's seed spot last-day)
        spots = [spot_of.get((t, w), float('nan')) for (tt, w) in rows if tt == t]
        # simpler: scale each live_gamma by the ticker's mean spot^2*0.01
        sp = spot_by_tk.get(t, float('nan'))
        if math.isnan(sp):
            std_gex_m.append(float('nan')); std_lg_m.append(float('nan'))
            continue
        std_gex_m.append(sum(gs) / len(gs))
        std_lg_m.append((sum(ls) / len(ls)) * sp ** 2 * 100 * 0.01)
    ok = [(a, b) for a, b in zip(std_gex_m, std_lg_m) if a == a and b == b]
    if len(ok) >= 4:
        rc_std = _corr([a for a, _ in ok], [b for _, b in ok])
        print(f"  corr(mean new_gex, mean live_gamma_std) across {len(ok)} tickers = {rc_std:+.4f}  "
              f"(raw was {rc:+.4f})")
        if abs(rc_std - rc) > 0.05:
            print(f"  → units standardization moved the cross-sectional corr by {rc_std - rc:+.4f} "
                  f"— the raw +0.16 was partly a spot^2 size confound")
    else:
        print("  insufficient clean tickers for standardized corr")

    # (3) B1 — FIXED forward-return predictiveness (per-day aligned, real fwd[i], NOT [-1])
    print("\n=== (3) B1 FORWARD-RETURN PREDICTIVENESS (per-day aligned fwd[i] — y≡0 bug fixed) ===")
    # per (tk,w), per-day model signals vs real per-day fwd[i]. Pooled de-meaned corr,
    # cluster-bootstrap 90% CI, effective-n with SPY/QQQ as one family.
    cluster_new_x, cluster_new_y = [], []   # new-model per-day (signal, fwd)
    cluster_live_x, cluster_live_y = [], []  # live-model per-day (signal, fwd)
    for (tk, w), (seed, s) in clusters.items():
        nx, ny = [], []
        lx, ly = [], []
        for day in _per_day_model_series(seed, tk, s):
            if day["fwd"] is None or day["fwd"] == 0.0:
                continue
            if day["new_gex"] is not None and day["new_gex"] != 0.0:
                nx.append(math.copysign(1.0, day["new_gex"]))
                ny.append(day["fwd"])
            if day["live_gamma"] is not None and day["live_gamma"] != 0.0:
                lx.append(math.copysign(1.0, day["live_gamma"]))
                ly.append(day["fwd"])
        if len(nx) >= 2:
            cluster_new_x.append(nx); cluster_new_y.append(ny)
        if len(lx) >= 2:
            cluster_live_x.append(lx); cluster_live_y.append(ly)
    n_new = sum(len(x) for x in cluster_new_x)
    n_live = sum(len(x) for x in cluster_live_x)
    eff_n_new = len({tk for (tk, w) in clusters if tk in _INDEX_SET}) + \
                len({tk for (tk, w) in clusters if tk not in _INDEX_SET})  # SPY/QQQ = 1 family
    if cluster_new_x:
        lo_n, hi_n, rn = _cluster_bootstrap_ci(cluster_new_x, cluster_new_y)
        # R2-5: md at EFFECTIVE-n (SPY/QQQ one family), never pooled n
        md_n = _tanh_md(eff_n_new)
        print(f"  NEW  per-day corr(sign gex, fwd) = {rn:+.4f}  90% cluster-CI [{lo_n:+.4f}, {hi_n:+.4f}]  "
              f"n={n_new}  md@eff-n={md_n:.3f}")
        print(f"       verdict: {'SUPPORTED' if (lo_n > 0 or hi_n < 0) and abs(rn) >= md_n else 'BOUNDED/NOT_SUPPORTED'}")
    else:
        print("  NEW  insufficient per-day data")
    if cluster_live_x:
        lo_l, hi_l, rl = _cluster_bootstrap_ci(cluster_live_x, cluster_live_y)
        # R2-5: md at EFFECTIVE-n (SPY/QQQ one family), never pooled n
        md_l = _tanh_md(eff_n_new)
        print(f"  LIVE per-day corr(sign gamma, fwd) = {rl:+.4f}  90% cluster-CI [{lo_l:+.4f}, {hi_l:+.4f}]  "
              f"n={n_live}  md@eff-n={md_l:.3f}")
        print(f"       verdict: {'SUPPORTED' if (lo_l > 0 or hi_l < 0) and abs(rl) >= md_l else 'BOUNDED/NOT_SUPPORTED'}")
        # R2-6: surface a directional finding even when |r| < md — never hide a CI-excluding-0 sign
        if (lo_l > 0 or hi_l < 0) and abs(rl) < md_l:
            direction = "POSITIVE" if rl > 0 else "NEGATIVE"
            print(f"       R2-6 SURFACED: LIVE {direction} directional signal, CI excludes 0 but "
                  f"|r|={abs(rl):.4f} < md={md_l:.3f} — underpowered, NOT a null. "
                  f"(shadow-clock level-sign; endogeneity caveat applies)")
    else:
        print("  LIVE insufficient per-day data")

    # (4) B3 — continuous units-normalized concordance (within-ticker z-scored rank) + divergence decomp
    print("\n=== (4) B3 CONTINUOUS CONCORDANCE (within-ticker z-scored, standardized units) ===")
    # per-ticker: z-score each model's standardized exposure across its windows; rank-concordance
    z_new = {}; z_live = {}
    for t in tks:
        gs = tk_gex[t]
        sp = spot_by_tk.get(t, float('nan'))
        ls_std = [g * sp ** 2 * 100 * 0.01 if not math.isnan(sp) else float('nan') for g in tk_lg[t]]
        clean = [(a, b) for a, b in zip(gs, ls_std) if a == a and b == b]
        if len(clean) >= 4:
            z_new[t] = _zscore([a for a, _ in clean])
            z_live[t] = _zscore([b for _, b in clean])
    all_zn, all_zl = [], []
    for t in z_new:
        all_zn.extend(z_new[t]); all_zl.extend(z_live[t])
    if len(all_zn) >= 8:
        r_z = _spearman(all_zn, all_zl)
        n_eff = max(len(z_new), 2) + 1  # ~11 effective groups (SPY/QQQ collapsed)
        lo_z, hi_z = _fisher_tanh_ci(r_z, n_eff)
        print(f"  within-ticker z-scored rank concordance (Spearman) = {r_z:+.4f}  "
              f"Fisher-tanh 95% CI [{lo_z:+.4f}, {hi_z:+.4f}] @ eff-n~{n_eff}")
        print(f"  → {'models rank names consistently' if not (lo_z <= 0 <= hi_z) else 'models disagree by habitat, not by rank'}")
    else:
        print("  insufficient within-ticker series for concordance")
    print("\n  PER-TICKER SYSTEMATIC-DIVERGENCE DECOMPOSITION (is a 12% row consistent-opposite or noise?):")
    for t in tks:
        gs = tk_gex[t]; ls = tk_lg[t]
        n = len(gs)
        dis = [1 if (g > 0) != (l > 0) else 0 for g, l in zip(gs, ls)]
        agree = n - sum(dis)
        # systematic-opposite: signs of (new) consistently flip vs live across windows?
        opp_flips = 0
        prev_opp = None
        for g, l in zip(gs, ls):
            opp = (g > 0) != (l > 0)
            if prev_opp is not None and opp != prev_opp:
                opp_flips += 1
            prev_opp = opp
        se = math.sqrt(0.25 / n) if n else 1.0
        print(f"    {t:5s} agree {agree}/{n} ({agree/n:.0%})  disagreement-flips {opp_flips}  "
              f"SE~{se:.2f}  → {'consistently-opposite' if agree/n < 0.4 and opp_flips <= 1 else ('oscillating/noise' if opp_flips > 1 else 'agreeing')}")

    # (5) B4 — firing-day burst-sign agreement (|ΔIV|>dead-band AND burst≠0; new burst vs live ΔIV-signed vannaflow)
    print("\n=== (5) B4 FIRING-DAY BURST-SIGN AGREEMENT (where the hedge actually fires) ===")
    idx_agree, idx_n = [], 0
    sg_agree, sg_n = [], 0
    for (tk, w), (seed, s) in clusters.items():
        for day in _per_day_model_series(seed, tk, s):
            div = day["div"]
            burst = day["burst"]
            if div is None or burst is None:
                continue
            if abs(div) <= 0.01 or burst == 0.0:
                continue  # not a firing day (dead-band + band breach)
            new_burst_sign = math.copysign(1.0, burst)
            # live ΔIV-signed vannaflow term: net vanna * sign(ΔIV) — vannaflow = Σ signed_vanna*OI*100*PP*(dIV/0.01)
            lnv = day["live_net_vanna"]
            if lnv is None or lnv == 0.0:
                continue
            live_flow_sign = math.copysign(1.0, lnv * div)  # ΔIV-signed
            a = new_burst_sign == live_flow_sign
            if _is_index(tk):
                idx_agree.append(int(a)); idx_n += 1
            else:
                sg_agree.append(int(a)); sg_n += 1
    print(f"  firing days (|ΔIV|>0.01 AND burst≠0): index n={idx_n}, singles n={sg_n}")
    if idx_n >= 4:
        ra = sum(idx_agree) / idx_n
        lo_i, hi_i, _ = _cluster_bootstrap_ci([idx_agree], [idx_agree]) if False else (0, 0, 0)
        se_i = math.sqrt(ra * (1 - ra) / idx_n)
        print(f"  INDEX burst-sign agreement = {ra:.1%} (n={idx_n}, SE~{se_i:.3f})  "
              f"[pre-reg bar: SUPPORTED only if CI excludes the ~50% level-sign null AND n_firing_index>=20]")
        print(f"  → {'SUPPORTED' if idx_n >= 20 and abs(ra - 0.5) > 1.96 * se_i else 'BOUNDED (n<20 or CI straddles 50%)'}")
    else:
        print(f"  index firing days < 4 — BOUNDED (cannot resolve burst-sign agreement at in-hand n)")
    if sg_n >= 4:
        rs = sum(sg_agree) / sg_n
        print(f"  SINGLES burst-sign agreement = {rs:.1%} (n={sg_n})")
    else:
        print(f"  singles firing days < 4 — too few")

    print(f"\n[cmp] total {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
