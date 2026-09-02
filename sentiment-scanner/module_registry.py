"""module_registry.py (sentiment-scanner)

Phase 4 of the Modularization Overhaul: thin ModuleSpec adapters over the
existing 7 scanner scan_*() functions. No new scanner logic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_SENTIMENT_DIR = Path(__file__).resolve().parent
if str(_SENTIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_SENTIMENT_DIR))
_REPO_ROOT = _SENTIMENT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.module_registry import ArchiveHint, ArtifactRef, ModuleResult, ModuleSpec

def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(
        status="failed",
        artifacts=[],
        metrics={"error": str(exc)},
        context_patch=None,
    )

# ---------------------------------------------------------------------------


def _run_gex(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.gex_scanner import scan_gex
        ticker = context.get("ticker", "SPY")
        res = scan_gex(ticker)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "total_net_dollar_gamma": getattr(res, "total_net_dollar_gamma", 0)},
            context_patch={"gex_result": res},
        )
    except Exception as exc:
        return _failed(exc)


def _run_unusual_oi(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.unusual_oi_scanner import scan_unusual_oi
        ticker = context.get("ticker", "SPY")
        res = scan_unusual_oi(ticker)
        return ModuleResult(status="ok", artifacts=[], metrics={"ticker": ticker}, context_patch={"unusual_oi_result": res})
    except Exception as exc:
        return _failed(exc)


def _run_iv_rank(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.iv_rank_scanner import scan_iv_rank
        ticker = context.get("ticker", "SPY")
        focus = context.get("focus") or {}
        garch = focus.get("garch_conditional_vol") or focus.get("garch_cond_vol_pct")
        fair = focus.get("fair_vol_pct")
        res = scan_iv_rank(ticker, garch_cond_vol_pct=garch, fair_vol_pct=fair)
        return ModuleResult(status="ok", artifacts=[], metrics={"ticker": ticker}, context_patch={"iv_rank_result": res})
    except Exception as exc:
        return _failed(exc)


def _run_skew(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.skew_scanner import scan_skew
        ticker = context.get("ticker", "SPY")
        res = scan_skew(ticker)
        return ModuleResult(status="ok", artifacts=[], metrics={"ticker": ticker}, context_patch={"skew_result": res})
    except Exception as exc:
        return _failed(exc)


def _run_max_pain(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.max_pain_scanner import scan_max_pain
        ticker = context.get("ticker", "SPY")
        focus = context.get("focus") or {}
        garch = focus.get("garch_conditional_vol") or focus.get("garch_cond_vol_pct")
        fair = focus.get("fair_vol_pct")
        expiry = focus.get("expiration_date") or focus.get("expiry") or context.get("expiry")
        res = scan_max_pain(ticker, expiry=expiry, garch_cond_vol_pct=garch, fair_vol_pct=fair)
        return ModuleResult(status="ok", artifacts=[], metrics={"ticker": ticker}, context_patch={"max_pain_result": res})
    except Exception as exc:
        return _failed(exc)


def _run_vol_dispersion(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.vol_dispersion_scanner import scan_vol_dispersion
        ticker = context.get("ticker", "SPY")
        res = scan_vol_dispersion(ticker)
        return ModuleResult(status="ok", artifacts=[], metrics={"ticker": ticker}, context_patch={"vol_dispersion_result": res})
    except Exception as exc:
        return _failed(exc)


def _run_earnings(context: dict[str, Any]) -> ModuleResult:
    try:
        from earnings_scanner import scan_earnings_vol_premium
        ticker = context.get("ticker", "SPY")
        res = scan_earnings_vol_premium(ticker)
        return ModuleResult(status="ok", artifacts=[], metrics={"ticker": ticker}, context_patch={"earnings_result": res})
    except Exception as exc:
        return _failed(exc)


# ---------------------------------------------------------------------------


MODULES: list[ModuleSpec] = [
    ModuleSpec(
        name="GEX Scanner",
        slug="gex",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_gex,
        cli_entry="sentiment-scanner/scanner/gex_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Unusual OI",
        slug="unusual_oi",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_unusual_oi,
        cli_entry="sentiment-scanner/scanner/unusual_oi_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="IV Rank",
        slug="iv_rank",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_iv_rank,
        cli_entry="sentiment-scanner/scanner/iv_rank_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Skew",
        slug="skew",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_skew,
        cli_entry="sentiment-scanner/scanner/skew_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Max Pain",
        slug="max_pain",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_max_pain,
        cli_entry="sentiment-scanner/scanner/max_pain_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Vol Dispersion",
        slug="vol_dispersion",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_vol_dispersion,
        cli_entry="sentiment-scanner/scanner/vol_dispersion_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Earnings",
        slug="earnings",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_earnings,
        cli_entry="sentiment-scanner/earnings_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
]


def resolve_modules(slugs: list[str]) -> list[ModuleSpec]:
    """Local resolver for this suite's slugs (Phase 4)."""
    by_slug = {m.slug: m for m in MODULES}
    return [by_slug[s] for s in slugs if s in by_slug]
