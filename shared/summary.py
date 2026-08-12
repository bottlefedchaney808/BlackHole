"""shared/summary.py -- builds `quant_summary.json` from a run's result markers.

Quant Console plan, Phase 1 / Task 3
(docs/superpowers/plans/2026-08-01-quant-console.md); design spec at
docs/superpowers/specs/2026-08-01-quant-console-design.md.

Each suite already leaves a validated marker file in its run's output
directory -- ``vol_result.json``, ``options_result.json``, ``var_result.json``,
``sentiment_result.json`` (see ``shared/suite_validation.py::SUITE_REQUIREMENTS``
for the authoritative filename/schema pairing this module mirrors). This
module reduces those already-schema-checked payloads to the small
headline/metrics/warnings shape the dashboard's module cards render, per the
``quant_summary`` schema owned by ``shared.schemas.validate_quant_summary``.

Two entry points:

* One small extractor per suite, ``_extract_vol`` / ``_extract_options`` /
  ``_extract_var`` / ``_extract_sentiment``, each ``(result: dict) -> dict``
  matching the ``modules[]`` entry shape (``module``, ``status``, ``headline``,
  ``metrics``, ``warnings``, ``source_result``). ``_extract_vol`` ports the
  gamma/correlation/variance-swap metric logic from
  ``.claude/skills/vol-suite.py::extract_metrics_from_run`` -- conceptually,
  not textually: that script scraped Vol_Suite's *CSV* output directory, while
  this extractor reads the *same* metrics (fair vol per leg, vol spread,
  dealer gamma positioning, top gamma strike, basket dispersion) from the
  validated ``vol_result.json`` dict Vol_Suite now writes for itself, which
  did not exist when the CSV-scraping script was written. An extractor never
  raises: missing/malformed input is caught and turned into a ``degraded``
  entry, because a single unreadable result must not abort the whole summary.
  ``status == "error"`` in the source result (the suite ran and reported a
  failure) is passed through as module ``status: "error"``, distinct from
  ``degraded`` (the result file couldn't be turned into a summary at all).

* ``build_run_summary(run_dir, run_id, ticker, module_registry=None) -> dict``
  -- scans *run_dir* for the known marker filenames, calls the matching
  extractor for each one present, and assembles (but does NOT write) a
  ``quant_summary``-shaped dict. The caller (``dashboard/app.py``'s
  ``_execute_run``, Task 4) is responsible for the actual
  temp-file-plus-atomic-rename write.

``module_registry`` is optional and accepts Task 5's ``MODULE_REGISTRY`` shape
-- a sequence of ``{"id": ..., "runnable": bool, ...}`` dicts. When given, it
does two things: it restricts the module set considered to exactly the ids
present in the registry (rather than the fixed four this module knows about
by default), and it implements the spec's runnable-gate short-circuit -- a
module whose registry entry has ``runnable=False`` (Options today, per the
``tool-launcher`` skill's documented context-mode FAIL) goes straight to
``status: "unsupported"`` without ever reading or attempting to extract its
result file, even if one is present on disk. As of this writing
``dashboard/quant_modules.py`` (Task 5) does not yet exist; this parameter is
forward-declared so Task 5 can wire a real registry through without changing
this function's signature. Task 5 is expected to supply
``dashboard/quant_modules.py::MODULE_REGISTRY`` here once it lands.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from shared.schemas import QUANT_SUMMARY_SCHEMA_VERSION

__all__ = [
    "build_run_summary",
]

#: The four suites this module knows how to summarize today. Mirrors
#: `shared/suite_validation.py::SUITE_REQUIREMENTS`'s key set (it deliberately
#: does not import that module -- suite_validation is about gating the
#: orchestrator's stage handoff, a different concern from this best-effort,
#: never-raising summary reducer).
_MODULE_IDS = ("vol", "options", "var", "sentiment")


def _result_filename(module_id: str) -> str:
    """The marker filename a module's result lives in, e.g. 'vol_result.json'.

    Matches `shared/suite_validation.py::SUITE_REQUIREMENTS[*].marker` for
    every module this function knows about.
    """
    return f"{module_id}_result.json"


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── helpers ──────────────────────────────────────────────────────────────


def _degraded(module_id: str, reason: str) -> Dict[str, Any]:
    """A `modules[]` entry for a result that exists but couldn't be summarized."""
    return {
        "module": module_id,
        "status": "degraded",
        "headline": reason,
        "metrics": {},
        "warnings": [reason],
        "source_result": "",
    }


