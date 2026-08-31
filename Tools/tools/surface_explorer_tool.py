"""surface_explorer_tool.py

Surface Explorer: strike x expiry (and strike x time) 3D surfaces / heatmaps
for IV and dealer-frame Greeks, plus options-flow heatmaps, wrapping
Vol_Suite/surface_grids.py as a standalone Tool.

Dispatched on context['mode']:
  'greek_surface'      -- dealer-frame BS greek (context['greek'], one of
                           delta/gamma/vega/vanna/charm/volga) across every
                           listed expiry.
  'iv_surface_market'  -- vendor implied-vol surface (strike x tenor).
  'flow_strike_time'   -- options-flow premium heatmap, strike x intraday
                           time bucket, one session.
  'flow_strike_expiry' -- options-flow premium heatmap, strike x expiry,
                           one session snapshot across the whole chain.
  'iv_smile_by_model'  -- IV smile (strike, at ONE expiry) as solved
                           independently by every Options_Suite pricing
                           model that build_smile_comparison publishes a
                           curve for (CRR/Leisen-Reimer/Newton-Raphson/SABR/
                           VannaVolga, plus optional MC/Heston -- BAW is
                           priced/reported elsewhere in Options_Suite but
                           build_smile_comparison does not build a BAW
                           smile curve), vs. market vendor IV. Unlike the
                           four modes above, single-expiry, multi-model --
                           not a surface looped across expiries.

Every mode returns the JSON grid (for an interactive client-side render)
AND, when an output_dir is resolvable, a static matplotlib PNG (same
"nice-to-have, never fails the whole tool" convention vrp_term_structure_tool.py
uses for its chart_path).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"
_OPTIONS_SUITE_ROOT = _REPO_ROOT / "Options_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))
if str(_OPTIONS_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_OPTIONS_SUITE_ROOT))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec

GREEK_SURFACE_MODES = {"greek_surface", "greek-surface", "greeks"}
IV_SURFACE_MODES = {"iv_surface_market", "iv-surface-market", "iv_surface", "iv"}
FLOW_TIME_MODES = {"flow_strike_time", "flow-strike-time", "flow_time"}
FLOW_EXPIRY_MODES = {"flow_strike_expiry", "flow-strike-expiry", "flow_expiry"}
SMILE_BY_MODEL_MODES = {
    "iv_smile_by_model",
    "iv-smile-by-model",
    "smile_by_model",
}

DEFAULT_GREEK = "gamma"


def _import_surface_grids():
    import surface_grids as sg

    return sg


def _import_smile_by_model():
    import smile_by_model as sbm

    return sbm


def _resolve_output_dir(context: dict[str, Any]) -> str | None:
    override = context.get("_output_dir_override")
    raw = override or context.get("output_dir")
    return str(raw) if raw else None


def _resolve_ticker(context: dict[str, Any]) -> str:
    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError(
            "surface-explorer requires a ticker (context['ticker'] or "
            "context['focus']['ticker'])."
        )
    return str(ticker).upper()


# ---------------------------------------------------------------------------
# Static PNG rendering (matplotlib, Agg -- same convention every other suite
# chart in this repo uses). Best-effort: a plotting failure never discards an
# already-computed grid.
# ---------------------------------------------------------------------------
# Dashboard dark-theme palette (dashboard/templates/base.html's CSS custom
# properties), imported from shared/chart_theme.py -- the single source of
# truth every Python chart module now shares. shared/ is already a common
# import root for every suite, so this doesn't add a dashboard-package
# dependency.
from shared import chart_theme

_DARK_BG = chart_theme.DASHBOARD_BG
_DARK_PANEL = chart_theme.DASHBOARD_PANEL
_DARK_TEXT = chart_theme.DASHBOARD_TEXT
_DARK_MUTED = chart_theme.DASHBOARD_MUTED
_DARK_ACCENT_CMAP = chart_theme.DASHBOARD_ACCENT_CMAP


def _apply_dark_theme(fig, ax) -> None:
    fig.patch.set_facecolor(_DARK_BG)
    ax.set_facecolor(_DARK_PANEL)
    ax.xaxis.label.set_color(_DARK_MUTED)
    ax.yaxis.label.set_color(_DARK_MUTED)
    if hasattr(ax, "zaxis"):
        ax.zaxis.label.set_color(_DARK_MUTED)
    ax.title.set_color(_DARK_TEXT)
    ax.tick_params(colors=_DARK_MUTED)
    for pane in (
        getattr(ax, "xaxis", None),
        getattr(ax, "yaxis", None),
        getattr(ax, "zaxis", None),
    ):
        if pane is not None and hasattr(pane, "pane"):
            pane.pane.set_facecolor(_DARK_PANEL)
            pane.pane.set_alpha(1.0)


def _plot_surface_3d(
    grid_result, x_key, y_key, z_label, title, path, *, dark_theme: bool = False
) -> str | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    X = np.array(grid_result[x_key])
    Y = np.array(grid_result[y_key])
    Z = np.array(grid_result["grid"])
    YY, XX = np.meshgrid(Y, X, indexing="ij")

    # A real vol/greek surface routinely has one row (e.g. the front-week
    # tenor on a market IV surface) whose amplitude is 5-8x every other
    # row's -- confirmed live on SPXW: front-week wings hit ~0.9 IV while
    # every other tenor out to ~10 months stayed within 0.10-0.24, all
    # genuinely curved. Auto-scaling the z-axis (and color map) to that one
    # outlier's range doesn't make it wrong, but it crushes every other
    # row's real, visible curvature into a sliver of the vertical axis --
    # which reads as "the surface is flat" even though it isn't. Clip the
    # RENDERED z-range (data returned to the caller is untouched) to a
    # percentile band so one extreme row can't flatten everyone else; the
    # outlier row still pokes out the top/bottom of the frame rather than
    # being hidden.
    z_lo, z_hi = np.nanpercentile(Z, [2, 92])
    if z_hi > z_lo:
        pad = (z_hi - z_lo) * 0.1
        z_lo, z_hi = z_lo - pad, z_hi + pad
        Z_render = np.clip(Z, z_lo, z_hi)
    else:
        z_lo, z_hi = None, None
        Z_render = Z

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection="3d")
    cmap = _DARK_ACCENT_CMAP if dark_theme else "viridis"
    surf = ax.plot_surface(XX, YY, Z_render, cmap=cmap, edgecolor="none", alpha=0.92)
    if z_lo is not None:
        surf.set_clim(z_lo, z_hi)
        ax.set_zlim3d(z_lo, z_hi)
    ax.set_xlabel("Strike ($)")
    ax.set_ylabel(y_key.replace("_", " "))
    ax.set_zlabel(z_label)
    ax.set_title(title)
    cbar = fig.colorbar(surf, ax=ax, shrink=0.6, aspect=20, label=z_label)
    if dark_theme:
        _apply_dark_theme(fig, ax)
        cbar.ax.yaxis.label.set_color(_DARK_MUTED)
        cbar.ax.tick_params(colors=_DARK_MUTED)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _plot_heatmap(
    grid_result, x_edges_key, y_labels, y_label, z_label, title, path
) -> str | None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    x_edges = np.array(grid_result[x_edges_key])
    Z = np.array(grid_result["grid"])
    n_rows = Z.shape[0]

    fig, ax = plt.subplots(figsize=(12, 7))
    vmax = float(np.max(np.abs(Z))) or 1.0
    mesh = ax.pcolormesh(
        x_edges, np.arange(n_rows + 1), Z, cmap="RdYlGn", vmin=-vmax, vmax=vmax
    )
    ax.set_xlabel("Strike ($)")
    ax.set_ylabel(y_label)
    ax.set_yticks(np.arange(n_rows) + 0.5)
    ax.set_yticklabels([str(v) for v in y_labels], fontsize=7)
    ax.set_title(title)
    fig.colorbar(mesh, ax=ax, label=z_label)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_smile_by_model(result, path) -> str | None:
    """2D line chart -- NOT a 3D surface. This mode is a single-expiry,
    multi-model comparison, so one line per model plus a market IV
    scatter overlay is the natural chart, unlike the other four modes'
    strike x expiry/time surfaces/heatmaps."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    for label, curve in (result.get("curves") or {}).items():
        ks = curve.get("strikes") or []
        ivs = curve.get("ivs") or []
        if ks and ivs:
            ax.plot(ks, ivs, marker="o", markersize=3, linewidth=1.5, label=label)
    market = result.get("market") or {}
    m_ks = market.get("strikes") or []
    m_ivs = market.get("ivs") or []
    if m_ks and m_ivs:
        ax.scatter(
            m_ks, m_ivs, color="black", marker="x", s=40, label="Market", zorder=5
        )
    ax.set_xlabel("Strike ($)")
    ax.set_ylabel("Implied vol")
    ax.set_title(
        f"{result.get('ticker', '')} IV smile by model "
        f"({result.get('expiry', '')}, {result.get('option_type', '')})"
    )
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _chart_path(out_dir: str, ticker: str, tag: str) -> str:
    ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(Path(out_dir) / f"{ticker}_{tag}_{ts_tag}.png")


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------
def _run_greek_surface(context, sg, ticker, out_dir):
    greek = str(context.get("greek") or DEFAULT_GREEK).strip().lower()
    max_expiries = int(context.get("max_expiries") or 12)
    dark_theme = bool(context.get("dark_theme"))
    result = sg.build_greek_surface(ticker, greek, max_expiries=max_expiries)

    chart_path = None
    if out_dir:
        try:
            path = _chart_path(out_dir, ticker, f"{greek}_surface")
            chart_path = _plot_surface_3d(
                result,
                "strikes",
                "dtes",
                f"{greek} exposure ({result.get('units', '')})",
                f"{ticker} dealer-frame {greek} surface (strike x DTE)",
                path,
                dark_theme=dark_theme,
            )
        except Exception:
            chart_path = None

    result["mode"] = "greek_surface"
    result["chart_path"] = chart_path
    return result


