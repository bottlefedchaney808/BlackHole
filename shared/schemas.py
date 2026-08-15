"""Shared JSON schema validators for all inter-suite artifacts.

Each validator accepts dict data (as parsed from JSON) and raises ValueError
on the first violation.  This module mirrors and supplements the inline
validation in each suite with a central, importable copy suitable for testing
and cross-suite tooling.

The artifacts:
  1. suite_context.json  — schema_version=1  (Vol_Suite → everyone via --context)
  2. options_result.json — no schema_version (Options_Suite → Vol_Suite)
  3. var_result.json     — no schema_version (VaR_Tools_Simulations → Vol_Suite)
  4. Sentiment pack JSON — no schema_version (sentiment-scanner ticker pack)
  5. Sentiment context   — schema_version=2  (sentiment-scanner context export)
  6. vol_result.json     — schema_version=1  (Vol_Suite → orchestrator)
  7. quant_summary.json  — schema_version=1  (dashboard: per-run module summary,
     built by shared/summary.py::build_run_summary() from artifacts 2/3/4/6)
"""

from __future__ import annotations

import warnings
from typing import Any, Dict


# ── helpers ──────────────────────────────────────────────────────────────

def _require(condition: bool, message: str) -> None:
    """Raise ValueError when *condition* is falsy."""
    if not condition:
        raise ValueError(message)


def _is_bool(val: Any) -> bool:
    """``isinstance(val, bool)`` — ``bool`` is a subclass of ``int`` in Python."""
    return isinstance(val, bool)


def _is_number(val: Any) -> bool:
    """True for int or float, False for bool (which is a subclass of int)."""
    return isinstance(val, (int, float)) and not _is_bool(val)


def _warn_missing_schema_version(data: Dict[str, Any], artifact: str) -> None:
    if "schema_version" not in data:
        warnings.warn(
            f"{artifact}: missing 'schema_version' — treating as v1 "
            f"(backward-compatibility path)",
            stacklevel=2,
        )


# ── 0. sentiment block (shared by artifacts 1 and 5) ────────────────────

#: The four fields that make up a sentiment block, in canonical order.  This is
#: the only part of suite_context.json that the orchestrator mutates after the
#: producer stage, so it is factored out here and validated on its own.
SENTIMENT_BLOCK_KEYS = ("manifest_path", "pack_json_path", "group_id",
                        "ranked_tickers")


def validate_sentiment_block(block: Any, strict: bool = False,
                             path: str = "sentiment") -> None:
    """Validate the sentiment block shared by two artifacts.

    It appears embedded in suite_context.json (artifact 1) and as the
    top-level ``sentiment`` object of the sentiment context export
    (artifact 5).  The two differ only in strictness:

    ``strict=False`` — the suite_context flavour.  ``manifest_path`` must be a
    non-empty string; ``pack_json_path`` and ``group_id`` may be null, because
    ``build_suite_context`` fills them in only once a pack exists.

    ``strict=True`` — the context-export flavour.  All three path/id fields
    must be non-empty strings, because the producer has by then actually
    written a pack and has nothing to be uncertain about.

    *path* prefixes the error messages so a caller validating a nested copy
    can say where it lives.  Raises ValueError on the first violation.
    """
    _require(isinstance(block, dict), f"{path} must be an object")

    for key in SENTIMENT_BLOCK_KEYS:
        _require(key in block, f"Missing required field: {path}.{key}")

    _require(isinstance(block["manifest_path"], str) and block["manifest_path"].strip(),
             f"{path}.manifest_path must be a non-empty string")

    if strict:
        _require(isinstance(block["pack_json_path"], str)
                 and block["pack_json_path"].strip(),
                 f"{path}.pack_json_path must be a non-empty string")
        _require(isinstance(block["group_id"], str) and block["group_id"].strip(),
                 f"{path}.group_id must be a non-empty string")
    else:
        _require(block["pack_json_path"] is None
                 or isinstance(block["pack_json_path"], str),
                 f"{path}.pack_json_path must be null or string")
        _require(block["group_id"] is None or isinstance(block["group_id"], str),
                 f"{path}.group_id must be null or string")

    _require(isinstance(block["ranked_tickers"], list),
             f"{path}.ranked_tickers must be a list")


# ── 1. suite_context.json ───────────────────────────────────────────────