def _unsupported(module_id: str) -> Dict[str, Any]:
    """A `modules[]` entry for a module gated off by `runnable=False`."""
    return {
        "module": module_id,
        "status": "unsupported",
        "headline": f"{module_id} is not currently wired up (runnable=False)",
        "metrics": {},
        "warnings": [],
        "source_result": _result_filename(module_id),
    }


def _passthrough_error(
    module_id: str, result: Dict[str, Any], default: str
) -> Dict[str, Any]:
    """A `modules[]` entry for a source result that itself reported failure."""
    return {
        "module": module_id,
        "status": "error",
        "headline": str(result.get("error") or default),
        "metrics": {},
        "warnings": [],
        "source_result": "",
    }


# ── extractors ───────────────────────────────────────────────────────────


def _extract_vol(result: Any) -> Dict[str, Any]:
    """Vol_Suite's `vol_result.json` -> a `modules[]` entry.

    Ports the metric selection from
    `.claude/skills/vol-suite.py::extract_metrics_from_run` (fair vol per
    leg, vol spread, dealer gamma positioning, top gamma strike, basket
    dispersion) onto the validated `vol_result.json` shape
    (`shared.schemas.validate_vol_result`) rather than that script's CSV scrape.
    """
    try:
        if not isinstance(result, dict):
            raise TypeError(
                f"vol result must be an object, got {type(result).__name__}"
            )

        status = result.get("status")
        if status == "error":
            return _passthrough_error("vol", result, "Vol_Suite reported an error")
        if status != "ok":
            raise ValueError(f"unexpected vol result status: {status!r}")

        vol_surface = result["vol_surface"]
        dealer = result.get("dealer_positioning") or {}
        gamma_records = result.get("gamma_records") or []

        focus = vol_surface.get("focus") or {}
        index = vol_surface.get("index") or {}
        focus_ticker = vol_surface.get("focus_ticker") or ""
        index_ticker = vol_surface.get("index_ticker") or ""
        focus_fair_vol = focus.get("fair_vol_pct")
        index_fair_vol = index.get("fair_vol_pct")
        vol_spread = vol_surface.get("vol_spread_pts")

        metrics: Dict[str, Any] = {}
        warnings: List[str] = []

        if focus_fair_vol is not None:
            metrics[f"{focus_ticker or 'focus'}_fair_vol_pct"] = focus_fair_vol
        else:
            warnings.append(f"{focus_ticker or 'focus'} leg missing fair_vol_pct")

        if index_fair_vol is not None:
            metrics[f"{index_ticker or 'index'}_fair_vol_pct"] = index_fair_vol
        else:
            warnings.append(f"{index_ticker or 'index'} leg missing fair_vol_pct")

        if vol_spread is not None:
            metrics["vol_spread_pts"] = vol_spread

        garch_cond_vol = vol_surface.get("garch_conditional_vol")
        if garch_cond_vol is not None:
            metrics["garch_conditional_vol"] = garch_cond_vol

        basket = vol_surface.get("basket") or {}
        dispersion = basket.get("dispersion_score")
        if dispersion is not None:
            metrics["basket_dispersion_score"] = dispersion

        if dealer.get("available"):
            for key in (
                "total_net_gamma",
                "total_net_dollar_gamma",
                "hedge_requirement",
                "gamma_flip_level",
            ):
                val = dealer.get(key)
                if val is not None:
                    metrics[f"dealer_{key}"] = val
        else:
            warnings.append("dealer positioning unavailable")

        if gamma_records:
            top = max(gamma_records, key=lambda r: abs(r.get("dollar_gamma") or 0))
            metrics["top_gamma_strike"] = top.get("strike")
            metrics["top_gamma_dollar"] = top.get("dollar_gamma")

        if (
            focus_fair_vol is not None
            and index_fair_vol is not None
            and vol_spread is not None
        ):
            headline = (
                f"{focus_ticker or 'Focus'} fair vol {focus_fair_vol:.1f} vs "
                f"{index_ticker or 'index'} {index_fair_vol:.1f} "
                f"(spread {vol_spread:+.1f} pts)"
            )
        elif vol_spread is not None:
            headline = f"Vol spread {vol_spread:+.1f} pts"
        elif metrics:
            headline = f"{focus_ticker or result.get('ticker') or 'Ticker'} vol surface computed"
        else:
            headline = "Vol_Suite run completed with no usable vol-surface data"

        return {
            "module": "vol",
            "status": "ok",
            "headline": headline,
            "metrics": metrics,
            "warnings": warnings,
            "source_result": "",
        }
    except Exception as exc:  # noqa: BLE001 -- extractors must never raise
        return _degraded(
            "vol", f"could not extract vol metrics: {type(exc).__name__}: {exc}"
        )


