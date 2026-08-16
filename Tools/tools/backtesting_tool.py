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
from typing import Any, Dict, Optional

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

from Tools.registry import ToolSpec  # noqa: E402

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


_DEALER_SIGN_MODELS = {'v1', 'v2_live', 'dealer_exposure', 'all'}


def run_dealer_gamma_study(context: Dict[str, Any]) -> Dict[str, Any]:
    import backtest_stage3 as bs3

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("mode='dealer_gamma_study' requires a ticker "
                          "(context['ticker'] or context.focus.ticker)")

    sign_model = str(context.get("sign_model") or "all").strip().lower()
    if sign_model not in _DEALER_SIGN_MODELS:
        raise ValueError(
            f"sign_model must be one of {sorted(_DEALER_SIGN_MODELS)}; got {sign_model!r}")

    expiration = context.get("expiration") or _iso_to_compact(focus.get("expiration_date"))
    target_years = float(context.get("target_years", focus.get("target_years", 0.25)))
    lookback_days = int(context.get("lookback_days", bs3.DEFAULT_LOOKBACK_DAYS))
    forward_window_days = int(context.get("forward_window_days", bs3.DEFAULT_FORWARD_WINDOW_DAYS))

    result = bs3.run_backtest(
        ticker,
        expiration=expiration,
        target_years=target_years,
        lookback_days=lookback_days,
        forward_window_days=forward_window_days,
    )
    return {
        "mode": "dealer_gamma_study",
        "sign_model": sign_model,
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


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict (see
    context_loader.load_context), plus a required "mode" key selecting
    which of the two independent backtests to run:

      mode="dealer_gamma_study" -- optional overrides: ticker, expiration
        (YYYYMMDD or ISO), target_years, lookback_days, forward_window_days.
        Falls back to context.focus.ticker / .expiration_date / .target_years.

      mode="strategy_pnl" -- requires entry_date; optional strategy (a
        single strategy dict) or strategy_index into context["strategies"];
        optional exit_date (default: hold to expiration), expiry (falls
        back to context.focus.expiration_date), contract_multiplier.

    Returns a dict with "mode", a human-readable "report" string, and the
    full "result" (the wrapped function's dataclass, as a plain dict).
    """
    mode = context.get("mode")
    if mode == "dealer_gamma_study":
        return run_dealer_gamma_study(context)
    elif mode == "strategy_pnl":
        return run_strategy_pnl(context)
    else:
        raise ValueError(
            f"backtesting_tool requires context['mode'] to be "
            f"'dealer_gamma_study' or 'strategy_pnl'; got {mode!r}"
        )


TOOL_SPEC = ToolSpec(
    name="Backtesting Tool",
    slug="backtesting",
    description=(
        "Two backtests in one tool: a dealer-gamma-sign realized-vol study "
        "(v1 oi_heuristic vs v2 vol_surface_replication), and a multi-leg "
        "strategy P&L simulation from an entry date to an exit/expiration "
        "date. Select via context['mode']."
    ),
    run=run,
)