def validate_suite_context(data: Dict[str, Any]) -> None:
    """Validate the full suite_context.json structure produced by
    Vol_Suite/suite_context.py::build_suite_context().

    Raises ValueError on first violation.
    """
    _require(isinstance(data, dict), "suite_context must be a JSON object")

    _warn_missing_schema_version(data, "suite_context")

    # ── required top-level keys ──────────────────────────────────────
    for key in ("schema_version", "run_id", "created_at_utc", "output_dir",
                "focus", "basket", "sentiment", "var", "controls", "paths"):
        _require(key in data, f"Missing required field: {key}")

    _require(data["schema_version"] == 1, "schema_version must be 1")
    _require(isinstance(data["run_id"], str) and data["run_id"].strip(),
             "run_id must be a non-empty string")
    _require(isinstance(data["created_at_utc"], str) and data["created_at_utc"].strip(),
             "created_at_utc must be a non-empty string")
    _require(isinstance(data["output_dir"], str) and data["output_dir"].strip(),
             "output_dir must be a non-empty string")

    # ── focus ─────────────────────────────────────────────────────────
    focus = data["focus"]
    _require(isinstance(focus, dict), "focus must be an object")
    for key in ("ticker", "option_type", "strike", "target_years", "expiration_date"):
        _require(key in focus, f"Missing required field: focus.{key}")
    _require(isinstance(focus["ticker"], str) and focus["ticker"].strip(),
             "focus.ticker must be a non-empty string")
    _require(focus["option_type"] in ("call", "put"),
             "focus.option_type must be 'call' or 'put'")
    _require(focus["strike"] is None or _is_number(focus["strike"]),
             "focus.strike must be null or numeric")
    _require(isinstance(focus["target_years"], (int, float)),
             "focus.target_years must be numeric")
    _require(isinstance(focus["expiration_date"], str) and focus["expiration_date"].strip(),
             "focus.expiration_date must be a non-empty string")
    # Optional, not required: contexts written before Vol_Suite started
    # publishing the GARCH conditional vol are still valid.
    if "garch_conditional_vol" in focus:
        _require(focus["garch_conditional_vol"] is None
                 or _is_number(focus["garch_conditional_vol"]),
                 "focus.garch_conditional_vol must be numeric or null")
    # Optional, not required: contexts written before an expected-return was
    # published are still valid.
    if "expected_return" in focus:
        _require(focus["expected_return"] is None
                 or _is_number(focus["expected_return"]),
                 "focus.expected_return must be numeric or null")

    # ── basket ────────────────────────────────────────────────────────
    basket = data["basket"]
    _require(isinstance(basket, dict), "basket must be an object")
    for key in ("index_ticker", "tickers", "weights"):
        _require(key in basket, f"Missing required field: basket.{key}")
    _require(isinstance(basket["index_ticker"], str) and basket["index_ticker"].strip(),
             "basket.index_ticker must be a non-empty string")
    _require(isinstance(basket["tickers"], list) and len(basket["tickers"]) > 0,
             "basket.tickers must be a non-empty list")
    _require(isinstance(basket["weights"], list)
             and len(basket["weights"]) == len(basket["tickers"]),
             "basket.weights must match basket.tickers length")
    _require(all(_is_number(w) for w in basket["weights"]),
             "basket.weights must all be numeric")
    _require(sum(float(w) for w in basket["weights"]) > 0,
             "basket.weights must sum to a positive value")

    # ── sentiment ─────────────────────────────────────────────────────
    # Lenient flavour: pack_json_path/group_id are null until a pack exists.
    validate_sentiment_block(data["sentiment"], strict=False)

    # ── var ────────────────────────────────────────────────────────────
    var = data["var"]
    _require(isinstance(var, dict), "var must be an object")
    for key in ("horizon_days", "confidence", "positions"):
        _require(key in var, f"Missing required field: var.{key}")
    _require(isinstance(var["horizon_days"], int) and not _is_bool(var["horizon_days"])
             and var["horizon_days"] > 0,
             "var.horizon_days must be a positive integer")
    _require(_is_number(var["confidence"]),
             "var.confidence must be numeric")
    _require(0.0 < float(var["confidence"]) < 1.0,
             "var.confidence must be a probability strictly between 0 and 1"
             " (e.g. 0.99, not 99)")
    _require(var["positions"] is None or isinstance(var["positions"], list),
             "var.positions must be null or a list")
    if isinstance(var["positions"], list):
        _require(len(var["positions"]) == len(basket["tickers"]),
                 "var.positions must be null or match basket.tickers length "
                 f"({len(basket['tickers'])}); got {len(var['positions'])}")

    # ── controls ──────────────────────────────────────────────────────
    controls = data["controls"]
    _require(isinstance(controls, dict), "controls must be an object")
    for key in ("run_options_suite", "run_var_suite", "compile_pdf"):
        _require(key in controls, f"Missing required field: controls.{key}")
        _require(_is_bool(controls[key]), f"controls.{key} must be boolean")

    # ── paths ──────────────────────────────────────────────────────────
    paths = data["paths"]
    _require(isinstance(paths, dict), "paths must be an object")
    for key in ("options_suite_root", "var_suite_root", "sentiment_suite_root"):
        _require(key in paths, f"Missing required field: paths.{key}")
        _require(isinstance(paths[key], str) and paths[key].strip(),
                 f"paths.{key} must be a non-empty string")

    # ── strategies (optional, from chain scanner) ────────────────────────
    # Strategies recommended by chain scanner. Empty list when no edges detected.
    if "strategies" in data:
        _require(isinstance(data["strategies"], list),
                 "strategies must be a list")
        for i, strategy in enumerate(data["strategies"]):
            _require(isinstance(strategy, dict),
                     f"strategies[{i}] must be an object")
            # Required strategy fields
            for key in ("strategy_type", "legs", "vol_regime", "rationale"):
                _require(key in strategy,
                         f"strategies[{i}] missing required field: {key}")

            # strategy_type must be a string
            _require(isinstance(strategy["strategy_type"], str)
                     and strategy["strategy_type"].strip(),
                     f"strategies[{i}].strategy_type must be a non-empty string")

            # vol_regime must be one of RICH, CHEAP, FAIR
            _require(strategy["vol_regime"] in ("RICH", "CHEAP", "FAIR"),
                     f"strategies[{i}].vol_regime must be 'RICH', 'CHEAP', or 'FAIR', "
                     f"got {strategy['vol_regime']}")

            # rationale must be a string
            _require(isinstance(strategy["rationale"], str),
                     f"strategies[{i}].rationale must be a string")

            # legs must be a non-empty list of leg objects
            legs = strategy["legs"]
            _require(isinstance(legs, list) and len(legs) > 0,
                     f"strategies[{i}].legs must be a non-empty list")
            for j, leg in enumerate(legs):
                _require(isinstance(leg, dict),
                         f"strategies[{i}].legs[{j}] must be an object")
                # Required leg fields
                for key in ("instrument_type", "strike", "quantity"):
                    _require(key in leg,
                             f"strategies[{i}].legs[{j}] missing required field: {key}")

                # instrument_type must be 'call' or 'put'
                _require(leg["instrument_type"] in ("call", "put"),
                         f"strategies[{i}].legs[{j}].instrument_type must be 'call' or 'put', "
                         f"got {leg['instrument_type']}")

                # strike must be numeric
                _require(_is_number(leg["strike"]),
                         f"strategies[{i}].legs[{j}].strike must be numeric")

                # quantity must be an integer
                _require(isinstance(leg["quantity"], int)
                         and not _is_bool(leg["quantity"]),
                         f"strategies[{i}].legs[{j}].quantity must be an integer")

            # Optional fields validation
            if "edge_strikes_used" in strategy:
                _require(isinstance(strategy["edge_strikes_used"], list),
                         f"strategies[{i}].edge_strikes_used must be a list")
                for k, strike in enumerate(strategy["edge_strikes_used"]):
                    _require(_is_number(strike),
                             f"strategies[{i}].edge_strikes_used[{k}] must be numeric")

            if "greeks_summary" in strategy:
                _require(isinstance(strategy["greeks_summary"], dict),
                         f"strategies[{i}].greeks_summary must be an object")
                for greek_name, greek_value in strategy["greeks_summary"].items():
                    _require(greek_value is None or _is_number(greek_value),
                             f"strategies[{i}].greeks_summary.{greek_name} must be numeric or null")

            if "rank_score" in strategy:
                _require(_is_number(strategy["rank_score"]),
                         f"strategies[{i}].rank_score must be numeric")


