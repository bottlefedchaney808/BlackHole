"""orchestrator.py -- module-runner CLI + shared helpers (post-Phase 7).

Suite-level launching (``--unified`` / ``--suite`` / ``--interactive``,
subprocess child runs, marker-file validation, the in-memory run registry)
was removed 2026-09-04 in Phase 7 of the widget-native quant console plan.
The replacement paths:

- Interactive/programmatic runs: the dashboard's generic widget API --
  ``POST /api/widgets/{slug}/run`` (synchronous, in-process, no child
  processes). Catalog: ``GET /api/widgets/catalog``.
- Scripted/CI runs: this file's ``--modules`` / ``--modules-category`` /
  ``--all-modules`` CLI, which calls
  ``shared.module_execution.run_selected_modules`` in-process (the same
  machinery, relocated there in Phase 7 step 2; this module re-exports it).

What still lives here, deliberately:

- ``build_context`` -- the shared suite-context builder every module run
  starts from.
- ``run_market_signals_stage`` -- the in-process option-chain scanner stage
  (vol -> market-signals ordering is preserved by callers that need it).
- ``shared/module_execution`` shim -- ``run_selected_modules`` and friends
  re-exported so existing importers keep working; the archiver hook
  (``_archive_module_result``) is routed through by that machinery.
- DB helpers used across the repo: ``DB_PATH``, ``get_recent_swap_activity``,
  ``log_run`` (writes ``orchestrator_runs`` history rows).
- ``discover_adapters`` -- external data-source adapters (dashboard reads it).
- ``_find_shared_python`` / ``SHARED_PYTHON`` -- the consolidated root venv
  interpreter path, still referenced by external tooling.

The historical ``run_unified``/``run_suite`` design notes are preserved in
git history (pre-Phase-7 revisions of this file) and in
docs/superpowers/plans/2026-09-03-widget-native-quant-console-plan.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.logging import setup_logging

# Setup structured JSON logging
logger = setup_logging(
    name="orchestrator",
    level=logging.INFO,
    use_json=True,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

# `shared` is a root-level package. When orchestrator.py is launched by path
# from another cwd, ROOT is not automatically importable, so pin it before the
# shared import below.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.context_audit import (
    ContextMutationAudit,
    audit_sentiment_mutation,
)
from shared.module_registry import (
    all_modules,
    resolve_modules,
)
from shared.suite_validation import (
    ValidationResult,
    marker_filename,
    validate_suite_output,
)


def _find_shared_python() -> str:
    """Locate the interpreter in the consolidated root `.venv`, cross-platform.

    Every child suite is launched with this interpreter, never `sys.executable`
    -- see the module docstring. Windows venvs place it at
    `.venv/Scripts/python.exe`; POSIX venvs (Linux/Mac) place it at
    `.venv/bin/python` (often alongside a `python3` symlink). We check the
    real filesystem rather than branching on `os.name` alone so a venv created
    inside e.g. WSL or Git Bash on Windows still resolves correctly.
    """
    venv_dir = Path(ROOT) / ".venv"
    candidates = [
        venv_dir / "Scripts" / "python.exe",  # Windows
        venv_dir / "bin" / "python3",  # Linux / Mac
        venv_dir / "bin" / "python",  # Linux / Mac fallback
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    # Nothing found -- return the platform-appropriate default path anyway so
    # the `os.path.exists(SHARED_PYTHON)` check below fails with a clear
    # "shared interpreter not found" message instead of a confusing crash.
    default = (
        venv_dir / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv_dir / "bin" / "python"
    )
    return str(default)


# The consolidated virtualenv. Every child is launched with this interpreter,
# never with `sys.executable` -- see the module docstring.
SHARED_PYTHON = _find_shared_python()

SUITE_ROOTS = {
    "options": os.path.join(ROOT, "Options_Suite"),
    "vol": os.path.join(ROOT, "Vol_Suite"),
    "var": os.path.join(ROOT, "VaR_Tools_Simulations"),
    "sentiment": os.path.join(ROOT, "sentiment-scanner"),
}

# Same default as Vol_Suite's `_CHILD_SUITE_TIMEOUT_SEC`, and honours the same
# environment override, so raising it for a slow box raises it everywhere.
DEFAULT_TIMEOUT_SEC = int(os.environ.get("SUITE_CHILD_TIMEOUT_SEC", "1800"))

# SWAPS_DB_PATH env var overrides, e.g. for a mounted Docker volume; see
# .env.example / docker-compose.yml
DB_PATH = os.environ.get("SWAPS_DB_PATH") or os.path.join(ROOT, "swaps.db")


def _warn_if_schema_outdated() -> None:
    """Startup check: warn on stderr if swaps.db is behind the latest
    migration in migrations/, but never block startup over it.

    A stale schema is a "go run setup_db.py --migrate" nudge, not a reason
    to abort a suite run -- most orchestrator runs don't touch swap_trades
    columns that a pending migration would add, and get_recent_swap_activity
    already treats a cold/missing swaps.db as an optional enrichment. Import
    of setup_db is done lazily, and any failure here (e.g. setup_db.py
    itself missing) is swallowed, for the same reason.
    """
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import setup_db

        setup_db.check_schema_version(DB_PATH)
    except Exception as e:
        logger.warning(f"Schema version check skipped: {e}")


# Per-suite launch spec. `flags` is a callable taking (ctx_path, out_path) so
# the sentiment producer can use its own flag names without a special case at
# the call site.
#
# `writes_context_out` is True for every entry now. It was a real distinction
# only while Vol_Suite had no `--context-out` writer; keeping the key means a
# future producer-shaped suite can be added without reintroducing an implicit
# rule about which suites are exempt from the marker invariant.
_SUITE_SPECS: dict[str, dict[str, Any]] = {
    "options": {
        "entrypoint": "main.py",
        "flags": lambda ctx, out: ["--context", ctx, "--context-out", out],
        "writes_context_out": True,
        "note": "",
    },
    "var": {
        "entrypoint": "main.py",
        "flags": lambda ctx, out: ["--context", ctx, "--context-out", out],
        "writes_context_out": True,
        "note": "",
    },
    "sentiment": {
        # Producer, not consumer: it has no --context, only --export-context,
        # and it loops forever without --no-loop.
        # Skip YouTube scanner for orchestrator speed (not critical for context).
        "entrypoint": "main.py",
        "flags": lambda ctx, out: [
            "--export-context",
            out,
            "--no-loop",
            "--skip-youtube",
        ],
        "writes_context_out": True,
        "note": "context producer: --export-context (schema_version 2 sentiment block)",
    },
    "vol": {
        "entrypoint": "volatility_suite.py",
        "flags": lambda ctx, out: ["--context", ctx, "--context-out", out, "--no-loop"],
        "writes_context_out": True,
        "note": "vol_result.json: vol surface + dealer positioning + gamma records",
    },
}


# --------------------------------------------------------------------------
# suite_context reuse
# --------------------------------------------------------------------------


def _import_suite_context():
    """Import Vol_Suite/suite_context.py so the context is built and validated
    by the module that owns the schema, not by a copy of it that can drift."""
    vol_root = SUITE_ROOTS["vol"]
    if vol_root not in sys.path:
        sys.path.insert(0, vol_root)
    import suite_context

    return suite_context


def _import_sentiment_scanners():
    """Import the 4 non-social option-chain scanners directly (IV Rank, Max
    Pain, Skew, Unusual OI) -- no StockTwits/Reddit/YouTube/GEX, no ticker
    discovery, since the orchestrator already knows the ticker."""
    sentiment_root = SUITE_ROOTS["sentiment"]
    if sentiment_root not in sys.path:
        sys.path.insert(0, sentiment_root)
    from scanner.iv_rank_scanner import format_iv_rank, scan_iv_rank
    from scanner.max_pain_scanner import format_max_pain, scan_max_pain
    from scanner.skew_scanner import format_skew, scan_skew
    from scanner.unusual_oi_scanner import format_unusual_oi, scan_unusual_oi

    return (
        scan_iv_rank,
        format_iv_rank,
        scan_max_pain,
        format_max_pain,
        scan_skew,
        format_skew,
        scan_unusual_oi,
        format_unusual_oi,
    )


def _import_var_engine_builders():
    """Import the non-interactive builder functions from VaR_Tools_Simulations
    /main.py (mc_sim, copula, corr_sim, hedge_optimizer). Safe to import --
    main.py only launches the interactive CLI under `if __name__ == '__main__'`.

    Loaded under a unique module name via importlib rather than `import main`
    -- Options_Suite, VaR_Tools_Simulations and sentiment-scanner each have
    their own `main.py`, so a bare `import main` returns whichever one first
    landed in sys.modules under that generic name (e.g. from an earlier
    Tools/ call in the same long-lived dashboard process), silently handing
    back a module missing these builder functions instead of raising.
    """
    var_root = SUITE_ROOTS["var"]
    if var_root not in sys.path:
        sys.path.insert(0, var_root)
    module_name = "var_tools_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(var_root, "main.py")
    )
    var_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = var_main
    spec.loader.exec_module(var_main)
    return var_main


def _import_direction_suite():
    """Import the 5-tool Direction signal suite (whale/elliott/bollinger/
    trend/liquidity, combined into one conviction call)."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from Direction.signal_generator import generate

    return generate


