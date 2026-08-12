"""backtest_greeks.py — H2: model greeks vs market greeks, two levels.

Level 1 (first-order): delta, gamma, theta, vega, rho.
Level 2 (second-order): vanna, charm, vomma, speed, color.

All engines report ThetaData-compatible conventions (per-day theta,
per-year charm/color, etc. — calibrated against live Market rows in the
Options_Suite refactors). Metrics per (model, greek): MAE, RMSE, bias,
Pearson corr, sign agreement. Ranks per greek by MAE.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from Backtests.core import ALL_GREEK_FIELDS, GREEK_FIELDS_L1, GREEK_FIELDS_L2, summarize_errors
from Backtests.models import MODEL_ORDER, evaluate_model

LEVELS = {"L1": GREEK_FIELDS_L1, "L2": GREEK_FIELDS_L2}


def run_greeks(chains: Sequence[Any]) -> Dict[str, Any]:
    """chains: iterable of ChainDay. Returns per-level per-model per-greek tables."""
    acc: Dict[str, Dict[str, Dict[str, List]]] = {
        level: {m: {g: [] for g in fields} for m in MODEL_ORDER}
        for level, fields in LEVELS.items()
    }
    l2_diagnostics = [
        {
            "ticker": getattr(cd, "ticker", None),
            "expiry": getattr(cd, "expiry", None),
            "date": getattr(cd, "date", None),
            "status": getattr(cd, "l2_status", "not_requested"),
            "error": getattr(cd, "l2_error", None),
        }
        for cd in chains
    ]
    for cd in chains:
        for m in MODEL_ORDER:
            ev = evaluate_model(
                m, cd.rows, cd.spot, cd.T, cd.r, cd.q,
                vv_ctx=cd.vv_ctx, sabr_cal=cd.sabr_cal, heston_ctx=cd.heston_ctx,
            )
            for level, fields in LEVELS.items():
                for g in fields:
                    acc[level][m][g].extend(ev["greeks"][g])

    tables: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for level, fields in LEVELS.items():
        tables[level] = {}
        for m in MODEL_ORDER:
            tables[level][m] = {}
            for g in fields:
                tables[level][m][g] = summarize_errors(acc[level][m][g])

    # per-level per-greek rank by MAE
    ranks: Dict[str, Dict[str, List[str]]] = {}
    for level, fields in LEVELS.items():
        ranks[level] = {}
        for g in fields:
            ranked = sorted(
                [(m, tables[level][m][g].get("mae")) for m in MODEL_ORDER],
                key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else float("inf")),
            )
            ranks[level][g] = [m for m, _ in ranked]

    return {
        "tables": tables,
        "ranks": ranks,
        "n_days": len(chains),
        "l2_diagnostics": l2_diagnostics,
    }


def render_greeks_report(result: Dict[str, Any]) -> List[str]:
    lines = [
        "== H2 Greeks accuracy: model vs market (ThetaData) ==",
        "Level 1 (first-order): delta, gamma, theta, vega, rho",
        "Level 2 (second-order): vanna, charm, vomma, speed, color",
        f"days: {result['n_days']}",
        "",
    ]
    lines.append("L2 market-data availability:")
    diagnostics = result.get("l2_diagnostics", [])
    if diagnostics:
        for diagnostic in diagnostics:
            location = "@".join([
                str(diagnostic.get("ticker", "?")),
                str(diagnostic.get("expiry", "?")),
            ])
            location = f"{location} {diagnostic.get('date', '?')}"
            status = diagnostic.get("status", "not_requested")
            error = diagnostic.get("error")
            detail = f" ({error})" if error else ""
            lines.append(f"  {location}: {status}{detail}")
    else:
        lines.append("  none")
    lines.append("")
    for level, fields in LEVELS.items():
        lines.append(f"--- Level {level} ---")
        lines.append("rank by MAE per greek:")
        for g in fields:
            lines.append(f"  {g:<8}: " + " > ".join(result["ranks"][level][g]))
        lines.append("")
        lines.append("model     " + "".join(f"{g:>10}" for g in fields) + "   <- MAE per greek")
        for m in MODEL_ORDER:
            cells = []
            for g in fields:
                s = result["tables"][level][m][g]
                mae = s.get("mae")
                cells.append(f"{mae if mae is not None else float('nan'):>10.4f}")
            lines.append(f"{m:<10}" + "".join(cells))
        lines.append("")
        lines.append("corr per greek (scale-invariant; sign agreement in parens):")
        lines.append("model     " + "".join(f"{g:>16}" for g in fields))
        for m in MODEL_ORDER:
            cells = []
            for g in fields:
                s = result["tables"][level][m][g]
                corr = s.get("corr")
                sa = s.get("sign_agreement")
                c = f"{corr:.2f}" if corr is not None else "-"
                a = f"{sa * 100:.0f}%" if sa is not None else "-"
                cells.append(f"({a:>3}){c:>12}")
            lines.append(f"{m:<10}" + "".join(f"{c:>16}" for c in cells))
        lines.append("")
    lines.append("units: theta per day; charm/color per year; all engines calibrated to")
    lines.append("ThetaData conventions in the Options_Suite refactors (see engine docstrings).")
    lines.append("")
    return lines