# ── 2. options_result.json ──────────────────────────────────────────────

def validate_options_result(data: Dict[str, Any]) -> None:
    """Validate an options_result.json payload produced by
    Options_Suite/main.py::run_context_mode().

    Accepts both success (status='ok') and error (status='error') shapes.
    """
    _require(isinstance(data, dict), "options_result must be a JSON object")

    _warn_missing_schema_version(data, "options_result")

    _require(data.get("suite") == "options",
             "suite must be 'options'")
    _require(data.get("status") in ("ok", "error"),
             "status must be 'ok' or 'error'")
    _require(isinstance(data.get("ticker"), str) and data["ticker"].strip(),
             "ticker must be a non-empty string")
    _require(isinstance(data.get("method"), str) and data["method"].strip(),
             "method must be a non-empty string")
    _require(isinstance(data.get("timestamp"), str) and data["timestamp"].strip(),
             "timestamp must be a non-empty string")

    if data["status"] == "ok":
        _require("sigma" in data, "missing 'sigma' for status='ok'")
        _require(_is_number(data["sigma"]), "sigma must be numeric")
        _require("price" in data, "missing 'price' for status='ok'")
        _require(_is_number(data["price"]), "price must be numeric")
        _require(isinstance(data.get("greeks"), dict) and len(data["greeks"]) > 0,
                 "greeks must be a non-empty dict for status='ok'")
    else:
        _require(isinstance(data.get("error"), str) and data["error"].strip(),
                 "error must be a non-empty string for status='error'")


