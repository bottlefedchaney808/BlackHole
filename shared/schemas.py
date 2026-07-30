"""Shared JSON schema validators for all 5 inter-suite artifacts.

Each validator accepts dict data (as parsed from JSON) and raises ValueError
on the first violation.  This module mirrors and supplements the inline
validation in each suite with a central, importable copy suitable for testing
and cross-suite tooling.

The 5 artifacts:
  1. suite_context.json  — schema_version=1  (Vol_Suite → everyone via --context)
  2. options_result.json — no schema_version (Options_Suite → Vol_Suite)
  3. var_result.json     — no schema_version (VaR_Tools_Simulations → Vol_Suite)
  4. Sentiment pack JSON — no schema_version (sentiment-scanner ticker pack)
  5. Sentiment context   — schema_version=2  (sentiment-scanner context export)
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
    sentiment = data["sentiment"]
    _require(isinstance(sentiment, dict), "sentiment must be an object")
    for key in ("manifest_path", "pack_json_path", "group_id", "ranked_tickers"):
        _require(key in sentiment, f"Missing required field: sentiment.{key}")
    _require(isinstance(sentiment["manifest_path"], str) and sentiment["manifest_path"].strip(),
             "sentiment.manifest_path must be a non-empty string")
    _require(sentiment["pack_json_path"] is None
             or isinstance(sentiment["pack_json_path"], str),
             "sentiment.pack_json_path must be null or string")
    _require(sentiment["group_id"] is None or isinstance(sentiment["group_id"], str),
             "sentiment.group_id must be null or string")
    _require(isinstance(sentiment["ranked_tickers"], list),
             "sentiment.ranked_tickers must be a list")

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

    sentiment = data.get("sentiment")
    _require(isinstance(sentiment, dict), "sentiment must be an object")
    for key in ("manifest_path", "pack_json_path", "group_id", "ranked_tickers"):
        _require(key in sentiment, f"Missing required field: sentiment.{key}")
    _require(isinstance(sentiment["manifest_path"], str)
             and sentiment["manifest_path"].strip(),
             "sentiment.manifest_path must be a non-empty string")
    _require(isinstance(sentiment["pack_json_path"], str)
             and sentiment["pack_json_path"].strip(),
             "sentiment.pack_json_path must be a non-empty string")
    _require(isinstance(sentiment["group_id"], str)
             and sentiment["group_id"].strip(),
             "sentiment.group_id must be a non-empty string")
    _require(isinstance(sentiment["ranked_tickers"], list),
             "sentiment.ranked_tickers must be a list")