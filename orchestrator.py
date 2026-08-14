"""orchestrator.py -- root-level driver for the four sibling suites.

This does NOT invent a handoff format. It reuses the one that already exists:
`Vol_Suite/suite_context.py`'s validated schema_version=1 object, written to
disk once per run and passed to each child via `--context`, with each child
writing its own standardized result JSON to `--context-out`. That is exactly
the contract `Vol_Suite/volatility_suite.py::_run_child_suite` already speaks,
so this file is a re-implementation of that runner at the repo root rather
than a parallel protocol.

Two things differ from `_run_child_suite`, both deliberate:

1. It launches `SHARED_PYTHON` (the consolidated root `.venv`) instead of
   `sys.executable`. `_run_child_suite` inherits whichever interpreter started
   Vol_Suite, which was correct when each suite had its own virtualenv. Now
   there is one merged environment at the root and the orchestrator is
   frequently going to be run from a bare `python` on PATH, so pinning the
   interpreter is the only way the children reliably see their dependencies.

2. It knows that not every suite speaks `--context/--context-out`. Options_Suite,
   VaR_Tools_Simulations and Vol_Suite all do (each exposes `run_context_mode`).
   sentiment-scanner does not -- it is the *producer* end of the chain and
   exposes `--export-context <path>` instead, which is why it runs first.

Vol_Suite used to be the exception, and it was the weakest joint in this file.
`volatility_suite.py` had no argparse at all -- `main()` went straight to
`input()` -- so driving it headless meant feeding its prompt sequence on
scripted stdin ("1", "1", ticker, then a run of blank lines and y/n answers),
coupled to the exact prompt ORDER inside `run_focus_workflow`. And because it
wrote nothing to `--context-out`, its result payload had to be *synthesized*
here by listing files in the output directory -- which meant a run where every
data fetch failed and a run that worked perfectly were nearly indistinguishable,
since both leave files behind.

Vol_Suite now has a real `--context/--context-out` mode and publishes its own
validated `vol_result.json` (schema: `shared/schemas.py::validate_vol_result`)
carrying the vol surface, dealer positioning and gamma records it actually
computed. Both hacks are therefore gone: no scripted stdin, no synthesized
payload, and Vol_Suite goes through the identical launch/validate path as every
other consumer suite.

Dependency edges between stages are validated explicitly rather than inferred
from exit codes. Every suite must leave a marker file in the run's output_dir
(`<suite>_result.json`), and `_validate_suite_output` checks it for presence,
parseability, schema conformance and -- for Vol_Suite -- the required CSV side
artifacts before the next stage is allowed to consume it. The rules live in
`shared/suite_validation.py`; each PASS/FAIL verdict is written to
`orchestrator_runs` so a bad handoff is diagnosable from the audit trail alone.
By default a FAIL is reported and the chain continues degraded (the historical
behaviour); `--fail-on-suite-error` turns it into a hard abort.

The context itself gets the same treatment where it is mutated. `run_unified`
folds the sentiment producer's block back into the shared context, and that is
the one write to an object every later stage reads. It runs through
`shared/context_audit.py` as an audited transaction -- baseline hash, schema
check before, fold, schema and scope check after -- and a mutation that does not
survive the second check is rolled back in place and fails the run outright.
`--fail-on-suite-error` has no say here: a suite failing is a judgement call
about how much degradation to tolerate, whereas a context that just failed
validation is not something the remaining stages can be run against at all. Each
audit lands in `orchestrator_runs` as a `context_audit:sentiment` row carrying
before/after hashes, the changed keys and the per-check verdicts.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.logging import setup_logging, log_operation, get_metrics, LogContext

# Setup structured JSON logging
logger = setup_logging(
    name='orchestrator',
    level=logging.INFO,
    use_json=True,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

# `shared` is a root-level package. When orchestrator.py is launched by path
# from another cwd, ROOT is not automatically importable, so pin it before the
# shared import below.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.context_audit import (  # noqa: E402  (path pinned immediately above)
    ContextMutationAudit,
    audit_sentiment_mutation,
)
from shared.suite_validation import (  # noqa: E402  (path pinned immediately above)
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
    venv_dir = Path(ROOT) / '.venv'
    candidates = [
        venv_dir / 'Scripts' / 'python.exe',  # Windows
        venv_dir / 'bin' / 'python3',         # Linux / Mac
        venv_dir / 'bin' / 'python',          # Linux / Mac fallback
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    # Nothing found -- return the platform-appropriate default path anyway so
    # the `os.path.exists(SHARED_PYTHON)` check below fails with a clear
    # "shared interpreter not found" message instead of a confusing crash.
    default = venv_dir / 'Scripts' / 'python.exe' if os.name == 'nt' else venv_dir / 'bin' / 'python'
    return str(default)


# The consolidated virtualenv. Every child is launched with this interpreter,
# never with `sys.executable` -- see the module docstring.
SHARED_PYTHON = _find_shared_python()

SUITE_ROOTS = {
    'options': os.path.join(ROOT, 'Options_Suite'),
    'vol': os.path.join(ROOT, 'Vol_Suite'),
    'var': os.path.join(ROOT, 'VaR_Tools_Simulations'),
    'sentiment': os.path.join(ROOT, 'sentiment-scanner'),
}

# Same default as Vol_Suite's `_CHILD_SUITE_TIMEOUT_SEC`, and honours the same
# environment override, so raising it for a slow box raises it everywhere.
DEFAULT_TIMEOUT_SEC = int(os.environ.get('SUITE_CHILD_TIMEOUT_SEC', '1800'))

# SWAPS_DB_PATH env var overrides, e.g. for a mounted Docker volume; see
# .env.example / docker-compose.yml
DB_PATH = os.environ.get('SWAPS_DB_PATH') or os.path.join(ROOT, 'swaps.db')


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
_SUITE_SPECS: Dict[str, Dict[str, Any]] = {
    'options': {
        'entrypoint': 'main.py',
        'flags': lambda ctx, out: ['--context', ctx, '--context-out', out],
        'writes_context_out': True,
        'note': '',
    },
    'var': {
        'entrypoint': 'main.py',
        'flags': lambda ctx, out: ['--context', ctx, '--context-out', out],
        'writes_context_out': True,
        'note': '',
    },
    'sentiment': {
        # Producer, not consumer: it has no --context, only --export-context,
        # and it loops forever without --no-loop.
        # Skip YouTube scanner for orchestrator speed (not critical for context).
        'entrypoint': 'main.py',
        'flags': lambda ctx, out: ['--export-context', out, '--no-loop', '--skip-youtube'],
        'writes_context_out': True,
        'note': 'context producer: --export-context (schema_version 2 sentiment block)',
    },
    'vol': {
        'entrypoint': 'volatility_suite.py',
        'flags': lambda ctx, out: ['--context', ctx, '--context-out', out,
                                   '--no-loop'],
        'writes_context_out': True,
        'note': 'vol_result.json: vol surface + dealer positioning + gamma records',
    },
}


# --------------------------------------------------------------------------
# suite_context reuse
# --------------------------------------------------------------------------

def _import_suite_context():
    """Import Vol_Suite/suite_context.py so the context is built and validated
    by the module that owns the schema, not by a copy of it that can drift."""
    vol_root = SUITE_ROOTS['vol']
    if vol_root not in sys.path:
        sys.path.insert(0, vol_root)
    import suite_context  # noqa: E402  (path is set immediately above)
    return suite_context


def _import_sentiment_scanners():
    """Import the 4 non-social option-chain scanners directly (IV Rank, Max
    Pain, Skew, Unusual OI) -- no StockTwits/Reddit/YouTube/GEX, no ticker
    discovery, since the orchestrator already knows the ticker."""
    sentiment_root = SUITE_ROOTS['sentiment']
    if sentiment_root not in sys.path:
        sys.path.insert(0, sentiment_root)
    from scanner.iv_rank_scanner import scan_iv_rank, format_iv_rank
    from scanner.max_pain_scanner import scan_max_pain, format_max_pain
    from scanner.skew_scanner import scan_skew, format_skew
    from scanner.unusual_oi_scanner import scan_unusual_oi, format_unusual_oi
    return (scan_iv_rank, format_iv_rank, scan_max_pain, format_max_pain,
            scan_skew, format_skew, scan_unusual_oi, format_unusual_oi)


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
    var_root = SUITE_ROOTS['var']
    if var_root not in sys.path:
        sys.path.insert(0, var_root)
    module_name = 'var_tools_main'
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(var_root, 'main.py'))
    var_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = var_main
    spec.loader.exec_module(var_main)
    return var_main


def _import_direction_suite():
    """Import the 5-tool Direction signal suite (whale/elliott/bollinger/
    trend/liquidity, combined into one conviction call)."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from Direction.signal_generator import generate  # noqa: E402
    return generate