def _extract_options(result: Any) -> Dict[str, Any]:
    """Options_Suite's `options_result.json` -> a `modules[]` entry."""
    try:
        if not isinstance(result, dict):
            raise TypeError(
                f"options result must be an object, got {type(result).__name__}"
            )

        status = result.get("status")
        if status == "error":
            return _passthrough_error(
                "options", result, "Options_Suite reported an error"
            )
        if status != "ok":
            raise ValueError(f"unexpected options result status: {status!r}")

        ticker = result["ticker"]
        method = result["method"]
        price = result["price"]
        sigma = result["sigma"]
        greeks = result.get("greeks") or {}

        metrics: Dict[str, Any] = {
            "price": price,
            "implied_vol_pct": sigma * 100.0,
            "method": method,
        }
        for greek_name in ("delta", "gamma", "theta", "vega", "rho"):
            if greek_name in greeks:
                metrics[greek_name] = greeks[greek_name]

        headline = f"{ticker}: {method} priced ${price:.2f} (IV {sigma * 100:.1f}%)"

        return {
            "module": "options",
            "status": "ok",
            "headline": headline,
            "metrics": metrics,
            "warnings": [],
            "source_result": "",
        }
    except Exception as exc:  # noqa: BLE001
        return _degraded(
            "options", f"could not extract options metrics: {type(exc).__name__}: {exc}"
        )


def _extract_var(result: Any) -> Dict[str, Any]:
    """VaR_Tools_Simulations's `var_result.json` -> a `modules[]` entry."""
    try:
        if not isinstance(result, dict):
            raise TypeError(
                f"var result must be an object, got {type(result).__name__}"
            )

        status = result.get("status")
        if status == "error":
            return _passthrough_error("var", result, "VaR suite reported an error")
        if status != "ok":
            raise ValueError(f"unexpected var result status: {status!r}")

        module_name = result["module"]
        var_value = result["var"]
        cvar_value = result["cvar"]
        confidence = result["confidence"]
        horizon_days = result["horizon_days"]

        metrics: Dict[str, Any] = {
            "var": var_value,
            "cvar": cvar_value,
            "confidence": confidence,
            "horizon_days": horizon_days,
            "var_module": module_name,
        }
        headline = (
            f"{module_name}: VaR {var_value:,.2f} / CVaR {cvar_value:,.2f} "
            f"at {confidence * 100:.0f}% over {horizon_days}d"
        )

        return {
            "module": "var",
            "status": "ok",
            "headline": headline,
            "metrics": metrics,
            "warnings": [],
            "source_result": "",
        }
    except Exception as exc:  # noqa: BLE001
        return _degraded(
            "var", f"could not extract var metrics: {type(exc).__name__}: {exc}"
        )