def _run_iv_surface_market(context, sg, ticker, out_dir):
    dark_theme = bool(context.get("dark_theme"))
    min_dte_raw = context.get("min_dte")
    try:
        min_dte = int(min_dte_raw) if min_dte_raw not in (None, "") else 0
    except (TypeError, ValueError):
        min_dte = 0
    result = sg.build_market_iv_surface(ticker, min_dte=min_dte)

    chart_path = None
    if out_dir:
        try:
            path = _chart_path(out_dir, ticker, "iv_surface_market")
            chart_path = _plot_surface_3d(
                result,
                "strikes",
                "tenors_years",
                "Implied vol",
                f"{ticker} market IV surface (strike x tenor)",
                path,
                dark_theme=dark_theme,
            )
        except Exception:
            chart_path = None

    result["mode"] = "iv_surface_market"
    result["chart_path"] = chart_path
    return result


def _run_flow_strike_time(context, sg, ticker, out_dir):
    session = context.get("session")
    result = sg.build_flow_strike_time(ticker, session=session)

    chart_path = None
    if out_dir:
        try:
            path = _chart_path(out_dir, ticker, "flow_strike_time")
            chart_path = _plot_heatmap(
                result,
                "strike_edges",
                result["time_labels"],
                "Time bucket",
                "Net premium (call - put)",
                f"{ticker} options-flow heatmap: strike x time ({result['session']})",
                path,
            )
        except Exception:
            chart_path = None

    result["mode"] = "flow_strike_time"
    result["chart_path"] = chart_path
    return result