def run_market_signals_stage(ticker: str, context: Dict[str, Any]) -> Dict[str, Any]:
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

    bundle: Dict[str, Any] = {
        'suite': 'sentiment',
        'status': 'ok',
        'ticker': ticker,
        'timestamp': _iso_utc_now(),
        'scanners': {},
        'simulations': {},
        'direction': None,
    }
    errors: List[str] = []

    # ---- option-chain scanners (IV Rank, Max Pain, Skew, Unusual OI) ----
    try:
        (scan_iv_rank, format_iv_rank, scan_max_pain, format_max_pain,
         scan_skew, format_skew, scan_unusual_oi, format_unusual_oi) = _import_sentiment_scanners()

        for key, scan_fn, fmt_fn in (
            ('iv_rank', scan_iv_rank, format_iv_rank),
            ('max_pain', scan_max_pain, format_max_pain),
            ('skew', scan_skew, format_skew),
            ('unusual_oi', scan_unusual_oi, format_unusual_oi),
        ):
            try:
                if key == 'max_pain':
                    # Pin Max Pain to the expiry this run is analyzing instead
                    # of letting it self-select its own nearest-~30DTE one.
                    scan = scan_fn(
                        ticker,
                        expiry=(context.get('focus') or {}).get('expiration_date'),
                    )
                else:
                    scan = scan_fn(ticker)
                line = fmt_fn(scan)
                print(line)
                bundle['scanners'][key] = _to_jsonable(scan)
            except Exception as e:
                msg = f"{key} scanner failed: {e}"
                print(f"  {ticker:6s} | {key.upper()}: ERROR — {e}")
                errors.append(msg)
                bundle['scanners'][key] = {'error': str(e)}
    except Exception as e:
        errors.append(f"scanner import failed: {e}")
        print(f"  [market-signals] scanner import failed: {e}")

    # ---- 1-year-out simulations (MC, copula, correlation) ----
    # This stage now runs AFTER Vol_Suite (see run_unified) specifically so
    # `context['focus']['garch_conditional_vol']` -- threaded in by
    # `_thread_vol_stats_into_context` from Vol_Suite's own GARCH(1,1) fit --
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
            ('mc_sim', var_main._build_mc_sim_from_context),
            ('copula', var_main._build_copula_from_context),
            ('corr_sim', var_main._build_corr_sim_peer_from_context),
        ):
            try:
                sim_result = builder(context, ticker)
                bundle['simulations'][key] = sim_result
                # Bulk array-shaped fields (matrices, the 20-bin terminal-price
                # histogram) are kept in the bundle but out of the console line.
                _noisy = ('correlation_matrix', 'sim_vols', 'sim_corr',
                          'terminal_price_histogram')
                print(f"  [{key}] {json.dumps({k: v for k, v in sim_result.items() if k not in _noisy})}")
            except Exception as e:
                msg = f"{key} sim failed: {e}"
                print(f"  [market-signals] {msg}")
                errors.append(msg)
                bundle['simulations'][key] = {'error': str(e)}
    except Exception as e:
        errors.append(f"var_engine import failed: {e}")
        print(f"  [market-signals] var_engine import failed: {e}")

    # ---- Direction 5-tool suite ----
    try:
        generate = _import_direction_suite()
        direction = generate(ticker)
        bundle['direction'] = direction
        sig = direction.get('signals', {})
        print(f"  [direction] {ticker}: {direction.get('conviction')} | "
              f"score {direction.get('score')}/5 | "
              + " ".join(f"{k}={'ON' if v else 'off'}" for k, v in sig.items()))
    except Exception as e:
        errors.append(f"direction suite failed: {e}")
        print(f"  [market-signals] direction suite failed: {e}")

    if errors:
        bundle['status'] = 'partial' if any(
            bundle['scanners'].get(k, {}).get('error') is None
            for k in ('iv_rank', 'max_pain', 'skew', 'unusual_oi')
        ) or bundle['direction'] is not None else 'error'
        bundle['errors'] = errors

    return bundle


def _import_volatility_suite():
    """Import Vol_Suite/volatility_suite.py so basket resolution reuses the
    same index-constituents logic the interactive flow uses (_resolve_basket),
    instead of the orchestrator silently defaulting every run to a one-name
    basket. Import-safe: main() only runs under __main__."""
    vol_root = SUITE_ROOTS['vol']
    if vol_root not in sys.path:
        sys.path.insert(0, vol_root)
    import volatility_suite  # noqa: E402  (path is set immediately above)
    return volatility_suite


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _run_id_now() -> str:
    now = datetime.now(timezone.utc)
    # Microsecond suffix: concurrent unified runs launched in the same second
    # previously collided on one run_id and clobbered each other's output dir.
    return now.strftime('%Y%m%dT%H%M%SZ') + f'{now.microsecond:06d}'


def _default_manifest_path() -> str:
    """Same location Vol_Suite's `_default_pack_manifest_path()` points at."""
    return os.path.join(
        SUITE_ROOTS['sentiment'], 'data', 'exports',
        'highlighted_ticker_packs', 'latest_manifest.json')


# --------------------------------------------------------------------------
# swaps.db
# --------------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    """Same connection pattern as db_loader.SwapsLoader.get_connection()."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_recent_swap_activity(limit: int = 50) -> List[Dict[str, Any]]:
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
            latest = row['d'] if row else None
        finally:
            conn.close()
        if not latest:
            return []

        rows = q.top_notional_products(query_date=latest, limit=limit)
    except Exception:
        return []

    if hasattr(rows, 'to_dict'):          # pandas DataFrame
        rows = rows.to_dict(orient='records')

    out: List[Dict[str, Any]] = []
    for r in rows or []:
        d = dict(r)
        out.append({
            'product': d.get('product'),
            'total_notional': (float(d['total_notional'])
                               if d.get('total_notional') is not None else None),
            'trade_count': (int(d['trade_count'])
                            if d.get('trade_count') is not None else None),
            'effective_date': str(latest),
        })
    return out


