"""options_strategy_tool.py

Wraps Vol_Suite/strategy_recommender.py (StrategyRecommender, recommend(),
format_strategies_artifact()) as a standalone Tool that can be pointed at
ANY suite's suite_context.json, not just a Vol_Suite run.

Two modes, selected via context["mode"]:

  "cached" (default) -- reads Vol_Suite's options_chain_scanner.py's
  ALREADY-COMPUTED chain_strategies.json out of the context's output
  directory. That file is exactly format_strategies_artifact()'s return
  shape (confirmed against volatility_suite.py's _run_core_analysis Step 5
  and options_chain_scanner.run_chain_scanner, which builds chain_data via
  _extract_chain_data_from_df, edge_strikes via _transform_edge_strikes,
  and calls StrategyRecommender(...).recommend() + format_strategies_artifact
  itself, writing the result to <output_dir>/chain_strategies.json). This
  mode is instant and needs no network/data-vendor access -- it is just
  reading back what a prior suite run already produced.

  "live" -- re-runs options_chain_scanner.run_chain_scanner() for the
  context's focus ticker/expiration/target_years, which pulls a fresh
  option chain (ThetaData), rebuilds chain_data/edge_strikes/vol_regime/
  current_price/expiry_days, and calls StrategyRecommender(...).recommend()
  + format_strategies_artifact() exactly as volatility_suite.py's own
  pipeline does. Requires ThetaData access; used when the operator wants
  an up-to-date recommendation rather than whatever was cached at the time
  the suite last ran.

Either mode returns format_strategies_artifact()'s dict verbatim, so a
caller does not need to know which mode produced it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

from Tools.spec import ToolSpec

CHAIN_STRATEGIES_FILENAME = "chain_strategies.json"


def _resolve_output_dir(context: dict[str, Any]) -> Path:
    """Prefer an explicit override (useful when the context's own
    output_dir was recorded on a different machine/OS than the one this
    tool is running on -- see context_loader's docstring on that same
    cross-platform gap), else fall back to the context's own output_dir.
    """
    override = context.get("_output_dir_override")
    raw = override or context.get("output_dir")
    if not raw:
        raise ValueError(
            "context has no usable output_dir (and no _output_dir_override "
            "was supplied) -- cannot locate chain-scan artifacts."
        )
    return Path(raw)


def _run_cached(context: dict[str, Any]) -> dict[str, Any]:
    output_dir = _resolve_output_dir(context)
    artifact_path = output_dir / CHAIN_STRATEGIES_FILENAME
    if not artifact_path.is_file():
        raise FileNotFoundError(
            f"No {CHAIN_STRATEGIES_FILENAME} found in {output_dir} -- this suite "
            f"run either didn't include an options chain scan, or hasn't "
            f"reached that step yet. Try mode='live' to run a fresh scan, "
            f"or point at a different run's output_dir."
        )
    with artifact_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _run_live(context: dict[str, Any]) -> dict[str, Any]:
    # Imported lazily -- pulls in ThetaDataController and friends, which
    # only need to exist/succeed when this mode is actually used.
    import options_chain_scanner as ocs

    focus = context.get("focus") or {}
    # The desk sends a flat scope (ticker / expiry); a suite_context carries
    # focus.ticker / focus.expiration_date. Accept both.
    ticker = focus.get("ticker") or context.get("ticker")
    if not ticker:
        raise ValueError(
            "a ticker is required for a live strategy scan (set one in the scope bar)"
        )
    expiration_date = focus.get("expiration_date") or context.get("expiry")
    if str(expiration_date or "").strip().lower() in ("", "auto"):
        expiration_date = None
    target_years = float(focus.get("target_years", context.get("target_years") or 0.25))
    output_dir = _resolve_output_dir(context)
    output_dir.mkdir(parents=True, exist_ok=True)

    _files, _interp, scan_result = ocs.run_chain_scanner(
        ticker, target_years, expiration=expiration_date, output_dir=str(output_dir)
    )

    # run_chain_scanner already wrote <output_dir>/chain_strategies.json
    # (format_strategies_artifact's dict) as a side effect -- read it back
    # rather than reconstructing the artifact a second time, so this mode
    # returns byte-for-byte what's now sitting on disk.
    artifact_path = output_dir / CHAIN_STRATEGIES_FILENAME
    with artifact_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # The artifact names an expiry but not the underlying; a strategy can't be
    # added to the console book as option legs without knowing which one.
    data.setdefault("ticker", str(ticker).upper())
    return data


def run(context: dict[str, Any]) -> dict[str, Any]:
    """context: a validated suite_context.json dict (see
    context_loader.load_context), optionally carrying:
      - "mode": "cached" (default) or "live"
      - "_output_dir_override": str -- filesystem path to use instead of
        context["output_dir"], for when that recorded path belongs to a
        different machine/OS than the one running this tool.

    Returns format_strategies_artifact()'s dict: version, timestamp,
    chain_verdict, vol_regime, current_price, expiration_date, strategies
    (list of strategy dicts), summary.
    """
    mode = context.get("mode")
    if not mode:
        # "cached" was the unconditional default, but a desk run mints a fresh
        # output_dir that never holds a prior chain_strategies.json -- so every
        # card run failed with FileNotFoundError. Read the cache only when
        # there IS one; otherwise scan live.
        raw = context.get("_output_dir_override") or context.get("output_dir")
        has_cache = bool(raw) and (Path(raw) / CHAIN_STRATEGIES_FILENAME).is_file()
        mode = "cached" if has_cache else "live"
    if mode == "cached":
        data = _run_cached(context)
        ticker = (context.get("focus") or {}).get("ticker") or context.get("ticker")
        if ticker and isinstance(data, dict):
            data.setdefault("ticker", str(ticker).upper())
        return data
    elif mode == "live":
        return _run_live(context)
    else:
        raise ValueError(f"Unknown mode {mode!r}; expected 'cached' or 'live'")


TOOL_SPEC = ToolSpec(
    name="Options Strategy Tool",
    slug="options-strategy",
    description=(
        "Recommends multi-leg options strategies (call/put spreads, "
        "straddles, strangles, iron condors, collars) from a suite's "
        "vol-regime and edge-strike chain scan. Reads a prior run's cached "
        "chain_strategies.json by default, or can re-scan live."
    ),
    run=run,
)