# ── 3. var_result.json ──────────────────────────────────────────────────

def validate_var_result(data: Dict[str, Any]) -> None:
    """Validate a var_result.json payload produced by
    VaR_Tools_Simulations/main.py::run_context_mode().

    Accepts both success (status='ok') and error (status='error') shapes.
    """
    _require(isinstance(data, dict), "var_result must be a JSON object")

    _warn_missing_schema_version(data, "var_result")

    _require(data.get("suite") == "var",
             "suite must be 'var'")
    _require(data.get("status") in ("ok", "error"),
             "status must be 'ok' or 'error'")
    _require(isinstance(data.get("timestamp"), str) and data["timestamp"].strip(),
             "timestamp must be a non-empty string")

    # Common optional fields that should be present (but may be None on error)
    if data["status"] == "ok":
        _require(isinstance(data.get("module"), str) and data["module"].strip(),
                 "module must be a non-empty string for status='ok'")
        _require("var" in data, "missing 'var' for status='ok'")
        _require(_is_number(data["var"]), "var must be numeric")
        _require("cvar" in data, "missing 'cvar' for status='ok'")
        _require(_is_number(data["cvar"]), "cvar must be numeric")
        _require("confidence" in data, "missing 'confidence' for status='ok'")
        _require(_is_number(data["confidence"]),
                 "confidence must be numeric")
        _require(0.0 < float(data["confidence"]) < 1.0,
                 "confidence must be a probability strictly between 0 and 1")
        _require("horizon_days" in data, "missing 'horizon_days' for status='ok'")
        _require(isinstance(data["horizon_days"], int) and not _is_bool(data["horizon_days"])
                 and data["horizon_days"] > 0,
                 "horizon_days must be a positive integer")
    else:
        _require(isinstance(data.get("error"), str) and data["error"].strip(),
                 "error must be a non-empty string for status='error'")


# ── 4. Sentiment pack JSON ──────────────────────────────────────────────

def validate_sentiment_pack(data: Dict[str, Any]) -> None:
    """Validate a sentiment-scanner ticker-pack JSON file produced by
    sentiment-scanner/scanner/ticker_pack.py::export_alert_group().

    The pack includes top-level metadata plus a ``tickers`` array of scored
    symbols.
    """
    _require(isinstance(data, dict), "sentiment_pack must be a JSON object")

    _warn_missing_schema_version(data, "sentiment_pack")

    _require(isinstance(data.get("version"), int),
             "version must be an integer")
    _require(isinstance(data.get("group_id"), str) and data["group_id"].strip(),
             "group_id must be a non-empty string")
    _require(isinstance(data.get("group_name"), str) and data["group_name"].strip(),
             "group_name must be a non-empty string")
    _require(isinstance(data.get("created_at"), str) and data["created_at"].strip(),
             "created_at must be a non-empty string")

    # tickers array
    _require(isinstance(data.get("tickers"), list) and len(data["tickers"]) > 0,
             "tickers must be a non-empty list")

    for i, t in enumerate(data["tickers"]):
        _require(isinstance(t, dict), f"tickers[{i}] must be an object")
        # Required per-ticker fields
        for key in ("symbol", "rank", "cns", "war_score",
                     "thesis_ratio", "pump_ratio", "volume",
                     "bullish_pct", "bearish_pct", "confidence",
                     "social_sources"):
            _require(key in t, f"tickers[{i}] missing required field: {key}")

        _require(isinstance(t["symbol"], str) and t["symbol"].strip(),
                 f"tickers[{i}].symbol must be a non-empty string")
        _require(isinstance(t["rank"], int) and t["rank"] > 0,
                 f"tickers[{i}].rank must be a positive integer")
        _require(_is_number(t["cns"]),
                 f"tickers[{i}].cns must be numeric")
        _require(_is_number(t["war_score"]),
                 f"tickers[{i}].war_score must be numeric")
        _require(_is_number(t["thesis_ratio"]),
                 f"tickers[{i}].thesis_ratio must be numeric")
        _require(_is_number(t["pump_ratio"]),
                 f"tickers[{i}].pump_ratio must be numeric")
        _require(isinstance(t["volume"], int) and not _is_bool(t["volume"]),
                 f"tickers[{i}].volume must be an integer")
        _require(_is_number(t["bullish_pct"]),
                 f"tickers[{i}].bullish_pct must be numeric")
        _require(_is_number(t["bearish_pct"]),
                 f"tickers[{i}].bearish_pct must be numeric")
        _require(_is_number(t["confidence"]) and 0.0 <= float(t["confidence"]) <= 1.0,
                 f"tickers[{i}].confidence must be numeric in [0, 1]")
        _require(isinstance(t["social_sources"], list),
                 f"tickers[{i}].social_sources must be a list")


