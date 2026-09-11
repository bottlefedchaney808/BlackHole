"""module_registry.py (sentiment-scanner)

Phase 4 of the Modularization Overhaul: thin ModuleSpec adapters over the
existing 7 scanner scan_*() functions. No new scanner logic.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Any

_SENTIMENT_DIR = Path(__file__).resolve().parent
if str(_SENTIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_SENTIMENT_DIR))
_REPO_ROOT = _SENTIMENT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.module_registry import (
    ArchiveHint,
    InputSpec,
    ModuleResult,
    ModuleSpec,
    ParamSpec,
)


def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(
        status="failed",
        artifacts=[],
        metrics={"error": str(exc)},
        context_patch=None,
    )


def _payload(res: Any) -> dict[str, Any]:
    """Flatten a scan_*() dataclass (or dict) into a JSON-shaped dict."""
    if dataclasses.is_dataclass(res) and not isinstance(res, type):
        return dataclasses.asdict(res)
    if isinstance(res, dict):
        return dict(res)
    return {"value": res}


def _ok(res: Any, patch_key: str, headline: str | None = None) -> ModuleResult:
    """Build a ModuleResult that puts the scan payload on the CARD as well as
    in the pipe.

    Every scanner here used to return ``metrics={"ticker": ticker}`` and hand
    the entire payload to ``context_patch``. Since the widget UI renders
    ``metrics``, that made a perfectly successful scan render as a card
    showing one row ("ticker: SPY") -- the single biggest reason the widgets
    looked broken. ``metrics`` and ``context_patch`` are two audiences (human
    / next module), not two alternatives, so the payload now goes to both.

    A scanner that reports its own ``error`` field surfaces as
    ``status="failed"`` instead of a green "ok" pill over an error row.
    """
    payload = _payload(res)
    error = payload.get("error")
    metrics: dict[str, Any] = {}
    if headline:
        metrics["headline"] = headline
    metrics.update(payload)
    return ModuleResult(
        status="failed" if error else "ok",
        artifacts=[],
        metrics=metrics,
        context_patch={patch_key: res},
    )


def _fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    """Format a scalar for a headline; '--' when the scanner had no value."""
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value}{suffix}"


# ---------------------------------------------------------------------------


def _explicit_expiry(context: dict[str, Any]) -> str | None:
    """The scope's expiry, or None when the caller said "pick one for me".

    Vol_Suite's modules use the sentinel string ``"auto"`` for "resolve the
    expiry yourself" (see `Vol_Suite/module_registry.py::_resolve_expiry`),
    and the dashboard's scope bar passes whatever is in the expiry box
    straight through -- including an empty box, and including ``auto`` when a
    Vol_Suite card put it there. Callers that want a *date* therefore have to
    map those to None rather than handing the literal string ``"auto"`` to a
    chain lookup, which fails deep inside the vendor call with an unhelpful
    parse error.
    """
    focus = context.get("focus") or {}
    raw = context.get("expiry") or focus.get("expiry") or focus.get("expiration_date")
    text = str(raw or "").strip()
    return None if text.lower() in ("", "auto", "none", "null") else text


def _run_gex(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.gex_scanner import scan_gex

        ticker = context.get("ticker", "SPY")
        res = scan_gex(ticker)
        return _ok(
            res,
            "gex_result",
            headline=(
                f"{ticker} net dealer gamma "
                f"{_fmt(getattr(res, 'total_net_dollar_gamma', None), digits=0)}"
            ),
        )
    except Exception as exc:
        return _failed(exc)


def _run_unusual_oi(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.unusual_oi_scanner import scan_unusual_oi

        ticker = context.get("ticker", "SPY")
        res = scan_unusual_oi(ticker)
        return _ok(
            res,
            "unusual_oi_result",
            headline=(
                f"{ticker} OI {_fmt(getattr(res, 'current_total_oi', None), digits=0)} "
                f"vs baseline {_fmt(getattr(res, 'baseline_total_oi', None), digits=0)}"
                + (" · SURGE" if getattr(res, "surge_detected", False) else "")
            ),
        )
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
        return _ok(
            res,
            "iv_rank_result",
            headline=(
                f"{ticker} {getattr(res, 'regime', '')} · "
                f"IV {_fmt(getattr(res, 'atm_iv_pct', None), '%')} vs "
                f"RV30 {_fmt(getattr(res, 'rv_30_pct', None), '%')} · "
                f"VRP {_fmt(getattr(res, 'vrp_pct', None), ' pts')}"
            ),
        )
    except Exception as exc:
        return _failed(exc)


def _run_skew(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.skew_scanner import scan_skew

        ticker = context.get("ticker", "SPY")
        res = scan_skew(ticker)
        return _ok(
            res,
            "skew_result",
            headline=(
                f"{ticker} {getattr(res, 'skew_signal', '')} · "
                f"put skew {_fmt(getattr(res, 'put_skew_pts', None), ' pts')} · "
                f"ATM IV {_fmt(getattr(res, 'atm_iv_pct', None), '%')}"
            ),
        )
    except Exception as exc:
        return _failed(exc)


def _run_max_pain(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.max_pain_scanner import scan_max_pain

        ticker = context.get("ticker", "SPY")
        focus = context.get("focus") or {}
        garch = focus.get("garch_conditional_vol") or focus.get("garch_cond_vol_pct")
        fair = focus.get("fair_vol_pct")
        # "auto" is Vol_Suite's sentinel for "resolve it yourself", and the
        # shared scope bar passes it through verbatim -- scan_max_pain wants a
        # date or None, and got the literal string.
        expiry = _explicit_expiry(context)
        res = scan_max_pain(
            ticker, expiry=expiry, garch_cond_vol_pct=garch, fair_vol_pct=fair
        )
        return _ok(
            res,
            "max_pain_result",
            headline=(
                f"{ticker} max pain {_fmt(getattr(res, 'max_pain_strike', None))} "
                f"vs spot {_fmt(getattr(res, 'spot', None))}"
            ),
        )
    except Exception as exc:
        return _failed(exc)


def _run_vol_dispersion(context: dict[str, Any]) -> ModuleResult:
    try:
        from scanner.vol_dispersion_scanner import scan_vol_dispersion

        ticker = context.get("ticker", "SPY")
        res = scan_vol_dispersion(ticker)
        return _ok(
            res,
            "vol_dispersion_result",
            headline=(f"{ticker} vol dispersion"),
        )
    except Exception as exc:
        return _failed(exc)


def _run_earnings(context: dict[str, Any]) -> ModuleResult:
    """Earnings vol premium: ATM IV of the expiry after earnings vs before.

    Two things were wrong here and both raised before any scanning happened,
    so this module could never have returned a result:

    * the import was `from earnings_scanner import ...`, but the module lives
      in the `scanner` package alongside its six siblings (every other runner
      in this file already uses `from scanner.X import ...`) -- so it failed
      with `No module named 'earnings_scanner'`;
    * the name it imported, `scan_earnings_vol_premium`, does not exist in
      that module at all. The single-ticker entry point is `scan_ticker`.

    `scan_ticker` returns None when the ticker has no locatable earnings date,
    which is a data condition rather than a failure -- it surfaces as
    `skipped` with the reason, not a red card.
    """
    try:
        from scanner.earnings_scanner import scan_ticker

        ticker = str(context.get("ticker") or "SPY").strip().upper()
        res = scan_ticker(ticker)
        if res is None:
            return ModuleResult(
                status="skipped",
                artifacts=[],
                metrics={
                    "ticker": ticker,
                    "message": (
                        f"no upcoming earnings date resolved for {ticker}, or no "
                        "usable expiry either side of it"
                    ),
                },
                context_patch=None,
            )
        # Don't print a premium the scan did not measure. When the scanner
        # sets `error` (e.g. no_suitable_expiry_before_or_after), premium_pct
        # is still 0.0 from the dataclass default, and "+0.00 pts" reads as
        # "measured, and it's flat" rather than "not measured".
        if getattr(res, "error", None):
            headline = f"{ticker} earnings vol premium unavailable: {res.error}"
        else:
            headline = (
                f"{ticker} earnings vol premium "
                f"{getattr(res, 'premium_pct', float('nan')):+.2f} pts "
                f"({getattr(res, 'signal', '?')})"
            )
        return _ok(res, "earnings_result", headline=headline)
    except Exception as exc:
        return _failed(exc)


# ---------------------------------------------------------------------------
# highlight_packs
# ---------------------------------------------------------------------------
#
# The scanner writes "highlighted ticker packs" -- named groups of tickers
# that crossed a contested-narrative threshold on a run, each with a thesis
# summary and downstream hints -- to
# `data/exports/highlighted_ticker_packs/<YYYYMMDD>/`, with a
# `latest_manifest.json` at the root pointing at the newest of each group.
#
# Until now the only way to see them was the Output tab's filesystem
# scraper, which dumps the CSV and the JSON of every pack as two separate
# raw blobs with no way to act on either. As a module they become a basket
# source: the pack's tickers ride out on `context_patch` as `held_tickers`,
# which is the same key the position book publishes and the same key every
# tool run is seeded with -- so "scan found these eight names" and "run the
# vol tools over them" become one step instead of copy-paste.


def _packs_root() -> Path:
    return _SENTIMENT_DIR / "data" / "exports" / "highlighted_ticker_packs"


def _run_highlight_packs(context: dict[str, Any]) -> ModuleResult:
    """Load the newest highlighted-ticker packs from the scanner's manifest."""
    import json

    try:
        root = _packs_root()
        manifest_path = root / "latest_manifest.json"
        if not manifest_path.exists():
            return ModuleResult(
                status="idle",
                artifacts=[],
                metrics={
                    "message": (
                        f"no pack manifest at {manifest_path} -- run the "
                        "sentiment scanner to produce one"
                    )
                },
                context_patch=None,
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        packs = list(manifest.get("packs") or [])

        # Optional filters, both exposed as card params.
        group = str(context.get("pack_group") or "").strip()
        if group and group.lower() != "all":
            packs = [p for p in packs if p.get("group_name") == group]
        priority = str(context.get("pack_priority") or "").strip()
        if priority and priority.lower() != "any":
            packs = [p for p in packs if str(p.get("priority", "")) == priority]

        packs.sort(key=lambda p: str(p.get("last_updated_at") or ""), reverse=True)
        limit = int(context.get("pack_limit") or 5)
        packs = packs[: max(1, limit)]
    except Exception as exc:
        return _failed(exc)

    rows = []
    every_ticker: list[str] = []
    for pack in packs:
        tickers = [str(t).upper() for t in (pack.get("tickers") or [])]
        for t in tickers:
            if t not in every_ticker:
                every_ticker.append(t)
        rows.append(
            {
                "group": pack.get("group_name"),
                "priority": pack.get("priority"),
                "tickers": ", ".join(tickers),
                "count": pack.get("ticker_count", len(tickers)),
                "thesis": pack.get("thesis_summary"),
                "updated": pack.get("last_updated_at"),
                "run_id": pack.get("source_run_id"),
            }
        )

    # ".X" suffixes are the scanner's own crypto/FX notation and are not
    # option-chain roots; keep them visible in the rows but out of a basket
    # the pricing tools will be pointed at.
    basket = [t for t in every_ticker if "." not in t]

    metrics: dict[str, Any] = {
        "headline": (
            f"{len(rows)} pack{'' if len(rows) == 1 else 's'} · "
            f"{len(basket)} tradable ticker{'' if len(basket) == 1 else 's'}"
            + (f" · updated {manifest.get('updated_at', '')[:16]}" if manifest.get("updated_at") else "")
        ),
        "packs": rows,
        "tickers": basket,
        "all_tickers": every_ticker,
    }
    return ModuleResult(
        status="ok" if rows else "skipped",
        artifacts=[],
        metrics=metrics,
        # `held_tickers` is deliberately the same key the position book
        # publishes: whichever ran most recently is what the tools pick up.
        context_patch={"held_tickers": basket} if basket else None,
    )


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
        description="Gamma exposure scanner: net dollar gamma by strike/expiry.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
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
        description="Flags strikes/expirations with unusual open-interest patterns.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
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
        description="Implied-vol rank vs historical realized vol.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
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
        description="Option-implied skew metrics by expiry.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
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
        description="Max pain strike concentration per expiry.",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={"ticker": "SPY", "expiry": "20261016"},
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
        description="Index-vs-component implied-vol dispersion scanner.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
    ),
    ModuleSpec(
        name="Earnings",
        slug="earnings",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_earnings,
        cli_entry="sentiment-scanner/scanner/earnings_scanner.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Earnings implied-move and vol-premium scanner.",
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
    ),
    ModuleSpec(
        name="Highlight Packs",
        slug="highlight_packs",
        suite="sentiment_scanner",
        category="scanner",
        run=_run_highlight_packs,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description=(
            "Newest highlighted-ticker packs from the scanner. Publishes "
            "their tickers as the basket for every other tool."
        ),
        inputs=InputSpec(),
        output_kind="metrics",
        sample={},
        params=(
            ParamSpec(
                name="pack_group",
                label="Group",
                kind="text",
                default="all",
                help="Pack group name to filter to, or 'all'.",
            ),
            ParamSpec(
                name="pack_priority",
                label="Priority",
                kind="choice",
                default="any",
                choices=("any", "high", "medium", "low"),
            ),
            ParamSpec(
                name="pack_limit",
                label="Max packs",
                kind="number",
                default=5,
            ),
        ),
    ),
]


def resolve_modules(slugs: list[str]) -> list[ModuleSpec]:
    """Local resolver for this suite's slugs (Phase 4)."""
    by_slug = {m.slug: m for m in MODULES}
    return [by_slug[s] for s in slugs if s in by_slug]

