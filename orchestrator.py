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

2. It knows that not every suite speaks `--context/--context-out`. Options_Suite
   and VaR_Tools_Simulations do (both expose `run_context_mode`).
   sentiment-scanner does not -- it is the *producer* end of the chain and
   exposes `--export-context <path>` instead, which is why it runs first.
   Vol_Suite has no context CLI at all; see `_VOL_NOTE` below.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))

# The consolidated virtualenv. Every child is launched with this interpreter,
# never with `sys.executable` -- see the module docstring.
SHARED_PYTHON = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')

SUITE_ROOTS = {
    'options': os.path.join(ROOT, 'Options_Suite'),
    'vol': os.path.join(ROOT, 'Vol_Suite'),
    'var': os.path.join(ROOT, 'VaR_Tools_Simulations'),
    'sentiment': os.path.join(ROOT, 'sentiment-scanner'),
}

# Same default as Vol_Suite's `_CHILD_SUITE_TIMEOUT_SEC`, and honours the same
# environment override, so raising it for a slow box raises it everywhere.
DEFAULT_TIMEOUT_SEC = int(os.environ.get('SUITE_CHILD_TIMEOUT_SEC', '1800'))

DB_PATH = os.path.join(ROOT, 'swaps.db')

# Vol_Suite's entrypoint is `volatility_suite.py`; there is no `main.py` and no
# argparse in it at all -- `main()` goes straight to `input()`. It therefore
# cannot be driven by `--context`, and passing those flags is harmless only
# because sys.argv is never read. Running it headless means feeding its prompts
# on stdin, which `_vol_stdin_script()` does on a best-effort basis. When that
# drifts, the child dies on EOFError and the stderr tail comes back in the
# result dict rather than being swallowed.
_VOL_NOTE = ("Vol_Suite has no --context CLI; driven via scripted stdin. "
             "If prompts change, this run fails loudly with an EOFError tail.")