# ── 5. Sentiment context export ─────────────────────────────────────────

def validate_sentiment_context(data: Dict[str, Any]) -> None:
    """Validate a sentiment context-export JSON file produced by
    sentiment-scanner/main.py::_write_context_export().

    This is the ``--export-context`` artifact with schema_version=2.
    """
    _require(isinstance(data, dict), "sentiment_context must be a JSON object")

    _warn_missing_schema_version(data, "sentiment_context")

    _require(data.get("schema_version") == 2,
             "schema_version must be 2")
    _require(isinstance(data.get("run_id"), str) and data["run_id"].strip(),
             "run_id must be a non-empty string")
    _require(isinstance(data.get("created_at_utc"), str)
             and data["created_at_utc"].strip(),
             "created_at_utc must be a non-empty string")

    # Strict flavour: the producer has written a pack, so every path/id is real.
    validate_sentiment_block(data.get("sentiment"), strict=True)


# ── 6. vol_result.json ──────────────────────────────────────────────────

VOL_RESULT_SCHEMA_VERSION = 1

#: Keys every ``vol_result.json`` carries regardless of status. They are
#: required even on the error path — empty dict / empty list rather than
#: absent — so a consumer can read ``payload["gamma_records"]`` unconditionally
#: and branch on ``status``, instead of guarding every access with ``.get()``.
VOL_RESULT_REQUIRED_KEYS = (
    "suite", "status", "ticker", "timestamp",
    "vol_surface", "dealer_positioning", "gamma_records",
)

#: Scalar summary of one variance-swap replication leg. Only ``ticker`` and
#: ``fair_vol_pct`` are required; the rest of the leg (spot, forward, ATM IV,
#: convexity premium, strike count) is present in practice but a leg that
#: partially resolved is still more useful than no leg at all.
_VOL_LEG_REQUIRED = ("ticker", "fair_vol_pct")

#: Per-record fields of the dealer gamma ladder.
_GAMMA_RECORD_REQUIRED = (
    "strike", "expiry", "right", "oi", "gamma", "dollar_gamma", "iv", "tte",
)


def _validate_vol_leg(leg: Any, label: str) -> None:
    """One replication leg: ``None`` when that leg failed, else an object."""
    if leg is None:
        return
    _require(isinstance(leg, dict), f"vol_surface.{label} must be null or an object")
    for key in _VOL_LEG_REQUIRED:
        _require(key in leg, f"Missing required field: vol_surface.{label}.{key}")
    _require(isinstance(leg["ticker"], str) and leg["ticker"].strip(),
             f"vol_surface.{label}.ticker must be a non-empty string")
    _require(leg["fair_vol_pct"] is None or _is_number(leg["fair_vol_pct"]),
             f"vol_surface.{label}.fair_vol_pct must be numeric or null")


