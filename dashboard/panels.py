"""panels.py -- the panel registry behind the Volatility / Models / Sentiment tabs.

WHY THIS EXISTS (and why it is not just more `<quant-widget>` cards)
--------------------------------------------------------------------------
The generic widget card renders a `ModuleResult`: `metrics` as a table,
`artifacts` as images. That contract is fine; what was missing is that
several of the most-wanted modules never produce anything it can render:

* the four `surface_*` modules return `artifacts=[]` -- the whole grid rides
  on `context_patch`, and nothing draws it. A card got five scalars
  ("n_expiries_used: 12") and called that a surface.
* `vrp_term_structure` in Vol_Suite is a `runnable=False` selection-only
  marker whose `run()` raises by design (it is a gated step inside
  `_run_core_analysis`). The runnable VRP lives in `Tools/` under a
  different slug, which the catalog groups elsewhere, so from the desk it
  looked like VRP was simply broken.
* `jump_diffusion` is metrics-only with no chart at all.
* `variance_swap` and `garch` DO write PNGs -- into
  `outputs/<run_id>/` -- which `GET /files` used to refuse with a 403
  because it only allowed `artifacts/`. (Fixed in app.py; see
  FILE_SERVE_ROOTS.)

A panel is the seam that fixes that: a small server-side spec that knows how
to get a renderable result for one idea, whether that means running a
registered module, calling a `Tools/` renderer that turns a grid into a PNG,
or reading a directory of notes. Every panel returns the SAME
ModuleResult-shaped dict the widget API returns, so the browser reuses
`widget-renderers.js::renderResult` unchanged.

WHAT A PANEL IS NOT
--------------------------------------------------------------------------
It is not a second module registry. Every panel that computes anything
delegates to `shared.module_execution.run_selected_modules` or to a
`Tools/tools/*` entry point -- the same code paths the CLI and the desk use,
with `requires` expansion, Context-Store seeding and archiving intact. No
quant logic lives in this file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shared.module_registry import TOOL_GRID_KEYS, tool_payload_to_result

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where panels that render their own PNGs write them. Under artifacts/ so
# `GET /files` serves them, and one directory per tab so a stale Volatility
# chart is never picked up by a Models panel.
PANEL_OUTPUT_ROOT = os.path.join(ROOT, "artifacts", "panels")


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PanelParam:
    """One panel-specific knob, rendered as a control on the panel card.

    Mirrors `shared.module_registry.ParamSpec` deliberately: a panel that
    wraps a module with params re-declares them here rather than the browser
    having to fetch two different catalogs and merge them.
    """

    name: str
    label: str
    kind: str = "text"  # "bool" | "choice" | "text" | "number"
    default: Any = None
    choices: Sequence[Any] = ()
    help: str = ""


@dataclass(frozen=True)
class PanelSpec:
    id: str
    title: str
    tab: str  # "volatility" | "models" | "sentiment"
    blurb: str
    run: Callable[[dict[str, Any]], dict[str, Any]]
    output_kind: str = "metrics"
    params: tuple[PanelParam, ...] = ()
    # Whether this panel is checked when the tab is first opened. Kept small
    # and cheap on every tab: a "run all" that fires eleven billed ThetaData
    # pulls the first time someone opens a page is a bad default.
    default_on: bool = False
    group: str = ""
    # Context keys this panel reads if a previous run left them behind. Shown
    # on the card so "where did this number come from" is answerable without
    # reading the source.
    consumes: tuple[str, ...] = ()
    scope: str = "ticker"  # "ticker" | "basket" | "none"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "tab": self.tab,
            "blurb": self.blurb,
            "output_kind": self.output_kind,
            "default_on": self.default_on,
            "group": self.group,
            "consumes": list(self.consumes),
            "scope": self.scope,
            "params": [
                {
                    "name": p.name,
                    "label": p.label,
                    "kind": p.kind,
                    "default": p.default,
                    "choices": list(p.choices),
                    "help": p.help,
                }
                for p in self.params
            ],
        }


# ---------------------------------------------------------------------------
# Shared runner helpers
# ---------------------------------------------------------------------------


def panel_output_dir(tab: str) -> str:
    path = os.path.join(PANEL_OUTPUT_ROOT, tab)
    os.makedirs(path, exist_ok=True)
    return path


def panel_run_dir(tab: str, panel_id: str) -> str:
    """A fresh directory for ONE panel run's artifacts.

    Not the shared per-tab directory, and the reason is a real trap: several
    suite entry points collect their output by LISTING their output_dir and
    matching a filename prefix rather than tracking what they just wrote --
    `garch_analysis.run_garch_module` globs `{ticker}_garch_*.png`, for
    instance. Point two runs at one directory and the second returns the
    first's charts alongside its own; a week later the card renders twenty
    stale PNGs and the newest one is somewhere in the middle. Confirmed live
    on SPY: the second GARCH run came back with six artifacts, three of them
    from a run eleven seconds earlier.

    One directory per run makes the glob correct by construction, without
    touching any suite's file-collection code.
    """
    import uuid
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(
        PANEL_OUTPUT_ROOT, tab, f"{panel_id}_{stamp}_{uuid.uuid4().hex[:4]}"
    )
    os.makedirs(path, exist_ok=True)
    return path


def _artifact_dicts(artifacts: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for a in artifacts or []:
        if isinstance(a, dict) and a.get("path"):
            out.append({"path": str(a["path"]), "kind": str(a.get("kind") or "")})
        elif hasattr(a, "path"):
            out.append({"path": str(a.path), "kind": str(getattr(a, "kind", ""))})
    return out


def _error_result(exc: BaseException, *, panel_id: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "artifacts": [],
        "metrics": {
            "error": f"{type(exc).__name__}: {exc}",
            "panel": panel_id,
        },
        "ran": [],
    }


def module_panel(
    slug: str, *, extra: dict[str, Any] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Run one registered module through the normal execution path.

    Deliberately `run_selected_modules` and not `spec.run(context)`: that is
    what expands `requires`, mints `run_id`/`output_dir`, merges each
    module's `context_patch` into the in-flight context, and persists it to
    the Context Store. Calling `run()` directly is the bug that left
    `dealer_flow` reporting `status: skipped` forever.
    """

    def _run(context: dict[str, Any]) -> dict[str, Any]:
        from shared.module_execution import run_selected_modules

        ctx = dict(context)
        if extra:
            for key, value in extra.items():
                ctx.setdefault(key, value)
        try:
            run = run_selected_modules([slug], ctx)
        except Exception as exc:  # noqa: BLE001 -- surfaces on the card
            return _error_result(exc, panel_id=slug)
        results = run.get("results") or {}
        result = results.get(slug)
        if result is None:
            return {
                "status": "failed",
                "artifacts": [],
                "metrics": {"error": f"no result for {slug!r}"},
                "ran": list(run.get("order") or []),
            }
        return {
            "status": result.status,
            "artifacts": _artifact_dicts(result.artifacts),
            "metrics": result.metrics,
            "context_patch": result.context_patch,
            "ran": list(run.get("order") or []),
        }

    return _run