def _extract_sentiment(result: Any) -> Dict[str, Any]:
    """sentiment-scanner's `sentiment_result.json` -> a `modules[]` entry.

    `sentiment_result.json` is the `--export-context` marker
    (`shared.schemas.validate_sentiment_context`), not the ticker-pack JSON --
    per `shared/suite_validation.py::SUITE_REQUIREMENTS['sentiment']`. It has
    no top-level `status`/`error` field of its own, so unlike the other three
    extractors there is no `error` passthrough branch here: a malformed or
    incomplete block falls straight through to `degraded` via the exception
    handler below.
    """
    try:
        if not isinstance(result, dict):
            raise TypeError(
                f"sentiment result must be an object, got {type(result).__name__}"
            )

        block = result.get("sentiment")
        if not isinstance(block, dict):
            raise ValueError("missing 'sentiment' block")

        ranked = block.get("ranked_tickers")
        if not isinstance(ranked, list):
            raise ValueError("sentiment.ranked_tickers missing or not a list")

        group_id = block.get("group_id")
        pack_json_path = block.get("pack_json_path")

        metrics: Dict[str, Any] = {"ranked_ticker_count": len(ranked)}
        if group_id:
            metrics["group_id"] = group_id

        top_names = ", ".join(str(t) for t in ranked[:5])
        if top_names:
            headline = f"Sentiment scan ranked {len(ranked)} tickers; top: {top_names}"
        else:
            headline = "Sentiment scan produced no ranked tickers"

        warnings: List[str] = []
        if not pack_json_path:
            warnings.append("no pack_json_path recorded (sentiment pack not exported)")

        return {
            "module": "sentiment",
            "status": "ok",
            "headline": headline,
            "metrics": metrics,
            "warnings": warnings,
            "source_result": "",
        }
    except Exception as exc:  # noqa: BLE001
        return _degraded(
            "sentiment",
            f"could not extract sentiment metrics: {type(exc).__name__}: {exc}",
        )


_EXTRACTORS: Dict[str, Callable[[Any], Dict[str, Any]]] = {
    "vol": _extract_vol,
    "options": _extract_options,
    "var": _extract_var,
    "sentiment": _extract_sentiment,
}


# ── build_run_summary ────────────────────────────────────────────────────


def _module_ids_and_runnable(
    module_registry: Optional[Sequence[Dict[str, Any]]],
) -> tuple:
    """Resolve which module ids to consider and their runnable flags.

    Without a registry, falls back to the fixed four suites this module knows
    about, all treated as runnable (the pre-Task-5 default). With a registry
    (Task 5's `MODULE_REGISTRY` shape), only registry-listed ids that this
    module also has an extractor for are considered, using each entry's own
    `runnable` flag (defaulting to True if the key is absent).
    """
    if module_registry is None:
        return list(_MODULE_IDS), {mid: True for mid in _MODULE_IDS}

    module_ids: List[str] = []
    runnable_map: Dict[str, bool] = {}
    for entry in module_registry:
        mid = entry.get("id")
        if mid not in _EXTRACTORS:
            continue
        module_ids.append(mid)
        runnable_map[mid] = bool(entry.get("runnable", True))
    return module_ids, runnable_map


def build_run_summary(
    run_dir: Union[str, Path],
    run_id: str,
    ticker: str,
    module_registry: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Scan *run_dir* for `*_result.json` markers and assemble a
    `quant_summary`-shaped dict (does NOT write it -- see module docstring).

    For each module considered (see `_module_ids_and_runnable`):

    * `runnable=False` (per *module_registry*) -> `status: "unsupported"`,
      the result file is never opened.
    * marker file absent -> the module is skipped entirely (no entry). A
      suite that never ran has nothing to summarize; this is not the same as
      a suite that ran and failed, which does get an `error`/`degraded` entry.
    * marker file present but unreadable/malformed JSON -> `status: "degraded"`.
    * marker file present and parses -> handed to the module's extractor,
      which itself never raises (see extractor docstrings).

    Raises nothing itself for a bad *run_dir* other than what `Path.iterdir`/
    `open` would (a caller passing a directory that doesn't exist at all is a
    programming error, not a runtime condition this function degrades).
    """
    run_path = Path(run_dir)
    module_ids, runnable_map = _module_ids_and_runnable(module_registry)

    modules_out: List[Dict[str, Any]] = []

    for module_id in module_ids:
        if not runnable_map.get(module_id, True):
            modules_out.append(_unsupported(module_id))
            continue

        filename = _result_filename(module_id)
        result_path = run_path / filename
        if not result_path.is_file():
            continue

        try:
            raw = result_path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            entry = _degraded(
                module_id,
                f"{filename} could not be read/parsed: {type(exc).__name__}: {exc}",
            )
            entry["source_result"] = filename
            modules_out.append(entry)
            continue

        entry = _EXTRACTORS[module_id](data)
        entry["module"] = module_id
        entry["source_result"] = filename
        modules_out.append(entry)

    return {
        "schema_version": QUANT_SUMMARY_SCHEMA_VERSION,
        "run_id": run_id,
        "ticker": ticker,
        "created_at_utc": _iso_utc_now(),
        "modules": modules_out,
    }
