"""In-process MCP tools bridging BlackHole Investments into the FinancialDevelopment
quant-finance monorepo: read-only swap data lookups, and triggering analysis
runs through the generic widget-run API (POST /api/widgets/{slug}/run).

The analysis launcher no longer subprocesses orchestrator.py -- Phase 7 migrated
suite launching to the dashboard's generic widget-run route. This project treats
FinancialDevelopment as an external service over HTTP (dashboard), exactly the
posture orchestrator.py itself used.

Deliberately talks to swaps.db with raw sqlite3 rather than importing swaps_query.SwapsQuery,
since SwapsQuery pulls in shared/ (pandas, etc.) from the root .venv, which this project's
own project-local .venv does not have installed.
"""
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.environ.get("SWAPS_DB_PATH") or str(REPO_ROOT / "swaps.db")

# Phase 7: the analysis suites are no longer launched by subprocessing
# orchestrator.py. They run through the dashboard's generic widget API:
#   POST {DASHBOARD_URL}/api/widgets/{slug}/run
# with a flat context dict (or {"scope": ..., "params": ...}) as the body.
# Synchronous; response is {status, slug, scope, artifacts, metrics, context_patch}.
# Module failures come back as status=error with metrics.error, not a 500.
DASHBOARD_URL = os.environ.get("DASHBOARD_URL") or "http://127.0.0.1:8787"

# Legacy suite-level semantics, preserved for callers that still pass a suite
# name rather than a module slug. The old CLI `--suite options|vol|var|sentiment`
# each mapped to a family of modules; the widget API is slug-level, so a legacy
# suite name expands to the concrete module slugs that make up that suite.
# Slugs below are the live ones from GET /api/widgets/catalog (each module's
# `suite` field equals the mapping key's suite here).
LEGACY_SUITE_SLUGS: dict[str, list[str]] = {
    "options": [
        "crr", "leisen_reimer", "newton_raphson_iv", "sabr", "vanna_volga",
        "mc", "mc_heston_lsm", "baw", "model_comparison",
    ],
    "vol": [
        "dealer_exposure", "dealer_flow", "position_book", "dual_book",
        "chain_scanner", "svi_smile", "surface_greek", "surface_market_iv",
        "surface_flow_strike_time", "surface_flow_strike_expiry",
        "group_screener", "vol_surface_2d", "vrp_term_structure",
        "sentiment_backtest",
    ],
    "var": [
        "hist_sim", "mc_sim", "corr_sim", "copulas", "forex_var",
        "cashflow_map", "stress_test", "var_agg", "hedge_optimizer",
        "price_dist",
    ],
    "sentiment": [
        "gex", "unusual_oi", "iv_rank", "skew", "max_pain",
        "vol_dispersion", "earnings",
    ],
}

# 'unified' had no single suite mapping; run every known module slug.
ALL_SUITE_SLUGS: list[str] = sorted(
    {slug for slugs in LEGACY_SUITE_SLUGS.values() for slug in slugs}
)


def _widget_run(slug: str, context: dict[str, Any]) -> dict[str, Any]:
    """POST {slug}/run synchronously and return the parsed JSON response.

    Raises the underlying urllib error on transport/HTTP failure so the caller
    can render it; unknown slugs surface as a 404 which we turn into a clear
    message.
    """
    url = f"{DASHBOARD_URL}/api/widgets/{slug}/run"
    body = json.dumps(context).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        return json.loads(resp.read().decode("utf-8"))