def _tool_result(
    payload: dict[str, Any],
    *,
    chart_keys: Sequence[str] = ("chart_path",),
    drop: Sequence[str] = (),
) -> dict[str, Any]:
    """Adapt a `Tools/tools/*` run() dict into the ModuleResult shape.

    Thin wrapper over `shared.module_registry.tool_payload_to_result`, which
    is the single implementation the desk's widget-card path
    (`from_tool_spec`) also goes through -- when the lift lived only here,
    the same tool rendered its chart on a panel tab and dropped it on a desk
    card.
    """
    result = tool_payload_to_result(
        payload, chart_keys=tuple(chart_keys), drop=tuple(drop)
    )
    return {
        "status": result.status,
        "artifacts": _artifact_dicts(result.artifacts),
        "metrics": result.metrics,
        "ran": [],
    }


# Grid payload keys that must never reach a metrics table -- each is a dense
# numeric array the chart already draws. Defined in shared/module_registry.py
# so the desk-card path drops exactly the same keys these panels do.
_GRID_KEYS = TOOL_GRID_KEYS


def surface_panel(
    mode: str, *, defaults: dict[str, Any] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Render one Surface Explorer mode to a PNG.

    `Tools/tools/surface_explorer_tool.py` already delegates its four surface
    modes to the matching `Vol_Suite/module_registry.py` modules and adds the
    matplotlib render on top -- so this is the module result PLUS the picture
    the module never drew, not a second implementation of the grid.
    """

    def _run(context: dict[str, Any]) -> dict[str, Any]:
        from Tools.tools import surface_explorer_tool

        ctx = dict(defaults or {})
        ctx.update(context)
        ctx["mode"] = mode
        ctx["dark_theme"] = True
        ctx.setdefault(
            "output_dir", panel_run_dir(ctx.get("_tab") or "volatility", mode)
        )
        ctx["focus"] = {"ticker": ctx.get("ticker")} if ctx.get("ticker") else {}
        try:
            payload = surface_explorer_tool.run(ctx)
        except Exception as exc:  # noqa: BLE001
            return _error_result(exc, panel_id=mode)
        return _tool_result(payload, drop=_GRID_KEYS)

    return _run


def vrp_panel(context: dict[str, Any]) -> dict[str, Any]:
    """VRP term structure -- via the Tools entry point, not the Vol_Suite slug.

    `Vol_Suite/module_registry.py`'s `vrp_term_structure` is a selection-only
    marker: `runnable=False`, and its `run()` raises NotImplementedError
    explaining that it is a gated step inside `_run_core_analysis`. That is
    correct and stays. The independently-callable VRP is
    `Tools/tools/vrp_term_structure_tool.py`, which wraps
    `Vol_Suite/vrp_term_structure.py::compute_vrp_term_structure` directly and
    writes a chart.
    """
    from Tools.tools import vrp_term_structure_tool

    ctx = dict(context)
    ctx.setdefault("output_dir", panel_run_dir("volatility", "vrp_term_structure"))
    ctx["focus"] = {"ticker": ctx.get("ticker")} if ctx.get("ticker") else {}
    try:
        payload = vrp_term_structure_tool.run(ctx)
    except Exception as exc:  # noqa: BLE001
        return _error_result(exc, panel_id="vrp_term_structure")
    result = _tool_result(payload)
    points = (payload or {}).get("points") or []
    if points:
        result["metrics"]["points"] = points
    return result


def smile_compare_panel(context: dict[str, Any]) -> dict[str, Any]:
    """One expiry's IV smile as solved independently by every pricing model.

    This is the Models tab's headline comparison: CRR / Leisen-Reimer /
    Newton-Raphson / SABR / Vanna-Volga (plus MC and Heston when asked)
    against the vendor's own IV, on one chain. Divergence between the model
    curves and the market curve at a strike is the mispricing signal.
    """
    from Tools.tools import surface_explorer_tool

    ctx = dict(context)
    ctx["mode"] = "iv_smile_by_model"
    ctx.setdefault("output_dir", panel_run_dir("models", "smile_compare"))
    ctx["focus"] = {"ticker": ctx.get("ticker")} if ctx.get("ticker") else {}
    try:
        payload = surface_explorer_tool.run(ctx)
    except Exception as exc:  # noqa: BLE001
        return _error_result(exc, panel_id="smile_compare")
    return _tool_result(payload, drop=_GRID_KEYS)


def tool_panel(
    module_name: str, *, defaults: dict[str, Any] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Run a `Tools/tools/<module_name>.py` entry point as a panel."""

    def _run(context: dict[str, Any]) -> dict[str, Any]:
        import importlib

        ctx = dict(defaults or {})
        ctx.update(context)
        ctx.setdefault(
            "output_dir", panel_run_dir(ctx.get("_tab") or "models", module_name)
        )
        ctx["focus"] = {"ticker": ctx.get("ticker")} if ctx.get("ticker") else {}
        try:
            mod = importlib.import_module(f"Tools.tools.{module_name}")
            payload = mod.run(ctx)
        except Exception as exc:  # noqa: BLE001
            return _error_result(exc, panel_id=module_name)
        if not isinstance(payload, dict):
            return {
                "status": "ok",
                "artifacts": [],
                "metrics": {"result": payload},
                "ran": [],
            }
        return _tool_result(payload, drop=_GRID_KEYS)

    return _run


# ---------------------------------------------------------------------------
# Portfolio surface
# ---------------------------------------------------------------------------
#
# The desk's surfaces panel models ONE underlying. The other question a book
# raises is what the whole thing is exposed to -- so this blends every held
# name's market IV surface into one, weighted by market value.
#
# WHAT THIS IS, PRECISELY, because it is easy to misread: a market-value-
# weighted average of implied-vol surfaces in MONEYNESS space. It answers
# "what does the vol my book is exposed to look like across strike and
# tenor". It is NOT portfolio volatility -- averaging vols ignores
# correlation entirely, and a two-name book at 30% vol each is not a 30%-vol
# portfolio unless the two are perfectly correlated. Portfolio vol lives on
# the correlation_matrix panel, which has the covariance to compute it.
#
# Absolute strikes cannot be averaged across underlyings (a $700 SPY strike
# and a $4 NOK strike are not the same point on any axis), so every surface
# is re-expressed as K/S before blending. Tenors are already comparable.

PORTFOLIO_MONEYNESS = (0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20)
PORTFOLIO_TENORS = (0.02, 0.08, 0.25, 0.50, 1.00)
# Between per-ticker chain pulls. CLAUDE.md's ThetaData rule: sleep
# 0.3-0.5s, never fan out. A ten-name book is ten sequential chain builds.
PORTFOLIO_FETCH_GAP_S = 0.4
PORTFOLIO_MAX_NAMES = 12


def _interp_row(xs: list[float], ys: list[float], x: float) -> float | None:
    """Linear interpolation with NO extrapolation.

    Returning an edge value for a moneyness the chain never listed would
    invent a quote; a name whose chain does not reach 0.8 moneyness should
    drop out of that cell's average, not pad it.
    """
    if not xs or len(xs) != len(ys):
        return None
    if x < xs[0] or x > xs[-1]:
        return None
    for i in range(1, len(xs)):
        if x <= xs[i]:
            span = xs[i] - xs[i - 1]
            if span <= 0:
                return ys[i]
            frac = (x - xs[i - 1]) / span
            left, right = ys[i - 1], ys[i]
            if left is None or right is None:
                return right if left is None else left
            return float(left) + frac * (float(right) - float(left))
    return ys[-1]


def portfolio_surface_panel(context: dict[str, Any]) -> dict[str, Any]:
    """Market-value-weighted IV surface across the whole book."""
    import time

    tickers = _basket_from_context(context)
    if not tickers:
        return {
            "status": "idle",
            "artifacts": [],
            "metrics": {
                "message": (
                    "no basket -- press 'Basket = my book' in the scope bar, or "
                    "push positions to the desk first"
                )
            },
            "ran": [],
        }
    weights = _weights_from_context(context, tickers)
    tickers = tickers[:PORTFOLIO_MAX_NAMES]
    weights = weights[: len(tickers)]

    try:
        import sys

        vol_suite = os.path.join(ROOT, "Vol_Suite")
        if vol_suite not in sys.path:
            sys.path.insert(0, vol_suite)
        import surface_grids
    except Exception as exc:  # noqa: BLE001
        return _error_result(exc, panel_id="portfolio_surface")

    moneyness = list(PORTFOLIO_MONEYNESS)
    tenors = list(PORTFOLIO_TENORS)
    stack: list[tuple[str, float, list[list[float | None]]]] = []
    skipped: list[dict[str, str]] = []

    for i, ticker in enumerate(tickers):
        if i:
            time.sleep(PORTFOLIO_FETCH_GAP_S)
        try:
            surface = surface_grids.build_market_iv_surface(ticker)
        except Exception as exc:  # noqa: BLE001 -- one bad name must not
            # lose the other nine. A book usually holds something with no
            # listed chain at all.
            skipped.append({"ticker": ticker, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        spot = float(surface.get("spot") or 0.0)
        if spot <= 0:
            skipped.append({"ticker": ticker, "reason": "no spot"})
            continue
        strikes = [float(k) / spot for k in surface.get("strikes") or []]
        src_tenors = [float(t) for t in surface.get("tenors_years") or []]
        grid = surface.get("grid") or []
        if not strikes or not src_tenors or not grid:
            skipped.append({"ticker": ticker, "reason": "empty surface"})
            continue

        # Interpolate onto the common axes: first across moneyness within
        # each source tenor, then across tenor.
        by_tenor = [[_interp_row(strikes, row, m) for m in moneyness] for row in grid]
        resampled: list[list[float | None]] = []
        for want_t in tenors:
            column = [
                _interp_row(src_tenors, [r[j] for r in by_tenor], want_t)
                for j in range(len(moneyness))
            ]
            resampled.append(column)
        stack.append((ticker, weights[tickers.index(ticker)], resampled))

    if not stack:
        return {
            "status": "failed",
            "artifacts": [],
            "metrics": {
                "error": "no held name produced a usable IV surface",
                "skipped": skipped,
            },
            "ran": [],
        }

    # Weighted blend, per cell, over whichever names actually reach it.
    blended: list[list[float | None]] = []
    coverage: list[list[int]] = []
    for ti in range(len(tenors)):
        row: list[float | None] = []
        cover: list[int] = []
        for mi in range(len(moneyness)):
            total_w = 0.0
            total_v = 0.0
            hits = 0
            for _ticker, weight, grid in stack:
                value = grid[ti][mi]
                if value is None or weight <= 0:
                    continue
                total_w += weight
                total_v += weight * float(value)
                hits += 1
            row.append(round(total_v / total_w, 4) if total_w > 0 else None)
            cover.append(hits)
        blended.append(row)
        coverage.append(cover)

    chart_path = _render_portfolio_surface(blended, moneyness, tenors, stack)

    used = [{"ticker": t, "weight_pct": round(100.0 * w, 1)} for t, w, _ in stack]
    atm_index = moneyness.index(1.00) if 1.00 in moneyness else len(moneyness) // 2
    atm_curve = [
        {
            "tenor_years": tenors[i],
            "atm_iv_pct": None
            if blended[i][atm_index] is None
            else round(100.0 * blended[i][atm_index], 2),
            "names_covering": coverage[i][atm_index],
        }
        for i in range(len(tenors))
    ]

    metrics: dict[str, Any] = {
        "headline": (
            f"{len(stack)} of {len(tickers)} names blended by market value"
            + (f" \u00b7 {len(skipped)} skipped" if skipped else "")
        ),
        "what_this_is": (
            "Market-value-weighted average of each holding's IV surface in "
            "moneyness space. This is the vol your book is exposed to, NOT "
            "portfolio volatility -- averaging vols ignores correlation. For "
            "portfolio vol, run the Correlation / Covariance panel."
        ),
        "weights": used,
        "atm_term_structure": atm_curve,
        "moneyness_axis": moneyness,
        "tenor_axis": tenors,
    }
    if skipped:
        metrics["skipped"] = skipped

    artifacts = [{"path": chart_path, "kind": "png"}] if chart_path else []
    return {
        "status": "ok",
        "artifacts": artifacts,
        "metrics": metrics,
        "ran": [],
        "context_patch": {
            "portfolio_iv_surface": {
                "moneyness": moneyness,
                "tenors": tenors,
                "grid": blended,
                "weights": used,
            }
        },
    }


def _render_portfolio_surface(grid, moneyness, tenors, stack) -> str | None:
    """Heatmap of the blended surface. Never fails the panel -- the numbers
    above are real whether or not matplotlib could draw them."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        from shared import chart_theme

        values = np.array(
            [[np.nan if v is None else float(v) for v in row] for row in grid],
            dtype=float,
        )
        fig, ax = plt.subplots(figsize=(9, 5))
        mesh = ax.imshow(
            values * 100.0,
            aspect="auto",
            origin="lower",
            cmap=getattr(chart_theme, "DASHBOARD_ACCENT_CMAP", "viridis"),
        )
        ax.set_xticks(range(len(moneyness)))
        ax.set_xticklabels([f"{m:.2f}" for m in moneyness])
        ax.set_yticks(range(len(tenors)))
        ax.set_yticklabels([f"{t:.2f}y" for t in tenors])
        ax.set_xlabel("Moneyness (K / S)")
        ax.set_ylabel("Tenor")
        ax.set_title(
            "Portfolio IV surface \u2014 market-value weighted ("
            + ", ".join(t for t, _w, _g in stack[:6])
            + ("\u2026" if len(stack) > 6 else "")
            + ")"
        )
        for i in range(values.shape[0]):
            for j in range(values.shape[1]):
                if not np.isnan(values[i, j]):
                    ax.text(
                        j,
                        i,
                        f"{values[i, j] * 100:.0f}",
                        ha="center",
                        va="center",
                        fontsize=7,
                        color="#f8fafc",
                    )
        fig.colorbar(mesh, ax=ax, label="Implied vol (%)")
        try:
            fig.patch.set_facecolor(chart_theme.DASHBOARD_BG)
            ax.set_facecolor(chart_theme.DASHBOARD_PANEL)
            for spine in ax.spines.values():
                spine.set_color(chart_theme.DASHBOARD_MUTED)
            ax.tick_params(colors=chart_theme.DASHBOARD_TEXT)
            ax.xaxis.label.set_color(chart_theme.DASHBOARD_TEXT)
            ax.yaxis.label.set_color(chart_theme.DASHBOARD_TEXT)
            ax.title.set_color(chart_theme.DASHBOARD_TEXT)
        except Exception:
            pass  # theme is cosmetic; an unthemed chart still reads
        import datetime as _dt

        out = os.path.join(
            panel_run_dir("volatility", "portfolio_surface"),
            "portfolio_iv_surface_"
            + _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".png",
        )
        fig.tight_layout()
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return out
    except Exception:
        return None


def _basket_from_context(context: dict[str, Any]) -> list[str]:
    for key in ("basket", "tickers", "held_tickers"):
        raw = context.get(key)
        if isinstance(raw, str):
            raw = raw.split(",")
        if isinstance(raw, (list, tuple)) and raw:
            out = [str(t).strip().upper() for t in raw if str(t).strip()]
            if out:
                return list(dict.fromkeys(out))
    return []


def _weights_from_context(context: dict[str, Any], tickers: list[str]) -> list[float]:
    """Normalized market-value weights, falling back to equal weight."""
    book = context.get("positions")
    rows = book.get("positions") if isinstance(book, dict) else book
    by_ticker: dict[str, float] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            tkr = str(row.get("ticker") or "").strip().upper()
            try:
                value = abs(float(row.get("market_value")))
            except (TypeError, ValueError):
                continue
            if tkr and value:
                by_ticker[tkr] = by_ticker.get(tkr, 0.0) + value
    raw = [by_ticker.get(t, 0.0) for t in tickers]
    total = sum(raw)
    if total <= 0:
        return [1.0 / len(tickers)] * len(tickers)
    return [v / total for v in raw]


# ---------------------------------------------------------------------------
# Scan library (Sentiment tab)
# ---------------------------------------------------------------------------
#
# "Pull a note like you'd pull a book off a shelf": the morning scans, the
# X/Twitter buzz reports, the rumor watchlists and the desk notes are written
# as markdown, and they are the actual record of what people were saying.
# They live in the Obsidian vault (moved 2026-09-03 to ~/obsidian-vault),
# with the repo's own trading_journal/ as a secondary shelf.

LIBRARY_ROOTS: tuple[tuple[str, str], ...] = (
    ("vault", os.path.join(os.path.expanduser("~"), "obsidian-vault", "Trading")),
    ("journal", os.path.join(ROOT, "trading_journal")),
    ("docs", os.path.join(ROOT, "docs", "desk_notes")),
)

# Buckets a note lands in, matched case-insensitively against its filename.
# Order matters: the first hit wins, so put the specific shelves first.
LIBRARY_SHELVES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x/twitter", ("x.com", "twitter", "buzz", "tweet")),
    ("rumors", ("rumor", "rumour", "chatter", "social")),
    ("reddit", ("reddit", "wsb", "stocktwits")),
    ("morning scans", ("open play", "morning", "premarket", "pre-market", "scan")),
    ("sentiment", ("sentiment",)),
    ("desk notes", ("desk note", "powerhour", "power hour", "journal")),
    ("research", ("research", "watchlist", "plan")),
)


def classify_note(name: str) -> str:
    lowered = name.lower()
    for shelf, needles in LIBRARY_SHELVES:
        if any(needle in lowered for needle in needles):
            return shelf
    return "other"


def list_library_notes(limit: int = 200) -> list[dict[str, Any]]:
    """Every markdown note on the shelves, newest first.

    Never raises on a missing root: the vault lives outside the repo and may
    simply not be present on another machine. A missing shelf is an empty
    shelf, not an error page.
    """
    rows: list[dict[str, Any]] = []
    for label, root in LIBRARY_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if not filename.lower().endswith((".md", ".markdown")):
                    continue
                full = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                rows.append(
                    {
                        "name": os.path.splitext(filename)[0],
                        "path": full,
                        "source": label,
                        "shelf": classify_note(filename),
                        "modified": stat.st_mtime,
                        "size": stat.st_size,
                    }
                )
    rows.sort(key=lambda r: -r["modified"])
    return rows[:limit]


def read_library_note(path: str, *, max_chars: int = 60_000) -> dict[str, Any]:
    """Read one note, but only from inside a configured shelf.

    The path comes from the browser, so it is checked with realpath against
    every library root before anything is opened -- the same containment rule
    `GET /files` applies to artifacts. Without it this endpoint would read any
    file on the machine.
    """
    target = os.path.realpath(path)
    for _label, root in LIBRARY_ROOTS:
        if not os.path.isdir(root):
            continue
        real_root = os.path.realpath(root)
        try:
            if os.path.commonpath([real_root, target]) == real_root:
                break
        except ValueError:
            continue
    else:
        raise PermissionError("note is outside the configured library roots")
    if not os.path.isfile(target):
        raise FileNotFoundError(target)
    with open(target, encoding="utf-8", errors="replace") as handle:
        text = handle.read(max_chars + 1)
    truncated = len(text) > max_chars
    return {
        "path": target,
        "name": os.path.splitext(os.path.basename(target))[0],
        "shelf": classify_note(os.path.basename(target)),
        "text": text[:max_chars],
        "truncated": truncated,
    }


def library_panel(context: dict[str, Any]) -> dict[str, Any]:
    """The shelf index as a panel result.

    Filters to the scope ticker when one is set, because "what was being said
    about NOK" is the question this shelf usually gets asked. Matching is on
    the filename only -- a full-text search over the vault is a different
    (and much slower) feature.
    """
    ticker = str(context.get("ticker") or "").strip().upper()
    shelf = str(context.get("shelf") or "").strip().lower()
    rows = list_library_notes()
    if ticker:
        rows = [r for r in rows if ticker in r["name"].upper()] or rows
    if shelf and shelf != "all":
        rows = [r for r in rows if r["shelf"] == shelf]
    import datetime as _dt

    notes = [
        {
            "note": r["name"],
            "shelf": r["shelf"],
            "where": r["source"],
            "modified": _dt.datetime.fromtimestamp(r["modified"]).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "path": r["path"],
        }
        for r in rows[:60]
    ]
    shelves = sorted({r["shelf"] for r in rows})
    return {
        "status": "ok" if notes else "idle",
        "artifacts": [],
        "metrics": {
            "headline": (
                f"{len(notes)} notes on the shelf"
                + (f" mentioning {ticker}" if ticker else "")
            ),
            "shelves": shelves,
            "notes": notes,
        },
        "ran": [],
    }


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_GREEKS = ("gamma", "vanna", "charm", "delta", "vega", "volga")
_JUMP_MODELS = ("none", "Bates", "Merton", "Kou", "VarianceGamma", "Heston")

# ---------------------------------------------------------------------------
# Contested-narrative sentiment scanner (the loop scanner itself)
# ---------------------------------------------------------------------------
#
# The Sentiment tab surfaced everything ATTACHED to sentiment-scanner -- its 7
# options scanners and the highlight packs it exports -- but not the scanner
# it is named after: the StockTwits/Reddit/YouTube contested-narrative engine
# plus correlation/engine.py that `sentiment.bat` loops. That engine is not in
# sentiment-scanner/module_registry.py::MODULES at all (which registers only
# gex / unusual_oi / iv_rank / skew / max_pain / vol_dispersion / earnings /
# highlight_packs), so no widget or panel could reach it.
#
# It cannot simply be registered as a module, for two reasons that are
# properties of the scanner, not of the registry:
#   * it has its OWN project-local venv (sentiment-scanner/.venv) and installs
#     its deps there, so it cannot be imported into the dashboard's process;
#   * its default mode LOOPS on config.SCAN_INTERVAL_MINUTES and prompts on
#     stdin, neither of which a request/response card can host.
#
# So this panel does the two things that ARE well-defined: READ the latest
# completed scan (instant, always works, no network), and optionally LAUNCH
# one bounded pass as a subprocess in the scanner's own interpreter. The
# bounded pass uses --universe, which the scanner documents as "one
# directional scan pass ... and exits -- no looping, no sector-rotation/PDF
# prompts" -- the only non-interactive, terminating mode it has.
SENTIMENT_SCANNER_DIR = os.path.join(ROOT, "sentiment-scanner")


def _sentiment_scanner_python() -> str | None:
    """The scanner's OWN interpreter -- never the dashboard's.

    sentiment-scanner is the one suite that does not share the root .venv, so
    running it with our python would import a different dependency set.
    """
    exe = os.path.join(SENTIMENT_SCANNER_DIR, ".venv", "Scripts", "python.exe")
    if os.path.isfile(exe):
        return exe
    exe = os.path.join(SENTIMENT_SCANNER_DIR, ".venv", "bin", "python")
    return exe if os.path.isfile(exe) else None


def _latest_directional_scan():
    """Newest outputs/directional_scan_*.json, parsed. (None, None) if absent."""
    import glob
    import json

    hits = glob.glob(
        os.path.join(SENTIMENT_SCANNER_DIR, "outputs", "directional_scan_*.json")
    )
    if not hits:
        return None, None
    newest = max(hits, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8") as fh:
            return newest, json.load(fh)
    except Exception:  # noqa: BLE001 -- a malformed scan file is not fatal
        return newest, None


def _latest_cns_alerts(limit: int = 20):
    """Rows from the newest cns-threshold-alerts pack, plus its header.

    Real schema (confirmed against a live export, not guessed): the file is a
    dict with `thesis_summary` / `created_at` / `source_run_id` / `priority`
    at the top and a `tickers` LIST underneath, each entry keyed `symbol` --
    not `ticker` -- and carrying `cns`, `war_score`, `thesis_ratio`,
    `pump_ratio`, `bullish_pct`, `bearish_pct`, `confidence` and
    `social_sources`. `cns` is the contested-narrative score the pack
    thresholds on, so rows come back ranked by it.
    """
    import glob
    import json

    hits = glob.glob(
        os.path.join(
            SENTIMENT_SCANNER_DIR,
            "data",
            "exports",
            "highlighted_ticker_packs",
            "*",
            "cns-threshold-alerts-*.json",
        )
    )
    if not hits:
        return None, []
    newest = max(hits, key=os.path.getmtime)
    try:
        with open(newest, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:  # noqa: BLE001 -- a malformed pack is not fatal
        return newest, []

    header = {
        "pack_file": os.path.basename(newest),
        "thesis_summary": payload.get("thesis_summary"),
        "created_at": payload.get("created_at"),
        "source_run_id": payload.get("source_run_id"),
        "priority": payload.get("priority"),
    }
    rows = []
    for item in payload.get("tickers") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "symbol": item.get("symbol"),
                "cns": item.get("cns"),
                "war_score": item.get("war_score"),
                "bullish_pct": item.get("bullish_pct"),
                "bearish_pct": item.get("bearish_pct"),
                "confidence": item.get("confidence"),
                "sources": ",".join(item.get("social_sources") or []),
            }
        )
    rows.sort(key=lambda r: (r.get("cns") is None, -(r.get("cns") or 0)))
    return header, rows[:limit]


def sentiment_scanner_panel(context: dict[str, Any]) -> dict[str, Any]:
    """Read the last contested-narrative scan; optionally run one new pass."""
    try:
        params = context.get("params") or {}
        run_scan = bool(params.get("run_scan"))

        metrics: dict[str, Any] = {}
        newest_path, scan = _latest_directional_scan()
        pack_header, alerts = _latest_cns_alerts()

        if run_scan:
            import subprocess

            exe = _sentiment_scanner_python()
            if exe is None:
                raise RuntimeError(
                    "sentiment-scanner/.venv not found -- run its sentiment "
                    "launcher once to create it. This suite does NOT share "
                    "the root .venv."
                )
            universe = context.get("basket") or context.get("ticker") or ""
            if isinstance(universe, (list, tuple)):
                universe = ",".join(str(t) for t in universe)
            universe = str(universe).strip()
            if not universe:
                raise ValueError("a scan needs a scope: set a ticker or basket first")
            cmd = [
                exe,
                "main.py",
                "--universe",
                universe,
                "--skip-sector-prompt",
                "--skip-report-prompt",
            ]
            if params.get("skip_youtube"):
                cmd.append("--skip-youtube")
            env = dict(os.environ)
            # Same Hermes-venv hygiene the rest of the repo uses before
            # invoking a project interpreter.
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=SENTIMENT_SCANNER_DIR,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=int(params.get("timeout_s") or 900),
                )
                metrics["scan_returncode"] = proc.returncode
                tail = (proc.stdout or "").strip().splitlines()[-12:]
                metrics["scan_tail"] = "\n".join(tail)
                if proc.returncode != 0:
                    metrics["scan_stderr"] = (proc.stderr or "").strip()[-800:]
            except subprocess.TimeoutExpired:
                metrics["scan_returncode"] = "timeout"
                metrics["scan_tail"] = (
                    "scan exceeded the timeout and was killed; the previous "
                    "results below are unchanged"
                )
            newest_path, scan = _latest_directional_scan()
            pack_header, alerts = _latest_cns_alerts()

        # The PO-token server gates YouTube captions and fails SILENTLY when
        # down (captions are skipped, not errored), which shows up much later
        # as unexplained thin sentiment -- so say so on the card instead.
        import socket

        with socket.socket() as sock:
            sock.settimeout(0.35)
            po_up = sock.connect_ex(("127.0.0.1", 4416)) == 0
        metrics["youtube_po_token_server"] = (
            "up (:4416)"
            if po_up
            else "DOWN (:4416) -- YouTube captions are being skipped silently"
        )

        if newest_path:
            metrics["last_scan_file"] = os.path.basename(newest_path)
            metrics["last_scan_at"] = datetime.fromtimestamp(
                os.path.getmtime(newest_path)
            ).isoformat(timespec="seconds")
            age_h = (datetime.now().timestamp() - os.path.getmtime(newest_path)) / 3600
            metrics["last_scan_age_hours"] = round(age_h, 1)
            if age_h > 24:
                metrics["staleness"] = (
                    f"STALE -- last pass was {age_h / 24:.1f} days ago; "
                    "these are leftovers, not a live read"
                )
        else:
            metrics["last_scan_file"] = "none found"

        if isinstance(scan, dict):
            results = scan.get("results") or scan.get("tickers") or []
            metrics["tickers_in_last_scan"] = (
                len(results) if hasattr(results, "__len__") else None
            )
        if pack_header:
            metrics.update({k: v for k, v in pack_header.items() if v is not None})
        metrics["cns_alerts_found"] = len(alerts)
        if alerts:
            metrics["cns_alerts"] = alerts

        return {
            "status": "ok",
            "artifacts": [],
            "metrics": metrics,
            "ran": ["sentiment_scanner"],
        }
    except Exception as exc:  # noqa: BLE001
        return _error_result(exc, panel_id="sentiment_scanner")


PANELS: tuple[PanelSpec, ...] = (
    # ---------------------------- Volatility ----------------------------
    PanelSpec(
        id="garch",
        title="GARCH(1,1)",
        tab="volatility",
        group="Term structure & premium",
        blurb=(
            "Conditional volatility from the return series. Writes "
            "garch_conditional_vol, which is what the VaR tools on the desk "
            "read instead of a flat 0.25."
        ),
        run=module_panel("garch"),
        output_kind="metrics",
        default_on=True,
        params=(
            PanelParam(
                "jump_filter",
                "Filter jump days before fitting",
                "bool",
                False,
                help=(
                    "Winsorize jump-attributable days so the fit reflects "
                    "diffusive clustering only."
                ),
            ),
            PanelParam(
                "jump_filter_model",
                "Jump filter model",
                "choice",
                "Merton",
                ("Merton", "Kou", "VarianceGamma"),
                help="Only models with a constant diffusive sigma qualify.",
            ),
            PanelParam(
                "jump_adjust_forecast",
                "Adjust forecast by jump share",
                "bool",
                False,
                help="Scale the forecast by Bates' option-implied jump share.",
            ),
        ),
        consumes=("jump_diffusion",),
    ),
    PanelSpec(
        id="variance_swap",
        title="Variance Swap Replication",
        tab="volatility",
        group="Term structure & premium",
        blurb=(
            "Carr-Madan / Demeterfi fair variance strike vs ATM IV, and the "
            "convexity premium between them."
        ),
        run=module_panel("variance_swap"),
        output_kind="metrics",
        default_on=True,
        params=(
            PanelParam(
                "jump_model",
                "Companion jump fit",
                "choice",
                "none",
                _JUMP_MODELS,
                help=(
                    "Fits a jump model to the same chain beside the "
                    "replication. Does not change the fair strike; with "
                    "Bates it splits fair variance into jump and diffusive "
                    "legs."
                ),
            ),
        ),
    ),
    PanelSpec(
        id="vrp_term_structure",
        title="VRP Term Structure",
        tab="volatility",
        group="Term structure & premium",
        blurb=(
            "Implied-minus-realized variance premium across tenors. Runs via "
            "the Tools entry point -- Vol_Suite's same-named slug is a "
            "selection-only marker that cannot run standalone."
        ),
        run=vrp_panel,
        output_kind="chart",
        default_on=True,
    ),
    PanelSpec(
        id="svi_smile",
        title="SVI Smile",
        tab="volatility",
        group="Term structure & premium",
        blurb="Arbitrage-free SVI parameterization fitted to the live smile.",
        run=module_panel("svi_smile"),
        output_kind="metrics",
    ),
    PanelSpec(
        id="jump_diffusion",
        title="Jump Diffusion (default fit)",
        tab="volatility",
        group="Jump models",
        blurb=(
            "Calibrates the default model (Bates unless JUMP_MODEL_DEFAULT "
            "says otherwise) plus the Merton sigma the GARCH jump filter "
            "wants. RMSE is scoped to the calibration mask, not the full "
            "smile."
        ),
        run=module_panel("jump_diffusion"),
        output_kind="metrics",
        default_on=True,
    ),
    PanelSpec(
        id="jump_model_comparison",
        title="Jump Model Zoo",
        tab="volatility",
        group="Jump models",
        blurb=(
            "Calibrates VG / Heston / Bates / Kou / Merton on one chain and "
            "ranks them by RMSE in IV space."
        ),
        run=module_panel("jump_model_comparison"),
        output_kind="metrics",
    ),
    PanelSpec(
        id="surface_market_iv",
        title="Market IV Surface",
        tab="volatility",
        group="Surfaces",
        blurb="Vendor implied-vol surface, strike x tenor, as a 3D render.",
        run=surface_panel("iv_surface_market"),
        output_kind="chart",
        default_on=True,
        params=(
            PanelParam(
                "min_dte",
                "Minimum DTE",
                "number",
                0,
                help=(
                    "Drop expiries inside N days -- e.g. 14 for a term-"
                    "structure view instead of the front-week-inclusive "
                    "default."
                ),
            ),
        ),
    ),
    PanelSpec(
        id="surface_greek",
        title="Greek Surface",
        tab="volatility",
        group="Surfaces",
        blurb="Dealer-frame greek across every listed expiry, strike x DTE.",
        run=surface_panel("greek_surface"),
        output_kind="chart",
        default_on=True,
        params=(PanelParam("greek", "Greek", "choice", "gamma", _GREEKS),),
    ),
    PanelSpec(
        id="surface_flow_strike_expiry",
        title="Flow Surface - strike x expiry",
        tab="volatility",
        group="Surfaces",
        blurb="Net premium (call minus put) heatmap across the whole chain.",
        run=surface_panel("flow_strike_expiry"),
        output_kind="chart",
        params=(
            PanelParam("max_dte", "Max DTE", "number", 60),
            PanelParam("min_dte", "Min DTE", "number", 0),
        ),
    ),
    PanelSpec(
        id="surface_flow_strike_time",
        title="Flow Surface - strike x time",
        tab="volatility",
        group="Surfaces",
        blurb="One session's options flow by strike and intraday time bucket.",
        run=surface_panel("flow_strike_time"),
        output_kind="chart",
    ),
    PanelSpec(
        id="correlation_matrix",
        title="Correlation / Covariance",
        tab="volatility",
        group="Cross-sectional",
        blurb=(
            "Correlation and covariance across the basket, plus basket vol, "
            "beta and diversification ratio. Feeds the VaR tools on the desk "
            "-- without it they simulate at identity correlation."
        ),
        run=module_panel("correlation_matrix"),
        output_kind="metrics",
        scope="basket",
        params=(
            PanelParam(
                "period", "History window", "choice", "2y", ("6m", "1y", "2y", "5y")
            ),
            PanelParam("market", "Market proxy (beta)", "text", "SPY"),
        ),
        consumes=("positions",),
    ),
    PanelSpec(
        id="portfolio_surface",
        title="Portfolio IV Surface",
        tab="volatility",
        group="Cross-sectional",
        blurb=(
            "Every holding's IV surface blended by market value, in moneyness "
            "space. The vol your book is exposed to -- not portfolio vol, "
            "which needs the correlation matrix."
        ),
        run=portfolio_surface_panel,
        output_kind="chart",
        scope="basket",
        consumes=("positions", "held_tickers"),
    ),
    PanelSpec(
        id="chain_scanner",
        title="Chain Scanner",
        tab="volatility",
        group="Cross-sectional",
        blurb="Full-chain scan for the scope ticker: IV, OI and flow by strike.",
        run=module_panel("chain_scanner"),
        output_kind="metrics",
    ),
    # ------------------------------ Models ------------------------------
    PanelSpec(
        id="leisen_reimer",
        title="Leisen-Reimer",
        tab="models",
        group="American pricers",
        blurb=(
            "The repo default. Better strike/step convergence than CRR for "
            "American options; main.py resolves sigma and greeks from this "
            "tree."
        ),
        run=module_panel("leisen_reimer"),
        default_on=True,
        consumes=("sigma",),
    ),
    PanelSpec(
        id="crr",
        title="CRR Binomial",
        tab="models",
        group="American pricers",
        blurb="Cox-Ross-Rubinstein tree. A separate model, never the default.",
        run=module_panel("crr"),
    ),
    PanelSpec(
        id="baw",
        title="Barone-Adesi-Whaley",
        tab="models",
        group="American pricers",
        blurb="Quadratic-approximation American pricer; fast, closed-form-ish.",
        run=module_panel("baw"),
    ),
    PanelSpec(
        id="mc",
        title="Monte Carlo (LSM)",
        tab="models",
        group="Simulation pricers",
        blurb="Longstaff-Schwartz least-squares Monte Carlo, plain diffusion.",
        run=module_panel("mc"),
    ),
    PanelSpec(
        id="mc_heston_lsm",
        title="Heston MC (LSM)",
        tab="models",
        group="Simulation pricers",
        blurb="LSM under stochastic variance -- pulls a Heston fit when present.",
        run=module_panel("mc_heston_lsm"),
        consumes=("jump_diffusion", "jump_model_comparison"),
    ),
    PanelSpec(
        id="sabr",
        title="SABR",
        tab="models",
        group="Smile models",
        blurb="Stochastic-alpha-beta-rho smile fit.",
        run=module_panel("sabr"),
    ),
    PanelSpec(
        id="vanna_volga",
        title="Vanna-Volga",
        tab="models",
        group="Smile models",
        blurb="Castagna-Mercurio three-point smile construction.",
        run=module_panel("vanna_volga"),
    ),
    PanelSpec(
        id="newton_raphson_iv",
        title="Newton-Raphson IV",
        tab="models",
        group="Smile models",
        blurb="Implied-vol solve by Newton-Raphson against the live quote.",
        run=module_panel("newton_raphson_iv"),
    ),
    PanelSpec(
        id="model_comparison",
        title="Model Comparison",
        tab="models",
        group="Comparison",
        blurb="Every pricer on one contract, side by side against the quote.",
        run=module_panel("model_comparison"),
        output_kind="chart",
        default_on=True,
    ),
    PanelSpec(
        id="smile_compare",
        title="Smile Compare",
        tab="models",
        group="Comparison",
        blurb=(
            "One expiry's smile as each model solves it, against vendor IV. "
            "Where a model curve leaves the market curve is where it says the "
            "chain is mispriced."
        ),
        run=smile_compare_panel,
        output_kind="chart",
        default_on=True,
        params=(
            PanelParam("option_type", "Right", "choice", "call", ("call", "put")),
            PanelParam("include_mc", "Include MC", "bool", True),
            PanelParam("include_heston", "Include Heston", "bool", True),
        ),
    ),
    PanelSpec(
        id="options_strategy",
        title="Strategy Builder",
        tab="models",
        group="Comparison",
        blurb="Builds and scores option structures on the scope contract.",
        run=tool_panel("options_strategy_tool", defaults={"_tab": "models"}),
    ),
    # ----------------------------- Sentiment -----------------------------
    PanelSpec(
        id="highlight_packs",
        title="Screener Focus Tickers",
        tab="sentiment",
        group="Context",
        blurb=(
            "The scanner's latest highlighted-ticker packs. Publishing these "
            "as the basket is how 'the scan found these names' becomes scope."
        ),
        run=module_panel("highlight_packs"),
        default_on=True,
        scope="none",
        params=(
            PanelParam("pack_group", "Pack group", "text", ""),
            PanelParam("pack_priority", "Priority", "text", ""),
            PanelParam("pack_limit", "Max packs", "number", 5),
        ),
    ),
    PanelSpec(
        id="positions_context",
        title="My Positions",
        tab="sentiment",
        group="Context",
        blurb="The book, as context for everything else on this tab.",
        run=module_panel("positions"),
        default_on=True,
        scope="none",
    ),
    PanelSpec(
        id="sentiment_scanner",
        title="Contested-Narrative Scanner",
        tab="sentiment",
        group="Context",
        blurb=(
            "The loop scanner itself (StockTwits / Reddit / YouTube + the "
            "correlation engine). Shows the last completed pass; tick Run a "
            "new scan pass to launch one bounded run in the scanner's own venv."
        ),
        run=sentiment_scanner_panel,
        output_kind="metrics",
        default_on=True,
        scope="none",
        params=(
            PanelParam(
                "run_scan",
                "Run a new scan pass (slow -- scrapes social)",
                kind="bool",
                default=False,
                help=(
                    "Launches main.py --universe <scope> in sentiment-scanner's "
                    "own venv. Unticked, this panel only READS the last pass."
                ),
            ),
            PanelParam(
                "skip_youtube",
                "Skip YouTube",
                kind="bool",
                default=False,
                help="Skip transcript sentiment (pointless if :4416 is down).",
            ),
            PanelParam(
                "timeout_s",
                "Scan timeout (seconds)",
                kind="number",
                default=900,
                help="The subprocess is killed after this; prior results survive.",
            ),
        ),
    ),
    PanelSpec(
        id="scan_library",
        title="Scan Library",
        tab="sentiment",
        group="Context",
        blurb=(
            "Morning scans, X/Twitter buzz reports, reddit and rumor notes "
            "off the shelf. Filters to the scope ticker when one is set."
        ),
        run=library_panel,
        output_kind="metrics",
        default_on=True,
        scope="none",
        params=(
            PanelParam(
                "shelf",
                "Shelf",
                "choice",
                "all",
                ("all",) + tuple(name for name, _ in LIBRARY_SHELVES) + ("other",),
            ),
        ),
    ),
    PanelSpec(
        id="gex",
        title="GEX",
        tab="sentiment",
        group="Options flow",
        blurb="Net dollar gamma by strike and expiry.",
        run=module_panel("gex"),
        default_on=True,
    ),
    PanelSpec(
        id="unusual_oi",
        title="Unusual OI",
        tab="sentiment",
        group="Options flow",
        blurb="Strikes and expiries with open-interest patterns out of line.",
        run=module_panel("unusual_oi"),
        default_on=True,
    ),
    PanelSpec(
        id="iv_rank",
        title="IV Rank",
        tab="sentiment",
        group="Options flow",
        blurb="Implied vol against its own history and against realized.",
        run=module_panel("iv_rank"),
        default_on=True,
    ),
    PanelSpec(
        id="skew",
        title="Skew",
        tab="sentiment",
        group="Options flow",
        blurb="Option-implied skew by expiry -- what the tails are paying for.",
        run=module_panel("skew"),
    ),
    PanelSpec(
        id="max_pain",
        title="Max Pain",
        tab="sentiment",
        group="Options flow",
        blurb="Strike of minimum aggregate option-holder value at expiry.",
        run=module_panel("max_pain"),
    ),
    PanelSpec(
        id="vol_dispersion",
        title="Vol Dispersion",
        tab="sentiment",
        group="Options flow",
        blurb="Index vol against the vol of its members.",
        run=module_panel("vol_dispersion"),
    ),
    PanelSpec(
        id="earnings",
        title="Earnings",
        tab="sentiment",
        group="Options flow",
        blurb="Upcoming earnings and the vol premium priced into them.",
        run=module_panel("earnings"),
    ),
    PanelSpec(
        id="sentiment_vrp",
        title="VRP",
        tab="sentiment",
        group="Options flow",
        blurb=(
            "Variance risk premium across tenors -- the same VRP as the "
            "Volatility tab, here because a rumor with no premium behind it "
            "is just noise."
        ),
        run=vrp_panel,
        output_kind="chart",
    ),
)

PANELS_BY_ID: dict[str, PanelSpec] = {p.id: p for p in PANELS}

TABS: tuple[tuple[str, str], ...] = (
    ("volatility", "Volatility"),
    ("models", "Models"),
    ("sentiment", "Sentiment"),
)


def panels_for_tab(tab: str) -> list[PanelSpec]:
    return [p for p in PANELS if p.tab == tab]


def get_panel(panel_id: str) -> PanelSpec | None:
    return PANELS_BY_ID.get(panel_id)
