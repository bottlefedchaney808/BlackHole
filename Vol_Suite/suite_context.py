"""suite_context.py

The handoff contract between Vol_Suite and its sibling suites (Options_Suite,
VaR_Tools_Simulations, sentiment-scanner): one validated JSON object written
once per run, then passed to each child via `--context`.

The point of validating here rather than in each child is that a malformed
context should fail at the moment it's built -- while the operator is still
sitting at the prompt that produced it -- not several minutes later inside a
subprocess whose stdout is captured and hidden.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


# Sibling-suite locations, resolved RELATIVE to this file's parent directory
# (FinancialDevelopment/) rather than hardcoded to one machine's absolute
# paths. The hardcoded form broke the moment this folder was renamed from
# Variance_Swap_Module to Vol_Suite, which is exactly the kind of breakage
# worth not having twice. Each is overridable by environment variable for
# anyone whose layout differs.
_SIBLING_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OPTIONS_SUITE_ROOT = os.environ.get(
    "OPTIONS_SUITE_ROOT", str(_SIBLING_ROOT / "Options_Suite"))
DEFAULT_VAR_SUITE_ROOT = os.environ.get(
    "VAR_SUITE_ROOT", str(_SIBLING_ROOT / "VaR_Tools_Simulations"))
DEFAULT_SENTIMENT_SUITE_ROOT = os.environ.get(
    "SENTIMENT_SUITE_ROOT", str(_SIBLING_ROOT / "sentiment-scanner"))

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE = re.compile(r"^\d{8}$")


def _normalize_expiration(value: str) -> str:
    """Accept either ThetaData's compact "20261016" or ISO "2026-10-16", and
    always STORE ISO.

    Both forms were flowing into this field: the interactive flow passes
    `expiry_selector`'s `exp_str` (compact), while the tests use ISO. That
    ambiguity matters because Options_Suite parses this field with
    `datetime.fromisoformat()` as a fallback when `target_years` is absent --
    and `fromisoformat` only learned to accept the compact form in Python
    3.11. So the same context file silently yields a valid expiry on 3.12 and
    `None` on 3.10. Pinning one stored format removes the version dependence
    rather than relying on every consumer to handle both.
    """
    raw = str(value).strip()
    if _ISO_DATE.match(raw):
        datetime.strptime(raw, "%Y-%m-%d")   # reject e.g. 2026-13-45
        return raw
    if _COMPACT_DATE.match(raw):
        return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    raise ValueError(
        f"focus.expiration_date must be YYYY-MM-DD or YYYYMMDD, got {value!r}")


def _to_abs_path_str(path_value: str) -> str:
    return Path(path_value).expanduser().resolve().as_posix()


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def build_suite_context(
    *,
    output_dir: str,
    run_id: str,
    ticker: str,
    option_type: str,
    strike: Optional[float],
    target_years: float,
    expiration_date: str,
    index_ticker: str,
    basket_tickers: Sequence[str],
    basket_weights: Sequence[float],
    sentiment_manifest_path: str,
    sentiment_pack_json_path: Optional[str] = None,
    sentiment_group_id: Optional[str] = None,
    sentiment_ranked_tickers: Optional[Sequence[str]] = None,
    var_horizon_days: int = 1,
    var_confidence: float = 0.99,
    var_positions: Optional[Sequence[Dict[str, Any]]] = None,
    run_options_suite: bool = False,
    run_var_suite: bool = False,
    compile_pdf: bool = False,
    options_suite_root: str = DEFAULT_OPTIONS_SUITE_ROOT,
    var_suite_root: str = DEFAULT_VAR_SUITE_ROOT,
    sentiment_suite_root: str = DEFAULT_SENTIMENT_SUITE_ROOT,
) -> Dict[str, Any]:
    ranked = [str(t).upper() for t in (sentiment_ranked_tickers or []) if str(t).strip()]
    context = {
        "schema_version": 1,
        "run_id": str(run_id),
        "created_at_utc": _iso_utc_now(),
        "output_dir": _to_abs_path_str(output_dir),
        "focus": {
            "ticker": str(ticker).upper(),
            "option_type": str(option_type).lower(),
            "strike": strike,
            "target_years": float(target_years),
            "expiration_date": _normalize_expiration(expiration_date),
        },
        "basket": {
            "index_ticker": str(index_ticker).upper(),
            "tickers": [str(t).upper() for t in basket_tickers],
            "weights": [float(w) for w in basket_weights],
        },
        "sentiment": {
            "manifest_path": _to_abs_path_str(sentiment_manifest_path),
            "pack_json_path": _to_abs_path_str(sentiment_pack_json_path) if sentiment_pack_json_path else None,
            "group_id": str(sentiment_group_id) if sentiment_group_id else None,
            "ranked_tickers": ranked,
        },
        "var": {
            "horizon_days": int(var_horizon_days),
            "confidence": float(var_confidence),
            # null, NOT [], when the operator supplied no positions.
            #
            # This distinction is load-bearing and used to be a hard break.
            # VaR_Tools_Simulations reads `var.positions` as: None means
            # "not supplied -- derive proportional notionals from the basket
            # weights", while a list means "these are the notionals", which
            # it then length-checks against the basket. Emitting [] for "I
            # have none" took the list branch and failed every single time
            # with `Field 'positions' length must match basket length (N)`.
            # The unified flow never collects positions, so the VaR handoff
            # could not succeed at all. null lets the child's own fallback
            # do what it was written to do.
            "positions": list(var_positions) if var_positions else None,
        },
        "controls": {
            "run_options_suite": bool(run_options_suite),
            "run_var_suite": bool(run_var_suite),
            "compile_pdf": bool(compile_pdf),
        },
        "paths": {
            "options_suite_root": _to_abs_path_str(options_suite_root),
            "var_suite_root": _to_abs_path_str(var_suite_root),
            "sentiment_suite_root": _to_abs_path_str(sentiment_suite_root),
        },
    }
    validate_suite_context(context)
    return context


def validate_suite_context(context: Dict[str, Any]) -> None:
    _require(isinstance(context, dict), "suite_context must be a JSON object")
    for key in ("schema_version", "run_id", "created_at_utc", "output_dir", "focus", "basket", "sentiment", "var", "controls", "paths"):
        _require(key in context, f"Missing required field: {key}")

    _require(context["schema_version"] == 1, "schema_version must be 1")
    _require(isinstance(context["run_id"], str) and context["run_id"].strip(), "run_id must be a non-empty string")
    _require(isinstance(context["created_at_utc"], str) and context["created_at_utc"].strip(), "created_at_utc must be a non-empty string")
    _require(Path(context["output_dir"]).is_absolute(), "output_dir must be an absolute path")

    focus = context["focus"]
    _require(isinstance(focus, dict), "focus must be an object")
    for key in ("ticker", "option_type", "strike", "target_years", "expiration_date"):
        _require(key in focus, f"Missing required field: focus.{key}")
    _require(isinstance(focus["ticker"], str) and focus["ticker"].strip(), "focus.ticker must be a non-empty string")
    _require(focus["option_type"] in {"call", "put"}, "focus.option_type must be 'call' or 'put'")
    _require(focus["strike"] is None or isinstance(focus["strike"], (int, float)), "focus.strike must be null or number")
    _require(isinstance(focus["target_years"], (int, float)), "focus.target_years must be numeric")
    _require(isinstance(focus["expiration_date"], str) and focus["expiration_date"].strip(), "focus.expiration_date must be a non-empty string")

    basket = context["basket"]
    _require(isinstance(basket, dict), "basket must be an object")
    for key in ("index_ticker", "tickers", "weights"):
        _require(key in basket, f"Missing required field: basket.{key}")
    _require(isinstance(basket["index_ticker"], str) and basket["index_ticker"].strip(), "basket.index_ticker must be a non-empty string")
    _require(isinstance(basket["tickers"], list) and len(basket["tickers"]) > 0, "basket.tickers must be a non-empty list")
    _require(isinstance(basket["weights"], list) and len(basket["weights"]) == len(basket["tickers"]), "basket.weights must match basket.tickers length")
    # VaR_Tools_Simulations divides by the weight sum to normalize, so a
    # non-positive total is a divide-by-zero waiting to happen inside a
    # subprocess. Catch it here, where the error is still attributable.
    _require(all(isinstance(w, (int, float)) and not isinstance(w, bool) for w in basket["weights"]),
             "basket.weights must all be numeric")
    _require(sum(float(w) for w in basket["weights"]) > 0, "basket.weights must sum to a positive value")

    sentiment = context["sentiment"]
    _require(isinstance(sentiment, dict), "sentiment must be an object")
    for key in ("manifest_path", "pack_json_path", "group_id", "ranked_tickers"):
        _require(key in sentiment, f"Missing required field: sentiment.{key}")
    _require(isinstance(sentiment["manifest_path"], str) and sentiment["manifest_path"].strip(), "sentiment.manifest_path must be a non-empty string")
    _require(sentiment["pack_json_path"] is None or isinstance(sentiment["pack_json_path"], str), "sentiment.pack_json_path must be null or string")
    _require(sentiment["group_id"] is None or isinstance(sentiment["group_id"], str), "sentiment.group_id must be null or string")
    _require(isinstance(sentiment["ranked_tickers"], list), "sentiment.ranked_tickers must be a list")

    var = context["var"]
    _require(isinstance(var, dict), "var must be an object")
    for key in ("horizon_days", "confidence", "positions"):
        _require(key in var, f"Missing required field: var.{key}")
    # `not isinstance(..., bool)` because bool subclasses int in Python, so
    # horizon_days=True would otherwise sail through as "a positive integer".
    _require(isinstance(var["horizon_days"], int) and not isinstance(var["horizon_days"], bool)
             and var["horizon_days"] > 0, "var.horizon_days must be a positive integer")
    _require(isinstance(var["confidence"], (int, float)) and not isinstance(var["confidence"], bool),
             "var.confidence must be numeric")
    # Range-check, not just type-check: 99 instead of 0.99 is the obvious
    # slip, it's numeric, and it produces confident-looking nonsense rather
    # than an error. VaR rejects it downstream, but there's no reason to
    # spend a subprocess launch discovering that.
    _require(0.0 < float(var["confidence"]) < 1.0,
             "var.confidence must be a probability strictly between 0 and 1 (e.g. 0.99, not 99)")
    # null means "not supplied -- let VaR derive notionals from the basket
    # weights". A list must line up with the basket, since that's precisely
    # the check VaR applies to it.
    _require(var["positions"] is None or isinstance(var["positions"], list),
             "var.positions must be null or a list")
    if isinstance(var["positions"], list):
        _require(len(var["positions"]) == len(basket["tickers"]),
                 "var.positions must be null or match basket.tickers length "
                 f"({len(basket['tickers'])}); got {len(var['positions'])}")

    controls = context["controls"]
    _require(isinstance(controls, dict), "controls must be an object")
    for key in ("run_options_suite", "run_var_suite", "compile_pdf"):
        _require(key in controls, f"Missing required field: controls.{key}")
        _require(isinstance(controls[key], bool), f"controls.{key} must be boolean")

    paths = context["paths"]
    _require(isinstance(paths, dict), "paths must be an object")
    for key in ("options_suite_root", "var_suite_root", "sentiment_suite_root"):
        _require(key in paths, f"Missing required field: paths.{key}")
        _require(isinstance(paths[key], str) and paths[key].strip(), f"paths.{key} must be a non-empty string")


def write_suite_context(context: Dict[str, Any], path: str) -> str:
    validate_suite_context(context)
    out_path = Path(path).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(context, f, indent=2)
        f.write("\n")
    return str(out_path)


def read_suite_context(path: str) -> Dict[str, Any]:
    in_path = Path(path).expanduser().resolve()
    if not in_path.exists():
        raise FileNotFoundError(f"suite_context.json not found: {in_path}")
    with in_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("suite_context.json must contain a JSON object")
    validate_suite_context(data)
    return data