# Per-suite launch spec. `flags` is a callable taking (ctx_path, out_path) so
# the sentiment producer can use its own flag names without a special case at
# the call site.
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
        'entrypoint': 'main.py',
        'flags': lambda ctx, out: ['--export-context', out, '--no-loop'],
        'writes_context_out': True,
        'note': 'context producer: --export-context (schema_version 2 sentiment block)',
    },
    'vol': {
        'entrypoint': 'volatility_suite.py',
        'flags': lambda ctx, out: ['--context', ctx, '--context-out', out],
        # False, because volatility_suite.py has no --context-out writer -- it
        # publishes its work as files in VS_OUTPUT_DIR instead. Demanding a
        # result JSON it never writes would mark every successful Vol_Suite run
        # as failed, so a clean exit is synthesized into a payload from the
        # output directory. See _synthesize_vol_payload.
        'writes_context_out': False,
        'note': _VOL_NOTE,
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


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _run_id_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


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

    basket_tickers = list(focus.get('basket_tickers') or [ticker])
    basket_weights = list(focus.get('basket_weights')
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
        index_ticker=str(focus.get('index_ticker') or 'SPY').upper(),
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
# child launching
# --------------------------------------------------------------------------

def _vol_stdin_script(context: Dict[str, Any]) -> str:
    """Best-effort answers for volatility_suite.py's prompt sequence (mode 1).

    Blank lines take each prompt's documented default, so this only has to
    supply the two answers that have no default -- run mode and focus ticker --
    plus enough trailing blanks to satisfy the expiry selector and the tail-end
    y/n prompts. It is inherently coupled to the prompt order in
    `run_focus_workflow`; when that changes, the child EOFs and the failure is
    reported rather than hidden.
    """
    ticker = context['focus']['ticker']
    return "\n".join([
        "1",       # run mode: standard focus workflow
        "1",       # input mode: manual focus ticker
        ticker,    # focus ticker
        "",        # keep ticker as entered
        "",        # index choice -> default
        "",        # basket size -> default 10
        "",        # expiry selection -> default
        "",        # dealer sign model -> default 3
        "n",       # options chain scanner
        "n",       # compile PDF
    ]) + "\n"


def _synthesize_vol_payload(context: Dict[str, Any], output_dir: str,
                            stdout_tail: str) -> Dict[str, Any]:
    """Build a result payload for Vol_Suite from its filesystem output.

    Vol_Suite's actual deliverable is the set of PNG/CSV/TXT artifacts it drops
    in VS_OUTPUT_DIR plus `unified_run_summary.txt` -- it has no
    `--context-out`. Rather than inventing a fake vol block, this reports what
    it really produced, in the same `suite`/`status`/`timestamp` shape the
    other children's payloads use, so downstream handling stays uniform.
    """
    files: List[str] = []
    try:
        for entry in sorted(os.listdir(output_dir)):
            full = os.path.join(output_dir, entry)
            if os.path.isfile(full) and not entry.endswith('.json'):
                files.append(entry)
    except OSError:
        pass

    summary_text = ''
    summary_path = os.path.join(output_dir, 'unified_run_summary.txt')
    if os.path.exists(summary_path):
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                summary_text = f.read().strip()
        except OSError:
            pass

    return {
        'suite': 'vol',
        'status': 'ok',
        'ticker': context['focus']['ticker'],
        'produced_files': files,
        'output_dir': output_dir,
        'summary': summary_text,
        'stdout_tail': stdout_tail,
        'timestamp': _iso_utc_now(),
        'notes': 'Payload synthesized by orchestrator: ' + _VOL_NOTE,
    }


def run_suite(name: str, context: dict, timeout: int = 1800) -> dict:
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
    """
    if name not in SUITE_ROOTS:
        raise ValueError(f"Unknown suite {name!r}; expected one of "
                         f"{sorted(SUITE_ROOTS)}")

    started_at = _iso_utc_now()
    spec = _SUITE_SPECS[name]
    suite_root = SUITE_ROOTS[name]
    entrypoint = os.path.join(suite_root, spec['entrypoint'])

    output_dir = context.get('output_dir') or tempfile.mkdtemp(prefix='orch_')
    os.makedirs(output_dir, exist_ok=True)

    # The context file goes in the run's own output dir when there is one so it
    # survives the run for debugging; a temp file otherwise.
    ctx_path = os.path.join(output_dir, f'suite_context_{name}.json')
    out_path = os.path.join(output_dir, f'{name}_result.json')

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

    # Stale result from an earlier run must not be mistaken for this run's
    # output if the child dies before writing.
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass

    command = [SHARED_PYTHON, entrypoint] + list(spec['flags'](ctx_path, out_path))

    env = os.environ.copy()
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

    stdin_text = _vol_stdin_script(context) if name == 'vol' else None

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
            timeout=timeout,
            **({'input': stdin_text} if stdin_text is not None
               else {'stdin': subprocess.DEVNULL}),
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

    if not spec['writes_context_out'] and not os.path.exists(out_path):
        payload = _synthesize_vol_payload(context, output_dir, stdout_tail)
        payload['_orchestrator'] = {
            'command': ' '.join(command),
            'context_path': ctx_path,
            'context_out': None,
            'returncode': proc.returncode,
            'started_at': started_at,
            'completed_at': _iso_utc_now(),
        }
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

    if isinstance(payload, dict):
        payload.setdefault('suite', name)
        payload['_orchestrator'] = {
            'command': ' '.join(command),
            'context_path': ctx_path,
            'context_out': out_path,
            'returncode': proc.returncode,
            'started_at': started_at,
            'completed_at': _iso_utc_now(),
        }

    log_run(f'suite:{name}', context.get('focus', {}), started_at,
            _iso_utc_now(), 'ok', payload)
    return payload


# --------------------------------------------------------------------------
# unified flow
# --------------------------------------------------------------------------

def run_unified(focus: Dict[str, Any]) -> Dict[str, Any]:
    """Mirror of Vol_Suite's mode-2 dependency order, headless.

    sentiment -> vol -> {options, var}. sentiment runs first because it is the
    only *producer* of the `sentiment` block (its `--export-context` payload
    carries manifest_path / pack_json_path / group_id / ranked_tickers, which
    get folded into the context the rest of the chain consumes). Vol_Suite runs
    second because Options_Suite and VaR read the vol-surface / dealer
    positioning side of the run. Options and VaR run last and are siblings --
    neither reads the other's output, exactly as `run_unified_flow` launches
    them independently off the one context file.

    A failing upstream stage does not abort the run: the chain continues with
    whatever context it has, and each stage's error dict is returned under its
    own key. This matches `run_unified_flow`, which reports per-child
    `failed(rc=...)` in its summary rather than raising.
    """
    started_at = _iso_utc_now()
    results: Dict[str, Any] = {}

    controls = {
        'run_options_suite': True,
        'run_var_suite': True,
        'compile_pdf': bool(focus.get('compile_pdf', False)),
    }
    context = build_context(focus, controls=controls)
    output_dir = context['output_dir']
    print(f"\n[unified] run_id={context['run_id']} output_dir={output_dir}")

    # ---- 1. sentiment-scanner (producer) ----
    print("\n[unified] Stage 1/3: sentiment-scanner (context producer)...")
    sentiment_result = run_suite('sentiment', context,
                                 timeout=int(focus.get('timeout') or DEFAULT_TIMEOUT_SEC))
    results['sentiment'] = sentiment_result

    # Fold the producer's sentiment block back into the shared context so the
    # downstream suites see the manifest this run actually produced, not the
    # stale default path build_context guessed at.
    if 'error' not in sentiment_result:
        block = sentiment_result.get('sentiment') or {}
        for key in ('manifest_path', 'pack_json_path', 'group_id', 'ranked_tickers'):
            value = block.get(key)
            if value:
                context['sentiment'][key] = value

    # ---- 2. Vol_Suite (consumes sentiment, produces vol surface / dealer positioning) ----
    print("\n[unified] Stage 2/3: Vol_Suite (dealer positioning / vol surface)...")
    results['vol'] = run_suite('vol', context,
                               timeout=int(focus.get('timeout') or DEFAULT_TIMEOUT_SEC))

    # ---- 3. Options_Suite + VaR_Tools_Simulations (consume Vol_Suite's context) ----
    print("\n[unified] Stage 3/3: Options_Suite and VaR_Tools_Simulations...")
    results['options'] = run_suite('options', context,
                                   timeout=int(focus.get('timeout') or DEFAULT_TIMEOUT_SEC))
    results['var'] = run_suite('var', context,
                               timeout=int(focus.get('timeout') or DEFAULT_TIMEOUT_SEC))

    combined = {
        'run_id': context['run_id'],
        'output_dir': output_dir,
        'context_path': os.path.join(output_dir, 'suite_context.json'),
        'focus': context['focus'],
        'swap_activity_rows': len(context.get('swap_activity') or []),
        'results': results,
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
    status = 'ok' if ok == len(results) else ('partial' if ok else 'error')
    combined['status'] = status
    log_run('unified', context['focus'], started_at, combined['completed_at'],
            status, combined)
    return combined


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

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
    for name, result in combined['results'].items():
        if 'error' in result:
            lines.append(f"{name}=FAILED: {result['error']}")
            if result.get('stderr'):
                first = result['stderr'].splitlines()
                lines.append(f"  {name} stderr tail: {first[-1] if first else ''}")
        else:
            lines.append(f"{name}=ok ({result.get('status', 'ok')})")
    return "\n".join(lines)


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
        description='Run the sibling suites over the shared suite_context handoff.')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--unified', action='store_true',
                      help='Run sentiment -> vol -> options + var in dependency order.')
    mode.add_argument('--suite', choices=sorted(SUITE_ROOTS),
                      help='Run a single suite in context mode.')
    parser.add_argument('--ticker', required=True, help='Focus ticker, e.g. NVDA.')
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
    args = parser.parse_args(argv)

    focus = _focus_from_args(args)
    focus['timeout'] = args.timeout

    if args.unified:
        combined = run_unified(focus)
        print("\n" + ("=" * 60))
        print(json.dumps(combined, indent=2, default=str) if args.json
              else _summarize(combined))
        return 0 if combined['status'] == 'ok' else 1

    context = build_context(focus)
    result = run_suite(args.suite, context, timeout=args.timeout)
    print("\n" + ("=" * 60))
    print(json.dumps(result, indent=2, default=str))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(main())
