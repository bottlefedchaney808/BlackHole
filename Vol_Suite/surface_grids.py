"""surface_grids.py

Strike x expiry (and strike x time) grid builders for the Surface Explorer
tool (Tools/tools/surface_explorer_tool.py). This module does NOT introduce
a new pricing/Greeks/flow model -- it loops the existing, already-tested
single-expiry engines across multiple expiries and regularizes their output
onto a common axis, so a 3D surface / heatmap has a rectangular grid to plot:

  * build_greek_surface       -- dealer-frame BS greeks (expiry_book_exposure,
                                  the live production Greeks engine) across
                                  every listed expiry, not just near/mid/far.
  * build_market_iv_surface   -- thin wrapper around vol_surface_2d.build_surface,
                                  evaluated on a dense strike x tenor grid.
  * build_flow_strike_time    -- options-flow premium binned by strike AND
                                  time (chart_app/flow_pane.py only bins by
                                  time, collapsing strikes -- this adds the
                                  strike axis).
  * build_flow_strike_expiry  -- options-flow premium binned by strike AND
                                  expiry across the whole listed chain
                                  (expiry_book_production.py aggregates flow
                                  by strike at a single expiry only).

Every builder is fail-closed: missing spot / no listed expiries / no usable
trades raises ValueError with a specific reason, matching the "no fake
numbers" convention the rest of the dealer-exposure code follows. Partial
per-expiry failures are recorded in the result's "skipped" list rather than
aborting the whole surface.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import expiry_book_exposure as ebe
import numpy as np
from expiry_book_production import (
    ExpiryBookUnavailable,
    _dte_of,
    _merge_greeks_oi,
    normalize_snapshot_rows,
)

N_MONEYNESS = 41
MONEYNESS_BAND = 0.30  # +-30% of spot, log-moneyness

# build_market_iv_surface's grid is pure in-process math (VolSurface.iv()
# evaluates an already-fitted quadratic + a total-variance interpolation --
# no network calls), so its resolution isn't network-cost-bound the way the
# other builders in this module are. Confirmed live: at the shared
# N_MONEYNESS=41 x 30 default the plotted mesh looked visibly faceted/
# low-poly, especially across the steep near-dated skew. A denser grid here
# is essentially free (a few thousand extra Python-level calls, still
# comfortably sub-second) and renders a visibly smoother surface.
IV_SURFACE_N_STRIKES = 121
IV_SURFACE_N_TENORS = 60


def _client():
    from shared.thetadata import ThetaDataController

    return ThetaDataController()


def _select_expiries(td, ticker, max_expiries=12, min_dte=1, max_dte=400):
    """Every listed expiry with min_dte <= dte <= max_dte, nearest first,
    capped at max_expiries. Same shape as expiry_book_production._bucket's
    listed-expiry enumeration, but not restricted to near/mid/far buckets."""
    try:
        listed = sorted(
            str(e).replace("-", "") for e in (td.list_expirations(ticker) or [])
        )
    except Exception as exc:
        raise ValueError(f"could not list expirations for {ticker!r}: {exc}") from exc
    out = []
    for exp in listed:
        if len(exp) != 8 or not exp.isdigit():
            continue
        try:
            dte = _dte_of(exp)
        except Exception:
            continue
        if min_dte <= dte <= max_dte:
            out.append((exp, dte))
    out.sort(key=lambda t: t[1])
    return out[:max_expiries]


def _fetch_expiry_rows(td, ticker, expiry):
    rows = td.option_bulk_greeks(ticker, expiry)
    oi_rows = td.option_bulk_oi(ticker, expiry)
    merged = _merge_greeks_oi(rows, oi_rows)
    return normalize_snapshot_rows(merged)


# ---------------------------------------------------------------------------
# Greek surface (dealer-frame BS greeks, strike x expiry)
# ---------------------------------------------------------------------------
def build_greek_surface(
    ticker,
    greek,
    td=None,
    max_expiries=12,
    min_dte=1,
    max_dte=400,
    q=None,
    n_moneyness=N_MONEYNESS,
    moneyness_band=MONEYNESS_BAND,
):
    """strike (log-moneyness grid) x expiry surface of net dealer-frame
    exposure for one greek in ebe.GREEKS ('delta','gamma','vega','vanna',
    'charm','volga'). Reuses expiry_book_exposure.build_net_exposure -- the
    live production Greeks engine -- per expiry; does not add a new
    Greeks model.
    """
    if greek not in ebe.GREEKS:
        raise ValueError(f"unknown greek {greek!r}; choose from {ebe.GREEKS}")
    td = td or _client()
    ticker = str(ticker).upper()

    spot = float(td.fetch_spot_price(ticker))
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError(f"no usable spot price for {ticker!r}")
    if q is None:
        try:
            q = float(td.fetch_dividend_yield(ticker))
        except Exception:
            q = 0.0

    exps = _select_expiries(
        td, ticker, max_expiries=max_expiries, min_dte=min_dte, max_dte=max_dte
    )
    if not exps:
        raise ValueError(
            f"no listed expiries for {ticker!r} in [{min_dte},{max_dte}] DTE"
        )

    strikes_axis = spot * np.exp(
        np.linspace(-moneyness_band, moneyness_band, n_moneyness)
    )

    grid = []
    expiries_used = []
    dtes_used = []
    skipped = []
    units = ""
    for expiry, dte in exps:
        try:
            rows = _fetch_expiry_rows(td, ticker, expiry)
        except (ExpiryBookUnavailable, Exception) as exc:
            skipped.append({"expiry": expiry, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        t = dte / ebe.DEFAULT_A
        ne = ebe.build_net_exposure(
            rows, spot, ticker=ticker, expiry=expiry, T=t, dte=dte, q=q
        )
        if not ne.rows:
            skipped.append({"expiry": expiry, "reason": "no usable rows"})
            continue
        per_strike = {}
        for r in ne.rows:
            per_strike[r.strike] = per_strike.get(r.strike, 0.0) + r.exposure_of(greek)
            if not units:
                units = r.units.get(greek, "")
        ks = np.array(sorted(per_strike))
        if len(ks) < 3:
            skipped.append({"expiry": expiry, "reason": f"only {len(ks)} strikes"})
            continue
        vs = np.array([per_strike[k] for k in ks])
        row_vals = np.interp(strikes_axis, ks, vs, left=vs[0], right=vs[-1])
        grid.append(row_vals.tolist())
        expiries_used.append(expiry)
        dtes_used.append(dte)

    if not grid:
        raise ValueError(
            f"no expiry produced a usable {greek} surface row for {ticker!r} "
            f"(skipped: {skipped})"
        )

    return {
        "ticker": ticker,
        "greek": greek,
        "spot": spot,
        "strikes": strikes_axis.tolist(),
        "expiries": expiries_used,
        "dtes": dtes_used,
        "grid": grid,  # grid[i][j] = exposure at expiries[i], strikes[j]
        "units": units,
        "skipped": skipped,
        "meta": {
            "source": "expiry_book_exposure.build_net_exposure (dealer-frame BS greeks)",
            "interpolation": "linear over log-moneyness per expiry",
            "n_expiries_requested": len(exps),
            "n_expiries_used": len(expiries_used),
        },
    }


# ---------------------------------------------------------------------------
# Market IV surface (thin wrapper around vol_surface_2d)
# ---------------------------------------------------------------------------
def build_market_iv_surface(
    ticker,
    td=None,
    n_strikes=IV_SURFACE_N_STRIKES,
    n_tenors=IV_SURFACE_N_TENORS,
    moneyness_band=None,
    min_dte=0,
):
    """Dense strike x tenor grid of vendor implied vol, via
    vol_surface_2d.build_surface's quadratic-per-expiry + total-variance
    tenor interpolation (unchanged -- this only adds a JSON grid view of it,
    alongside its existing static-PNG plot()).

    Unlike the other builders in this module, this one does NOT default its
    strike axis to the module-wide +-30% MONEYNESS_BAND: vol_surface_2d's
    per-expiry quadratic is only ever FIT on strikes within
    vol_surface_2d.NEAR_ATM_BAND (+-15%) of spot (see
    vol_surface_2d._fit_quadratic_smile), and VolSurface.iv() clamps any
    evaluation outside that band to the fitted boundary value. Plotting a
    +-30% axis against a model only trusted to +-15% doesn't reproduce the
    old blow-up bug (the clamp prevents that), but it does render two flat,
    unrealistic plateaus/walls from 15%-30% moneyness on both sides where
    the model has nothing new to say -- confirmed live, this is what made
    the surface look "completely flat" beyond the ATM dip instead of a
    proper skew shape. Default the axis to the model's own trusted domain
    instead, so every plotted point reflects an actual per-expiry fit.

    min_dte: forwarded to vol_surface_2d.build_surface -- 0 (default)
    includes every listed expiry (front-week wings included, which will
    dominate the shared z-axis/color scale on a rendered plot -- that's
    real market behavior, not a bug, see vol_surface_2d.NEAR_ATM_BAND's
    module docstring). Pass e.g. 14 to drop anything inside 2 weeks for a
    term-structure-focused view instead -- both are legitimate, just
    answering different questions; this is a toggle, not a fix."""
    import vol_surface_2d as vs2d

    if moneyness_band is None:
        moneyness_band = vs2d.NEAR_ATM_BAND

    td = td or _client()
    ticker = str(ticker).upper()
    surface = vs2d.build_surface(ticker, td, min_dte=min_dte)
    if surface is None:
        raise ValueError(
            f"could not build an IV surface for {ticker!r} (fewer than "
            f"{vs2d.MIN_TENORS} usable tenors)"
        )

    spot = surface.fitted_params["spot"]
    tenors = surface.fitted_params["tenors"]
    min_t = max(0.0, min(tenors))
    max_t = max(tenors) * 1.05 + 1e-6

    # A real listed chain's strike range is NOT symmetric in log-moneyness
    # around spot -- confirmed live on SPY/QQQ: the widest expiries list
    # strikes from about -1.2 to +0.5 log-moneyness (equity/index chains
    # list further below spot than above it). A symmetric +-moneyness_band
    # axis formula either cuts off real downside strikes or, if widened
    # enough to reach them, extrapolates the upside axis deep into strikes
    # that were never actually listed (confirmed live: a symmetric +-1.5
    # band put the axis out past $3400 on a $769 SPY, 4.5x spot, well past
    # anything tradeable). Derive the axis from the REAL observed strike
    # range in surface.points instead (1st/99th percentile, so one rare
    # far-wing print from a single expiry can't drag the axis) -- this
    # naturally reflects the chain's actual asymmetric shape and never asks
    # VolSurface.iv() to evaluate a strike beyond what real data supports.
    raw_strikes = np.array([p.strike for p in surface.points], dtype=float)
    lo_k, hi_k = np.percentile(raw_strikes, [1, 99])
    if lo_k <= 0 or hi_k <= lo_k:
        lo_k, hi_k = spot * np.exp(-moneyness_band), spot * np.exp(moneyness_band)
    strikes_axis = np.exp(np.linspace(np.log(lo_k), np.log(hi_k), n_strikes))
    tenor_axis = np.linspace(min_t, max_t, n_tenors)

    grid = [[surface.iv(float(k), float(t)) for k in strikes_axis] for t in tenor_axis]

    return {
        "ticker": ticker,
        "spot": spot,
        "strikes": strikes_axis.tolist(),
        "tenors_years": tenor_axis.tolist(),
        "grid": grid,  # grid[i][j] = tenor_axis[i], strikes[j]
        "raw_points": [
            {"strike": p.strike, "tenor": p.tenor, "iv": p.iv} for p in surface.points
        ],
        "meta": {
            "source": "vol_surface_2d.build_surface (quadratic-per-expiry, "
            "total-variance tenor interpolation)",
        },
    }


# ---------------------------------------------------------------------------
# Options-flow grids (strike x time, strike x expiry)
# ---------------------------------------------------------------------------
def _trade_strike(row):
    try:
        k = float(row.get("strike_price") or row.get("strike") or 0.0)
    except (TypeError, ValueError):
        return None
    if k <= 0:
        return None
    if abs(k) >= 1000:
        k = k / 1000.0
    return k


def _trade_right(row):
    r = str(row.get("trade_right") or row.get("right") or "").upper()[:1]
    return r if r in ("C", "P") else None


def _flow_helpers():
    # Imported lazily: chart_app is a separate app whose module import
    # graph (shared.chart_data, WHALE_THRESHOLD) is otherwise unnecessary
    # for every other builder in this module.
    from chart_app.flow_stamp import _premium, _trade_ts

    return _premium, _trade_ts


def build_flow_strike_time(
    ticker,
    td=None,
    session=None,
    n_strike_bins=25,
    n_time_bins=26,
    moneyness_band=MONEYNESS_BAND,
):
    """Heatmap grid: net (call-put) premium by strike-bin x time-bin, for
    one session. Extends chart_app/flow_pane.py's bin_flow (time-only) with
    a strike axis."""
    _premium, _trade_ts = _flow_helpers()
    td = td or _client()
    ticker = str(ticker).upper()

    spot = float(td.fetch_spot_price(ticker))
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError(f"no usable spot price for {ticker!r}")
    session = session or date.today().strftime("%Y%m%d")
    trade_fn = getattr(td, "option_session_trades", None)
    if not callable(trade_fn):
        raise ValueError("ThetaDataController has no option_session_trades")
    trades = trade_fn(ticker, session) or []
    if not trades:
        raise ValueError(f"no trades for {ticker!r} on session {session}")

    parsed = []
    for row in trades:
        if not isinstance(row, dict):
            continue
        prem = _premium(row)
        if prem <= 0:
            continue
        right = _trade_right(row)
        strike = _trade_strike(row)
        ts = _trade_ts(row)
        if right is None or strike is None or ts is None:
            continue
        parsed.append((ts, strike, right, prem))
    if not parsed:
        raise ValueError(
            f"no trades with usable strike/right/timestamp/premium for "
            f"{ticker!r} session {session}"
        )

    strike_lo = spot * math.exp(-moneyness_band)
    strike_hi = spot * math.exp(moneyness_band)
    strike_edges = np.linspace(strike_lo, strike_hi, n_strike_bins + 1)

    t_min = min(p[0] for p in parsed)
    t_max = max(p[0] for p in parsed)
    span = (t_max - t_min).total_seconds()
    if span <= 0:
        span = 60.0
        t_max = t_min + timedelta(seconds=span)
    time_edges = [
        t_min + timedelta(seconds=span * i / n_time_bins)
        for i in range(n_time_bins + 1)
    ]

    grid = [[0.0] * n_strike_bins for _ in range(n_time_bins)]
    n_used = 0
    for ts, strike, right, prem in parsed:
        if strike < strike_lo or strike > strike_hi:
            continue
        si = int(np.searchsorted(strike_edges, strike, side="right") - 1)
        si = min(max(si, 0), n_strike_bins - 1)
        ti = int((ts - t_min).total_seconds() / span * n_time_bins)
        ti = min(max(ti, 0), n_time_bins - 1)
        grid[ti][si] += prem if right == "C" else -prem
        n_used += 1

    if n_used == 0:
        raise ValueError(
            f"all {len(parsed)} usable trades fell outside the "
            f"+-{moneyness_band:.0%} strike window around spot={spot:.2f}"
        )

    return {
        "ticker": ticker,
        "spot": spot,
        "session": session,
        "strike_edges": strike_edges.tolist(),
        "time_labels": [t.isoformat() for t in time_edges[:-1]],
        "grid": grid,  # grid[time_bin][strike_bin] = net (call-put) premium
        "meta": {
            "source": "ThetaDataController.option_session_trades",
            "value": "net premium (call - put), signed",
            "n_trades_used": n_used,
            "n_trades_total": len(trades),
        },
    }


def build_flow_strike_expiry(
    ticker,
    td=None,
    session=None,
    max_expiries=12,
    min_dte=0,
    max_dte=60,
    n_strike_bins=25,
    moneyness_band=MONEYNESS_BAND,
):
    """Heatmap grid: net (call-put) premium by strike-bin x expiry, for one
    session snapshot across the whole listed chain. Extends
    expiry_book_production.py's per-strike, single-expiry flow aggregation
    across every listed expiry instead."""
    _premium, _trade_ts = _flow_helpers()
    td = td or _client()
    ticker = str(ticker).upper()

    spot = float(td.fetch_spot_price(ticker))
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError(f"no usable spot price for {ticker!r}")
    session = session or date.today().strftime("%Y%m%d")
    trade_fn = getattr(td, "option_session_trades", None)
    if not callable(trade_fn):
        raise ValueError("ThetaDataController has no option_session_trades")
    trades = trade_fn(ticker, session) or []
    if not trades:
        raise ValueError(f"no trades for {ticker!r} on session {session}")

    exps = _select_expiries(
        td, ticker, max_expiries=max_expiries, min_dte=min_dte, max_dte=max_dte
    )
    if not exps:
        raise ValueError(
            f"no listed expiries for {ticker!r} in [{min_dte},{max_dte}] DTE"
        )
    exp_order = [e for e, _ in exps]
    by_exp = {e: [] for e in exp_order}
    for row in trades:
        if not isinstance(row, dict):
            continue
        exp_digits = "".join(
            ch for ch in str(row.get("expiration") or "") if ch.isdigit()
        )[:8]
        if exp_digits not in by_exp:
            continue
        prem = _premium(row)
        if prem <= 0:
            continue
        right = _trade_right(row)
        strike = _trade_strike(row)
        if right is None or strike is None:
            continue
        by_exp[exp_digits].append((strike, right, prem))

    strike_lo = spot * math.exp(-moneyness_band)
    strike_hi = spot * math.exp(moneyness_band)
    strike_edges = np.linspace(strike_lo, strike_hi, n_strike_bins + 1)

    expiries_used = [e for e in exp_order if by_exp.get(e)]
    if not expiries_used:
        raise ValueError(
            f"no trades matched any of the {len(exp_order)} listed expiries "
            f"for {ticker!r} session {session}"
        )

    grid = []
    for exp in expiries_used:
        row_vals = [0.0] * n_strike_bins
        for strike, right, prem in by_exp[exp]:
            if strike < strike_lo or strike > strike_hi:
                continue
            si = int(np.searchsorted(strike_edges, strike, side="right") - 1)
            si = min(max(si, 0), n_strike_bins - 1)
            row_vals[si] += prem if right == "C" else -prem
        grid.append(row_vals)

    return {
        "ticker": ticker,
        "spot": spot,
        "session": session,
        "strike_edges": strike_edges.tolist(),
        "expiries": expiries_used,
        "grid": grid,  # grid[expiry_index][strike_bin] = net (call-put) premium
        "meta": {
            "source": "ThetaDataController.option_session_trades",
            "value": "net premium (call - put), signed",
            "n_trades_total": len(trades),
        },
    }