def _run_flow_strike_expiry(context, sg, ticker, out_dir):
    session = context.get("session")
    max_expiries = int(context.get("max_expiries") or 12)
    result = sg.build_flow_strike_expiry(
        ticker, session=session, max_expiries=max_expiries
    )

    chart_path = None
    if out_dir:
        try:
            path = _chart_path(out_dir, ticker, "flow_strike_expiry")
            chart_path = _plot_heatmap(
                result,
                "strike_edges",
                result["expiries"],
                "Expiry",
                "Net premium (call - put)",
                f"{ticker} options-flow heatmap: strike x expiry ({result['session']})",
                path,
            )
        except Exception:
            chart_path = None

    result["mode"] = "flow_strike_expiry"
    result["chart_path"] = chart_path
    return result


def _run_iv_smile_by_model(context, sbm, ticker, out_dir):
    expiry = context.get("expiry")
    strike = context.get("strike")
    strike = float(strike) if strike not in (None, "") else None
    option_type = str(context.get("option_type") or "call").strip().lower()
    include_mc = bool(context.get("include_mc", True))
    include_heston = bool(context.get("include_heston", True))
    result = sbm.build_iv_smile_by_model(
        ticker,
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        include_mc=include_mc,
        include_heston=include_heston,
    )

    chart_path = None
    if out_dir:
        try:
            path = _chart_path(out_dir, ticker, "iv_smile_by_model")
            chart_path = _plot_smile_by_model(result, path)
        except Exception:
            chart_path = None

    result["mode"] = "iv_smile_by_model"
    result["chart_path"] = chart_path
    return result