def validate_vol_result(data: Dict[str, Any]) -> None:
    """Validate a vol_result.json payload produced by
    Vol_Suite/volatility_suite.py::run_context_mode() (and by its interactive
    unified flow, which writes the identical artifact).

    This is the contract that replaced the orchestrator's old habit of
    *scripting Vol_Suite's stdin* and then synthesizing a result by listing
    files in the output directory. Because that synthesis could not tell a
    successful run from one where every data fetch failed — both leave a
    directory of files behind — the interesting half of this validator is what
    it demands for ``status == "ok"``:

      * ``vol_surface`` must carry the run's identity (focus/index ticker, the
        pinned expiry, target_years) and both legs, each either a real leg or
        an explicit ``null``.
      * ``dealer_positioning`` must carry an explicit boolean ``available``,
        and when it is true, the scalars that make the block meaningful.
      * at least one of {focus leg, index leg, dealer positioning} must
        actually be present. A payload where all three are empty is a failed
        run wearing a success label, and is rejected.

    Accepts both success (status='ok') and error (status='error') shapes.
    Raises ValueError on the first violation.
    """
    _require(isinstance(data, dict), "vol_result must be a JSON object")

    _warn_missing_schema_version(data, "vol_result")

    for key in VOL_RESULT_REQUIRED_KEYS:
        _require(key in data, f"Missing required field: {key}")

    if "schema_version" in data:
        _require(data["schema_version"] == VOL_RESULT_SCHEMA_VERSION,
                 f"schema_version must be {VOL_RESULT_SCHEMA_VERSION}")
    _require(data["suite"] == "vol", "suite must be 'vol'")
    _require(data["status"] in ("ok", "error"), "status must be 'ok' or 'error'")
    _require(isinstance(data["ticker"], str), "ticker must be a string")
    _require(isinstance(data["timestamp"], str) and data["timestamp"].strip(),
             "timestamp must be a non-empty string")

    vol_surface = data["vol_surface"]
    dealer = data["dealer_positioning"]
    records = data["gamma_records"]
    _require(isinstance(vol_surface, dict), "vol_surface must be an object")
    _require(isinstance(dealer, dict), "dealer_positioning must be an object")
    _require(isinstance(records, list), "gamma_records must be a list")

    if data["status"] == "error":
        _require(isinstance(data.get("error"), str) and data["error"].strip(),
                 "error must be a non-empty string for status='error'")
        return

    # ── status == 'ok' from here down ────────────────────────────────
    _require(data["ticker"].strip(),
             "ticker must be a non-empty string for status='ok'")

    for key in ("focus_ticker", "index_ticker", "expiration", "target_years",
                "focus", "index", "basket"):
        _require(key in vol_surface, f"Missing required field: vol_surface.{key}")
    _require(isinstance(vol_surface["focus_ticker"], str) and vol_surface["focus_ticker"].strip(),
             "vol_surface.focus_ticker must be a non-empty string")
    _require(isinstance(vol_surface["index_ticker"], str) and vol_surface["index_ticker"].strip(),
             "vol_surface.index_ticker must be a non-empty string")
    _require(isinstance(vol_surface["expiration"], str) and vol_surface["expiration"].strip(),
             "vol_surface.expiration must be a non-empty string")
    _require(_is_number(vol_surface["target_years"]),
             "vol_surface.target_years must be numeric")
    _validate_vol_leg(vol_surface["focus"], "focus")
    _validate_vol_leg(vol_surface["index"], "index")
    _require(vol_surface.get("vol_spread_pts") is None
             or _is_number(vol_surface["vol_spread_pts"]),
             "vol_surface.vol_spread_pts must be numeric or null")
    # Optional -- absent on runs where GARCH was skipped or failed. Present
    # means shared/summary.py copies it straight into quant_summary.json's
    # metrics, so a non-numeric value here would poison that artifact.
    _require(vol_surface.get("garch_conditional_vol") is None
             or _is_number(vol_surface["garch_conditional_vol"]),
             "vol_surface.garch_conditional_vol must be numeric or null")

    basket = vol_surface["basket"]
    _require(isinstance(basket, dict), "vol_surface.basket must be an object")
    for key in ("tickers", "weights"):
        _require(key in basket, f"Missing required field: vol_surface.basket.{key}")
    _require(isinstance(basket["tickers"], list),
             "vol_surface.basket.tickers must be a list")
    _require(isinstance(basket["weights"], list)
             and len(basket["weights"]) == len(basket["tickers"]),
             "vol_surface.basket.weights must match vol_surface.basket.tickers length")
    _require(basket.get("dispersion_score") is None or _is_number(basket["dispersion_score"]),
             "vol_surface.basket.dispersion_score must be numeric or null")

    _require("available" in dealer, "Missing required field: dealer_positioning.available")
    _require(_is_bool(dealer["available"]), "dealer_positioning.available must be boolean")
    _require(isinstance(dealer.get("sign_model"), str),
             "dealer_positioning.sign_model must be a string")
    if dealer["available"]:
        for key in ("spot", "total_net_gamma", "total_net_dollar_gamma",
                    "hedge_requirement", "gamma_flip_level"):
            _require(key in dealer, f"Missing required field: dealer_positioning.{key}")
            _require(dealer[key] is None or _is_number(dealer[key]),
                     f"dealer_positioning.{key} must be numeric or null")

    total = data.get("gamma_records_total", len(records))
    _require(isinstance(total, int) and not _is_bool(total) and total >= 0,
             "gamma_records_total must be a non-negative integer")
    _require(len(records) <= total,
             f"gamma_records has {len(records)} rows but gamma_records_total is {total}")
    _require(_is_bool(data.get("gamma_records_truncated", False)),
             "gamma_records_truncated must be boolean")
    for i, rec in enumerate(records):
        _require(isinstance(rec, dict), f"gamma_records[{i}] must be an object")
        for key in _GAMMA_RECORD_REQUIRED:
            _require(key in rec, f"gamma_records[{i}] missing required field: {key}")

    _require(bool(vol_surface["focus"]) or bool(vol_surface["index"]) or dealer["available"],
             "status='ok' but the payload is empty: no focus leg, no index leg, "
             "and dealer_positioning.available is false")