def log_run(run_type: str, focus: Dict[str, Any], started_at: str,
            completed_at: Optional[str], status: str,
            results: Any) -> Optional[int]:
    """Insert one row into orchestrator_runs (see setup_db.init_database).

    Swallows its own errors on purpose: audit logging must never be the reason
    an otherwise-successful analysis run reports failure. Same posture as
    db_loader.log_scrape, which also logs-and-continues.
    """
    try:
        conn = _get_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO orchestrator_runs (
                    run_type, focus_json, started_at, completed_at,
                    status, results_json
                ) VALUES (?, ?, ?, ?, ?, ?);
            """, (
                run_type,
                json.dumps(focus, default=str),
                started_at,
                completed_at,
                status,
                json.dumps(results, default=str),
            ))
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

def build_context(focus: Dict[str, Any],
                  controls: Dict[str, Any] = None) -> Dict[str, Any]:
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

    ticker = str(focus.get('ticker') or '').strip().upper()
    if not ticker:
        raise ValueError("focus.ticker is required")

    index_ticker = str(focus.get('index_ticker') or 'SPY').upper()
    basket_tickers = focus.get('basket_tickers')
    if not basket_tickers:
        # Previously defaulted straight to [ticker] -- a permanently
        # degenerate one-name "basket" with no correlation/dispersion
        # possible, for every orchestrator/unified/dashboard run that didn't
        # explicitly pass basket_tickers (nothing in the dashboard's trigger
        # form does). Resolve real index peers the same way the interactive
        # flow's _resolve_basket does, and only fall back to single-name if
        # that resolution genuinely comes back empty (e.g. holdings sources
        # unreachable).
        basket_size = int(focus.get('basket_size') or 10)
        try:
            vs = _import_volatility_suite()
            basket_tickers, resolved_weights = vs._resolve_basket(ticker, index_ticker, basket_size)
            if not basket_tickers:
                raise ValueError("basket resolution returned no names")
        except Exception as exc:
            print(f"WARNING: basket resolution against {index_ticker} failed ({exc}); "
                  f"falling back to a single-name basket for {ticker}.")
            basket_tickers = [ticker]
            resolved_weights = None
    else:
        basket_tickers = list(basket_tickers)
        resolved_weights = None

    basket_weights = list(focus.get('basket_weights') or resolved_weights
                          or [1.0 / len(basket_tickers)] * len(basket_tickers))

    expiration = focus.get('expiration_date')
    target_years = focus.get('target_years')
    if not expiration and target_years is None:
        raise ValueError("focus needs expiration_date and/or target_years")
    if not expiration:
        # suite_context requires a concrete expiration_date string. Derive one
        # from target_years rather than making the caller supply both.
        days = max(1, int(round(float(target_years) * 365)))
        expiration = (datetime.now(timezone.utc).date()
                      + _timedelta_days(days)).strftime('%Y-%m-%d')
    if target_years is None:
        exp_date = datetime.strptime(
            sc._normalize_expiration(expiration), '%Y-%m-%d').date()
        target_years = max(
            (exp_date - datetime.now(timezone.utc).date()).days, 1) / 365.0

    controls = controls or {}
    output_dir = focus.get('output_dir') or os.path.join(
        ROOT, 'orchestrator_output', _run_id_now())
    os.makedirs(output_dir, exist_ok=True)

    context = sc.build_suite_context(
        output_dir=output_dir,
        run_id=str(focus.get('run_id') or _run_id_now()),
        ticker=ticker,
        option_type=str(focus.get('option_type') or 'call').lower(),
        strike=focus.get('strike'),
        target_years=float(target_years),
        expiration_date=str(expiration),
        index_ticker=index_ticker,
        basket_tickers=basket_tickers,
        basket_weights=basket_weights,
        sentiment_manifest_path=(focus.get('sentiment_manifest_path')
                                 or _default_manifest_path()),
        sentiment_pack_json_path=focus.get('sentiment_pack_json_path'),
        sentiment_group_id=focus.get('sentiment_group_id'),
        sentiment_ranked_tickers=focus.get('sentiment_ranked_tickers') or [],
        var_horizon_days=int(focus.get('var_horizon_days') or 1),
        var_confidence=float(focus.get('var_confidence') or 0.99),
        var_positions=focus.get('var_positions'),
        run_options_suite=bool(controls.get('run_options_suite', False)),
        run_var_suite=bool(controls.get('run_var_suite', False)),
        compile_pdf=bool(controls.get('compile_pdf', False)),
        options_suite_root=SUITE_ROOTS['options'],
        var_suite_root=SUITE_ROOTS['var'],
        sentiment_suite_root=SUITE_ROOTS['sentiment'],
        data_sources=focus.get('data_sources') or [],  # Multi-source: pass enabled sources
    )

    if focus.get('include_swap_activity', True):
        context['swap_activity'] = get_recent_swap_activity(
            limit=int(focus.get('swap_activity_limit') or 50))

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

def _validate_suite_output(suite_name: str,
                           output_dir: str,
                           payload: Optional[Dict[str, Any]] = None,
                           focus: Optional[Dict[str, Any]] = None,
                           strict: Optional[bool] = None) -> ValidationResult:
    """Check that *suite_name* left usable output in *output_dir*, and log it.

    Thin orchestrator-side wrapper over
    `shared.suite_validation.validate_suite_output`: it resolves the focus
    ticker for the `{ticker}` glob patterns, prints the PASS/FAIL breakdown, and
    writes one `validate:<suite>` row into `orchestrator_runs` carrying every
    individual check. The verdict is returned rather than raised so the caller
    decides whether a FAIL degrades the run or aborts it.
    """
    focus = focus or {}
    started_at = _iso_utc_now()

    result = validate_suite_output(
        suite_name,
        output_dir,
        payload=payload,
        ticker=focus.get('ticker'),
        strict=strict,
    )

    print("  " + result.report().replace('\n', '\n  '))

    log_run(f'validate:{result.suite}', focus, started_at, _iso_utc_now(),
            result.status, result.to_dict())
    return result


def _audit_sentiment_fold(context: Dict[str, Any],
                          sentiment_result: Optional[Dict[str, Any]],
                          focus: Optional[Dict[str, Any]] = None
                          ) -> ContextMutationAudit:
    """Fold the producer's sentiment block into *context* under audit, and log it.

    Thin orchestrator-side wrapper over
    `shared.context_audit.audit_sentiment_mutation`, in the same shape as
    `_validate_suite_output` above: it runs the baseline -> mutate -> re-validate
    transaction, prints the concise before/after diff, and writes one
    `context_audit:sentiment` row into `orchestrator_runs` whose `results_json`
    is the full audit entry (before/after hashes and snapshots, the changed
    keys, the per-check breakdown, and whether a rollback happened).

    The verdict is returned rather than raised. `context` comes back either
    mutated-and-valid (PASS) or restored to the pre-mutation baseline (FAIL) --
    never in the rejected intermediate state.
    """
    focus = focus if focus is not None else context.get('focus', {})
    started_at = _iso_utc_now()

    audit = audit_sentiment_mutation(context, sentiment_result)

    print("  " + audit.report().replace('\n', '\n  '))

    log_run('context_audit:sentiment', focus, started_at, _iso_utc_now(),
            audit.status, audit.to_dict())
    return audit


def run_suite(name: str, context: dict, timeout: int = 1800,
              validate: bool = True) -> dict:
    """Launch one child suite in context mode and return its result JSON.

    Same command shape as Vol_Suite's `_run_child_suite` -- context written to
    a JSON file, `--context`/`--context-out` on argv, `SUITE_CONTEXT_PATH` /
    `SUITE_CONTEXT_MODE` / `VS_OUTPUT_DIR` in the environment, cwd set to the
    suite root, output captured, hard timeout -- but launched with
    SHARED_PYTHON.

    On success returns the parsed `--context-out` payload. On non-zero exit,
    timeout, or an unreadable/absent result file, returns a dict with an
    `error` key and the captured stderr, so a caller can branch on
    `'error' in result` without exception handling.

    With `validate=True` (the default) the child's marker file is checked
    against `shared/suite_validation.py` before the payload is returned. A
    failed check is folded into the same `error` key rather than raising, so a
    suite that exits 0 with unusable output is indistinguishable to callers from
    one that crashed -- which is the point: both mean "do not feed this
    downstream". The verdict is attached under `_validation` either way, so a
    PASS is auditable too. Pass `validate=False` to get the raw pre-validation
    behaviour (used by the tests that exercise the runner itself).
    """
    if name not in SUITE_ROOTS:
        raise ValueError(f"Unknown suite {name!r}; expected one of "
                         f"{sorted(SUITE_ROOTS)}")

    started_at = _iso_utc_now()
    start_time = time.time()
    spec = _SUITE_SPECS[name]
    suite_root = SUITE_ROOTS[name]
    entrypoint = os.path.join(suite_root, spec['entrypoint'])

    output_dir = context.get('output_dir') or tempfile.mkdtemp(prefix='orch_')
    os.makedirs(output_dir, exist_ok=True)

    # The context file goes in the run's own output dir when there is one so it
    # survives the run for debugging; a temp file otherwise.
    ctx_path = os.path.join(output_dir, f'suite_context_{name}.json')
    # The child's `--context-out` target IS the marker validation looks for, so
    # the filename comes from the validation module rather than being spelled
    # twice and left to drift.
    out_path = os.path.join(output_dir, marker_filename(name))

    def _fail(message: str, stderr: str = '', returncode: Optional[int] = None) -> dict:
        result = {
            'suite': name,
            'error': message,
            'stderr': stderr,
            'returncode': returncode,
            'command': None,
            'started_at': started_at,
        }
        log_run(f'suite:{name}', context.get('focus', {}), started_at,
                _iso_utc_now(), 'error', result)
        return result

    if not os.path.exists(SHARED_PYTHON):
        return _fail(f"Shared interpreter not found: {SHARED_PYTHON}")
    if not os.path.exists(entrypoint):
        return _fail(f"Entrypoint not found for suite {name!r}: {entrypoint}")

    try:
        with open(ctx_path, 'w', encoding='utf-8') as f:
            json.dump(context, f, indent=2)
            f.write('\n')
    except Exception as e:
        return _fail(f"Could not write context file {ctx_path}: {e}")

    # Canonical copy: bridge latest_run()/_module_latest() and the dashboard's
    # run discovery key on the canonical suite_context.json inside the run dir
    # (run_unified writes it explicitly at the end). A single-suite run must
    # publish it too, or the plugin/dashboard cannot see the run and fall back
    # to an older one. Written with the same context dict, so content is
    # identical to the unified path's canonical file.
    canonical_path = os.path.join(output_dir, 'suite_context.json')
    try:
        with open(canonical_path, 'w', encoding='utf-8') as f:
            json.dump(context, f, indent=2)
            f.write('\n')
    except Exception as e:
        print(f"  [{name}] WARNING: could not write canonical suite_context.json: {e}",
              file=sys.stderr)

    # Stale result from an earlier run must not be mistaken for this run's
    # output if the child dies before writing.
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass

    command = [SHARED_PYTHON, entrypoint] + list(spec['flags'](ctx_path, out_path))

    env = os.environ.copy()
    # Ensure shared module is in PYTHONPATH for all child suites
    env['PYTHONPATH'] = f"{ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env['SUITE_CONTEXT_PATH'] = ctx_path
    env['SUITE_CONTEXT_MODE'] = '1'
    env['VS_OUTPUT_DIR'] = output_dir
    # PYTHONIOENCODING fixes the captured pipes; PYTHONUTF8 also makes the
    # children's bare `open(path)` calls default to UTF-8. Without the latter,
    # a child reading its own JSON/markdown with the Windows cp1252 locale
    # encoding dies on any non-ASCII byte -- observed as a UnicodeDecodeError
    # out of charmap_decode partway through a Vol_Suite run.
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    # Vol_Suite's context mode skips the options chain scanner by default
    # (it's the one step expensive enough to opt out of in a headless batch)
    # unless VS_RUN_CHAIN_SCANNER is set. Orchestrator-driven runs -- unified
    # or single-suite -- should always produce chain_strategies.json so
    # Tools/tools/options_strategy_tool.py's cached mode has something to
    # read; an operator can still override by exporting the var themselves.
    env.setdefault('VS_RUN_CHAIN_SCANNER', '1')

    print(f"  [{name}] running {spec['entrypoint']} (timeout {timeout}s)... "
          f"output is captured, so this will look idle until it finishes.")
    if spec['note']:
        print(f"  [{name}] note: {spec['note']}")

    try:
        proc = subprocess.run(
            command,
            cwd=suite_root,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            # stdin closed for every child, with no exceptions. Vol_Suite used
            # to be fed a scripted prompt sequence here; now that it has a real
            # --context mode, a child that reaches an interactive prompt is a
            # bug, and EOF makes it die with a readable traceback instead of
            # hanging on a pipe nobody is watching.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr or b'').decode(errors='replace')
        result = {
            'suite': name,
            'error': f'TIMEOUT after {timeout}s -- child killed.',
            'stderr': "\n".join(stderr.splitlines()[-20:]),
            'returncode': -1,
            'command': ' '.join(command),
            'started_at': started_at,
        }
        log_run(f'suite:{name}', context.get('focus', {}), started_at,
                _iso_utc_now(), 'timeout', result)
        return result
    except Exception as e:
        return _fail(f"Failed to launch {name}: {e}")

    stderr_tail = "\n".join((proc.stderr or '').splitlines()[-20:])
    stdout_tail = "\n".join((proc.stdout or '').splitlines()[-20:])

    if proc.returncode != 0:
        result = {
            'suite': name,
            'error': f'{name} exited with returncode {proc.returncode}',
            'stderr': stderr_tail or stdout_tail,
            'returncode': proc.returncode,
            'command': ' '.join(command),
            'started_at': started_at,
        }
        log_run(f'suite:{name}', context.get('focus', {}), started_at,
                _iso_utc_now(), 'error', result)
        return result

    def _finalize(payload: Any, context_out: Optional[str]) -> Any:
        """Attach provenance, validate the marker, log, return.

        Every success path ends here so validation cannot be bypassed by
        whichever branch a future edit adds a `return` to.
        """
        if isinstance(payload, dict):
            payload.setdefault('suite', name)
            payload['_orchestrator'] = {
                'command': ' '.join(command),
                'context_path': ctx_path,
                'context_out': context_out,
                'returncode': proc.returncode,
                'started_at': started_at,
                'completed_at': _iso_utc_now(),
            }

        if not validate:
            log_run(f'suite:{name}', context.get('focus', {}), started_at,
                    _iso_utc_now(), 'ok', payload)
            return payload

        verdict = _validate_suite_output(
            name, output_dir,
            payload=payload if isinstance(payload, dict) else None,
            focus=context.get('focus', {}))

        if isinstance(payload, dict):
            payload['_validation'] = verdict.to_dict()

        if not verdict.passed:
            result = {
                'suite': name,
                'error': (f'{name} output validation FAILED: '
                          + '; '.join(verdict.errors)),
                'stderr': stderr_tail or stdout_tail,
                'returncode': proc.returncode,
                'command': ' '.join(command),
                'started_at': started_at,
                'validation': verdict.to_dict(),
                'payload': payload,
            }
            log_run(f'suite:{name}', context.get('focus', {}), started_at,
                    _iso_utc_now(), 'invalid', result)
            return result

        log_run(f'suite:{name}', context.get('focus', {}), started_at,
                _iso_utc_now(), 'ok', payload)
        return payload

    if not os.path.exists(out_path):
        result = {
            'suite': name,
            'error': f'{name} exited 0 but wrote no context-out at {out_path}',
            'stderr': stderr_tail or stdout_tail,
            'returncode': 0,
            'command': ' '.join(command),
            'started_at': started_at,
        }
        log_run(f'suite:{name}', context.get('focus', {}), started_at,
                _iso_utc_now(), 'error', result)
        return result

    try:
        with open(out_path, 'r', encoding='utf-8-sig') as f:
            payload = json.load(f)
    except Exception as e:
        result = {
            'suite': name,
            'error': f'Could not parse {name} context-out {out_path}: {e}',
            'stderr': stderr_tail,
            'returncode': 0,
            'command': ' '.join(command),
            'started_at': started_at,
        }
        log_run(f'suite:{name}', context.get('focus', {}), started_at,
                _iso_utc_now(), 'error', result)
        return result

    return _finalize(payload, out_path)


# --------------------------------------------------------------------------
# unified flow
# --------------------------------------------------------------------------

def _print_phase_header(phase_num: int, phase_name: str, description: str) -> None:
    """Print a formatted phase header for status display."""
    print(f"\n[{phase_num}/3] {phase_name}")
    print(f"       {description}")
    print("-" * 60)


def _thread_vol_stats_into_context(context: Dict[str, Any], vol_result: Dict[str, Any]) -> None:
    """Vol_Suite computes a real per-ticker realized annualized vol and a
    pairwise correlation matrix for the resolved basket (correlation_engine.py
    ::compute_basket_stats) and now publishes both in vol_result.json's basket
    block. Options/VaR are launched off the SAME context object built before
    vol ran (see run_unified's docstring: "consume Vol_Suite's context"), so
    without this mutation they never actually saw vol's numbers -- VaR's
    context mode silently fell back to an identity correlation matrix and a
    flat 0.25 annual vol for every basket ticker on every orchestrator run,
    independent of what vol just measured for that exact basket.

    Mutates `context` in place. Only injects data when the vol-side ticker
    ordering matches the context's basket ordering exactly (or, for
    volatilities, when every basket ticker has one) -- a mismatch usually
    means a data fetch failure trimmed vol's ticker list, and silently
    reindexing/subsetting a correlation matrix is worse than leaving VaR to
    fall back to its own documented defaults.
    """
    basket_out = ((vol_result or {}).get('vol_surface') or {}).get('basket') or {}
    ctx_tickers = list(context.get('basket', {}).get('tickers') or [])

    corr_tickers = basket_out.get('correlation_tickers')
    corr_matrix = basket_out.get('correlation_matrix')
    if corr_tickers and corr_matrix and list(corr_tickers) == ctx_tickers:
        context['basket']['correlation_matrix'] = corr_matrix
    elif corr_matrix:
        print(f"WARNING: vol's correlation ticker order {corr_tickers} doesn't match "
              f"the basket {ctx_tickers}; not threading a correlation matrix into VaR.")

    individual_vols = basket_out.get('individual_vols') or {}
    if ctx_tickers and all(t in individual_vols for t in ctx_tickers):
        context.setdefault('var', {})['volatilities'] = [float(individual_vols[t]) for t in ctx_tickers]
    elif individual_vols:
        missing = [t for t in ctx_tickers if t not in individual_vols]
        print(f"WARNING: vol's individual_vols is missing {missing}; "
              f"not threading volatilities into VaR.")

    # Vol_Suite fits GARCH(1,1) for the focus ticker as part of its own
    # analysis (vol_surface.garch_conditional_vol). Thread it into
    # `focus.garch_conditional_vol` so the Market Signals stage's 1yr-out
    # sims (which now run AFTER vol -- see run_unified) reuse this one real
    # fit via their existing context-vol-preferred resolution instead of
    # fitting GARCH a second time for the same ticker in the same run.
    garch_vol = ((vol_result or {}).get('vol_surface') or {}).get('garch_conditional_vol')
    if isinstance(garch_vol, (int, float)) and not isinstance(garch_vol, bool) and garch_vol > 0:
        context.setdefault('focus', {})['garch_conditional_vol'] = float(garch_vol)


def run_unified(focus: Dict[str, Any],
                fail_on_suite_error: Optional[bool] = None,
                validate: bool = True) -> Dict[str, Any]:
    """Mirror of Vol_Suite's mode-2 dependency order, headless.

    vol -> sentiment (market signals) -> {options, var}. Vol_Suite runs first
    now: it's the sole GARCH(1,1) fit for the focus ticker, and its result is
    threaded into `context['focus']['garch_conditional_vol']` by
    `_thread_vol_stats_into_context` so the Market Signals stage's 1yr-out
    MC/copula/corr sims reuse it instead of each falling back to (or, before
    this reorder, unconditionally doing) their own separate fit -- this used
    to run market signals first and fit GARCH a second time just for its own
    sims, doubling ThetaData load for the ticker every unified run. Options
    and VaR run last and are siblings -- neither reads the other's output,
    exactly as `run_unified_flow` launches them independently off the one
    context file.

    Each stage's output is validated before the next stage is allowed to
    consume it -- `run_suite` checks the marker file and folds any validation
    failure into the same `error` key a crash produces, so "exited 0 but wrote
    nothing usable" and "died" are handled identically here.

    What happens next depends on `fail_on_suite_error`:

    * False (default) -- a failing upstream stage does not abort the run: the
      chain continues with whatever context it has and each stage's error dict
      is returned under its own key. This matches `run_unified_flow`, which
      reports per-child `failed(rc=...)` in its summary rather than raising.
    * True -- the first failing stage aborts the chain. Downstream stages are
      recorded as `skipped` with the name of the stage that blocked them, rather
      than being run against input already known to be bad and failing later for
      a reason that has nothing to do with their own logic.
    """
    started_at = _iso_utc_now()
    results: Dict[str, Any] = {}
    if fail_on_suite_error is None:
        fail_on_suite_error = bool(focus.get('fail_on_suite_error', False))

    controls = {
        'run_options_suite': True,
        'run_var_suite': True,
        'compile_pdf': bool(focus.get('compile_pdf', False)),
    }
    context = build_context(focus, controls=controls)
    output_dir = context['output_dir']
    timeout = int(focus.get('timeout') or DEFAULT_TIMEOUT_SEC)
    print(f"\n[unified] run_id={context['run_id']} output_dir={output_dir}")
    if fail_on_suite_error:
        print("[unified] --fail-on-suite-error: the first invalid or failed "
              "stage aborts the chain.")

    aborted_by: Optional[str] = None

    def _skip(stage: str) -> Dict[str, Any]:
        # A context-audit abort is unconditional; a suite abort only happens
        # under --fail-on-suite-error. The reason has to say which, or a reader
        # of the skipped record goes looking for a flag that was never set.
        reason = (
            'the context mutation audit FAILED and the context was rolled back'
            if aborted_by == 'context_audit'
            else f'upstream stage {aborted_by!r} failed validation '
                 'and --fail-on-suite-error is set')
        return {
            'suite': stage,
            'status': 'skipped',
            'skipped': True,
            'error': f'skipped: {reason}',
            'blocked_by': aborted_by,
            'timestamp': _iso_utc_now(),
        }

    # ---- 1. Vol_Suite (produces vol surface / dealer positioning / GARCH fit) ----
    _print_phase_header(1, "VOL SUITE",
                       "Dealer positioning / vol surface / gamma exposure")
    results['vol'] = run_suite('vol', context, timeout=timeout,
                               validate=validate)
    if 'error' in results['vol'] and fail_on_suite_error:
        aborted_by = 'vol'
    elif 'error' not in results['vol']:
        _thread_vol_stats_into_context(context, results['vol'])

    # ---- 2. MARKET SIGNALS (option-chain scanners + 1yr sims + direction suite) ----
    # Replaces the old sentiment-scanner stage (StockTwits/Reddit/YouTube/GEX),
    # which was hard-skipped here as an unreliable network dependency. This
    # stage runs in-process (no subprocess, no producer/consumer context
    # handoff) since the ticker is already known. Runs AFTER Vol_Suite so its
    # sims can reuse Vol_Suite's GARCH fit via context instead of re-fitting.
    class _MarketSignalsAudit:
        def __init__(self, status: str):
            self.passed = status != 'error'
            self._status = status
        def to_dict(self):
            return {
                'validation_status': 'PASS' if self.passed else 'FAIL',
                'mutations_detected': [],
                'rolled_back': False,
                'validation_errors': ([] if self.passed else
                                      [f'market signals stage status={self._status}']),
                'reason': f'market signals stage status={self._status}; no context mutation performed',
            }

    if aborted_by:
        # Blocked by an earlier (vol) abort -- a skip here is not itself a new
        # failure, so it must not touch `aborted_by` (which already names the
        # real cause). `_skip()`'s dict always carries an `'error'` key, so
        # this branch is kept structurally apart from the "did market signals
        # itself fail" check below rather than trying to except it out there.
        print(f"\n[2/3] MARKET SIGNALS (SKIPPED — blocked by {aborted_by})")
        sentiment_result = _skip('sentiment')
        results['sentiment'] = sentiment_result
        context_audit = _MarketSignalsAudit('skipped')
    else:
        _print_phase_header(2, "MARKET SIGNALS",
                           "IV Rank / Max Pain / Skew / Unusual OI + MC/copula/corr sims + Direction suite")
        sentiment_result = run_market_signals_stage(context['focus']['ticker'], context)
        results['sentiment'] = sentiment_result

        # Write the same two marker files a subprocess suite would have written,
        # so the dashboard's existing 'sentiment' file-claiming logic picks this
        # bundle up under the (relabeled) unified Output tab section unchanged.
        try:
            sentiment_marker = os.path.join(output_dir, 'sentiment_result.json')
            with open(sentiment_marker, 'w', encoding='utf-8') as f:
                json.dump(sentiment_result, f, indent=2, default=str)
                f.write('\n')
            sentiment_ctx_copy = os.path.join(output_dir, 'suite_context_sentiment.json')
            with open(sentiment_ctx_copy, 'w', encoding='utf-8') as f:
                json.dump(context, f, indent=2, default=str)
                f.write('\n')
        except Exception as e:
            print(f"  [market-signals] WARNING: could not write marker files: {e}")

        # ---- 2b. context audit ----
        # Nothing mutates suite_context.json's sentiment block anymore (there
        # is no producer/consumer handoff for this stage), so there is no
        # real mutation to audit -- this just records the stage's own status.
        print("\n[unified] Stage 2b/3: context mutation audit (sentiment block)...")
        context_audit = _MarketSignalsAudit(sentiment_result.get('status', 'ok'))
        if not context_audit.passed:
            # Unlike a suite failure, this is not degradable by
            # --fail-on-suite-error: the context is the input to every
            # remaining stage, so running them against one that just failed
            # validation would only produce failures that say nothing about
            # the suites themselves.
            aborted_by = 'context_audit'
        elif 'error' in sentiment_result and fail_on_suite_error:
            aborted_by = 'sentiment'

    # ---- 3. Options_Suite + VaR_Tools_Simulations (consume Vol_Suite's context) ----
    if aborted_by:
        print(f"\n[3/3] OPTIONS & VAR SUITES (SKIPPED — blocked by {aborted_by})")
        results['options'] = _skip('options')
        results['var'] = _skip('var')
    else:
        _print_phase_header(3, "OPTIONS & VAR SUITES",
                           "Option pricing + value-at-risk analysis (parallel)")
        results['options'] = run_suite('options', context, timeout=timeout,
                                       validate=validate)
        results['var'] = run_suite('var', context, timeout=timeout,
                                   validate=validate)

    combined = {
        'run_id': context['run_id'],
        'output_dir': output_dir,
        'context_path': os.path.join(output_dir, 'suite_context.json'),
        'focus': context['focus'],
        'swap_activity_rows': len(context.get('swap_activity') or []),
        'results': results,
        'fail_on_suite_error': bool(fail_on_suite_error),
        'aborted_by': aborted_by,
        'validation': {
            stage: (r.get('_validation') or r.get('validation') or {}).get('status')
            for stage, r in results.items()
        },
        # Kept out of `results` on purpose: that dict is the per-suite tally
        # (`ok == len(results)` decides the run status), and the audit is not a
        # suite. It has its own row in orchestrator_runs either way.
        'context_audit': context_audit.to_dict(),
        'started_at': started_at,
        'completed_at': _iso_utc_now(),
    }

    # One canonical copy of the context alongside the per-suite copies, same
    # filename Vol_Suite writes so existing tooling finds it.
    try:
        sc = _import_suite_context()
        sc.write_suite_context(context, combined['context_path'])
    except Exception as e:
        print(f"  [unified] WARNING: could not write suite_context.json: {e}",
              file=sys.stderr)

    ok = sum(1 for r in results.values() if 'error' not in r)
    if not context_audit.passed:
        # 'error', not 'aborted': nothing here was a judgement call about how
        # much failure to tolerate. The context mutation did not survive
        # validation, the context was rolled back, and the run failed.
        status = 'error'
    elif aborted_by:
        # 'aborted' rather than 'partial': the missing stages were never
        # attempted, so reporting them as partial results would overstate what
        # the run actually knows.
        status = 'aborted'
    else:
        status = 'ok' if ok == len(results) else ('partial' if ok else 'error')
    combined['status'] = status

    # ---- Completion summary ----
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Suites completed: {ok}/{total}")
    if context_audit.passed:
        print(f"  Context audit: PASSED")
    else:
        print(f"  Context audit: FAILED (context rolled back)")
    print(f"{'=' * 60}")

    log_run('unified', context['focus'], started_at, combined['completed_at'],
            status, combined)
    return combined


# --------------------------------------------------------------------------
# multi-source orchestration
# --------------------------------------------------------------------------

def discover_adapters() -> List[str]:
    """Discover enabled data sources via DATA_SOURCES environment variable.

    Reads comma-separated source names from DATA_SOURCES env var. Each source
    name should map to an available adapter (DTCC, CME, OTC, etc.).

    Returns:
        List of enabled source names. Defaults to ['DTCC'] if env var is unset.
    """
    data_sources_str = os.environ.get('DATA_SOURCES', 'DTCC').strip()
    if not data_sources_str:
        return ['DTCC']

    sources = [s.strip().upper() for s in data_sources_str.split(',') if s.strip()]
    logger.info(f"Discovered {len(sources)} enabled data sources: {', '.join(sources)}")
    return sources or ['DTCC']


def _get_adapter_for_source(source_name: str) -> Optional[Any]:
    """Load the adapter module for a given source name.

    Maps source names (DTCC, CME, OTC) to adapter modules in adapters/ directory.
    Adapters are expected to have a class named <Source>Adapter.

    Returns:
        Instantiated adapter, or None if the source is not available.
    """
    adapters_dir = os.path.join(ROOT, 'adapters')
    if not os.path.isdir(adapters_dir):
        logger.warning(f"adapters/ directory not found at {adapters_dir}")
        return None

    source_lower = source_name.lower()
    module_path = os.path.join(adapters_dir, f'{source_lower}_adapter.py')

    if not os.path.exists(module_path):
        logger.warning(f"Adapter module not found for {source_name}: {module_path}")
        return None

    try:
        # Dynamically import the adapter module
        spec = importlib.util.spec_from_file_location(
            f'adapters.{source_lower}_adapter', module_path)
        if not spec or not spec.loader:
            logger.error(f"Could not load spec for {source_name} adapter")
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Find the adapter class (e.g., DTCCAdapter, CMEAdapter, OTCAdapter)
        adapter_class_name = f'{source_name.upper()}Adapter'
        if not hasattr(module, adapter_class_name):
            logger.error(f"Adapter class {adapter_class_name} not found in {module_path}")
            return None

        adapter_class = getattr(module, adapter_class_name)
        return adapter_class()

    except Exception as e:
        logger.error(f"Failed to load adapter for {source_name}: {e}")
        return None


def run_unified_sources(tickers: List[str],
                       target_years: float = 0.25,
                       sources: Optional[List[str]] = None,
                       timeout: int = None) -> Dict[str, Any]:
    """Orchestrate parallel data ingestion from multiple sources, then run unified suite.

    This is the main entry point for cross-source analytics. It:
    1. Discovers or validates enabled data sources (env var DATA_SOURCES)
    2. Launches parallel adapters for each source to ingest swap data
    3. Aggregates results before passing to Vol_Suite
    4. Runs the unified suite (sentiment -> vol -> options + var)

    Args:
        tickers: List of focus ticker(s) to analyze
        target_years: Time to expiration in fractional years (default 0.25)
        sources: Override list of sources to use (if None, uses discover_adapters())
        timeout: Per-suite timeout in seconds (default uses DEFAULT_TIMEOUT_SEC)

    Returns:
        Dict with:
            - run_id: Unique run identifier
            - sources_ingested: List of sources that provided data
            - trades_by_source: Dict mapping source name -> trade count
            - total_trades_ingested: Sum across all sources
            - unified_result: The combined suite output
            - started_at: ISO timestamp
            - completed_at: ISO timestamp

    Raises:
        ValueError: If tickers list is empty or sources list is invalid.
    """
    if not tickers:
        raise ValueError("tickers list cannot be empty")

    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SEC

    if sources is None:
        sources = discover_adapters()
    else:
        sources = [s.upper() for s in sources]
        logger.info(f"Using override sources: {', '.join(sources)}")

    started_at = _iso_utc_now()
    run_id = _run_id_now()

    print(f"\n[multi-source] run_id={run_id}")
    print(f"[multi-source] Tickers: {', '.join(tickers)}")
    print(f"[multi-source] Sources to ingest: {', '.join(sources)}")
    print(f"[multi-source] Target years: {target_years}")

    # ---- Stage 1: Parallel data ingestion from all sources ----
    print("\n[multi-source] Stage 1/3: Parallel data ingestion...")

    trades_by_source: Dict[str, int] = {}
    ingestion_errors: Dict[str, str] = {}

    # Import here to avoid circular dependency
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from db_loader import SwapsLoader
    except Exception as e:
        raise RuntimeError(f"Failed to import db_loader: {e}")

    loader = SwapsLoader(DB_PATH)

    # Ingest from each source sequentially (can be parallelized with threading/multiprocessing)
    for source in sources:
        logger.info(f"Ingesting from {source}...")
        adapter = _get_adapter_for_source(source)

        if not adapter:
            msg = f"Adapter not available for {source}"
            ingestion_errors[source] = msg
            trades_by_source[source] = 0
            logger.warning(msg)
            continue

        try:
            # Fetch trades from this source (no date filter for full backfill)
            trades = adapter.fetch_trades()
            trade_records = [t.to_dict() for t in trades] if trades else []

            if trade_records:
                result = loader.upsert_trades(trade_records, data_source=adapter)
                trades_by_source[source] = len(trade_records)
                logger.info(
                    f"  {source}: upserted {len(trade_records)} trades "
                    f"(inserted={result.get('inserted')}, updated={result.get('updated')})")
            else:
                trades_by_source[source] = 0
                logger.info(f"  {source}: no trades fetched")

        except Exception as e:
            msg = f"Ingestion from {source} failed: {e}"
            ingestion_errors[source] = msg
            trades_by_source[source] = 0
            logger.error(msg)

    # ---- Stage 2: Run unified suite with aggregated data ----
    total_trades = sum(trades_by_source.values())
    print(f"\n[multi-source] Stage 2/3: Aggregated {total_trades} trades from {len(sources)} sources")

    # Build focus for the first ticker (orchestrator is single-ticker-focused for now)
    focus = {
        'ticker': tickers[0].upper(),
        'option_type': 'call',
        'target_years': float(target_years),
        'data_sources': sources,  # Pass sources to context
    }

    print(f"[multi-source] Stage 3/3: Running unified suite (ticker={focus['ticker']})...")
    unified_result = run_unified(focus, fail_on_suite_error=False, validate=True)

    # ---- Combine results ----
    combined = {
        'run_id': run_id,
        'sources_requested': sources,
        'sources_ingested': [s for s in sources if trades_by_source.get(s, 0) > 0],
        'trades_by_source': trades_by_source,
        'total_trades_ingested': total_trades,
        'ingestion_errors': ingestion_errors or None,
        'unified_result': unified_result,
        'started_at': started_at,
        'completed_at': _iso_utc_now(),
    }

    # Log the multi-source orchestration run
    log_run('multi_source', focus, started_at, combined['completed_at'],
            'ok' if total_trades > 0 else 'partial', combined)

    return combined


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _stdin_is_available() -> bool:
    """Check if stdin is available (interactive terminal vs redirected/piped).

    Returns True if stdin is a terminal. Returns False if stdin is redirected,
    piped, or unavailable (CI, task scheduler, etc.).
    """
    try:
        # Use isatty() on all platforms (Windows, POSIX, Mac).
        # This is the standard Python check for TTY availability.
        return sys.stdin.isatty()
    except Exception:
        # Any error -> assume no stdin (safe default for headless/CI)
        return False


def _summarize(combined: Dict[str, Any]) -> str:
    lines = [
        f"run_id={combined['run_id']}",
        f"focus={combined['focus']['ticker']} {combined['focus']['expiration_date']} "
        f"{combined['focus']['option_type']}"
        + (f" K={combined['focus']['strike']}"
           if combined['focus'].get('strike') is not None else ""),
        f"output_dir={combined['output_dir']}",
        f"swap_activity_rows={combined['swap_activity_rows']}",
        f"status={combined['status']}",
    ]
    if combined.get('aborted_by'):
        reason = ('context mutation audit FAILED'
                  if combined['aborted_by'] == 'context_audit'
                  else '--fail-on-suite-error')
        lines.append(f"aborted_by={combined['aborted_by']} ({reason})")

    audit = combined.get('context_audit') or {}
    if audit:
        mutated = audit.get('mutations_detected') or []
        line = (f"context_audit={audit.get('validation_status')} "
                f"mutations={len(mutated)}")
        if audit.get('rolled_back'):
            line += " ROLLED_BACK"
        lines.append(line)
        # Concise diff: the changed keys, not the values and not the contexts.
        for key in mutated[:8]:
            lines.append(f"  mutated: {key}")
        if len(mutated) > 8:
            lines.append(f"  ... and {len(mutated) - 8} more mutated key(s)")
        for err in audit.get('validation_errors') or []:
            lines.append(f"  context_audit: {err}")

    for name, result in combined['results'].items():
        validation = (result.get('_validation') or result.get('validation') or {})
        verdict = validation.get('status')
        if result.get('skipped'):
            lines.append(f"{name}=SKIPPED ({result.get('blocked_by')} failed upstream)")
            continue
        if 'error' in result:
            lines.append(f"{name}=FAILED: {result['error']}")
            for err in validation.get('errors') or []:
                lines.append(f"  {name} validation: {err}")
            if result.get('stderr'):
                first = result['stderr'].splitlines()
                lines.append(f"  {name} stderr tail: {first[-1] if first else ''}")
        else:
            suffix = f", validation={verdict}" if verdict else ''
            lines.append(f"{name}=ok ({result.get('status', 'ok')}{suffix})")
            for warning in validation.get('warnings') or []:
                lines.append(f"  {name} validation WARN: {warning}")
    return "\n".join(lines)


def _check_ticker_exists(ticker: str) -> bool:
    """Validate that a ticker resolves via ThetaData.

    Returns True on any inconclusive result (network error, connection timeout, etc.)
    so validation failures never block a valid run.

    Contract: Reuses Vol_Suite.thetadata_client.ThetaDataController.
    Expected behavior: fetch_spot_price(ticker) returns float > 0 for valid tickers.
    """
    try:
        from Vol_Suite.thetadata_client import ThetaDataController
        td = ThetaDataController()
        try:
            spot = td.fetch_spot_price(ticker)
            return isinstance(spot, (int, float)) and spot > 0
        finally:
            td.close()
    except Exception:
        # Any error (import, network, API): assume valid to avoid blocking
        return True


def _prompt_ticker_interactive() -> str:
    """Prompt for focus ticker with validation, re-prompting on invalid symbol."""
    while True:
        t = input("Focus ticker (e.g. NVDA): ").strip().upper()
        if not t:
            t = "NVDA"
            print(f"  Using default: {t}")
            return t
        if _check_ticker_exists(t):
            return t
        print(f"  '{t}' doesn't resolve to a tradable symbol -- check spelling.")
        retry = input("  Try a different ticker, or press Enter to use it anyway: ").strip().upper()
        if not retry:
            return t
        if _check_ticker_exists(retry):
            return retry
        print(f"  '{retry}' doesn't resolve either -- continuing with it.")
        return retry


def _prompt_run_mode() -> str:
    """Prompt for run mode with accurate suite dependency labels."""
    print("\n" + "=" * 60)
    print("  ORCHESTRATOR — Run Mode")
    print("=" * 60)
    print("\n  (1) UNIFIED (recommended)")
    print("      Runs: sentiment (context) → vol → options + var")
    print("\n  (2) VOL SUITE")
    print("      Runs: sentiment (context) → vol only (fastest)")
    print("\n  (3) OPTIONS SUITE")
    print("      Runs: sentiment (context) → vol → options")
    print("\n  (4) VAR TOOLS")
    print("      Runs: sentiment (context) → vol → var")
    print("\n  (5) CUSTOM")
    print("      Choose individual suites")

    while True:
        choice = input("\nSelect mode [default 1]: ").strip() or "1"
        if choice in "12345":
            return choice
        print(f"  Invalid choice '{choice}'. Enter 1-5.")


def _prompt_expiration_interactive() -> Tuple[Optional[str], Optional[float]]:
    """Prompt for expiration date OR target years.

    Returns tuple (expiration_date, target_years) where exactly one is not None:
      - If user chooses ISO date: (YYYY-MM-DD, None)
      - If user chooses target years: (None, 0.25)
    """
    while True:
        choice = input("\nExpiration method: (1) ISO date YYYY-MM-DD, (2) target years [default 2]: ").strip() or "2"
        if choice == "1":
            while True:
                exp = input("  Enter expiration (YYYY-MM-DD): ").strip()
                if exp and len(exp) == 10 and exp.count('-') == 2:
                    return exp, None
                print("  Invalid format. Use YYYY-MM-DD (e.g., 2026-10-16).")
        elif choice == "2":
            while True:
                years = input("  Target years (e.g., 0.25, 0.5, 1.0) [default 0.25]: ").strip() or "0.25"
                try:
                    y = float(years)
                    if y > 0:
                        return None, y
                    print("  Years must be greater than 0.")
                except ValueError:
                    print(f"  Invalid: '{years}' is not a number.")
        else:
            print(f"  Invalid choice '{choice}'. Enter 1 or 2.")


def run_interactive_orchestrator() -> int:
    """Main entry point for interactive mode.

    Guides user through mode selection, ticker, expiration, and optional parameters,
    then executes run_unified(). Validates all inputs and re-prompts on errors.
    """
    print("\n" + "=" * 60)
    print("  ORCHESTRATOR — Interactive Mode")
    print("=" * 60)
    print("\nThis mode guides you through a full run with sensible defaults.")
    print("Press Enter to accept defaults (shown in brackets).\n")

    # Step 1: Mode selection
    mode_choice = _prompt_run_mode()

    # Map mode to suite list (includes required dependencies)
    suite_map: Dict[str, List[str]] = {
        "1": ["sentiment", "vol", "options", "var"],  # Unified
        "2": ["sentiment", "vol"],                    # Vol only
        "3": ["sentiment", "vol", "options"],        # Options suite
        "4": ["sentiment", "vol", "var"],            # VaR tools
        "5": None,                                     # Custom (handled below)
    }

    requested_suites = suite_map.get(mode_choice)
    if requested_suites is None:
        print("\nCustom mode: select suites (space or comma separated)")
        print("Available: options vol var sentiment")
        while True:
            suite_input = input("Suites [default: vol options var]: ").strip()
            if not suite_input:
                requested_suites = ["vol", "options", "var"]
                break
            # Parse comma or space separated
            requested_suites = [s.strip().lower() for s in suite_input.replace(',', ' ').split() if s.strip()]
            valid = all(s in ['options', 'vol', 'var', 'sentiment'] for s in requested_suites)
            if valid and requested_suites:
                break
            print("  Invalid suite name(s). Use: options vol var sentiment")

    # Step 2: Ticker
    print("\n" + "-" * 60)
    ticker = _prompt_ticker_interactive()

    # Step 3: Expiration
    print("\n" + "-" * 60)
    print("Expiration / Time Horizon")
    expiration, target_years = _prompt_expiration_interactive()

    # Step 4: Optional parameters
    print("\n" + "-" * 60)
    print("Additional Options (press Enter for defaults)")

    strike_input = input("  Strike (optional, ATM if blank): ").strip()
    strike: Optional[float] = None
    if strike_input:
        try:
            strike = float(strike_input)
        except ValueError:
            print(f"  Warning: '{strike_input}' is not valid; using ATM instead.")

    option_type = input("  Option type (call/put) [default call]: ").strip().lower() or "call"
    if option_type not in ["call", "put"]:
        print(f"  Warning: '{option_type}' is invalid; using 'call' instead.")
        option_type = "call"

    index = input("  Benchmark index [default SPY]: ").strip().upper() or "SPY"

    compile_pdf_input = input("  Compile results to PDF? (y/n) [default n]: ").strip().lower() or "n"
    compile_pdf = compile_pdf_input == "y"

    # Step 5: Build and validate focus dict
    focus: Dict[str, Any] = {
        'ticker': ticker,
        'option_type': option_type,
        'strike': strike,
        'index_ticker': index,
        'fail_on_suite_error': False,
        'compile_pdf': compile_pdf,
    }
    if expiration:
        focus['expiration_date'] = expiration
    else:
        focus['target_years'] = target_years

    # Validate focus before proceeding
    if not focus.get('ticker'):
        print("\nERROR: Ticker is required.")
        return 1
    if focus.get('strike') is not None and not isinstance(focus['strike'], (int, float)):
        print("\nERROR: Strike must be a number.")
        return 1
    if not (focus.get('expiration_date') or focus.get('target_years')):
        print("\nERROR: Expiration or target_years is required.")
        return 1

    # Step 6: Confirmation summary
    exp_display = expiration if expiration else f"{target_years:.4f}yr"
    print("\n" + "=" * 60)
    print("  ORCHESTRATOR — Run Summary")
    print("=" * 60)
    print(f"  Ticker       : {ticker}")
    print(f"  Expiration   : {exp_display}")
    print(f"  Strike       : {strike or 'ATM'}")
    print(f"  Option Type  : {option_type}")
    print(f"  Index        : {index}")
    print(f"  Suites       : {', '.join(requested_suites)}")
    print(f"  Compile PDF  : {'Yes' if compile_pdf else 'No'}")

    while True:
        confirm = input("\nProceed? (y/n) [default y]: ").strip().lower() or "y"
        if confirm in ["y", "yes"]:
            break
        elif confirm in ["n", "no"]:
            print("Cancelled.")
            return 0
        else:
            print("  Enter 'y' or 'n'.")

    # Step 7: Execute the run
    print("\n" + "=" * 60)
    print("  Starting run...")
    print("=" * 60)

    combined = run_unified(focus, fail_on_suite_error=False, validate=True)

    print("\n" + ("=" * 60))
    print(_summarize(combined))
    return 0 if combined['status'] == 'ok' else 1


def _focus_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    focus: Dict[str, Any] = {
        'ticker': args.ticker,
        'option_type': args.option_type,
        'strike': args.strike,
    }
    if args.expiry:
        focus['expiration_date'] = args.expiry
    else:
        focus['target_years'] = args.target_years
    if args.index:
        focus['index_ticker'] = args.index
    return focus


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog='orchestrator.py',
        description='Run the sibling suites over the shared suite_context handoff.',
        epilog="""