def run(context: dict[str, Any]) -> dict[str, Any]:
    """Dispatch on context['mode']. See module docstring for the five modes.

    Fail-closed on missing ticker or an unrecognized mode (raises); any
    downstream data failure (no spot, no listed expiries, no trades) also
    raises with a specific reason, rather than returning a partially-filled
    or fabricated grid.
    """
    mode = str(context.get("mode") or "greek_surface").strip().lower()
    ticker = _resolve_ticker(context)
    out_dir = _resolve_output_dir(context)

    if mode in SMILE_BY_MODEL_MODES:
        sbm = _import_smile_by_model()
        return _run_iv_smile_by_model(context, sbm, ticker, out_dir)

    sg = _import_surface_grids()

    if mode in GREEK_SURFACE_MODES:
        return _run_greek_surface(context, sg, ticker, out_dir)
    if mode in IV_SURFACE_MODES:
        return _run_iv_surface_market(context, sg, ticker, out_dir)
    if mode in FLOW_TIME_MODES:
        return _run_flow_strike_time(context, sg, ticker, out_dir)
    if mode in FLOW_EXPIRY_MODES:
        return _run_flow_strike_expiry(context, sg, ticker, out_dir)

    raise ValueError(
        f"unknown surface-explorer mode {mode!r}. Expected one of: "
        f"greek_surface, iv_surface_market, flow_strike_time, "
        f"flow_strike_expiry, iv_smile_by_model"
    )


TOOL_SPEC = ToolSpec(
    name="Surface Explorer",
    slug="surface-explorer",
    description=(
        "3D strike x expiry surfaces for dealer-frame Greeks (delta/gamma/"
        "vega/vanna/charm/volga) and market IV, plus options-flow heatmaps "
        "(strike x time, strike x expiry), plus a single-expiry multi-model "
        "IV smile comparison. mode='greek_surface' (+context['greek']), "
        "'iv_surface_market' (+context['min_dte'], default 0 -- pass e.g. "
        "14 to drop expiries inside 2 weeks for a term-structure view "
        "instead of the front-week-inclusive default), 'flow_strike_time', "
        "'flow_strike_expiry', 'iv_smile_by_model' (+context['strike'], "
        "'option_type', 'include_mc', 'include_heston')."
    ),
    run=run,
)