# ── 7. quant_summary.json ───────────────────────────────────────────────

QUANT_SUMMARY_SCHEMA_VERSION = 1

#: The four module-status values the dashboard's `/quant` module cards render
#: with distinct visual treatment. `degraded` (result file present but only
#: partially extractable) is deliberately distinct from `error` (the suite run
#: itself failed), and both are distinct from `unsupported` (the module's
#: `runnable` flag is False, e.g. Options today per the `tool-launcher` skill
#: — a known, pre-existing limitation, not a regression). See the design spec's
#: Phase 1 table (`docs/superpowers/specs/2026-08-01-quant-console-design.md`).
QUANT_SUMMARY_MODULE_STATUSES = ("ok", "error", "degraded", "unsupported")

#: Fields every `quant_summary.json` top-level object carries.
QUANT_SUMMARY_REQUIRED_KEYS = (
    "schema_version", "run_id", "ticker", "created_at_utc", "modules",
)

#: Fields every `modules[]` entry carries, regardless of status.
QUANT_SUMMARY_MODULE_REQUIRED_KEYS = (
    "module", "status", "headline", "metrics", "warnings", "source_result",
)

#: Units a `modules[].distributions[]` histogram can be measured in.
#: `price` = one ticker's terminal price (mc_sim / copula / price_dist);
#: `portfolio_value` = corr_sim's terminal *portfolio* value, summed across up
#: to three tickers' positions; `unknown` = the producing sim published a
#: histogram without declaring `histogram_unit`. The dashboard labels its chart
#: from this, so an unrecognized value is rejected rather than defaulted --
#: silently reading corr_sim's bins as a price is exactly the mislabeling this
#: field exists to prevent.
QUANT_SUMMARY_DISTRIBUTION_UNITS = ("price", "portfolio_value", "unknown")


def validate_quant_summary(data: Dict[str, Any]) -> None:
    """Validate a quant_summary.json payload produced by
    shared/summary.py::build_run_summary() and written by the dashboard's
    _execute_run() at the end of a suite/orchestrator run.

    One entry in ``modules`` per suite that ran (or was skipped as
    ``unsupported``); an empty ``modules`` list is schema-valid on its own —
    it just means no suite produced anything worth summarizing yet — but a
    missing/malformed ``modules`` key is not.

    Raises ValueError on the first violation.
    """
    _require(isinstance(data, dict), "quant_summary must be a JSON object")

    _warn_missing_schema_version(data, "quant_summary")

    for key in QUANT_SUMMARY_REQUIRED_KEYS:
        _require(key in data, f"Missing required field: {key}")

    _require(data["schema_version"] == QUANT_SUMMARY_SCHEMA_VERSION,
             f"schema_version must be {QUANT_SUMMARY_SCHEMA_VERSION}")
    _require(isinstance(data["run_id"], str) and data["run_id"].strip(),
             "run_id must be a non-empty string")
    _require(isinstance(data["ticker"], str) and data["ticker"].strip(),
             "ticker must be a non-empty string")
    _require(isinstance(data["created_at_utc"], str) and data["created_at_utc"].strip(),
             "created_at_utc must be a non-empty string")

    modules = data["modules"]
    _require(isinstance(modules, list), "modules must be a list")

    for i, mod in enumerate(modules):
        _require(isinstance(mod, dict), f"modules[{i}] must be an object")

        for key in QUANT_SUMMARY_MODULE_REQUIRED_KEYS:
            _require(key in mod, f"modules[{i}] missing required field: {key}")

        _require(isinstance(mod["module"], str) and mod["module"].strip(),
                 f"modules[{i}].module must be a non-empty string")
        _require(mod["status"] in QUANT_SUMMARY_MODULE_STATUSES,
                 f"modules[{i}].status must be one of {QUANT_SUMMARY_MODULE_STATUSES}, "
                 f"got {mod['status']!r}")
        _require(isinstance(mod["headline"], str),
                 f"modules[{i}].headline must be a string")
        _require(isinstance(mod["metrics"], dict),
                 f"modules[{i}].metrics must be an object")
        _require(isinstance(mod["warnings"], list)
                 and all(isinstance(w, str) for w in mod["warnings"]),
                 f"modules[{i}].warnings must be a list of strings")
        _require(isinstance(mod["source_result"], str),
                 f"modules[{i}].source_result must be a string")

        # Optional: chartable terminal distributions lifted out of the market-
        # signals bundle's sim payloads (shared/summary.py). Absent for every
        # module that has no histogram to draw.
        if "distributions" in mod:
            _validate_quant_summary_distributions(mod["distributions"], i)