INTERACTIVE MODE (Recommended):
  orchestrator.py --interactive
    Guided prompts for mode selection, ticker, expiration, and optional parameters.
    Auto-validates input and re-prompts on errors. Requires interactive terminal.

UNIFIED RUN (CLI flags):
  orchestrator.py --unified --ticker NVDA --expiry 2026-10-16
    Runs: sentiment -> vol -> options + var (all four suites in dependency order)

SINGLE SUITE (CLI flags):
  orchestrator.py --suite vol --ticker AAPL --target-years 0.25
    Runs only Vol Suite (with sentiment context producer as dependency)

ADVANCED OPTIONS:
  orchestrator.py --unified --ticker SPY --expiry 2026-08-15 --fail-on-suite-error
    Abort on first suite failure instead of continuing degraded.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Make mode mutually exclusive, but --interactive optional (not required)
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument('--interactive', action='store_true',
                      help='Launch in interactive mode: guided prompts for all parameters.')
    mode.add_argument('--unified', action='store_true',
                      help='Run sentiment -> vol -> options + var in dependency order.')
    mode.add_argument('--suite', choices=sorted(SUITE_ROOTS),
                      help='Run a single suite in context mode.')
    # Make --ticker optional (only required in CLI mode if --unified/--suite chosen)
    parser.add_argument('--ticker', required=False,
                        help='Focus ticker, e.g. NVDA. Required if --unified or --suite is used.')
    parser.add_argument('--strike', type=float, default=None,
                        help='Optional strike; null means the child picks ATM.')
    parser.add_argument('--expiry', default=None,
                        help='Expiration YYYY-MM-DD (or YYYYMMDD; normalized to ISO).')
    parser.add_argument('--option-type', default='call', choices=['call', 'put'])
    parser.add_argument('--index', default=None, help='Basket index ticker (default SPY).')
    parser.add_argument('--target-years', type=float, default=0.25,
                        help='Used only when --expiry is omitted (default 0.25).')
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT_SEC,
                        help=f'Per-child timeout in seconds (default {DEFAULT_TIMEOUT_SEC}).')
    parser.add_argument('--json', action='store_true',
                        help='Print the full combined result as JSON instead of a summary.')
    parser.add_argument('--fail-on-suite-error', action='store_true',
                        help='Abort the unified chain at the first suite whose '
                             'output fails validation, instead of continuing '
                             'downstream against known-bad input. Downstream '
                             'stages are reported as skipped. Exit code 1.')
    parser.add_argument('--no-validate', action='store_true',
                        help='Skip explicit output validation entirely '
                             '(pre-validation behaviour; for debugging a suite '
                             'whose marker contract is in flux).')
    args = parser.parse_args(argv)

    _warn_if_schema_outdated()

    # Validate CLI args: if using --unified or --suite, --ticker must be provided
    if (args.unified or args.suite) and not args.ticker:
        parser.error('--ticker is required when using --unified or --suite')

    if args.no_validate and args.fail_on_suite_error:
        parser.error('--no-validate and --fail-on-suite-error are contradictory: '
                     'there is nothing to fail on with validation disabled.')

    # If --interactive, check for stdin and dispatch
    if args.interactive:
        if not _stdin_is_available():
            print("ERROR: --interactive requires an interactive terminal.",
                  file=sys.stderr)
            print("stdin is redirected or not available (piped input, CI, etc.).",
                  file=sys.stderr)
            print("", file=sys.stderr)
            print("Fallback: use CLI flags instead:",
                  file=sys.stderr)
            print("  orchestrator.py --unified --ticker TICKER [--expiry YYYY-MM-DD]",
                  file=sys.stderr)
            return 1
        return run_interactive_orchestrator()

    focus = _focus_from_args(args)
    focus['timeout'] = args.timeout
    focus['fail_on_suite_error'] = bool(args.fail_on_suite_error)

    if args.unified:
        combined = run_unified(focus,
                               fail_on_suite_error=bool(args.fail_on_suite_error),
                               validate=not args.no_validate)
        print("\n" + ("=" * 60))
        print(json.dumps(combined, indent=2, default=str) if args.json
              else _summarize(combined))
        return 0 if combined['status'] == 'ok' else 1

    context = build_context(focus)
    result = run_suite(args.suite, context, timeout=args.timeout,
                       validate=not args.no_validate)
    print("\n" + ("=" * 60))
    print(json.dumps(result, indent=2, default=str))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(main())
