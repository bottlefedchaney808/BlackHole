"""backtesting_tool.py

Wraps Vol_Suite/backtest_stage3.py as a standalone Tool with TWO distinct
backtest modes -- the module has two genuinely independent capabilities
bolted together (see backtest_stage3.py's own comment above
StrategyBacktestResult: "This is intentionally independent of DayRecord/
BacktestResult and every function above it"), so this tool exposes both
rather than picking one:

  mode="dealer_gamma_study" -- wraps run_backtest() / format_backtest_report():
  the Stage 3 dealer-gamma-sign validation study. For each trading day in a
  lookback window, classifies the dealer book as net long/short gamma under
  both the v1 (oi_heuristic) and v2 (vol_surface_replication) sign
  conventions, then Welch's-t-tests forward realized vol between the two
  regimes. Answers "does gamma sign predict subsequent realized vol, and
  does v2's convention track that better than v1's?" -- not tied to any
  particular strategy.

  mode="strategy_pnl" -- wraps run_strategy_backtest() / 
  format_strategy_backtest_report(): simulates a specific multi-leg
  strategy's P&L from an entry date to an exit date (default: hold to
  expiration, settled at intrinsic value). Takes a strategy dict shaped
  exactly like one entry of a suite_context.json "strategies" list (or one
  produced fresh by options_strategy_tool.py) -- both carry the same
  strike/instrument_type/quantity leg shape, confirmed against
  backtest_stage3._leg_key.

Both wrapped functions' real signatures were re-read directly from
backtest_stage3.py before wrapping (not assumed) and match what was
expected:
  run_backtest(ticker, expiration=None, target_years=0.25,
               lookback_days=90, forward_window_days=5) -> BacktestResult
  run_strategy_backtest(strategy, ticker, expiry, entry_date,
                         exit_date=None, contract_multiplier=100.0)
               -> StrategyBacktestResult
No signature drift to flag.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import math
from datetime import datetime, timedelta

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.module_registry import ParamSpec

from Tools.spec import ToolSpec  # noqa: E402

_CHAIN_STRATEGIES_FILENAME = "chain_strategies.json"


def _resolve_strategies(context: Dict[str, Any]) -> list:
    """Resolve the strategy list to backtest.

    The chain scanner writes its recommended strategies to
    <output_dir>/chain_strategies.json (format_strategies_artifact's shape),
    NOT into suite_context.json -- so a context's own `strategies` key is
    usually empty. Prefer the artifact when present, else fall back to the
    context's inline `strategies`.
    """
    strategies = context.get("strategies") or []
    if strategies:
        return strategies
    out_dir = context.get("_output_dir_override") or context.get("output_dir")
    if out_dir:
        artifact = Path(out_dir) / _CHAIN_STRATEGIES_FILENAME
        if artifact.is_file():
            try:
                data = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
            return data.get("strategies") or []
    return []


def _iso_to_compact(date_str: Optional[str]) -> Optional[str]:
    """suite_context.json stores expiration_date as ISO 'YYYY-MM-DD'
    (suite_context._normalize_expiration always stores ISO); backtest_stage3
    and expiry_selector both work in compact 'YYYYMMDD'. Convert once here
    rather than making every caller remember which format which function
    wants.
    """
    if not date_str:
        return None
    if "-" in date_str:
        return date_str.replace("-", "")
    return date_str


def _row_date(row: Dict[str, Any]) -> Optional[str]:
    """Extract a compact 'YYYYMMDD' trading date from an EOD history row.

    ThetaData history endpoints label the date field inconsistently: some
    rows carry a bare ``date`` (compact or ISO), others only ``created`` /
    ``last_trade`` (ISO timestamps). Try each, in order, and normalize to
    compact form so callers can key on it uniformly.
    """
    for key in ("date", "created", "last_trade"):
        v = row.get(key)
        if not v:
            continue
        s = str(v).strip()
        if len(s) == 8 and s.isdigit():
            return s
        s2 = s.replace("T", " ")
        if "-" in s2:
            return s2[:10].replace("-", "")
    return None


_DEALER_MODEL_ALIASES = {
    # -> 'live'  : the merged live dealer-frame engine (expiry_book GEX) runs.
    'live': 'live', 'dealer_exposure': 'live', 'dealer': 'live', 'all': 'live',
    # -> 'legacy': live engine OFF -- the old sign conventions only (v1/v2_live).
    'legacy': 'legacy', 'v1': 'legacy', 'v2_live': 'legacy',
}


def run_dealer_gamma_study(context: Dict[str, Any]) -> Dict[str, Any]:
    import backtest_stage3 as bs3

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("mode='dealer_gamma_study' requires a ticker "
                          "(context['ticker'] or context.focus.ticker)")

    expiration = context.get("expiration") or _iso_to_compact(focus.get("expiration_date"))
    target_years = float(context.get("target_years", focus.get("target_years", 0.25)))
    lookback_days = int(context.get("lookback_days", bs3.DEFAULT_LOOKBACK_DAYS))
    forward_window_days = int(context.get("forward_window_days", bs3.DEFAULT_FORWARD_WINDOW_DAYS))

    # The MODEL selector picks whether the LIVE dealer-frame engine (the merged
    # expiry_book GEX model) runs. 'live' (default) turns it on; 'legacy' turns
    # it off and keeps only the old sign conventions. v1/v2_live are always
    # computed by the study and are retained purely as legacy comparison arms
    # (see dealer-model-adoption: comparison arms stay in backtests, production
    # uses the live model only).
    raw_model = str(context.get("sign_model") or "live").strip().lower()
    if raw_model not in _DEALER_MODEL_ALIASES:
        raise ValueError(
            f"mode='dealer_gamma_study' sign_model must be one of "
            f"{sorted(_DEALER_MODEL_ALIASES)}; got {raw_model!r}")
    model = _DEALER_MODEL_ALIASES[raw_model]
    sign_model = 'live' if model == 'live' else 'legacy'

    result = bs3.run_backtest(
        ticker,
        expiration=expiration,
        target_years=target_years,
        lookback_days=lookback_days,
        forward_window_days=forward_window_days,
        accumulate=True,
        sign_model=sign_model,
    )
    return {
        "mode": "dealer_gamma_study",
        "sign_model": model,
        "report": bs3.format_backtest_report(result),
        "result": dataclasses.asdict(result),
    }


def run_strategy_pnl(context: Dict[str, Any]) -> Dict[str, Any]:
    import backtest_stage3 as bs3

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("mode='strategy_pnl' requires a ticker "
                          "(context['ticker'] or context.focus.ticker)")

    strategy = context.get("strategy")
    if strategy is None:
        strategies = _resolve_strategies(context)
        strategy_index = int(context.get("strategy_index", 0))
        if not strategies:
            raise ValueError(
                "mode='strategy_pnl' requires a 'strategy' dict, or a "
                "non-empty context['strategies'] list plus 'strategy_index' "
                "(defaults to 0) to pick one from."
            )
        if strategy_index >= len(strategies):
            raise ValueError(
                f"strategy_index {strategy_index} out of range for "
                f"{len(strategies)} strategies"
            )
        strategy = strategies[strategy_index]

    expiry = context.get("expiry") or _iso_to_compact(focus.get("expiration_date"))
    if not expiry:
        raise ValueError("mode='strategy_pnl' requires 'expiry' (or "
                          "context.focus.expiration_date) in YYYYMMDD/ISO form")

    entry_date = context.get("entry_date")
    if not entry_date:
        raise ValueError("mode='strategy_pnl' requires 'entry_date' (YYYYMMDD)")
    entry_date = _iso_to_compact(entry_date)

    exit_date = _iso_to_compact(context.get("exit_date"))
    contract_multiplier = float(context.get("contract_multiplier", 100.0))

    result = bs3.run_strategy_backtest(
        strategy, ticker, expiry, entry_date,
        exit_date=exit_date, contract_multiplier=contract_multiplier,
    )
    return {
        "mode": "strategy_pnl",
        "report": bs3.format_strategy_backtest_report(result),
        "result": dataclasses.asdict(result),
    }


# ---------------------------------------------------------------------------
# mode="stock_strategy_backtest" -- a standard, directionally-honest stock
# backtest. Pulls the underlying's daily closes over a window and evaluates
# one of three rules -- buy_hold, sma_cross (long when the fast SMA is above
# the slow SMA, flat otherwise), or momentum (long while the trailing N-day
# return is positive) -- producing the strategy's daily equity curve plus the
# usual metrics (total/annualized return, max drawdown, annualized vol,
# Sharpe, daily win-rate, number of entries) alongside a buy-and-hold
# benchmark over the identical window. The numeric core is pure (it takes a
# close series + a position series) so it is unit-testable offline.
#
# mode="option_strategy_backtest" -- a standard options-strategy backtest.
# Builds a standard multi-leg strategy (long_call, long_put, covered_call,
# protective_put, long_straddle, long_strangle) at an entry date against a
# chosen expiry, then prices it out to an exit date (default: hold to
# expiration, option legs settled at intrinsic value off the underlying's
# close). Unlike the option-only engine in backtest_stage3, this one handles
# the stock leg of covered_call / protective_put correctly (shares marked to
# close on both sides, option settled at intrinsic at/past expiry, so a
# covered call nets to its strike cap at expiration).
# ---------------------------------------------------------------------------

_OPTION_MULT = 100.0
_STOCK_SHARES = 100.0  # one option contract controls 100 shares


def _stock_daily_metrics(dates: List[str], equity: List[float],
                         rf_annual: float = 0.0) -> Dict[str, Any]:
    """Pure metrics over a daily equity curve. `equity`[i] is the account
    value at close of `dates`[i], starting from equity[0]. Returns the
    standard risk/return stats computed off the *simple* daily returns."""
    if len(equity) < 2:
        return {"total_return_pct": 0.0, "annualized_return_pct": 0.0,
                "max_drawdown_pct": 0.0, "annualized_vol_pct": 0.0,
                "sharpe": None, "daily_win_rate": None, "num_days": len(equity)}
    returns = [(equity[i] / equity[i - 1] - 1.0) for i in range(1, len(equity))]
    total_return = equity[-1] / equity[0] - 1.0
    n = len(returns)
    years = n / 252.0
    if years > 0 and equity[0] > 0:
        annualized_return = (equity[-1] / equity[0]) ** (1.0 / years) - 1.0
    else:
        annualized_return = 0.0
    # max drawdown from the running peak
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
    mean_r = sum(returns) / n
    if n > 1:
        var = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    else:
        var = 0.0
    std_r = math.sqrt(var)
    annualized_vol = std_r * math.sqrt(252.0)
    excess = mean_r - (rf_annual / 252.0)
    sharpe = (excess / std_r * math.sqrt(252.0)) if std_r > 0 else None
    positives = sum(1 for r in returns if r > 0)
    win_rate = positives / n
    return {
        "total_return_pct": round(total_return * 100.0, 3),
        "annualized_return_pct": round(annualized_return * 100.0, 3),
        "max_drawdown_pct": round(max_dd * 100.0, 3),
        "annualized_vol_pct": round(annualized_vol * 100.0, 3),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "daily_win_rate": round(win_rate, 4),
        "num_days": len(equity),
    }


def _sma_cross_positions(closes: List[float], fast: int, slow: int) -> List[int]:
    """1 (long) when the fast SMA is above the slow SMA, else 0 (flat).
    Positions are decided on the close of the current bar and applied to the
    NEXT bar's return (no lookahead): position[i] multiplies return[i+1]."""
    n = len(closes)
    pos = [0] * n
    for i in range(slow, n):
        fast_sma = sum(closes[i - fast + 1:i + 1]) / fast
        slow_sma = sum(closes[i - slow + 1:i + 1]) / slow
        pos[i] = 1 if fast_sma > slow_sma else 0
    return pos


def _momentum_positions(closes: List[float], lookback: int) -> List[int]:
    """1 (long) when the trailing `lookback`-day return is positive, else 0.
    Same no-lookahead convention as _sma_cross_positions."""
    n = len(closes)
    pos = [0] * n
    for i in range(lookback, n):
        pos[i] = 1 if closes[i] > closes[i - lookback] else 0
    return pos


def _apply_positions(closes: List[float], positions: List[int],
                     initial: float = 100.0) -> List[float]:
    """Equity curve from a position series. position[i] is decided on bar i's
    close and earns bar i+1's return (no lookahead). equity[0]=initial."""
    equity = [initial]
    for i in range(len(closes) - 1):
        ret = closes[i + 1] / closes[i] - 1.0
        equity.append(equity[-1] * (1.0 + positions[i] * ret))
    return equity


def _count_entries(positions: List[int]) -> int:
    return sum(1 for i in range(1, len(positions))
               if positions[i] == 1 and positions[i - 1] == 0)


def run_stock_strategy_backtest(context: Dict[str, Any]) -> Dict[str, Any]:
    from shared.thetadata import ThetaDataController

    ticker = context.get("ticker")
    if not ticker:
        raise ValueError("mode='stock_strategy_backtest' requires a ticker")
    strategy = str(context.get("strategy", "sma_cross")).strip().lower()
    if strategy not in ("buy_hold", "sma_cross", "momentum"):
        raise ValueError(
            f"mode='stock_strategy_backtest' strategy must be one of "
            f"[buy_hold, sma_cross, momentum]; got {strategy!r}")
    fast = int(context.get("fast_window", 20))
    slow = int(context.get("slow_window", 50))
    momentum_lookback = int(context.get("momentum_lookback", 20))
    rf_annual = float(context.get("risk_free_rate", 0.0) or 0.0)

    end_date = _iso_to_compact(context.get("end_date"))
    start_date = _iso_to_compact(context.get("start_date"))
    if not start_date or not end_date:
        lookback_days = int(context.get("lookback_days", 252))
        end_dt = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.now()
        start_dt = end_dt - timedelta(days=lookback_days)
        if not start_date:
            start_date = start_dt.strftime("%Y%m%d")
        if not end_date:
            end_date = end_dt.strftime("%Y%m%d")

    td = ThetaDataController()
    try:
        rows = td.hist_stock_eod(ticker, start_date, end_date)
    finally:
        td.close()

    closes: Dict[str, float] = {}
    for row in rows:
        d = _row_date(row)
        try:
            c = float(row.get('close', 0) or 0)
        except (TypeError, ValueError):
            continue
        if d and c > 0:
            closes[d] = c
    dates = sorted(closes)
    if len(dates) < max(slow, momentum_lookback, 5):
        raise ValueError(
            f"stock_strategy_backtest: only {len(dates)} trading days of closes "
            f"for {ticker}; need at least {max(slow, momentum_lookback, 5)}. "
            f"Widen the window.")
    close_series = [closes[d] for d in dates]

    if strategy == "buy_hold":
        positions = [1] * len(dates)
    elif strategy == "sma_cross":
        positions = _sma_cross_positions(close_series, fast, slow)
    else:
        positions = _momentum_positions(close_series, momentum_lookback)

    strat_equity = _apply_positions(close_series, positions)
    bh_equity = _apply_positions(close_series, [1] * len(dates))

    strat_metrics = _stock_daily_metrics(dates, strat_equity, rf_annual)
    bh_metrics = _stock_daily_metrics(dates, bh_equity, rf_annual)
    alpha = (strat_metrics["total_return_pct"]
             - bh_metrics["total_return_pct"])

    lines = [
        f"STOCK BACKTEST  {ticker}  [{start_date}..{end_date}]  strategy={strategy}",
        f"  window: {strat_metrics['num_days']} trading days, "
        f"{dates[0]} .. {dates[-1]}",
        f"  strategy  total={strat_metrics['total_return_pct']:+.2f}%  "
        f"ann={strat_metrics['annualized_return_pct']:+.2f}%  "
        f"maxDD={strat_metrics['max_drawdown_pct']:.2f}%  "
        f"Sharpe={strat_metrics['sharpe']}  vol={strat_metrics['annualized_vol_pct']:.2f}%",
        f"  buy&hold  total={bh_metrics['total_return_pct']:+.2f}%  "
        f"ann={bh_metrics['annualized_return_pct']:+.2f}%  "
        f"maxDD={bh_metrics['max_drawdown_pct']:.2f}%  "
        f"Sharpe={bh_metrics['sharpe']}",
        f"  alpha (strategy - buy&hold, total): {alpha:+.2f} pp",
    ]
    if strategy in ("sma_cross", "momentum"):
        lines.append(f"  entries (flat->long flips): {_count_entries(positions)}")
        lines.append(f"  time in market: "
                     f"{sum(1 for p in positions if p) / len(positions) * 100:.1f}%")

    return {
        "mode": "stock_strategy_backtest",
        "ticker": ticker,
        "strategy": strategy,
        "start_date": start_date,
        "end_date": end_date,
        "report": "\n".join(lines),
        "result": {
            "strategy": strat_metrics,
            "benchmark_buy_and_hold": bh_metrics,
            "alpha_total_pp": round(alpha, 3),
            "entries": _count_entries(positions),
            "dates": dates,
            "strategy_equity": strat_equity,
            "benchmark_equity": bh_equity,
            "positions": positions,
        },
    }


def _pick_strike(strikes: List[float], target: float,
                 at_least: bool = False, at_most: bool = False) -> Optional[float]:
    """Nearest available strike to `target` (or >= / <= when requested)."""
    if not strikes:
        return None
    if at_least:
        cands = [s for s in strikes if s >= target - 1e-9]
        return min(cands) if cands else max(strikes)
    if at_most:
        cands = [s for s in strikes if s <= target + 1e-9]
        return max(cands) if cands else min(strikes)
    return min(strikes, key=lambda s: abs(s - target))


def _build_option_strategy_legs(strategy_type: str, entry_spot: float,
                                strikes: List[float],
                                strike: Optional[float],
                                otm: Optional[float]) -> List[Dict[str, Any]]:
    """Build the legs of a standard options strategy as a list of
    {kind, strike, right, quantity, multiplier}. `kind` is 'stock', 'call',
    or 'put'. Stock legs carry quantity=_STOCK_SHARES, multiplier=1 (they are
    marked to the underlying close on both sides)."""
    if not strikes:
        raise ValueError("no available strikes for the chosen expiry")
    atm = strike if strike else round(entry_spot)

    def k(v: float, at_least=False, at_most=False) -> float:
        return _pick_strike(strikes, v, at_least=at_least, at_most=at_most)

    legs: List[Dict[str, Any]] = []
    if strategy_type == "long_call":
        legs.append({"kind": "call", "strike": k(atm), "right": "C",
                     "quantity": 1, "multiplier": _OPTION_MULT})
    elif strategy_type == "long_put":
        legs.append({"kind": "put", "strike": k(atm), "right": "P",
                     "quantity": 1, "multiplier": _OPTION_MULT})
    elif strategy_type == "covered_call":
        legs.append({"kind": "stock", "strike": None, "right": None,
                     "quantity": _STOCK_SHARES, "multiplier": 1.0})
        legs.append({"kind": "call", "strike": k(atm), "right": "C",
                     "quantity": -1, "multiplier": _OPTION_MULT})
    elif strategy_type == "protective_put":
        legs.append({"kind": "stock", "strike": None, "right": None,
                     "quantity": _STOCK_SHARES, "multiplier": 1.0})
        legs.append({"kind": "put", "strike": k(atm), "right": "P",
                     "quantity": 1, "multiplier": _OPTION_MULT})
    elif strategy_type == "long_straddle":
        kk = k(atm)
        legs.append({"kind": "call", "strike": kk, "right": "C",
                     "quantity": 1, "multiplier": _OPTION_MULT})
        legs.append({"kind": "put", "strike": kk, "right": "P",
                     "quantity": 1, "multiplier": _OPTION_MULT})
    elif strategy_type == "long_strangle":
        width = otm if otm is not None else max(1.0, 0.05 * entry_spot)
        kc = k(entry_spot + width, at_least=True)
        kp = k(entry_spot - width, at_most=True)
        legs.append({"kind": "call", "strike": kc, "right": "C",
                     "quantity": 1, "multiplier": _OPTION_MULT})
        legs.append({"kind": "put", "strike": kp, "right": "P",
                     "quantity": 1, "multiplier": _OPTION_MULT})
    else:
        raise ValueError(f"unknown option strategy {strategy_type!r}")
    return legs


def run_option_strategy_backtest(context: Dict[str, Any]) -> Dict[str, Any]:
    from shared.thetadata import ThetaDataController
    from Vol_Suite.backtest_stage3 import strike_to_theta
    import implied_vol as implied_vol_mod  # mid_price lives in implied_vol
    mid_price = implied_vol_mod.mid_price

    ticker = context.get("ticker")
    if not ticker:
        raise ValueError("mode='option_strategy_backtest' requires a ticker")
    strategy_type = str(context.get("strategy_type", "long_call")).strip().lower()
    valid = ("long_call", "long_put", "covered_call", "protective_put",
             "long_straddle", "long_strangle")
    if strategy_type not in valid:
        raise ValueError(
            f"mode='option_strategy_backtest' strategy_type must be one of "
            f"{list(valid)}; got {strategy_type!r}")

    expiry = _iso_to_compact(context.get("expiry")
                             or (context.get("focus") or {}).get("expiration_date"))
    if not expiry:
        raise ValueError("mode='option_strategy_backtest' requires 'expiry' "
                          "(YYYYMMDD or ISO)")
    entry_date = _iso_to_compact(context.get("entry_date"))
    if not entry_date:
        raise ValueError("mode='option_strategy_backtest' requires 'entry_date'")
    exit_date = _iso_to_compact(context.get("exit_date")) or expiry
    strike = context.get("strike")
    strike = float(strike) if strike else None
    # strangle OTM width in dollars; default None -> resolved to ~5% of spot
    otm = context.get("otm")
    otm = float(otm) if otm is not None else None

    td = ThetaDataController()
    try:
        # Underlying closes over the whole window (for spot-at-entry, the
        # stock leg, and intrinsic settlement at/past expiry).
        start_dt = datetime.strptime(entry_date, "%Y%m%d") - timedelta(days=7)
        end_anchor = min(datetime.strptime(exit_date, "%Y%m%d"),
                         datetime.strptime(expiry, "%Y%m%d"))
        end_dt = end_anchor + timedelta(days=7)
        start_str, end_str = start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")
        spot_by_date: Dict[str, float] = {}
        for row in td.hist_stock_eod(ticker, start_str, end_str):
            d = _row_date(row)
            try:
                c = float(row.get('close', 0) or 0)
            except (TypeError, ValueError):
                continue
            if d and c > 0:
                spot_by_date[d] = c
        if not spot_by_date:
            raise ValueError(f"no underlying closes for {ticker}")
        # entry spot: last close at/before entry_date (entry may be a holiday)
        prior = [d for d in sorted(spot_by_date) if d <= entry_date]
        if not prior:
            raise ValueError(f"no close at/before entry_date {entry_date}")
        entry_spot = spot_by_date[prior[-1]]

        strikes = td.list_strikes(ticker, expiry)
        legs = _build_option_strategy_legs(strategy_type, entry_spot, strikes,
                                           strike, otm)

        # Fetch each option leg's EOD price history (mid price per date).
        option_price: Dict[Tuple[float, str], Dict[str, float]] = {}
        for leg in legs:
            if leg["kind"] == "stock":
                continue
            k_theta = strike_to_theta(float(leg["strike"]))
            by_date: Dict[str, float] = {}
            for row in td.option_hist_eod_single(
                    ticker, expiry, k_theta, leg["right"], start_str, end_str):
                d = _row_date(row)
                px = mid_price(row.get('bid'), row.get('ask'), row.get('close'))
                if d and px is not None:
                    by_date[d] = px
            option_price[(float(leg["strike"]), leg["kind"])] = by_date

        def leg_entry_px(leg):
            if leg["kind"] == "stock":
                return spot_by_date.get(prior[-1])
            return option_price.get((float(leg["strike"]), leg["kind"]), {}).get(entry_date)

        def leg_exit_px(leg):
            if leg["kind"] == "stock":
                # shares held to exit, marked to close (last close at/before exit)
                p = [d for d in sorted(spot_by_date) if d <= exit_date]
                return spot_by_date.get(p[-1]) if p else None
            expired = exit_date >= expiry
            if expired:
                p = [d for d in sorted(spot_by_date) if d <= exit_date]
                spot_exit = spot_by_date.get(p[-1]) if p else None
                if spot_exit is None:
                    return None
                k = float(leg["strike"])
                return max(spot_exit - k, 0.0) if leg["kind"] == "call" \
                    else max(k - spot_exit, 0.0)
            return option_price.get((float(leg["strike"]), leg["kind"]), {}).get(exit_date)

        entry_cost = 0.0
        exit_value = 0.0
        missing = []
        for leg in legs:
            q = float(leg["quantity"])
            m = float(leg["multiplier"])
            ep = leg_entry_px(leg)
            xp = leg_exit_px(leg)
            if ep is None or xp is None:
                missing.append(leg["kind"] + (f" {leg['strike']}" if leg["kind"] != "stock" else ""))
                continue
            entry_cost += q * ep * m
            exit_value += q * xp * m
        if missing:
            raise ValueError(
                f"could not price leg(s) {missing} for {strategy_type} "
                f"(entry {entry_date} / exit {exit_date}). Check the ticker, "
                f"expiry {expiry}, and that the contracts traded.")
        pnl = exit_value - entry_cost
        pnl_pct = (pnl / abs(entry_cost) * 100.0) if entry_cost != 0 else float("nan")
    finally:
        td.close()

    leg_lines = []
    for leg in legs:
        lbl = leg["kind"] + (f" K={leg['strike']:g}" if leg["kind"] != "stock" else "")
        sign = "+" if leg["quantity"] > 0 else "-"
        leg_lines.append(f"    {sign} {abs(leg['quantity']):g} {lbl}")
    report = "\n".join([
        f"OPTION STRATEGY BACKTEST  {ticker}  {strategy_type}",
        f"  expiry={expiry}  entry={entry_date}  exit={exit_date} "
        f"(settle intrinsic={exit_date >= expiry})",
        f"  entry spot={entry_spot:.2f}",
        "  legs:",
        *leg_lines,
        f"  entry cost = {entry_cost:+,.2f}",
        f"  exit value = {exit_value:+,.2f}",
        f"  P&L = {pnl:+,.2f}  ({pnl_pct:+.2f}% of cost)",
    ])
    return {
        "mode": "option_strategy_backtest",
        "ticker": ticker,
        "strategy_type": strategy_type,
        "report": report,
        "result": {
            "ticker": ticker, "strategy_type": strategy_type, "expiry": expiry,
            "entry_date": entry_date, "exit_date": exit_date,
            "entry_spot": entry_spot,
            "legs": legs,
            "entry_cost": entry_cost, "exit_value": exit_value,
            "pnl": pnl, "pnl_pct": pnl_pct,
        },
    }


def run_broker_book_accuracy(context: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate the chain-scan corpus into a convention-free broker-book control
    and backtest its predictive content against forward returns (pooled + cross-
    sectional arms). See broker_book.py for the convention rules and design."""
    from Tools.tools import broker_book
    return broker_book.run_backtest(context)


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict (see
    context_loader.load_context), plus a required "mode" key selecting
    which of the independent backtests to run:

      mode="dealer_gamma_study" -- optional overrides: ticker, expiration
        (YYYYMMDD or ISO), target_years, lookback_days, forward_window_days.
        Falls back to context.focus.ticker / .expiration_date / .target_years.

      mode="strategy_pnl" -- requires entry_date; optional strategy (a
        single strategy dict) or strategy_index into context["strategies"];
        optional exit_date (default: hold to expiration), expiry (falls
        back to context.focus.expiration_date), contract_multiplier.

      mode="stock_strategy_backtest" -- a standard stock backtest over a
        date window: strategy in {buy_hold, sma_cross, momentum}; optional
        start_date/end_date (or lookback_days from today), fast_window,
        slow_window, momentum_lookback, risk_free_rate. Returns the strategy's
        daily equity + risk metrics (total/annualized return, max drawdown,
        annualized vol, Sharpe, win-rate, entries) vs a buy-and-hold benchmark.

      mode="option_strategy_backtest" -- a standard options-strategy backtest:
        strategy_type in {long_call, long_put, covered_call, protective_put,
        long_straddle, long_strangle}; requires expiry (YYYYMMDD/ISO) and
        entry_date; optional exit_date (default: hold to expiration), strike
        (default: nearest to entry spot), otm (strangle width in $, default
        ~5% of spot). Covered calls / protective puts price the stock leg to
        its close and settle the option at intrinsic at/past expiry.

      mode="broker_book_accuracy" -- aggregates every chain-scan CSV under
        orchestrator_output/ and Vol_Suite/outputs/ into convention-free
        broker-book nets, joins ThetaData forward returns, and runs the
        pooled + cross-sectional accuracy backtest. Optional context keys:
        roots, max_files, horizons, fetch_closes (see broker_book.run_backtest).

    Returns a dict with "mode", a human-readable "report" string, and the
    full "result" (the wrapped function's dataclass, as a plain dict).
    """
    mode = context.get("mode")
    if mode == "dealer_gamma_study":
        return run_dealer_gamma_study(context)
    elif mode == "strategy_pnl":
        return run_strategy_pnl(context)
    elif mode == "stock_strategy_backtest":
        return run_stock_strategy_backtest(context)
    elif mode == "option_strategy_backtest":
        return run_option_strategy_backtest(context)
    elif mode == "broker_book_accuracy":
        return run_broker_book_accuracy(context)
    else:
        raise ValueError(
            f"backtesting_tool requires context['mode'] to be one of "
            f"[dealer_gamma_study, strategy_pnl, stock_strategy_backtest, "
            f"option_strategy_backtest, broker_book_accuracy]; got {mode!r}"
        )


TOOL_SPEC = ToolSpec(
    name="Backtesting Tool",
    slug="backtesting",
    description=(
        "Five backtests in one tool: a dealer-gamma-sign realized-vol study "
        "(LIVE expiry_book dealer-frame model by default; 'legacy' mode keeps "
        "the old v1/v2 sign conventions for comparison), a multi-leg strategy "
        "P&L simulation (entry date to exit/expiration), a standard stock "
        "backtest (buy_hold / sma_cross / momentum vs buy-and-hold), a standard "
        "options-strategy backtest (long call/put, covered call, protective "
        "put, straddle, strangle), and a broker-book accuracy study "
        "(convention-free nets vs forward returns). Select via context['mode']."
    ),
    run=run,
    # `mode` has no default in run() -- it dispatches five unrelated
    # backtests and guessing one for you would silently run the wrong study.
    # Declaring it as a choice is what lets a card ask instead of failing.
    params=(
        ParamSpec(
            name="mode",
            label="Backtest",
            kind="choice",
            default="dealer_gamma_study",
            choices=(
                "dealer_gamma_study",
                "strategy_pnl",
                "stock_strategy_backtest",
                "option_strategy_backtest",
                "broker_book_accuracy",
            ),
            help=(
                "Which of the five backtests to run. These are independent "
                "studies, not settings of one study."
            ),
        ),
        ParamSpec(
            name="sign_model",
            label="Dealer sign model",
            kind="choice",
            default="live",
            choices=("live", "legacy"),
            help=(
                "dealer_gamma_study only. 'live' is the production "
                "expiry_book dealer-frame model; 'legacy' keeps the old "
                "v1/v2 sign conventions for comparison."
            ),
        ),
    ),
)