@tool(
    "query_swap_data",
    "Read-only lookup against the DTCC equity-swaps database (swaps.db). "
    "With no upi, returns top-notional products by UPI; with a upi, returns "
    "that product's recent trades.",
    {"upi": str, "limit": int},
)
async def query_swap_data(args: dict[str, Any]) -> dict[str, Any]:
    upi = (args.get("upi") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 200))

    if not os.path.exists(DB_PATH):
        return {
            "content": [{"type": "text", "text": f"swaps.db not found at {DB_PATH}"}],
            "isError": True,
        }

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            if upi:
                rows = conn.execute(
                    "SELECT * FROM swap_trades WHERE upi = ? "
                    "ORDER BY execution_timestamp DESC LIMIT ?",
                    (upi, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT upi, upi_underlier_name, COUNT(*) AS trade_count, "
                    "SUM(notional_amount_leg1) AS total_notional "
                    "FROM swap_trades WHERE upi IS NOT NULL GROUP BY upi "
                    "ORDER BY total_notional DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {
            "content": [{"type": "text", "text": f"swaps.db query failed: {exc}"}],
            "isError": True,
        }

    if not rows:
        text = "No rows found" + (f" for UPI {upi}" if upi else "")
    else:
        header = ", ".join(rows[0].keys())
        lines = [header] + [", ".join(str(v) for v in row) for row in rows]
        text = "\n".join(lines)

    return {"content": [{"type": "text", "text": text}]}


@tool(
    "run_suite",
    "Trigger a FinancialDevelopment analysis run via the dashboard widget API "
    "instead of orchestrator.py. Takes a module slug (or a legacy suite name: "
    "options, vol, var, sentiment, unified -- expanded to that suite's module "
    "slugs) plus a context dict (at minimum {'ticker': TICKER}). Runs "
    "synchronously and can take minutes for live-data modules.",
    {"slug": str, "suite": str, "ticker": str, "context": dict},
)
async def run_suite(args: dict[str, Any]) -> dict[str, Any]:
    slug = (args.get("slug") or "").strip()
    suite = (args.get("suite") or "").strip().lower()
    ticker = (args.get("ticker") or "").strip().upper()
    context = args.get("context") or {}
    if not isinstance(context, dict):
        return {
            "content": [{"type": "text", "text": "context must be a dict"}],
            "isError": True,
        }
    if ticker:
        context = {**context, "ticker": ticker}

    if suite:
        if suite == "unified":
            slugs = ALL_SUITE_SLUGS
        elif suite in LEGACY_SUITE_SLUGS:
            slugs = LEGACY_SUITE_SLUGS[suite]
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"suite must be 'unified' or one of "
                        f"{sorted(LEGACY_SUITE_SLUGS)}, got {suite!r}"
                    ),
                }],
                "isError": True,
            }
    elif slug:
        slugs = [slug]
    else:
        return {
            "content": [{
                "type": "text",
                "text": "provide a module slug (or a legacy suite name)",
            }],
            "isError": True,
        }

    if not context:
        return {
            "content": [{
                "type": "text",
                "text": "context is required (at least {\"ticker\": TICKER})",
            }],
            "isError": True,
        }

    summaries = []
    errors = []
    for s in slugs:
        try:
            resp = _widget_run(s, context)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            errors.append(f"{s}: HTTP {exc.code}: {detail}")
            continue
        except urllib.error.URLError as exc:
            errors.append(f"{s}: could not reach dashboard at {DASHBOARD_URL}: {exc.reason}")
            continue
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{s}: request failed: {exc}")
            continue

        status = resp.get("status", "unknown")
        metrics = resp.get("metrics") or {}
        meta = " ".join(f"{k}={v}" for k, v in (metrics or {}).items()
                        if k != "error" and not isinstance(v, (dict, list)))
        if status in ("ok", "skipped"):
            summaries.append(f"[{s}] {status}: {meta}".rstrip())
        else:
            err = metrics.get("error") if isinstance(metrics, dict) else None
            errors.append(f"{s}: {status}: {err or meta}".rstrip())

    text = "\n".join(summaries) if summaries else "(no successful runs)"
    if errors:
        text = "\n".join([text, "ERRORS:", *errors])
    return {
        "content": [{"type": "text", "text": text[-8000:]}],
        "isError": bool(errors),
    }


market_tools_server = create_sdk_mcp_server(
    name="market_tools",
    version="1.0.0",
    tools=[query_swap_data, run_suite],
)
