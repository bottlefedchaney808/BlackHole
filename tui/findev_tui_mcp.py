from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from fastmcp import FastMCP

API = os.environ.get("FINDEV_TUI_API", "http://127.0.0.1:8787").rstrip("/")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _http(method: str, path: str, body=None, timeout=60.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        raise Exception(f"HTTP {e.code}: {body_text[:200]}")


def default_scope(ticker: str) -> dict:
    scope = {"ticker": ticker.upper()}
    ctx = os.path.join(REPO, "default_context.json")
    if os.path.exists(ctx):
        try:
            with open(ctx, "r") as f:
                extra = json.load(f)
            for k, v in extra.items():
                scope.setdefault(k, v)
        except Exception:
            pass
    return scope


mcp = FastMCP("findev-tui")


@mcp.tool
def list_widgets() -> str:
    """List all 56 available quant widgets across all 6 suites (vol_suite, options_suite, var_tools, sentiment_scanner, tools, dashboard)."""
    payload = _http("GET", "/api/widgets/catalog")
    widgets = payload if isinstance(payload, list) else payload.get("widgets", [])
    return json.dumps(widgets, indent=2)


@mcp.tool
def get_widget(slug: str) -> str:
    """Get details for a specific widget by slug, including required parameters.
    
    Args:
        slug: Widget slug (e.g. 'leisen_reimer', 'hist_sim', 'jump_diffusion')
    """
    payload = _http("GET", "/api/widgets/catalog")
    widgets = payload if isinstance(payload, list) else payload.get("widgets", [])
    for w in widgets:
        if w.get("slug") == slug:
            return json.dumps(w, indent=2)
    raise Exception(f"Widget '{slug}' not found")


@mcp.tool
def run_widget(slug: str, ticker: str, params: dict = None) -> str:
    """Run a widget analysis against live data.
    
    Supports all pricing models (CRR, Leisen-Reimer, BAW, Newton-Raphson, MC, SABR,
    Vanna-Volga), surface tools, scanner, and VaR simulations.
    
    Args:
        slug: Widget slug (e.g. 'leisen_reimer', 'gex', 'hist_sim')
        ticker: Underlying ticker (e.g. 'SPY')
        params: Widget-specific parameters (strike, expiry, option_type, sigma, etc.)
    """
    result = _http("POST", f"/api/widgets/{slug}/run", {
        "scope": default_scope(ticker),
        "params": params or {},
    })
    return json.dumps(result, indent=2)


@mcp.tool
def set_ticker(ticker: str) -> str:
    """Set the active ticker for subsequent runs."""
    ticker = ticker.upper()
    return f"Ticker set to {ticker} for subsequent calls"


@mcp.tool
def get_state() -> str:
    """Get current TUI session state."""
    state = _http("GET", "/api/state")
    return json.dumps(state, indent=2)


if __name__ == "__main__":
    mcp.run()