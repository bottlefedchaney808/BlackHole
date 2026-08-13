"""main.py — VaR Tools Interactive CLI
Inputs are prompted (white). Results are computed and displayed (yellow/green).
Live prices pulled from ThetaData and cached.  No yfinance.

Run:  python main.py
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from datetime import datetime, timedelta

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.panel import Panel
    from rich.prompt import Prompt, FloatPrompt, IntPrompt, Confirm
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None


class ContextModeError(ValueError):
    """Raised when suite context input is invalid for non-interactive mode."""


# ── Terminal helpers ──────────────────────────────────────────────────────────

def header(title: str):
    if HAS_RICH:
        console.print(f"\n[bold white on blue]  {title}  [/]")
    else:
        print(f"\n{'='*60}\n  {title}\n{'='*60}")

def section(title: str):
    if HAS_RICH:
        console.print(f"\n[bold cyan]{title}[/]")
    else:
        print(f"\n── {title} ──")

def inp(label: str, default=None, cast=str):
    """Prompt for an input (white). Shows default in brackets."""
    prompt = f"  [white]{label}[/]" if HAS_RICH else f"  {label}"
    if default is not None:
        prompt += f" [{default}]"
    prompt += ": "
    if HAS_RICH:
        val = Prompt.ask(f"  [white]{label}[/]" + (f" [dim][{default}][/]" if default is not None else ""), default=str(default) if default is not None else "")
    else:
        val = input(f"  {label}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if not val and default is not None:
            val = str(default)
    try:
        return cast(val)
    except Exception:
        return default

def result(label: str, value, fmt=","):
    """Display a result (yellow)."""
    if isinstance(value, float):
        if fmt == "$":
            val_str = f"${value:>14,.2f}"
        elif fmt == "%":
            val_str = f"{value:>10.4f}%"
        else:
            val_str = f"{value:>14,.4f}"
    else:
        val_str = str(value)
    if HAS_RICH:
        console.print(f"  [yellow]{label:<40}[/][bold yellow]{val_str}[/]")
    else:
        print(f"  {label:<40}{val_str}")

def result_table(headers, rows, title="Results"):
    """Display a result table."""
    if HAS_RICH:
        t = Table(title=title, box=box.SIMPLE, show_header=True,
                  header_style="bold yellow", title_style="bold white")
        for h in headers:
            t.add_column(h, style="yellow")
        for row in rows:
            t.add_row(*[str(c) for c in row])
        console.print(t)
    else:
        print(f"\n  {title}")
        print("  " + "  ".join(f"{h:<18}" for h in headers))
        for row in rows:
            print("  " + "  ".join(f"{str(c):<18}" for c in row))


def _ensure_number_list(values, field_name: str, expected_len: int):
    if not isinstance(values, list):
        raise ContextModeError(f"Field '{field_name}' must be a list of length {expected_len}.")
    if len(values) != expected_len:
        raise ContextModeError(f"Field '{field_name}' length must match basket length ({expected_len}).")
    try:
        arr = np.array([float(v) for v in values], dtype=float)
    except Exception as e:
        raise ContextModeError(f"Field '{field_name}' must contain numeric values.") from e
    if not np.isfinite(arr).all():
        raise ContextModeError(f"Field '{field_name}' contains non-finite values.")
    return arr


def _load_context_payload(context_path: str):
    if not context_path:
        raise ContextModeError("Missing required --context path.")
    if not os.path.exists(context_path):
        raise ContextModeError(f"Context file not found: {context_path}")
    try:
        with open(context_path, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        raise ContextModeError(f"Invalid JSON in context file: {e}") from e
    except Exception as e:
        raise ContextModeError(f"Failed reading context file: {e}") from e
    if not isinstance(payload, dict):
        raise ContextModeError("Context root must be a JSON object.")
    return payload


def _build_corr_sim_from_context(payload: dict):
    basket_obj = payload.get("basket") if isinstance(payload.get("basket"), dict) else None
    basket = payload.get("basket")
    ticker = payload.get("ticker")
    notes = []

    if basket_obj is not None:
        basket = basket_obj.get("tickers")
        if ticker is None:
            ticker = payload.get("focus", {}).get("ticker") if isinstance(payload.get("focus"), dict) else None
    elif basket is None:
        if isinstance(ticker, str) and ticker.strip():
            basket = [ticker.strip().upper()]
            notes.append("basket missing; mapped single 'ticker' into basket.")
        else:
            raise ContextModeError("Missing required field 'basket' (or fallback 'ticker').")
    if not isinstance(basket, list) or not basket:
        raise ContextModeError("Field 'basket' must be a non-empty list.")

    tickers = []
    for tk in basket:
        if not isinstance(tk, str) or not tk.strip():
            raise ContextModeError("Field 'basket' must contain non-empty ticker strings.")
        tickers.append(tk.strip().upper())

    n_assets = len(tickers)
    weights_source = payload.get("weights")
    if weights_source is None and basket_obj is not None:
        weights_source = basket_obj.get("weights")
    weights = _ensure_number_list(weights_source, "weights", n_assets)
    wsum = float(weights.sum())
    if wsum <= 0:
        raise ContextModeError("Field 'weights' must sum to a positive value.")
    norm_weights = weights / wsum
    if abs(wsum - 1.0) > 1e-9:
        notes.append("weights were normalized to sum to 1.0.")

    var_cfg = payload.get("var")
    if not isinstance(var_cfg, dict):
        raise ContextModeError("Missing required object field 'var'.")
    if "confidence" not in var_cfg:
        raise ContextModeError("Missing required field 'var.confidence'.")

    # Horizon is optional: a top-level 'corr_sim_days' wins, then 'var.horizon_days',
    # then a 1-year (252 trading day) default matching the MC sim's horizon.
    horizon_source = payload.get("corr_sim_days")
    if horizon_source is None:
        horizon_source = var_cfg.get("horizon_days")
    if horizon_source is None:
        horizon_source = 252
        notes.append("horizon missing; used default 252-day (1y) horizon.")
    try:
        horizon_days = int(horizon_source)
    except Exception as e:
        raise ContextModeError(
            "Field 'corr_sim_days' (or 'var.horizon_days') must be an integer.") from e
    if horizon_days <= 0:
        raise ContextModeError(
            "Field 'corr_sim_days' (or 'var.horizon_days') must be > 0.")

    try:
        confidence = float(var_cfg["confidence"])
    except Exception as e:
        raise ContextModeError("Field 'var.confidence' must be numeric.") from e
    if not (0.0 < confidence < 1.0):
        raise ContextModeError("Field 'var.confidence' must be between 0 and 1.")

    positions_raw = payload.get("positions")
    if positions_raw is None:
        positions_raw = var_cfg.get("positions")
    if positions_raw is None:
        total_notional = 1_000_000.0
        notionals = norm_weights * total_notional
        notes.append("positions missing; derived proportional notionals from weights with total notional = 1,000,000.")
    elif isinstance(positions_raw, list):
        notionals = _ensure_number_list(positions_raw, "positions", n_assets)
    elif isinstance(positions_raw, dict):
        vals = []
        for tk in tickers:
            if tk not in positions_raw:
                raise ContextModeError(f"Field 'positions' missing entry for ticker '{tk}'.")
            try:
                vals.append(float(positions_raw[tk]))
            except Exception as e:
                raise ContextModeError(f"Field 'positions[{tk}]' must be numeric.") from e
        notionals = np.array(vals, dtype=float)
    else:
        raise ContextModeError("Field 'positions' must be a list, object keyed by ticker, or omitted.")
    if not np.isfinite(notionals).all():
        raise ContextModeError("Field 'positions' contains non-finite values.")

    corr_raw = payload.get("correlation_matrix")
    if corr_raw is None:
        corr_raw = payload.get("corr_matrix")
    if corr_raw is None and basket_obj is not None:
        corr_raw = basket_obj.get("correlation_matrix")
    if corr_raw is None:
        corr_matrix = np.eye(n_assets)
        notes.append("correlation matrix missing; used identity matrix.")
    else:
        try:
            corr_matrix = np.array(corr_raw, dtype=float)
        except Exception as e:
            raise ContextModeError("Field 'correlation_matrix' must be numeric matrix.") from e
        if corr_matrix.shape != (n_assets, n_assets):
            raise ContextModeError(f"Field 'correlation_matrix' must be shape ({n_assets},{n_assets}).")
        if not np.isfinite(corr_matrix).all():
            raise ContextModeError("Field 'correlation_matrix' contains non-finite values.")
        if not np.allclose(corr_matrix, corr_matrix.T, atol=1e-8):
            raise ContextModeError("Field 'correlation_matrix' must be symmetric.")
        corr_matrix = np.clip(corr_matrix, -0.999, 0.999)
        np.fill_diagonal(corr_matrix, 1.0)

    vols_raw = payload.get("volatilities")
    if vols_raw is None:
        vols_raw = var_cfg.get("volatilities") if isinstance(var_cfg, dict) else None
    if vols_raw is None:
        volatilities = np.full(n_assets, 0.25, dtype=float)
        notes.append("volatilities missing; used default annual volatility 0.25 for each asset.")
    else:
        volatilities = _ensure_number_list(vols_raw, "volatilities", n_assets)
    if np.any(volatilities <= 0):
        raise ContextModeError("Field 'volatilities' values must be > 0.")

    prices_raw = payload.get("prices")
    if prices_raw is None:
        prices_raw = payload.get("spot_prices")
    if prices_raw is None:
        current_prices = []
        defaulted = []
        for tk in tickers:
            px = float(live_price(tk))
            if px <= 0:
                px = 100.0
                defaulted.append(tk)
            current_prices.append(px)
        current_prices = np.array(current_prices, dtype=float)
        if defaulted:
            notes.append(f"live spot unavailable for {','.join(defaulted)}; used fallback price 100.0.")
    else:
        if isinstance(prices_raw, list):
            current_prices = _ensure_number_list(prices_raw, "prices", n_assets)
        elif isinstance(prices_raw, dict):
            vals = []
            for tk in tickers:
                if tk not in prices_raw:
                    raise ContextModeError(f"Field 'prices' missing entry for ticker '{tk}'.")
                vals.append(float(prices_raw[tk]))
            current_prices = np.array(vals, dtype=float)
        else:
            raise ContextModeError("Field 'prices' must be a list, object keyed by ticker, or omitted.")
    if np.any(current_prices <= 0):
        raise ContextModeError("Field 'prices' values must be > 0.")

    n_shares = notionals / current_prices

    n_sims = var_cfg.get("n_sims", 10_000)
    seed = var_cfg.get("seed", 42)
    try:
        n_sims = int(n_sims)
    except Exception as e:
        raise ContextModeError("Field 'var.n_sims' must be an integer when provided.") from e
    try:
        seed = int(seed)
    except Exception as e:
        raise ContextModeError("Field 'var.seed' must be an integer when provided.") from e
    if n_sims <= 0:
        raise ContextModeError("Field 'var.n_sims' must be > 0.")

    from var_engine.corr_sim import run, CorrSimInputs
    r = run(CorrSimInputs(
        current_prices=current_prices,
        n_shares=n_shares,
        volatilities=volatilities,
        corr_matrix=corr_matrix,
        var_days=horizon_days,
        trading_days=252,
        confidence=confidence,
        n_sims=n_sims,
        seed=seed,
        asset_names=tickers,
    ))

    return {
        "suite": "var",
        "status": "ok",
        "module": "corr_sim",
        "var": float(r.var),
        "cvar": float(r.cvar),
        "confidence": float(confidence),
        "horizon_days": int(horizon_days),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "notes": " | ".join(notes) if notes else "",
    }


def _focus_ticker(payload: dict, ticker: str = None) -> str:
    tk = ticker
    if not tk and isinstance(payload.get("focus"), dict):
        tk = payload["focus"].get("ticker")
    if not tk:
        tk = payload.get("ticker")
    if not isinstance(tk, str) or not tk.strip():
        raise ContextModeError("Missing ticker (payload.focus.ticker / payload.ticker).")
    return tk.strip().upper()


def _context_seed(payload: dict, default: int = 42) -> int:
    var_cfg = payload.get("var")
    if isinstance(var_cfg, dict) and "seed" in var_cfg:
        try:
            return int(var_cfg["seed"])
        except Exception:
            return default
    return default


def _histogram_bins(values: np.ndarray, n_bins: int = 20) -> list:
    """Bucket *values* into n_bins equal-width bins for chart rendering.

    The dashboard has no path-level data to draw (mc_sim/copula/corr_sim are
    single-step-to-horizon, not multi-step path simulators), so a terminal-
    distribution histogram is the honest visual: it shows the actual shape of
    the simulated outcome, not a fabricated path. Every simulated draw lands in
    exactly one bin, so sum(bin["count"]) == len(values).
    """
    values = np.asarray(values, dtype=float)
    counts, edges = np.histogram(values, bins=n_bins)
    return [{"low": float(edges[i]), "high": float(edges[i + 1]), "count": int(counts[i])}
            for i in range(n_bins)]


def _resolve_vol_and_quality(payload: dict, tk: str) -> tuple:
    """Prefer suite_context's Vol_Suite-computed GARCH vol; fall back to VaR's
    own GARCH fit, then to a fixed default. Returns (vol, vol_source)."""
    from var_engine import data_loader
    focus = payload.get("focus") if isinstance(payload.get("focus"), dict) else {}
    ctx_vol = focus.get("garch_conditional_vol")
    if isinstance(ctx_vol, (int, float)) and not isinstance(ctx_vol, bool) and ctx_vol > 0:
        return float(ctx_vol), "context"
    fit_vol = data_loader.estimate_garch_vol(tk)
    if fit_vol is not None:
        return float(fit_vol), "garch_fit"
    return 0.25, "fallback"


def _resolve_drift_and_quality(payload: dict, tk: str) -> tuple:
    """Prefer suite_context's focus.expected_return (published by Vol_Suite
    when it computes one); fall back to VaR's own historical geometric drift.
    Returns (drift, expected_return_source).

    Mirrors _resolve_vol_and_quality: read the context first so an upstream
    estimate wins, only fall back to a locally-computed value when the context
    carries none. A missing drift is 0.0 with source "unavailable" -- never a
    fabricated-looking nonzero value.
    """
    from var_engine import data_loader
    focus = payload.get("focus") if isinstance(payload.get("focus"), dict) else {}
    ctx_drift = focus.get("expected_return")
    if isinstance(ctx_drift, (int, float)) and not isinstance(ctx_drift, bool):
        return float(ctx_drift), "context"
    drift = data_loader.estimate_geometric_return(tk)
    if drift is None:
        return 0.0, "unavailable"
    return float(drift), "computed"


def _build_mc_sim_from_context(payload: dict, ticker: str = None) -> dict:
    """Non-interactive 1-year-out MC price-distribution sim, seeded from
    live spot + GARCH vol + historical geometric drift."""
    from var_engine import data_loader
    from var_engine.mc_sim import run, MCSimInputs, Position

    tk = _focus_ticker(payload, ticker)
    spot = data_loader.fetch_spot(tk)
    if spot <= 0:
        raise ContextModeError(f"Could not fetch live spot for {tk}.")
    vol, vol_source = _resolve_vol_and_quality(payload, tk)
    drift, drift_source = _resolve_drift_and_quality(payload, tk)
    seed = _context_seed(payload)
    n_sims = 10_000

    r = run(MCSimInputs(
        market_ids=[tk],
        spot_prices=np.array([spot]),
        volatilities=np.array([vol]),
        corr_matrix=np.array([[1.0]]),
        var_days=252,
        trading_days=252,
        confidence=0.99,
        n_sims=n_sims,
        seed=seed,
        positions=[Position(pos_type=1, market_id=tk, quantity=1.0)],
        expected_returns=np.array([drift]),
    ))
    terminal = r.terminal_prices[:, 0]
    return {
        "suite": "var", "status": "ok", "module": "mc_sim_1yr",
        "ticker": tk, "spot": float(spot), "vol": float(vol), "expected_return": float(drift),
        "seed": seed, "horizon_days": 252, "n_sims": n_sims,
        "terminal_price_mean": float(terminal.mean()),
        "terminal_price_median": float(np.median(terminal)),
        "terminal_price_p5": float(np.quantile(terminal, 0.05)),
        "terminal_price_p95": float(np.quantile(terminal, 0.95)),
        "terminal_price_histogram": _histogram_bins(terminal),
        "histogram_unit": "price",
        "var_1yr": float(r.var_full), "cvar_1yr": float(r.cvar_full),
        "data_quality": {"vol_source": vol_source, "expected_return_source": drift_source},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def _build_price_dist_from_context(payload: dict, ticker: str = None) -> dict:
    """Non-interactive 1-year-out price-distribution table + MC terminal
    histogram, same seed/vol/spot/drift inputs as _build_mc_sim_from_context.

    price_dist.py previously had no context-mode builder at all -- its analytic
    lognormal table and MC probability engine were reachable only from the
    interactive menu (run_price_dist), so no headless/dashboard caller could
    use them.
    """
    from var_engine import data_loader
    from var_engine.price_dist import lognormal_dist, mc_probabilities, MCProbInputs

    tk = _focus_ticker(payload, ticker)
    spot = data_loader.fetch_spot(tk)
    if spot <= 0:
        raise ContextModeError(f"Could not fetch live spot for {tk}.")
    vol, vol_source = _resolve_vol_and_quality(payload, tk)
    drift, drift_source = _resolve_drift_and_quality(payload, tk)
    seed = _context_seed(payload)
    n_sims = 10_000
    days = 252
    # One annualization convention for the analytic table, the MC probability
    # engine and the histogram draw below, so they can't silently diverge.
    trading_days = 252.0

    table = lognormal_dist(spot, days=days, vol=vol, mu=drift,
                           trading_days=trading_days, n_points=50)
    mc = mc_probabilities(MCProbInputs(
        spot=spot, upper=spot * 1.5, lower=spot * 0.5, days=days,
        vol=vol, mu=drift, n_sims=n_sims, seed=seed,
        trading_days=trading_days,
    ))

    # mc_probabilities reports summary probabilities only (no terminal-price
    # vector), so the histogram is drawn from an equivalent one-step GBM draw
    # to the same 1-year horizon.
    rng = np.random.default_rng(seed)
    T = days / trading_days
    terminal = spot * np.exp((drift - 0.5 * vol ** 2) * T
                             + vol * np.sqrt(T) * rng.standard_normal(n_sims))

    return {
        "suite": "var", "status": "ok", "module": "price_dist_1yr",
        "ticker": tk, "spot": float(spot), "vol": float(vol),
        "expected_return": float(drift),
        "seed": seed, "horizon_days": days, "n_sims": n_sims,
        "distribution_table": [
            {"price": e.price, "prob_at": e.prob_at,
             "prob_below": e.prob_below, "prob_above": e.prob_above}
            for e in table
        ],
        "terminal_price_histogram": _histogram_bins(terminal),
        "histogram_unit": "price",
        "avg_end_price": float(mc.avg_end_price),
        "prob_above_upper_at_expiry": float(mc.above_upper_at_expiry),
        "prob_below_lower_at_expiry": float(mc.below_lower_at_expiry),
        "prob_touch_upper_any_time": float(mc.above_upper_any_time),
        "prob_touch_lower_any_time": float(mc.below_lower_any_time),
        "upper_target": float(spot * 1.5), "lower_target": float(spot * 0.5),
        "data_quality": {"vol_source": vol_source,
                         "expected_return_source": drift_source},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def _build_copula_from_context(payload: dict, ticker: str = None) -> dict:
    """Non-interactive 1-year-out Student-T copula price-distribution sim,
    same seed/vol/spot/drift inputs as _build_mc_sim_from_context."""
    from var_engine import data_loader
    from var_engine.copulas import run, CopulaInputs

    tk = _focus_ticker(payload, ticker)
    spot = data_loader.fetch_spot(tk)
    if spot <= 0:
        raise ContextModeError(f"Could not fetch live spot for {tk}.")
    vol, vol_source = _resolve_vol_and_quality(payload, tk)
    drift, drift_source = _resolve_drift_and_quality(payload, tk)
    seed = _context_seed(payload)
    n_sims = 50_000

    r = run(CopulaInputs(
        tickers=[tk],
        position_vals=np.array([spot]),
        volatilities=np.array([vol]),
        corr_matrix=np.array([[1.0]]),
        copula_type="student_t",
        student_df=5.0,
        var_days=252,
        trading_days=252,
        confidence=0.99,
        n_sims=n_sims,
        seed=seed,
        spot_prices=np.array([spot]),
        expected_returns=np.array([drift]),
    ))
    terminal = r.terminal_prices[:, 0]
    return {
        "suite": "var", "status": "ok", "module": "copula_1yr",
        "ticker": tk, "spot": float(spot), "vol": float(vol), "expected_return": float(drift),
        "seed": seed, "horizon_days": 252, "n_sims": n_sims,
        "copula_type": "student_t",
        "terminal_price_mean": float(terminal.mean()),
        "terminal_price_median": float(np.median(terminal)),
        "terminal_price_p5": float(np.quantile(terminal, 0.05)),
        "terminal_price_p95": float(np.quantile(terminal, 0.95)),
        "terminal_price_histogram": _histogram_bins(terminal),
        "histogram_unit": "price",
        "var_1yr": float(r.var), "cvar_1yr": float(r.cvar),
        "data_quality": {"vol_source": vol_source, "expected_return_source": drift_source},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def _build_corr_sim_peer_from_context(payload: dict, ticker: str = None, max_peers: int = 2) -> dict:
    """Non-interactive 1-year-out correlation sim between the focus ticker and
    up to *max_peers* Vol_Suite basket peers, using the same GARCH-vol
    methodology and seed as the MC/copula sims above."""
    from var_engine import data_loader
    from var_engine.corr_sim import run, CorrSimInputs

    tk = _focus_ticker(payload, ticker)
    basket = payload.get("basket") if isinstance(payload.get("basket"), dict) else None
    basket_tickers = basket.get("tickers") if basket else []
    if not isinstance(basket_tickers, list):
        basket_tickers = []
    peers = [str(t).strip().upper() for t in basket_tickers if str(t).strip().upper() != tk][:max_peers]
    if not peers:
        raise ContextModeError("Need at least one basket peer distinct from the focus ticker for corr_sim.")

    tickers = [tk] + peers
    seed = _context_seed(payload)
    start, end = data_loader.default_date_range()

    focus_vol, vol_source = _resolve_vol_and_quality(payload, tk)

    spots, vols, rets = [], [], {}
    for t in tickers:
        spots.append(data_loader.fetch_spot(t))
        # Only the focus ticker has a context-supplied GARCH vol -- peers are
        # not covered by suite_context's `focus` block, so they refit.
        vols.append(focus_vol if t == tk else (data_loader.estimate_garch_vol(t) or 0.25))
        try:
            rets[t] = data_loader.fetch_log_returns(t, start, end)
        except Exception:
            rets[t] = None

    lens = [len(r) for r in rets.values() if r is not None]
    min_len = min(lens) if lens else 0
    if min_len >= 20 and all(rets[t] is not None for t in tickers):
        mat = np.array([rets[t][-min_len:] for t in tickers])
        corr_matrix = np.corrcoef(mat)
    else:
        corr_matrix = np.eye(len(tickers))

    spots = np.array(spots, dtype=float)
    if np.any(spots <= 0):
        raise ContextModeError(f"Could not fetch live spot for one of: {', '.join(tickers)}.")
    vols = np.array(vols, dtype=float)
    n_shares = np.ones(len(tickers))
    n_sims = 10_000

    r = run(CorrSimInputs(
        current_prices=spots,
        n_shares=n_shares,
        volatilities=vols,
        corr_matrix=corr_matrix,
        var_days=252,
        trading_days=252,
        confidence=0.99,
        n_sims=n_sims,
        seed=seed,
        asset_names=tickers,
    ))
    # corr_sim returns a portfolio P&L vector rather than per-asset terminal
    # prices, so the comparable histogram here is of terminal *portfolio*
    # values (start value + simulated P&L) -- one entry per simulation, same
    # as the single-name builders' terminal price vectors.
    terminal = r.portfolio_value + np.asarray(r.pnl_distribution, dtype=float)
    return {
        "suite": "var", "status": "ok", "module": "corr_sim_1yr",
        "tickers": tickers, "seed": seed, "horizon_days": 252, "n_sims": n_sims,
        "correlation_matrix": corr_matrix.tolist(),
        "sim_vols": r.sim_vols.tolist(), "sim_corr": r.sim_corr.tolist(),
        "var_1yr": float(r.var), "cvar_1yr": float(r.cvar),
        "portfolio_value": float(r.portfolio_value),
        "terminal_price_histogram": _histogram_bins(terminal),
        # Unlike the single-name builders, these bins are terminal *portfolio*
        # values, not prices -- consumers must read this field rather than
        # assume "price" from the histogram field's name.
        "histogram_unit": "portfolio_value",
        "cholesky_ok": bool(r.cholesky_ok),
        # vol_source describes the focus ticker only; peers always refit.
        "data_quality": {"vol_source": vol_source, "expected_return_source": "not_applicable"},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def _build_hedge_optimizer_from_context(payload: dict, ticker: str = None,
                                         output_dir: str = None, max_hedges: int = 3) -> dict:
    """Non-interactive minimum-variance hedge optimizer. Position notional is
    read from options_result.json in *output_dir* if present, else defaulted
    to spot*100 shares. Hedge-instrument candidates default to Vol_Suite
    basket peers, with vol/correlation estimated the same way as corr_sim."""
    from var_engine import data_loader
    from var_engine.hedge_optimizer import min_var_hedge, HedgeOptimizerInputs, HedgeInstrument

    tk = _focus_ticker(payload, ticker)

    position_value = None
    if output_dir:
        opt_path = os.path.join(output_dir, "options_result.json")
        if os.path.exists(opt_path):
            try:
                with open(opt_path, "r", encoding="utf-8-sig") as f:
                    opt_data = json.load(f)
                for key in ("notional", "position_value", "total_notional"):
                    if isinstance(opt_data.get(key), (int, float)):
                        position_value = float(opt_data[key])
                        break
            except Exception:
                position_value = None
    if not position_value:
        spot = data_loader.fetch_spot(tk)
        if spot <= 0:
            raise ContextModeError(f"Could not fetch live spot for {tk}.")
        position_value = spot * 100.0

    basket = payload.get("basket") if isinstance(payload.get("basket"), dict) else None
    basket_tickers = basket.get("tickers") if basket else []
    if not isinstance(basket_tickers, list):
        basket_tickers = []
    peers = [str(t).strip().upper() for t in basket_tickers if str(t).strip().upper() != tk][:max_hedges]
    if not peers:
        raise ContextModeError("Need at least one basket peer to build hedge-instrument candidates.")

    tk_vol = data_loader.estimate_garch_vol(tk) or 0.25
    start, end = data_loader.default_date_range()
    try:
        tk_rets = data_loader.fetch_log_returns(tk, start, end)
    except Exception:
        tk_rets = None

    hedge_instruments = []
    for p in peers:
        p_vol = data_loader.estimate_garch_vol(p) or 0.25
        corr = 0.0
        try:
            p_rets = data_loader.fetch_log_returns(p, start, end)
            if tk_rets is not None and p_rets is not None:
                n = min(len(tk_rets), len(p_rets))
                if n >= 20:
                    corr = float(np.corrcoef(tk_rets[-n:], p_rets[-n:])[0, 1])
        except Exception:
            pass
        beta = corr * (p_vol / tk_vol) if tk_vol else 0.0
        hedge_instruments.append(HedgeInstrument(
            name=p, volatility=p_vol,
            correlation_to_positions=np.array([corr]), beta=beta,
        ))

    cov_matrix = np.array([[tk_vol ** 2]])
    out = min_var_hedge(HedgeOptimizerInputs(
        positions=np.array([position_value]),
        cov_matrix=cov_matrix,
        hedge_instruments=hedge_instruments,
        var_horizon=252,
        trading_days=252,
        confidence=0.99,
    ))
    return {
        "suite": "var", "status": "ok", "module": "hedge_optimizer",
        "ticker": tk, "position_value": float(position_value),
        "hedge_names": out.hedge_names,
        "optimal_weights": [float(w) for w in out.optimal_weights],
        "base_var": float(out.base_var), "hedged_var": float(out.hedged_var),
        "var_reduction_pct": float(out.var_reduction_pct),
        "base_port_vol": float(out.base_port_vol), "hedged_port_vol": float(out.hedged_port_vol),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def run_context_mode(context_path: str, context_out_path: str = None, module: int = None) -> int:
    if module is not None and module != 1:
        payload = {
            "suite": "var",
            "status": "error",
            "module": "corr_sim",
            "var": None,
            "cvar": None,
            "confidence": None,
            "horizon_days": None,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "notes": "Context mode currently supports only module 1 (corr_sim).",
        }
    else:
        try:
            ctx = _load_context_payload(context_path)
            payload = _build_corr_sim_from_context(ctx)
        except Exception as e:
            payload = {
                "suite": "var",
                "status": "error",
                "module": "corr_sim",
                "var": None,
                "cvar": None,
                "confidence": None,
                "horizon_days": None,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "notes": str(e),
            }

    if context_out_path:
        out_dir = os.path.dirname(os.path.abspath(context_out_path))
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(context_out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") == "ok" else 1

def live_price(ticker: str) -> float:
    """Fetch live spot from ThetaData (cached)."""
    try:
        from var_engine.data_loader import fetch_spot
        p = fetch_spot(ticker.upper())
        if p and p > 0:
            return p
    except Exception:
        pass
    return 0.0

def live_rf() -> float:
    try:
        from var_engine.data_loader import fetch_risk_free_rate
        return fetch_risk_free_rate(0.25)
    except Exception:
        return 0.05


def _default_pack_manifest_path() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "..", "sentiment-scanner", "data", "exports", "highlighted_ticker_packs", "latest_manifest.json")


def _load_pack_entries(manifest_path: str):
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        packs = data.get("packs", []) if isinstance(data, dict) else []
        if isinstance(packs, list):
            return packs
    except Exception:
        pass
    return []


def _prompt_tickers_from_pack() -> list:
    default_manifest = _default_pack_manifest_path()
    manifest_path = inp("Ticker-pack manifest path", default=default_manifest, cast=str)
    packs = _load_pack_entries(manifest_path)
    if not packs:
        if HAS_RICH:
            console.print("  [red]No packs found in manifest; using manual ticker input.[/]")
        else:
            print("  No packs found in manifest; using manual ticker input.")
        return []
    section("Highlighted ticker packs")
    for i, p in enumerate(packs[:10], start=1):
        gname = p.get("group_name", "group")
        gid = p.get("group_id", "unknown")
        cnt = p.get("ticker_count", 0)
        updated = p.get("last_updated_at") or p.get("created_at") or ""
        if HAS_RICH:
            console.print(f"  [yellow]{i:2d}. {gname} | {cnt} tickers | {gid} | {updated}[/]")
        else:
            print(f"  {i:2d}. {gname} | {cnt} tickers | {gid} | {updated}")
    pick = inp("Pick pack number", default=1, cast=int)
    pick_idx = max(0, min(pick - 1, len(packs) - 1))
    top_n = inp("Top N tickers from pack (0=all)", default=0, cast=int)
    use_top_n = None if top_n <= 0 else top_n
    try:
        from var_engine.data_loader import load_highlighted_ticker_pack
        tickers = load_highlighted_ticker_pack(
            manifest_path=manifest_path,
            pack_index=pick_idx,
            top_n=use_top_n,
        )
        return tickers
    except Exception:
        return []


def inp_tickers(n_label="How many tickers") -> list:
    use_pack = inp("Load tickers from highlighted pack? [y/n]", default="n", cast=str).strip().lower()
    if use_pack == "y":
        pack_tickers = _prompt_tickers_from_pack()
        if pack_tickers:
            if HAS_RICH:
                console.print(f"  [green]Loaded {len(pack_tickers)} tickers from pack[/]")
            else:
                print(f"  Loaded {len(pack_tickers)} tickers from pack")
            return pack_tickers
    n = inp(n_label, default=2, cast=int)
    tickers = []
    for i in range(n):
        tk = inp(f"  Ticker {i+1}", cast=str).upper()
        tickers.append(tk)
    return tickers

def inp_corr_matrix(tickers: list) -> np.ndarray:
    n = len(tickers)
    section("Correlation Matrix (enter off-diagonal pairs)")
    corr = np.eye(n)
    for i in range(n):
        for j in range(i+1, n):
            c = inp(f"  corr({tickers[i]},{tickers[j]})", default=0.3, cast=float)
            c = float(np.clip(c, -0.999, 0.999))
            corr[i,j] = corr[j,i] = c
    return corr


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 1 — Correlated Simulation
# ══════════════════════════════════════════════════════════════════════════════

def run_corr_sim():
    from var_engine.corr_sim import run, CorrSimInputs
    header("1. Correlated GBM Simulation")

    section("1. Simulation Parameters")
    n = inp("Number of assets", default=3, cast=int)
    tickers, prices, shares, vols = [], [], [], []
    for i in range(n):
        tk  = inp(f"  Market ID {i+1} (ticker)", cast=str).upper()
        lp  = live_price(tk)
        px  = inp(f"  Current price [{lp:.2f} live]" if lp else f"  Current price", default=lp or 10.0, cast=float)
        sh  = inp(f"  No. shares/units", default=100, cast=float)
        vol = inp(f"  Annual volatility (e.g. 0.25)", default=0.25, cast=float)
        tickers.append(tk); prices.append(px); shares.append(sh); vols.append(vol)

    corr = inp_corr_matrix(tickers)

    var_days    = inp("VaR period (trading days)", default=1, cast=int)
    trd_days    = inp("Trading days per year", default=252, cast=int)
    confidence  = inp("Confidence level (e.g. 0.99)", default=0.99, cast=float)
    n_sims      = inp("Simulation iterations", default=10_000, cast=int)

    section("Running...")
    r = run(CorrSimInputs(
        current_prices = np.array(prices),
        n_shares       = np.array(shares),
        volatilities   = np.array(vols),
        corr_matrix    = corr,
        var_days       = var_days,
        trading_days   = trd_days,
        confidence     = confidence,
        n_sims         = n_sims,
        asset_names    = tickers,
    ))

    section("4. Results")
    result("Portfolio Value",       r.portfolio_value, "$")
    result(f"{var_days}d {confidence:.0%} VaR",  r.var,  "$")
    result(f"{var_days}d {confidence:.0%} CVaR", r.cvar, "$")
    result("Cholesky positive-def", "YES" if r.cholesky_ok else "NO (clipped)")
    section("Realised Vols (sim)")
    result_table(["Ticker","Theor Vol","Sim Vol"],
        [(tickers[i], f"{vols[i]:.4f}", f"{r.sim_vols[i]:.4f}") for i in range(n)],
        "Volatility Check")
    section("Realised Correlation (sim)")
    rows = []
    for i in range(n):
        rows.append([tickers[i]] + [f"{r.sim_corr[i,j]:.3f}" for j in range(n)])
    result_table([""] + tickers, rows, "Correlation Check")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 2 — Monte Carlo (partial vs full revaluation)
# ══════════════════════════════════════════════════════════════════════════════

def run_mc_sim():
    from var_engine.mc_sim import run, MCSimInputs, Position
    header("2. Monte Carlo VaR — Partial vs Full Revaluation")

    section("1. Simulation Parameters")
    rf_live = live_rf()

    # market IDs
    n_markets = inp("Number of market variables (stocks+bond yields)", default=3, cast=int)
    market_ids, spot_prices, vols_list = [], [], []
    for i in range(n_markets):
        mid  = inp(f"  Market ID {i+1} (ticker or 'BOND_YIELD')", cast=str).upper()
        lp   = live_price(mid) if "BOND" not in mid else 0.0
        px   = inp(f"  Current price/rate [{lp:.4f} live]" if lp else "  Current price/rate",
                   default=lp if lp else 0.07, cast=float)
        vol  = inp(f"  Annual volatility", default=0.20, cast=float)
        market_ids.append(mid); spot_prices.append(px); vols_list.append(vol)

    corr = inp_corr_matrix(market_ids)

    risk_free   = inp(f"Risk free rate [{rf_live:.4f} live]", default=rf_live, cast=float)
    trd_days    = inp("Trading days per year", default=250, cast=int)
    var_days    = inp("VaR period (trading days)", default=5, cast=int)
    cal_days    = var_days * 365 / trd_days
    if HAS_RICH: console.print(f"  [dim]Calendar days: {cal_days:.1f}[/]")
    confidence  = inp("Significance level (e.g. 0.01 → 99%)", default=0.01, cast=float)
    confidence  = 1.0 - confidence  # convert to upper tail
    n_sims      = inp("Simulation iterations", default=10_000, cast=int)

    positions = []

    # Stock positions
    section("Positions — Stock (type 1)")
    n_stocks = inp("Number of stock positions", default=1, cast=int)
    for i in range(n_stocks):
        mid = inp(f"  Market ID (must match above)", cast=str).upper()
        qty = inp(f"  Number of shares", default=10, cast=float)
        positions.append(Position(pos_type=1, market_id=mid, quantity=qty))

    # Option positions
    section("2. Option Specification (type 2)  — 0 to skip")
    n_opts = inp("Number of option positions", default=1, cast=int)
    for i in range(n_opts):
        mid      = inp(f"  Underlying market ID", cast=str).upper()
        strike   = inp(f"  Strike", default=10.0, cast=float)
        days_cal = inp(f"  Days to expiry (calendar)", default=100, cast=float)
        cp       = inp(f"  Call or Put [C/P]", default="C", cast=str).upper()
        is_call  = (cp == "C")
        qty      = inp(f"  Number of options in position", default=100, cast=float)
        positions.append(Position(pos_type=2, market_id=mid, quantity=qty,
                                   strike=strike, days_cal=days_cal, is_call=is_call))

    # Bond positions
    section("3. Bond Specification (type 3)  — 0 to skip")
    n_bonds = inp("Number of bond positions", default=1, cast=int)
    for i in range(n_bonds):
        mid      = inp(f"  Market ID (yield variable)", cast=str).upper()
        face     = inp(f"  Principal / Face value", default=1000.0, cast=float)
        mat_yrs  = inp(f"  Years to maturity", default=6.0, cast=float)
        coupon   = inp(f"  Coupon rate (e.g. 0.05)", default=0.05, cast=float)
        freq     = inp(f"  Coupon frequency per year", default=4, cast=int)
        positions.append(Position(pos_type=3, market_id=mid,
                                   face=face, coupon=coupon, freq=freq,
                                   maturity_years=mat_yrs))

    filter_type = inp("Position type filter (0=all, 1=stocks, 2=options, 3=bonds)", default=0, cast=int)

    section("Running simulation...")
    r = run(MCSimInputs(
        market_ids   = market_ids,
        spot_prices  = np.array(spot_prices),
        volatilities = np.array(vols_list),
        corr_matrix  = corr,
        risk_free    = risk_free,
        trading_days = trd_days,
        var_days     = var_days,
        confidence   = confidence,
        n_sims       = n_sims,
        positions    = positions,
        filter_type  = filter_type,
    ))

    section("4. Results")
    result("VaR from partial simulation (combined)",    r.var_partial,  "$")
    result("CVaR from partial simulation (combined)",   r.cvar_partial, "$")
    result("VaR from full revaluation (combined)",      r.var_full,     "$")
    result("CVaR from full revaluation (combined)",     r.cvar_full,    "$")
    if r.position_vars:
        section("VaR by position type (full revaluation)")
        names = {1:"Stock", 2:"Option", 3:"Bond"}
        for t, v in r.position_vars.items():
            result(f"  {names.get(t,t)} VaR", v, "$")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 3 — Historical Simulation
# ══════════════════════════════════════════════════════════════════════════════

def run_hist_sim():
    from var_engine.hist_sim import run, HistSimInputs
    header("3. Historical Simulation — Basic / Hull-White / FHS")

    section("Parameters")
    tickers = inp_tickers("Number of tickers")
    n       = len(tickers)
    pos_vals = []
    for tk in tickers:
        lp = live_price(tk)
        pv = inp(f"  Position value ${tk}" + (f" [spot ${lp:.2f}]" if lp else ""),
                 default=100_000, cast=float)
        pos_vals.append(pv)

    var_days   = inp("VaR period (trading days)", default=1, cast=int)
    conf       = inp("Confidence level (e.g. 0.95)", default=0.95, cast=float)
    method     = inp("Method [basic / hw / fhs]", default="fhs", cast=str).lower()
    start_date = inp("Start date YYYYMMDD", default=(datetime.now()-timedelta(days=756)).strftime("%Y%m%d"))
    end_date   = inp("End date YYYYMMDD",   default=datetime.now().strftime("%Y%m%d"))

    section("Fetching data and running...")
    r = run(HistSimInputs(
        tickers       = tickers,
        position_vals = np.array(pos_vals),
        var_days      = var_days,
        confidence    = conf,
        method        = method,
        start_date    = start_date,
        end_date      = end_date,
    ))

    section("Results")
    result(f"{method.upper()} {var_days}d {conf:.0%} VaR",  r.var,  "$")
    result(f"{method.upper()} {var_days}d {conf:.0%} CVaR", r.cvar, "$")
    if r.garch_params:
        section("GARCH Parameters")
        rows = []
        for tk, g in r.garch_params.items():
            rows.append([tk, f"{g['omega']:.2e}", f"{g['alpha']:.4f}",
                         f"{g['beta']:.4f}", f"{g['current_vol']:.4f}",
                         f"{g['long_run_vol']:.4f}"])
        result_table(["Ticker","Omega","Alpha","Beta","σ_now","σ_LR"], rows, "GARCH(1,1)")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 4 — Copula VaR
# ══════════════════════════════════════════════════════════════════════════════

def run_copulas():
    from var_engine.copulas import run, CopulaInputs, calibrate_marginal_dfs
    from var_engine.data_loader import fetch_log_returns
    header("4. Copula VaR — Gaussian / Student-T / Clayton")

    section("Parameters")
    tickers = inp_tickers("Number of tickers")
    n       = len(tickers)
    pos_vals, vols_list = [], []
    for tk in tickers:
        lp = live_price(tk)
        pv = inp(f"  Position value ${tk}", default=100_000, cast=float)
        vol= inp(f"  Annual vol ${tk}", default=0.25, cast=float)
        pos_vals.append(pv); vols_list.append(vol)

    corr = inp_corr_matrix(tickers)

    copula_type = inp("Copula type [gaussian / student_t / clayton]", default="student_t").lower()
    student_df  = inp("Student-T df (joint, ~3-7)", default=5.0, cast=float)
    clay_alpha  = inp("Clayton alpha (lower tail, ~0.5-1.5)", default=0.7, cast=float)
    var_days    = inp("VaR period (trading days)", default=10, cast=int)
    conf        = inp("Confidence level (e.g. 0.99)", default=0.99, cast=float)
    n_sims      = inp("Simulation iterations", default=50_000, cast=int)

    # calibrate marginal dfs from data if available
    mdf = np.zeros(n)
    if Confirm.ask("  Calibrate per-asset Student-T marginals from historical data?", default=False) if HAS_RICH else False:
        start = (datetime.now()-timedelta(days=756)).strftime("%Y%m%d")
        end   = datetime.now().strftime("%Y%m%d")
        try:
            rets = {tk: fetch_log_returns(tk, start, end) for tk in tickers}
            mdf  = calibrate_marginal_dfs(rets)
            section("Calibrated marginal dfs")
            for tk, df in zip(tickers, mdf):
                result(f"  {tk}", df, "")
        except Exception as e:
            console.print(f"  [red]Calibration failed: {e} — using normal marginals[/]") if HAS_RICH else print(f"  Calibration failed: {e}")

    section("Running simulation...")
    r = run(CopulaInputs(
        tickers=tickers, position_vals=np.array(pos_vals),
        volatilities=np.array(vols_list), corr_matrix=corr,
        copula_type=copula_type, student_df=student_df,
        marginal_dfs=mdf, clayton_alpha=clay_alpha,
        var_days=var_days, n_sims=n_sims, confidence=conf,
    ))

    section("Results")
    result(f"{copula_type} copula {var_days}d {conf:.0%} VaR",  r.var,  "$")
    result(f"{copula_type} copula {var_days}d {conf:.0%} CVaR", r.cvar, "$")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 5 — Forex VaR
# ══════════════════════════════════════════════════════════════════════════════

def run_forex_var():
    from var_engine.forex_var import run, ForexVaRInputs
    header("5. VaR with Foreign Currency Exposures")

    section("Parameters")
    n = inp("Number of assets (equities + FX)", default=3, cast=int)
    names, vols_list, pos_home, is_fx_list = [], [], [], []
    for i in range(n):
        nm  = inp(f"  Asset {i+1} name", default=f"Asset{i+1}", cast=str)
        vol = inp(f"  Annual vol", default=0.20, cast=float)
        pv  = inp(f"  Position in home currency ($)", default=1_000_000, cast=float)
        fx  = inp(f"  Is this a FX position? [y/n]", default="n", cast=str).lower() == "y"
        names.append(nm); vols_list.append(vol); pos_home.append(pv); is_fx_list.append(fx)

    corr      = inp_corr_matrix(names)
    var_days  = inp("VaR period (trading days)", default=5, cast=int)
    conf      = inp("Confidence level (e.g. 0.99)", default=0.99, cast=float)

    r = run(ForexVaRInputs(
        asset_names    = names,
        volatilities   = np.array(vols_list),
        corr_matrix    = corr,
        positions_home = np.array(pos_home),
        is_fx          = np.array(is_fx_list),
        var_days       = var_days,
        confidence     = conf,
    ))

    section("Results")
    result("Total VaR",                  r.total_var,       "$")
    result("Equity VaR",                 r.equity_var,      "$")
    result("FX VaR",                     r.fx_var,          "$")
    result("Diversification benefit",    r.diversification, "$")
    section("Standalone VaR")
    for nm, sv in zip(names, r.standalone_var):
        result(f"  {nm}", sv, "$")
    section("Component VaR (sums to Total VaR)")
    for nm, cv in zip(names, r.component_var):
        result(f"  {nm}", cv, "$")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 6 — Cash Flow Mapping
# ══════════════════════════════════════════════════════════════════════════════

def run_cashflow_map():
    from var_engine.cashflow_map import run, CashFlowMapInputs, Vertex, CashFlow
    header("6. Cash Flow Mapping")

    section("Standard Time Vertices")
    n_verts = inp("Number of standard vertices", default=2, cast=int)
    verts = []
    for i in range(n_verts):
        mat  = inp(f"  Vertex {i+1} maturity (years, e.g. 0.25)", default=0.25*(i+1), cast=float)
        rate = inp(f"  Zero rate (continuous, e.g. 0.045)", default=0.045, cast=float)
        vol  = inp(f"  Bond price vol (e.g. 0.01)", default=0.01, cast=float)
        verts.append(Vertex(maturity=mat, zero_rate=rate, vol=vol))

    corr = inp_corr_matrix([f"v{i+1}" for i in range(n_verts)])

    section("Cash Flows to Map")
    n_cfs = inp("Number of cash flows", default=1, cast=int)
    cfs = []
    for i in range(n_cfs):
        mat = inp(f"  CF {i+1} maturity (years)", default=0.3, cast=float)
        amt = inp(f"  CF {i+1} amount ($)", default=100_000, cast=float)
        cfs.append(CashFlow(maturity=mat, amount=amt))

    r = run(CashFlowMapInputs(vertices=verts, cashflows=cfs, corr_matrix=corr))

    section("Results")
    for m in r.mapped:
        result(f"CF maturity={m.original.maturity:.3f}y  PV", m.pv, "$")
        result(f"  Interp zero rate", m.interp_rate, "%")
        result(f"  Interp vol",       m.interp_vol,  "%")
        for i, v in enumerate(verts):
            result(f"  → Vertex {v.maturity:.2f}y allocation", m.vertex_amounts[i], "$")
    result("Portfolio VaR (1d 99%)", r.portfolio_var, "$")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 7 — Stress Testing
# ══════════════════════════════════════════════════════════════════════════════

def run_stress_test():
    from var_engine.stress_test import run, StressTestInputs
    header("7. VaR Stress Testing")

    section("Parameters")
    n  = inp("Number of assets", default=3, cast=int)
    names, pos_list, vols_list = [], [], []
    for i in range(n):
        nm  = inp(f"  Asset {i+1} name", default=f"Asset{i+1}")
        pv  = inp(f"  Position ($)", default=1_000_000, cast=float)
        vol = inp(f"  Historical vol (annualised)", default=0.18, cast=float)
        names.append(nm); pos_list.append(pv); vols_list.append(vol)

    corr = inp_corr_matrix(names)

    section("Stress Adjustments — Volatilities")
    vol_shocks = []
    for nm, bv in zip(names, vols_list):
        sh = inp(f"  Vol shock for {nm} (additive, e.g. +0.08)", default=0.0, cast=float)
        vol_shocks.append(sh)

    section("Stress Adjustments — Correlations")
    corr_shocks = np.zeros((n, n))
    more = True
    while more:
        if HAS_RICH:
            more = Confirm.ask("  Add a correlation shock?", default=False)
        else:
            more = input("  Add a correlation shock? [y/N]: ").strip().lower() == "y"
        if more:
            i = inp(f"  Asset index i (1-{n})", default=1, cast=int) - 1
            j = inp(f"  Asset index j (1-{n})", default=2, cast=int) - 1
            sh = inp(f"  Shock to corr({names[i]},{names[j]})", default=0.2, cast=float)
            corr_shocks[i,j] = corr_shocks[j,i] = sh

    var_days = inp("VaR period (trading days)", default=5, cast=int)
    conf     = inp("Confidence level (e.g. 0.99)", default=0.99, cast=float)

    r = run(StressTestInputs(
        asset_names = names, positions = np.array(pos_list),
        base_vols = np.array(vols_list), base_corr = corr,
        vol_shocks = np.array(vol_shocks), corr_shocks = corr_shocks,
        var_days = var_days, confidence = conf,
    ))

    section("Results")
    result("Base VaR",           r.base_var,    "$")
    result("Base CVaR",          r.base_cvar,   "$")
    result("Stress VaR",         r.stress_var,  "$")
    result("Stress CVaR",        r.stress_cvar, "$")
    result("VaR increase",       (r.stress_var/r.base_var - 1)*100 if r.base_var else 0, "%")
    result("Stressed corr PSD",  "YES" if r.corr_psd else "NO (nearest PSD applied)")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 8 — VaR Aggregation
# ══════════════════════════════════════════════════════════════════════════════

def run_var_agg():
    from var_engine.var_agg import run, VaRAggInputs
    header("8. VaR Aggregation — EWMA + Sub-Portfolio + PCA")

    section("Parameters")
    tickers = inp_tickers("Number of tickers")
    n       = len(tickers)
    pos_list, groups = [], []
    for tk in tickers:
        lp = live_price(tk)
        pv = inp(f"  Position $ {tk}" + (f" [spot ${lp:.2f}]" if lp else ""), default=1_000_000, cast=float)
        g  = inp(f"  Group (0=equity, 1=FX/rates)", default=0, cast=int)
        pos_list.append(pv); groups.append(g)

    ewma_lam   = inp("EWMA decay factor lambda (0.94=RiskMetrics)", default=0.94, cast=float)
    var_days   = inp("VaR period (trading days)", default=5, cast=int)
    conf       = inp("Confidence level (e.g. 0.95)", default=0.95, cast=float)
    n_pca      = inp("PCA components to use", default=2, cast=int)
    start_date = inp("Start date YYYYMMDD", default=(datetime.now()-timedelta(days=756)).strftime("%Y%m%d"))
    end_date   = inp("End date YYYYMMDD",   default=datetime.now().strftime("%Y%m%d"))

    section("Fetching data and running...")
    r = run(VaRAggInputs(
        asset_names = tickers, positions = np.array(pos_list),
        group_mask = np.array(groups),
        tickers = tickers, start_date = start_date, end_date = end_date,
        ewma_lambda = ewma_lam, var_days = var_days, confidence = conf,
        n_pca_components = n_pca,
    ))

    section("1. Full Portfolio VaR")
    result("Total VaR",  r.total_var,  "$")
    result("Total CVaR", r.total_cvar, "$")
    result_table(["Ticker","Component VaR","Standalone VaR"],
        [(tickers[i], f"${r.component_var[i]:,.0f}", f"${r.standalone_var[i]:,.0f}")
         for i in range(n)], "Component vs Standalone")

    section("2. Sub-Portfolio VaR")
    for nm, v in r.subport_var.items():
        result(f"  {nm}", v, "$")
    result("Aggregated VaR", r.aggregated_var, "$")

    section(f"3. PCA VaR ({n_pca} components)")
    result(f"PCA VaR (without residuals)", r.pca_var_without_resid, "$")
    result(f"PCA VaR (with residuals)",    r.pca_var_with_resid,    "$")
    result_table(["Component","Explained Variance %"],
        [(f"PC{i+1}", f"{r.explained_variance[i]:.1%}") for i in range(len(r.explained_variance))],
        "PCA Explained Variance")


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 9 — Price Distribution + Probability Calculator
# ══════════════════════════════════════════════════════════════════════════════

def run_price_dist():
    from var_engine.price_dist import (
        prob_at_expiry, prob_touch_any_time, spot_at_probability,
        mc_probabilities, lognormal_dist, price_distribution, MCProbInputs
    )
    from var_engine.data_loader import fetch_price_series, estimate_garch_vol, estimate_geometric_return, estimate_dividend_yield
    header("9. Price Distribution + Probability Calculator")

    section("Asset Parameters")
    tk    = inp("Ticker", cast=str).upper()
    lp    = live_price(tk)
    spot  = inp(f"Current spot [{lp:.2f} live]" if lp else "Current spot",
                default=lp if lp else 100.0, cast=float)

    # Auto-populate GARCH vol in background — no prompt delay
    garch_vol = estimate_garch_vol(tk)
    def_vol = garch_vol if garch_vol is not None and garch_vol > 0.0 else 0.30
    vol_label = f"Annual volatility [GARCH(1,1)={def_vol:.2f}]"
    vol   = inp(vol_label, default=def_vol, cast=float)

    # Auto-populate geometric mean return
    geo_ret = estimate_geometric_return(tk)
    def_mu = geo_ret if geo_ret is not None and geo_ret != 0.0 else 0.08
    mu_label = f"Expected annual return [geometric mean={def_mu:.4f}]"
    mu    = inp(mu_label, default=def_mu, cast=float)

    days  = 360   # fixed horizon (annualized convention)
    trd   = inp("Trading days per year", default=252, cast=int)

    section("A. Distribution Statistics (from historical prices)")
    load_hist = inp("Load historical prices for distribution stats? [y/n]", default="y").lower() == "y"
    if load_hist:
        start = inp("Start date YYYYMMDD", default=(datetime.now()-timedelta(days=756)).strftime("%Y%m%d"))
        end   = inp("End date YYYYMMDD",   default=datetime.now().strftime("%Y%m%d"))
        try:
            prices = fetch_price_series(tk, start, end)
            d = price_distribution(prices, trading_days=trd)
            result("N (observations)",     d.n,                "")
            result("Annual vol (hist)",    d.annual_vol*100,   "%")
            result("Skewness",             d.skewness,         "")
            result("Excess kurtosis",      d.excess_kurtosis,  "")
            result("Skew T-ratio",         d.t_ratio_skewness, "")
            result("Kurt T-ratio",         d.t_ratio_kurtosis, "")
            result("Skew ~ normal?",       "YES" if d.skew_normal else "NO (significant)")
            result("Kurt ~ normal?",       "YES" if d.kurt_normal else "NO (fat tails)")
        except Exception as e:
            if HAS_RICH: console.print(f"  [red]Failed: {e}[/]")
            else: print(f"  Failed: {e}")

    section(f"B. Lognormal Probability at Expiry ({days}d)")
    target = inp("Target price", default=round(spot*1.10, 2), cast=float)
    pa, pb = prob_at_expiry(spot, target, days, vol, mu, trd)
    result(f"P(above {target} at expiry)", pa*100, "%")
    result(f"P(below {target} at expiry)", pb*100, "%")

    section("C. Any-Time Barrier Probability")
    barrier = inp("Barrier price (any-time touch)", default=round(spot*1.10, 2), cast=float)
    obs_day = inp("Observations per day (1=daily, 390=intraday)", default=1, cast=float)
    pt = prob_touch_any_time(spot, barrier, days, vol, mu, trd, obs_day)
    result(f"P(touch {barrier} at any time)", pt*100, "%")

    section("D. Inverse — Spot at Given Probability")
    prob_tgt = inp("Target probability (e.g. 0.25)", default=0.25, cast=float)
    ptype    = inp("Type: at Expiry [E] or Any-Time [A]", default="A").upper()
    h = spot_at_probability(spot, prob_tgt, days, vol, mu, trd, ptype)
    result(f"Spot with {prob_tgt:.0%} {ptype}-prob", h, "$")

    section("E. Full MC Probability Engine")
    upper    = inp("Upper target price", default=round(spot*1.20, 2), cast=float)
    lower    = inp("Lower target price", default=round(spot*0.80, 2), cast=float)
    def_div = estimate_dividend_yield(tk)
    div_label = f"Dividend yield [ThetaData={def_div:.4f}]"
    div      = inp(div_label, default=def_div, cast=float)
    n_sims   = inp("MC iterations", default=50_000, cast=int)

    r = mc_probabilities(MCProbInputs(
        spot=spot, upper=upper, lower=lower, days=days,
        vol=vol, mu=mu, div_yield=div,
        n_sims=n_sims, prices_per_day=int(obs_day), trading_days=trd,
    ))
    result(f"Above {upper:.2f} at expiry",      r.above_upper_at_expiry*100, "%")
    result(f"Above {upper:.2f} at any time",    r.above_upper_any_time*100,  "%")
    result(f"Below {lower:.2f} at expiry",      r.below_lower_at_expiry*100, "%")
    result(f"Below {lower:.2f} at any time",    r.below_lower_any_time*100,  "%")
    result("Touching either target",             r.touching_either*100,       "%")
    result("Touching neither target",            r.touching_neither*100,      "%")
    result("Touching both targets",              r.touching_both*100,         "%")
    result("Average end price",                  r.avg_end_price,             "$")

    section("F. Lognormal Distribution Table")
    n_pts = inp("Number of price points in table", default=20, cast=int)
    tbl = lognormal_dist(spot, days, vol, mu, trd, n_points=n_pts)
    result_table(
        ["Price","P(price at bucket)","P(below)","P(above)"],
        [(f"${e.price:,.2f}", f"{e.prob_at:.6f}", f"{e.prob_below:.4f}", f"{e.prob_above:.4f}")
         for e in tbl],
        f"Lognormal Distribution — {tk} over {days}d"
    )


# ══════════════════════════════════════════════════════════════════════════════
# MODULE 10 — Hedge Optimizer (min-variance QP)
# ══════════════════════════════════════════════════════════════════════════════

def run_hedge_optimizer():
    from var_engine.hedge_optimizer import (
        HedgeInstrument, HedgeOptimizerInputs, min_var_hedge
    )
    header("10. Hedge Optimizer — Minimum-Variance QP")

    section("Positions to Hedge")
    n = inp("Number of positions", default=2, cast=int)
    names, pos_list, vols_list = [], [], []
    for i in range(n):
        nm  = inp(f"  Position {i+1} name", default=f"Asset{i+1}")
        pv  = inp(f"  Position ($)", default=1_000_000, cast=float)
        vol = inp(f"  Annualised vol", default=0.25, cast=float)
        names.append(nm); pos_list.append(pv); vols_list.append(vol)

    corr = inp_corr_matrix(names)
    vols_arr = np.array(vols_list)
    cov_matrix = np.diag(vols_arr) @ corr @ np.diag(vols_arr)

    section("Hedge Instruments")
    n_hedge = inp("Number of hedge instruments", default=1, cast=int)
    hedges = []
    for i in range(n_hedge):
        hn   = inp(f"  Hedge {i+1} name", default=f"HEDGE{i+1}")
        hvol = inp(f"  Hedge {i+1} annualised vol", default=0.18, cast=float)
        hbeta= inp(f"  Hedge {i+1} beta to positions", default=1.0, cast=float)
        corr_to_pos = []
        for nm in names:
            c = inp(f"    corr({hn},{nm})", default=0.5, cast=float)
            corr_to_pos.append(float(np.clip(c, -0.999, 0.999)))
        hedges.append(HedgeInstrument(
            name=hn, volatility=hvol,
            correlation_to_positions=np.array(corr_to_pos), beta=hbeta,
        ))

    section("VaR Parameters")
    var_days = inp("VaR horizon (trading days)", default=10, cast=int)
    trd      = inp("Trading days per year", default=252, cast=int)
    conf     = inp("Confidence level (e.g. 0.99)", default=0.99, cast=float)

    r = min_var_hedge(HedgeOptimizerInputs(
        positions=np.array(pos_list), cov_matrix=cov_matrix,
        hedge_instruments=hedges, var_horizon=var_days,
        trading_days=trd, confidence=conf,
    ))

    section("Results")
    result("Base VaR",            r.base_var,          "$")
    result("Hedged VaR",          r.hedged_var,        "$")
    result("VaR reduction",       r.var_reduction_pct,  "%")
    result("Base port vol",       r.base_port_vol,      "")
    result("Hedged port vol",     r.hedged_port_vol,    "")
    result_table(
        ["Hedge Instrument", "Optimal Weight"],
        [(hn, f"{w:,.4f}") for hn, w in zip(r.hedge_names, r.optimal_weights)],
        "Optimal Hedge Weights"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main menu
# ══════════════════════════════════════════════════════════════════════════════

MODULES = [
    ("Correlated Simulation",                run_corr_sim),
    ("Monte Carlo (partial vs full reval)",  run_mc_sim),
    ("Historical Simulation (basic/HW/FHS)", run_hist_sim),
    ("Copula VaR",                           run_copulas),
    ("VaR with Forex exposures",             run_forex_var),
    ("Cash Flow Mapping",                    run_cashflow_map),
    ("Stress Testing",                       run_stress_test),
    ("VaR Aggregation (EWMA + PCA)",         run_var_agg),
    ("Price Distribution + Probability Calc",run_price_dist),
    ("Hedge Optimizer (min-variance QP)",    run_hedge_optimizer),
]

def menu():
    if HAS_RICH:
        console.print(Panel("[bold white]VaR Tools — Python Engine[/]", expand=False))
    else:
        print("\nVaR Tools — Python Engine\n" + "-"*40)
    for i, (name, _) in enumerate(MODULES, 1):
        if HAS_RICH:
            console.print(f"  [white]{i:2d}. {name}[/]")
        else:
            print(f"  {i:2d}. {name}")
    print("   A. Run ALL")
    print("   Q. Quit\n")
    choice = input("Select module: ").strip().upper()
    if choice == "Q":
        return
    if choice == "A":
        for _, fn in MODULES:
            try: fn()
            except KeyboardInterrupt: break
            except Exception as e:
                if HAS_RICH: console.print(f"[red]Error: {e}[/]")
                else: print(f"Error: {e}")
        return
    try:
        idx = int(choice) - 1
        _, fn = MODULES[idx]
        fn()
    except (ValueError, IndexError):
        print("Invalid choice.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", type=int, default=None, help="Run module 1-9 directly")
    parser.add_argument("--context", type=str, default=None, help="Run non-interactive from suite context JSON")
    parser.add_argument("--context-out", type=str, default=None, help="Write standardized context-mode output JSON")
    args = parser.parse_args()
    if args.context:
        raise SystemExit(run_context_mode(args.context, args.context_out, args.module))
    elif args.module and 1 <= args.module <= len(MODULES):
        MODULES[args.module-1][1]()
    else:
        while True:
            try:
                menu()
                again = input("\nRun another? [Y/n]: ").strip().lower()
                if again == "n":
                    break
            except KeyboardInterrupt:
                break