def run_market_signals_stage(ticker: str, context: dict[str, Any]) -> dict[str, Any]:
    """Replaces the old sentiment-scanner stage: option-chain scanners that
    don't need social-media scraping, three 1-year-out simulations seeded
    from live spot + GARCH vol, and the 5-tool Direction suite.

    Every sub-piece is independently try/excepted -- one failing scanner or
    simulation must not blank out the rest of the bundle.
    """
    import dataclasses

    def _to_jsonable(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        return obj

    bundle: dict[str, Any] = {
        "suite": "sentiment",
        "status": "ok",
        "ticker": ticker,
        "timestamp": _iso_utc_now(),
        "scanners": {},
        "simulations": {},
        "direction": None,
    }
    errors: list[str] = []

    # ---- option-chain scanners via registry (Phase 4) ----
    try:
        import sys
        from pathlib import Path
        import importlib.util
        import types
        root = Path(__file__).resolve().parent
        sdir = root / "sentiment-scanner"
        if str(sdir) not in sys.path:
            sys.path.insert(0, str(sdir))
        # load the registry module under the expected 'sentiment_scanner' name
        spec = importlib.util.spec_from_file_location(
            "sentiment_scanner.module_registry", str(sdir / "module_registry.py")
        )
        reg_mod = importlib.util.module_from_spec(spec)
        sys.modules["sentiment_scanner.module_registry"] = reg_mod
        # also provide the package for 'import sentiment_scanner.module_registry'
        if "sentiment_scanner" not in sys.modules:
            pkg = types.ModuleType("sentiment_scanner")
            pkg.__path__ = [str(sdir)]
            sys.modules["sentiment_scanner"] = pkg
        spec.loader.exec_module(reg_mod)
        resolve_modules = reg_mod.resolve_modules
        scanner_slugs = ["iv_rank", "max_pain", "skew", "unusual_oi"]
        for scanner_spec in resolve_modules(scanner_slugs):
            try:
                res = scanner_spec.run({"ticker": ticker, "focus": context.get("focus") or {}})
                bundle["scanners"][scanner_spec.slug] = res.metrics or {}
            except Exception as e:
                errors.append(f"{scanner_spec.slug}: {e}")
                bundle["scanners"][scanner_spec.slug] = {"error": str(e)}
    except Exception as e:
        errors.append(f"sentiment registry: {e}")

    # (old scanner reimpl removed; Phase 4 registry active)

    # ---- 1-year-out simulations (MC, copula, correlation) ----
    # This stage runs after Vol_Suite in dashboard-driven runs specifically so
    # `context['focus']['garch_conditional_vol']` -- threaded in by
    # reading vol stats from the Context Store (Vol_Suite's GARCH(1,1) fit) --
    # is already present. The builders' own vol resolution prefers that
    # context value and only falls back to fitting GARCH themselves (once,
    # not once-per-builder -- see `_resolve_vol_and_quality`) when it's
    # missing, e.g. because Vol_Suite failed or found too little history.
    # This used to fit GARCH a second time here unconditionally (once for
    # this stage's own sims, once again inside Vol_Suite moments later) --
    # doubling ThetaData load for every unified run's focus ticker.
    try:
        var_main = _import_var_engine_builders()
        for key, builder in (
            ("mc_sim", var_main._build_mc_sim_from_context),
            ("copula", var_main._build_copula_from_context),
            ("corr_sim", var_main._build_corr_sim_peer_from_context),
        ):
            try:
                sim_result = builder(context, ticker)
                bundle["simulations"][key] = sim_result
                # Bulk array-shaped fields (matrices, the 20-bin terminal-price
                # histogram) are kept in the bundle but out of the console line.
                _noisy = (
                    "correlation_matrix",
                    "sim_vols",
                    "sim_corr",
                    "terminal_price_histogram",
                )
                print(
                    f"  [{key}] {json.dumps({k: v for k, v in sim_result.items() if k not in _noisy})}"
                )
            except Exception as e:
                msg = f"{key} sim failed: {e}"
                print(f"  [market-signals] {msg}")
                errors.append(msg)
                bundle["simulations"][key] = {"error": str(e)}
    except Exception as e:
        errors.append(f"var_engine import failed: {e}")
        print(f"  [market-signals] var_engine import failed: {e}")

    # ---- Direction 5-tool suite ----
    try:
        generate = _import_direction_suite()
        direction = generate(ticker)
        bundle["direction"] = direction
        sig = direction.get("signals", {})
        print(
            f"  [direction] {ticker}: {direction.get('conviction')} | "
            f"score {direction.get('score')}/5 | "
            + " ".join(f"{k}={'ON' if v else 'off'}" for k, v in sig.items())
        )
    except Exception as e:
        errors.append(f"direction suite failed: {e}")
        print(f"  [market-signals] direction suite failed: {e}")

    if errors:
        bundle["status"] = (
            "partial"
            if any(
                bundle["scanners"].get(k, {}).get("error") is None
                for k in ("iv_rank", "max_pain", "skew", "unusual_oi")
            )
            or bundle["direction"] is not None
            else "error"
        )
        bundle["errors"] = errors

    return bundle


def _import_volatility_suite():
    """Import Vol_Suite/volatility_suite.py so basket resolution reuses the
    same index-constituents logic the interactive flow uses (_resolve_basket),
    instead of the orchestrator silently defaulting every run to a one-name
    basket. Import-safe: main() only runs under __main__."""
    vol_root = SUITE_ROOTS["vol"]
    if vol_root not in sys.path:
        sys.path.insert(0, vol_root)
    import volatility_suite

    return volatility_suite


def _iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run_id_now() -> str:
    now = datetime.now(UTC)
    # Microsecond suffix: concurrent unified runs launched in the same second
    # previously collided on one run_id and clobbered each other's output dir.
    return now.strftime("%Y%m%dT%H%M%SZ") + f"{now.microsecond:06d}"


def _default_manifest_path() -> str:
    """Same location Vol_Suite's `_default_pack_manifest_path()` points at."""
    return os.path.join(
        SUITE_ROOTS["sentiment"],
        "data",
        "exports",
        "highlighted_ticker_packs",
        "latest_manifest.json",
    )


# --------------------------------------------------------------------------
# swaps.db
# --------------------------------------------------------------------------


def _get_connection() -> sqlite3.Connection:
    """Same connection pattern as db_loader.SwapsLoader.get_connection()."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_recent_swap_activity(limit: int = 50) -> list[dict[str, Any]]:
    """Recent high-notional swap activity as plain dicts.

    Direct in-process SQLite read via `swaps_query.SwapsQuery` -- no subprocess.
    Uses `top_notional_products`, which is the post-DTCC-rewrite method name,
    and specifically its `_top_notional_products_raw` sibling semantics: the
    pandas path returns a DataFrame, so this normalizes to `list[dict]` for
    JSON-serializability into the context.

    Returns [] rather than raising when the database is missing or empty --
    swap activity is an optional enrichment, and a cold swaps.db should not
    stop a vol/options run.
    """
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from swaps_query import SwapsQuery
    except Exception:
        return []

    if not os.path.exists(DB_PATH):
        return []

    try:
        q = SwapsQuery(DB_PATH)
        # top_notional_products defaults query_date to yesterday. The DTCC feed
        # is often several days stale on a dev box, so anchor on the latest
        # effective_date actually present instead of silently returning [].
        latest = None
        conn = q.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT MAX(effective_date) AS d FROM swap_trades;")
            row = cur.fetchone()
            latest = row["d"] if row else None
        finally:
            conn.close()
        if not latest:
            return []

        rows = q.top_notional_products(query_date=latest, limit=limit)
    except Exception:
        return []

    if hasattr(rows, "to_dict"):  # pandas DataFrame
        rows = rows.to_dict(orient="records")

    out: list[dict[str, Any]] = []
    for r in rows or []:
        d = dict(r)
        out.append(
            {
                "product": d.get("product"),
                "total_notional": (
                    float(d["total_notional"])
                    if d.get("total_notional") is not None
                    else None
                ),
                "trade_count": (
                    int(d["trade_count"]) if d.get("trade_count") is not None else None
                ),
                "effective_date": str(latest),
            }
        )
    return out


def log_run(
    run_type: str,
    focus: dict[str, Any],
    started_at: str,
    completed_at: str | None,
    status: str,
    results: Any,
) -> int | None:
    """Insert one row into orchestrator_runs (see setup_db.init_database).

    Swallows its own errors on purpose: audit logging must never be the reason
    an otherwise-successful analysis run reports failure. Same posture as
    db_loader.log_scrape, which also logs-and-continues.
    """
    try:
        conn = _get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO orchestrator_runs (
                    run_type, focus_json, started_at, completed_at,
                    status, results_json
                ) VALUES (?, ?, ?, ?, ?, ?);
            """,
                (
                    run_type,
                    json.dumps(focus, default=str),
                    started_at,
                    completed_at,
                    status,
                    json.dumps(results, default=str),
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"  [orchestrator] WARNING: could not log run: {e}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# context construction
# --------------------------------------------------------------------------


def build_context(
    focus: dict[str, Any], controls: dict[str, Any] = None
) -> dict[str, Any]:
    """Build a suite_context.json-shaped dict via Vol_Suite's own builder.

    `focus` accepts the schema's own focus field names (ticker, option_type,
    strike, target_years, expiration_date) plus the optional basket/var/output
    knobs the builder takes (index_ticker, basket_tickers, basket_weights,
    var_horizon_days, var_confidence, output_dir, run_id, ...). Anything not
    supplied gets a defensible default: a single-name basket of the focus
    ticker against SPY, 1-day / 99% VaR, positions=null so VaR derives its own
    notionals from the weights (the null-vs-[] distinction suite_context.py
    documents at length).

    Recent swap activity is attached under the extra top-level key
    `swap_activity`. It is deliberately OUTSIDE the validated blocks:
    `validate_suite_context` only asserts that required keys are present, so an
    unrecognized sibling key round-trips harmlessly through children that
    ignore it, and is there for the ones that grow to read it.
    """
    sc = _import_suite_context()

    ticker = str(focus.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("focus.ticker is required")

    index_ticker = str(focus.get("index_ticker") or "SPY").upper()
    basket_tickers = focus.get("basket_tickers")
    if not basket_tickers:
        # Previously defaulted straight to [ticker] -- a permanently
        # degenerate one-name "basket" with no correlation/dispersion
        # possible, for every orchestrator/unified/dashboard run that didn't
        # explicitly pass basket_tickers (nothing in the dashboard's trigger
        # form does). Resolve real index peers the same way the interactive
        # flow's _resolve_basket does, and only fall back to single-name if
        # that resolution genuinely comes back empty (e.g. holdings sources
        # unreachable).
        basket_size = int(focus.get("basket_size") or 10)
        try:
            vs = _import_volatility_suite()
            basket_tickers, resolved_weights = vs._resolve_basket(
                ticker, index_ticker, basket_size
            )
            if not basket_tickers:
                raise ValueError("basket resolution returned no names")
        except Exception as exc:
            print(
                f"WARNING: basket resolution against {index_ticker} failed ({exc}); "
                f"falling back to a single-name basket for {ticker}."
            )
            basket_tickers = [ticker]
            resolved_weights = None
    else:
        basket_tickers = list(basket_tickers)
        resolved_weights = None

    basket_weights = list(
        focus.get("basket_weights")
        or resolved_weights
        or [1.0 / len(basket_tickers)] * len(basket_tickers)
    )

    expiration = focus.get("expiration_date")
    target_years = focus.get("target_years")
    if not expiration and target_years is None:
        raise ValueError("focus needs expiration_date and/or target_years")
    if not expiration:
        # suite_context requires a concrete expiration_date string. Snap to a
        # real ThetaData-listed expiration near the requested horizon instead
        # of picking an arbitrary calendar date -- naive "today + N days"
        # arithmetic (the old behavior here) routinely lands on a date
        # ThetaData has never listed a snapshot for (e.g. target_years=0.25
        # from 2026-08-18 landed on 2026-11-17, which isn't one of SPY's
        # listed expiries), and that 404s every downstream bulk-snapshot call
        # -- dealer_positioning, chain scanner -- for EVERY ticker, not just
        # illiquid ones. Reuses the same expiry_selector.nearest_expiry the
        # interactive flow and screener already rely on for this.
        try:
            import expiry_selector

            from shared.thetadata import ThetaDataController

            _import_volatility_suite()  # puts Vol_Suite root on sys.path
            td = ThetaDataController()
            exp_str, _resolved_years = expiry_selector.nearest_expiry(
                td, ticker, float(target_years)
            )
            expiration = sc._normalize_expiration(exp_str)
        except Exception as exc:
            print(
                f"WARNING: could not snap target_years={target_years} to a "
                f"real ThetaData-listed expiration for {ticker} ({exc}); "
                f"falling back to naive calendar-date arithmetic, which may "
                f"pick a date ThetaData has no snapshot data for."
            )
            days = max(1, int(round(float(target_years) * 365)))
            expiration = (datetime.now(UTC).date() + _timedelta_days(days)).strftime(
                "%Y-%m-%d"
            )
    if target_years is None:
        exp_date = datetime.strptime(
            sc._normalize_expiration(expiration), "%Y-%m-%d"
        ).date()
        target_years = max((exp_date - datetime.now(UTC).date()).days, 1) / 365.0

    controls = controls or {}
    output_dir = focus.get("output_dir") or os.path.join(
        ROOT, "orchestrator_output", _run_id_now()
    )
    os.makedirs(output_dir, exist_ok=True)

    context = sc.build_suite_context(
        output_dir=output_dir,
        run_id=str(focus.get("run_id") or _run_id_now()),
        ticker=ticker,
        option_type=str(focus.get("option_type") or "call").lower(),
        strike=focus.get("strike"),
        target_years=float(target_years),
        expiration_date=str(expiration),
        index_ticker=index_ticker,
        basket_tickers=basket_tickers,
        basket_weights=basket_weights,
        sentiment_manifest_path=(
            focus.get("sentiment_manifest_path") or _default_manifest_path()
        ),
        sentiment_pack_json_path=focus.get("sentiment_pack_json_path"),
        sentiment_group_id=focus.get("sentiment_group_id"),
        sentiment_ranked_tickers=focus.get("sentiment_ranked_tickers") or [],
        var_horizon_days=int(
            focus.get("var_horizon_days") or focus.get("horizon_days") or 252
        ),
        var_confidence=float(focus.get("var_confidence") or 0.99),
        var_positions=focus.get("var_positions"),
        run_options_suite=bool(controls.get("run_options_suite", False)),
        run_var_suite=bool(controls.get("run_var_suite", False)),
        compile_pdf=bool(controls.get("compile_pdf", False)),
        options_suite_root=SUITE_ROOTS["options"],
        var_suite_root=SUITE_ROOTS["var"],
        sentiment_suite_root=SUITE_ROOTS["sentiment"],
        data_sources=focus.get("data_sources")
        or [],  # Multi-source: pass enabled sources
    )

    if focus.get("include_swap_activity", True):
        context["swap_activity"] = get_recent_swap_activity(
            limit=int(focus.get("swap_activity_limit") or 50)
        )

    # Re-validate after the extra key, proving the sibling field does not
    # break the contract for any child that re-reads it with read_suite_context.
    sc.validate_suite_context(context)
    return context


def _timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


# --------------------------------------------------------------------------
# output validation
# --------------------------------------------------------------------------


def _archive_module_result(
    module_slug: str, result: Any, context: dict[str, Any]
) -> None:
    """Archiver hook, called once per executed module in `run_selected_modules`.

    Phase 6 of the modularization overhaul (Task 7): delegates to
    `shared.module_archive.record`, which owns the dedicated archive DB,
    schema, and never-raise contract -- see that module's docstring.

    `triggered_by="orchestrator"` covers BOTH callers of
    `run_selected_modules` (orchestrator.py's own `--modules`/
    `--modules-category`/`--all-modules` CLI flags, and
    `dashboard/app.py::_execute_run`'s call into this same function when a
    `modules` list is present) -- traced directly: `_execute_run` does not
    duplicate this archiver hook itself, it just calls
    `orchestrator.run_selected_modules`, which reaches this exact call site.
    A single `triggered_by` value covering both is a deliberate choice
    (documented in the task-7 report) rather than plumbing a second
    parameter through `run_selected_modules` to distinguish CLI-direct from
    dashboard-triggered at this call site. `"cli"` is reserved for a suite's
    standalone `cli_entry` `__main__` block, a real separate OS process that
    bypasses `run_selected_modules` (and this hook) entirely -- those call
    `shared.module_archive.record` directly.

    Looks up the executed module's real `ModuleSpec` (needed for `.suite`/
    `.archive.key_shape`, which `record()` requires) via
    `shared.module_registry.resolve_modules` -- `run_selected_modules`
    already resolved `module_slug` once via the same registry lookup, so
    this can't fail for any `module_slug` this hook is actually called with;
    guarded anyway, matching `record()`'s own never-raise contract, so a
    hypothetical future caller passing an unregistered slug still can't
    fail the run.
    """
    try:
        module_spec = resolve_modules([module_slug])[0]
    except Exception:
        logging.getLogger(__name__).warning(
            "_archive_module_result: could not resolve ModuleSpec for "
            "module_slug=%r; skipping archive (module run itself is "
            "unaffected)",
            module_slug,
            exc_info=True,
        )
        return

    from shared import module_archive

    module_archive.record(result, module_spec, context, triggered_by="orchestrator")


from shared.module_execution import (  # noqa: F401 — Phase 7 relocation shim
    _expand_module_requires,
    _topo_sort_modules,
    run_selected_modules,
)


def discover_adapters() -> list[str]:
    """Discover enabled data sources via DATA_SOURCES environment variable.

    Reads comma-separated source names from DATA_SOURCES env var. Each source
    name should map to an available adapter (DTCC, CME, OTC, etc.).

    Returns:
        List of enabled source names. Defaults to ['DTCC'] if env var is unset.
    """
    data_sources_str = os.environ.get("DATA_SOURCES", "DTCC").strip()
    if not data_sources_str:
        return ["DTCC"]

    sources = [s.strip().upper() for s in data_sources_str.split(",") if s.strip()]
    logger.info(f"Discovered {len(sources)} enabled data sources: {', '.join(sources)}")
    return sources or ["DTCC"]


def _focus_from_args(args: argparse.Namespace) -> dict[str, Any]:
    focus: dict[str, Any] = {
        "ticker": args.ticker,
        "option_type": args.option_type,
        "strike": args.strike,
    }
    if args.expiry:
        focus["expiration_date"] = args.expiry
    else:
        focus["target_years"] = args.target_years
    if args.index:
        focus["index_ticker"] = args.index
    return focus


def _print_modules_table() -> None:
    """`--list-modules`: one line per registered module, name/slug/category/suite."""
    modules = sorted(all_modules(), key=lambda m: m.slug)
    if not modules:
        print("No modules registered.")
        return
    header = f"{'SLUG':<30} {'NAME':<35} {'CATEGORY':<18} {'SUITE'}"
    print(header)
    print("-" * len(header))
    for module in modules:
        print(
            f"{module.slug:<30} {module.name:<35} {module.category:<18} {module.suite}"
        )


def _summarize_modules(combined: dict[str, Any]) -> str:
    """Human-readable summary for `run_selected_modules`' return value, the
    --modules/--modules-category/--all-modules counterpart to `_summarize`
    (the human-readable module-run summary)."""
    lines = [f"status: {combined.get('status')}"]
    results = combined.get("results", {})
    for slug in combined.get("order", []):
        result = results.get(slug)
        status = getattr(result, "status", "?")
        lines.append(f"  {slug}: {status}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Module-runner CLI (the only mode left after Phase 7).

    Runs one or more registry modules in-process via
    ``shared.module_execution.run_selected_modules`` -- the same machinery the
    dashboard's generic widget-run API uses. Suite-level ``--unified`` /
    ``--suite`` / ``--interactive`` launching was removed 2026-09-04 (Phase 7
    of the widget-native console plan); use the dashboard (POST
    /api/widgets/{slug}/run) or this CLI's module flags instead.
    """
    parser = argparse.ArgumentParser(
        prog="orchestrator.py",
        description="Run registered modules in-process over the module registry.",
        epilog="""
EXAMPLES:
  orchestrator.py --list-modules
    Print name/slug/category/suite for every registered module.

  orchestrator.py --modules hist_sim,mc_sim --ticker SPY
    Run the named modules for SPY.

  orchestrator.py --modules-category var --ticker SPY --json
    Run every module in a category, full JSON output.

  orchestrator.py --all-modules --ticker SPY
    Run every registered module (respecting requires/order).

Dashboard equivalent (interactive): POST /api/widgets/{slug}/run on the
localhost dashboard -- see docs/guides/START_HERE.md.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--modules",
        default=None,
        help="Comma-separated module slugs to run via the module registry.",
    )
    parser.add_argument(
        "--modules-category",
        default=None,
        help="Run every registered module whose .category matches this string. "
        "Mutually exclusive with --modules/--all-modules.",
    )
    parser.add_argument(
        "--all-modules",
        action="store_true",
        help="Run every module returned by shared.module_registry.all_modules(). "
        "Mutually exclusive with --modules/--modules-category.",
    )
    parser.add_argument(
        "--list-modules",
        action="store_true",
        help="Print name/slug/category/suite for every registered module and "
        "exit without running anything.",
    )
    parser.add_argument(
        "--ticker",
        required=False,
        help="Focus ticker, e.g. NVDA. Required with --modules/--modules-category/--all-modules.",
    )
    parser.add_argument(
        "--expiry",
        default=None,
        help="Expiration YYYY-MM-DD (or YYYYMMDD; normalized to ISO).",
    )
    parser.add_argument(
        "--index", default=None, help="Basket index ticker (default SPY)."
    )
    parser.add_argument(
        "--target-years",
        type=float,
        default=0.25,
        help="Used only when --expiry is omitted (default 0.25).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full combined result as JSON instead of a summary.",
    )
    args = parser.parse_args(argv)

    _warn_if_schema_outdated()

    if args.list_modules:
        _print_modules_table()
        return 0

    _module_flags_given = sum(
        bool(x) for x in (args.modules, args.modules_category, args.all_modules)
    )
    if _module_flags_given > 1:
        parser.error(
            "--modules, --modules-category, and --all-modules are "
            "mutually exclusive; pick one."
        )
    if _module_flags_given and not args.ticker:
        parser.error(
            "--ticker is required when using --modules/"
            "--modules-category/--all-modules."
        )
    if not _module_flags_given:
        parser.error(
            "nothing to run: pass --modules, --modules-category, or "
            "--all-modules (suite-level --unified/--suite launching was "
            "removed 2026-09-04; use the dashboard widget API)."
        )

    focus = _focus_from_args(args)

    if args.all_modules:
        module_slugs = [m.slug for m in all_modules()]
    elif args.modules_category:
        module_slugs = [
            m.slug for m in all_modules() if m.category == args.modules_category
        ]
    else:
        module_slugs = [
            s.strip() for s in (args.modules or "").split(",") if s.strip()
        ]
    context = build_context(focus)
    combined = run_selected_modules(module_slugs, context)
    print("\n" + ("=" * 60))
    print(
        json.dumps(combined, indent=2, default=str)
        if args.json
        else _summarize_modules(combined)
    )
    return 0 if combined["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