def _validate_quant_summary_distributions(distributions: Any, i: int) -> None:
    """Validate one `modules[i].distributions` list (optional key)."""
    _require(isinstance(distributions, list),
             f"modules[{i}].distributions must be a list")

    for j, dist in enumerate(distributions):
        where = f"modules[{i}].distributions[{j}]"
        _require(isinstance(dist, dict), f"{where} must be an object")
        for key in ("label", "bins"):
            _require(key in dist, f"{where} missing required field: {key}")
        _require(isinstance(dist["label"], str) and dist["label"].strip(),
                 f"{where}.label must be a non-empty string")

        _require(isinstance(dist["bins"], list), f"{where}.bins must be a list")
        for k, bin_ in enumerate(dist["bins"]):
            _require(isinstance(bin_, dict), f"{where}.bins[{k}] must be an object")
            for key in ("low", "high", "count"):
                _require(key in bin_ and _is_number(bin_[key]),
                         f"{where}.bins[{k}].{key} must be numeric")

        if "unit" in dist:
            _require(dist["unit"] in QUANT_SUMMARY_DISTRIBUTION_UNITS,
                     f"{where}.unit must be one of "
                     f"{QUANT_SUMMARY_DISTRIBUTION_UNITS}, got {dist['unit']!r}")

        if "percentiles" in dist:
            pct = dist["percentiles"]
            _require(isinstance(pct, dict), f"{where}.percentiles must be an object")
            for key, value in pct.items():
                _require(_is_number(value),
                         f"{where}.percentiles.{key} must be numeric")


# ── 8. swaps_result.json ────────────────────────────────────────────────

SWAPS_RESULT_SCHEMA_VERSION = 1

#: 'ok' -- swap_activity had rows for this run's ticker/date.
#: 'no_data' -- the query ran fine but came back empty (cold swaps.db, or no
#:   recent DTCC activity for this name) -- not an error, just nothing to show.
#: 'error' -- the swaps enrichment itself failed (see orchestrator.py's
#:   get_recent_swap_activity, which normally swallows this into `[]`/'no_data'
#:   instead; 'error' is reserved for a caller that wants to report the
#:   underlying exception rather than silently degrading).
SWAPS_RESULT_STATUSES = ("ok", "no_data", "error")


def validate_swaps_result(data: Dict[str, Any]) -> None:
    """Validate a swaps_result.json payload produced by
    orchestrator.py::run_unified() from context['swap_activity']
    (get_recent_swap_activity's DTCC top_notional_products rows).

    Unlike options/var, there is no subprocess suite behind this artifact --
    it is a same-process DB read surfaced as its own file so the Output tab
    can show swap activity next to the suites that ran for the same ticker.
    """
    _require(isinstance(data, dict), "swaps_result must be a JSON object")

    _require(data.get("schema_version") == SWAPS_RESULT_SCHEMA_VERSION,
             f"schema_version must be {SWAPS_RESULT_SCHEMA_VERSION}")
    _require(data.get("suite") == "swaps", "suite must be 'swaps'")
    _require(data.get("status") in SWAPS_RESULT_STATUSES,
             f"status must be one of {SWAPS_RESULT_STATUSES}")
    _require(isinstance(data.get("ticker"), str) and data["ticker"].strip(),
             "ticker must be a non-empty string")
    _require(isinstance(data.get("timestamp"), str) and data["timestamp"].strip(),
             "timestamp must be a non-empty string")
    _require(isinstance(data.get("row_count"), int) and not _is_bool(data["row_count"])
             and data["row_count"] >= 0,
             "row_count must be a non-negative integer")

    top_notional = data.get("top_notional")
    _require(isinstance(top_notional, list), "top_notional must be a list")
    for i, row in enumerate(top_notional):
        _require(isinstance(row, dict), f"top_notional[{i}] must be an object")
        _require("product" in row, f"top_notional[{i}] missing 'product'")

    if data["status"] == "error":
        _require(isinstance(data.get("error"), str) and data["error"].strip(),
                 "error must be a non-empty string for status='error'")