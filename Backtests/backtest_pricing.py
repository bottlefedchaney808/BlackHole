"""backtest_pricing.py — H1: model price vs market mid.

Every model prices the SAME contracts from the SAME market IV (fair-input
rule); SABR/VV/Heston additionally use their own chain-derived vol
construction (reported separately so the common-IV comparison stays clean).
Metrics per model: MAE, RMSE, mean bias (model-mid), Pearson corr, %MAE vs
mid. Bucketed by moneyness. Slow models (MC/Heston) run a sampled sub-chain;
fast engines run the full chain — the sample sizes are reported per model.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from Backtests.core import summarize_errors
from Backtests.models import MODEL_ORDER, SLOW_MODELS, evaluate_model

BUCKETS = ["DeepOTM", "OTM", "ATM", "ITM", "DeepITM"]


def run_pricing(chains: Sequence[Any]) -> Dict[str, Any]:
    """chains: iterable of ChainDay. Returns per-model + bucket tables."""
    acc: Dict[str, Dict[str, Any]] = {
        m: {"n": 0, "failures": 0, "pairs": [], "buckets": {b: [] for b in BUCKETS}}
        for m in MODEL_ORDER
    }
    for cd in chains:
        for m in MODEL_ORDER:
            ev = evaluate_model(
                m, cd.rows, cd.spot, cd.T, cd.r, cd.q,
                vv_ctx=cd.vv_ctx, sabr_cal=cd.sabr_cal, heston_ctx=cd.heston_ctx,
            )
            p = ev["pricing"]
            a = acc[m]
            a["n"] += p["n"]
            a["failures"] += p["failures"]
            a["pairs"].extend(p["pairs"])
            for b in BUCKETS:
                a["buckets"][b].extend(p["buckets"].get(b, []))

    tables: Dict[str, Any] = {}
    for m in MODEL_ORDER:
        a = acc[m]
        summary = summarize_errors(a["pairs"]) if a["pairs"] else {}
        mae_val = summary.get("mae")
        if mae_val is not None and a["pairs"]:
            mean_mid = sum(mid for _, mid in a["pairs"]) / len(a["pairs"])
            summary["mae_pct"] = (mae_val / mean_mid) if mean_mid else None
        tables[m] = {
            "n": a["n"],
            "failures": a["failures"],
            "sampled": m in SLOW_MODELS,
            "summary": summary,
            "buckets": {b: summarize_errors(a["buckets"][b]) for b in BUCKETS},
        }

    ranked = sorted(
        [(m, tables[m]["summary"].get("rmse")) for m in MODEL_ORDER],
        key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else float("inf")),
    )
    return {
        "models": tables,
        "rank_by_rmse": [m for m, _ in ranked],
        "n_days": len(chains),
    }


def render_pricing_report(result: Dict[str, Any]) -> List[str]:
    lines = ["== H1 Pricing accuracy: model vs market mid ==",
             "common input: market IV; VV/SABR/Heston use own chain-derived smiles (see model notes)",
             f"days: {result['n_days']}   rank by RMSE: {' > '.join(result['rank_by_rmse'])}",
             "",
             "model        n    fail   MAE($)   RMSE($)   bias($)   %MAE    corr",
             "-------------------------------------------------------------------"]
    for m in result["rank_by_rmse"]:
        t = result["models"][m]
        s = t["summary"]
        corr = f"{s['corr']:.3f}" if s.get("corr") is not None else "-"
        pct = f"{s['mae_pct'] * 100:.1f}%" if s.get("mae_pct") is not None else "-"
        tag = " *" if t["sampled"] else ""
        lines.append(
            f"{m:<10} {t['n']:>5} {t['failures']:>6} "
            f"{_f(s.get('mae')):>8} {_f(s.get('rmse')):>8} {_f(s.get('bias')):>8} "
            f"{pct:>7} {corr:>5}{tag}"
        )
    lines.append("")
    lines.append("bucket breakdown (MAE $, RMSE $):")
    lines.append("model      " + "  ".join(f"{b:>22}" for b in BUCKETS))
    for m in MODEL_ORDER:
        cells = []
        for b in BUCKETS:
            s = result["models"][m]["buckets"][b]
            cells.append(f"{_f(s.get('mae'))}/{_f(s.get('rmse'))}")
        lines.append(f"{m:<10} " + "  ".join(f"{c:>22}" for c in cells))
    lines.append("")
    lines.append("model notes: VV/SABR/Heston priced at their own chain-calibrated smiles;")
    lines.append("CRR/LR/BAW/MC priced at the common market IV (isolates model error).")
    lines.append("(*) MC/Heston run a sampled sub-chain (ATM band + wing stride) for runtime.")
    lines.append("")
    return lines


def _f(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.4f}"
