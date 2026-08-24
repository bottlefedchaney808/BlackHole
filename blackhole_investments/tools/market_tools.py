"""In-process MCP tools bridging BlackHole Investments into the FinancialDevelopment
quant-finance monorepo: read-only swap data lookups, and triggering suite runs
(Vol_Suite / Options_Suite / VaR_Tools_Simulations / sentiment-scanner) via orchestrator.py.

Deliberately talks to swaps.db with raw sqlite3 rather than importing swaps_query.SwapsQuery,
since SwapsQuery pulls in shared/ (pandas, etc.) from the root .venv, which this project's
own project-local .venv does not have installed.
"""
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = os.environ.get("SWAPS_DB_PATH") or str(REPO_ROOT / "swaps.db")

# Same shared-interpreter convention orchestrator.py uses: subprocess calls into the
# suites always go through the root repo's .venv, never this project's own interpreter.
SHARED_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
if not SHARED_PYTHON.exists():
    SHARED_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

VALID_SUITES = {"options", "vol", "var", "sentiment"}


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
    "Trigger a FinancialDevelopment analysis suite run via orchestrator.py "
    "(options, vol, var, sentiment, or 'unified' for the full cross-suite pipeline). "
    "Runs synchronously and can take minutes for live-data suites.",
    {"suite": str, "ticker": str},
)
async def run_suite(args: dict[str, Any]) -> dict[str, Any]:
    suite = (args.get("suite") or "").strip().lower()
    ticker = (args.get("ticker") or "").strip().upper()

    if not SHARED_PYTHON.exists():
        return {
            "content": [{"type": "text", "text": f"Shared interpreter not found: {SHARED_PYTHON}"}],
            "isError": True,
        }
    if not ticker:
        return {"content": [{"type": "text", "text": "ticker is required"}], "isError": True}

    if suite == "unified":
        cmd = [str(SHARED_PYTHON), "orchestrator.py", "--unified", "--ticker", ticker, "--json"]
    elif suite in VALID_SUITES:
        cmd = [str(SHARED_PYTHON), "orchestrator.py", "--suite", suite, "--ticker", ticker, "--json"]
    else:
        return {
            "content": [{
                "type": "text",
                "text": f"suite must be 'unified' or one of {sorted(VALID_SUITES)}, got {suite!r}",
            }],
            "isError": True,
        }

    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return {
            "content": [{"type": "text", "text": f"{' '.join(cmd)} timed out after 900s"}],
            "isError": True,
        }
    except OSError as exc:
        return {
            "content": [{"type": "text", "text": f"Failed to launch orchestrator.py: {exc}"}],
            "isError": True,
        }

    output = result.stdout or result.stderr or "(no output)"
    return {
        "content": [{"type": "text", "text": output[-8000:]}],
        "isError": result.returncode != 0,
    }


market_tools_server = create_sdk_mcp_server(
    name="market_tools",
    version="1.0.0",
    tools=[query_swap_data, run_suite],
)
