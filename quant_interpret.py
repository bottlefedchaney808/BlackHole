"""Deterministic, trader-grade interpretation of a Quant run's artifacts.

Turns the structural artifacts a vol run leaves behind — the synthesis digest
(`quant_summary.json`) and the chain-strategy edge list (`chain_strategies.json`,
loaded through the Tools `options-strategy` tool) — into a readable written
narrative with concrete, directional trading insight.

It is deliberately hermetic: no LLM, no network. All signal is derived from the
run's own numbers, so it is fast, deterministic, and unit-testable. Where an
interpretation would benefit from a model, the caller can feed this text onward.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INTERPRETATION_FILENAME = "interpretation.json"
INTERPRETATION_MD_FILENAME = "interpretation.md"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _fmt_vol(x: Any, suffix: str = "") -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.1f}{suffix}"
    except (TypeError, ValueError):
        return str(x)


def _regime_blurb(regime: str | None) -> str:
    r = (regime or "").upper()
    if "RICH" in r:
        return "implied vol is trading above the environment's realized level — expensive premium"
    if "CHEAP" in r:
        return "implied vol is trading below realized — discounted premium relative to the move"
    if "FAIR" in r:
        return "implied vol is fairly priced against realized vol"
    return "implied-vs-realized regime not clearly characterized"
    

def _side_label(side: str) -> str:
    return "SELL" if side == "sell" else ("BUY" if side == "buy" else side)


def _strategy_lines(chain: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Split chain strategies into (sell list, buy list) of readable lines."""
    sells: list[str] = []
    buys: list[str] = []
    for s in chain.get("strategies") or [] if chain else []:
        if not isinstance(s, dict):
            continue
        side = s.get("side")
        right = str(s.get("right", "?")).upper()
        strike = s.get("strike")
        res = s.get("iv_residual_pts")
        oi = s.get("oi")
        line = f"{right} {strike}"
        details = []
        if res is not None:
            details.append(f"{res:+.2f} vol pts off fit")
        if oi is not None:
            details.append(f"OI {oi}")
        if details:
            line += "  (" + ", ".join(details) + ")"
        (sells if side == "sell" else buys).append(line)
    return sells, buys


def build_interpretation(run_dir: Path, module: str | None = None) -> dict[str, Any]:
    """Compose an interpretation from the run dir's artifacts and write it to disk.

    Returns the interpretation dict; also writes `interpretation.md` and
    `interpretation.json` into `run_dir`. Raises ValueError if the synthetic
    digest is missing/unreadable.
    """
    run_dir = Path(run_dir)
    summary_path = run_dir / "quant_summary.json"
    summary = _load(summary_path)
    if summary is None:
        raise ValueError("quant_summary.json not found or unreadable — run interpret after a vol run")

    # Chain strategies: loaded exactly as the Tools `options-strategy` tool does.
    chain: dict[str, Any] | None = None
    try:
        from Tools.tools.options_strategy_tool import run as _tool_run
        tool_out = _tool_run({"output_dir": str(run_dir)})
        chain = tool_out if isinstance(tool_out, dict) else None
    except Exception:
        chain = None

    metrics = summary.get("metrics") or {}
    vol = metrics.get("vol") or {}
    status = summary.get("status") or "unknown"
    risks = summary.get("risks") or []

    atm_iv = vol.get("atm_iv")
    fair_vol = vol.get("fair_vol")
    vrp = vol.get("vrp")
    signal = vol.get("signal")
    score = vol.get("score")
    regime = (chain or {}).get("regime") or vol.get("regime")

    sells, buys = _strategy_lines(chain)

    # ---- Build the narrative ----
    lines: list[str] = []
    header_ticker = (chain or {}).get("ticker") or summary.get("source_run_id") or "?"
    lines.append(f"# Quant Interpretation — {header_ticker}")
    lines.append("")
    if status in {"complete", "degraded", "succeeded"}:
        lines.append(f"**Verdict:** {signal or 'n/a'}  ·  **Regime:** {regime or 'n/a'}")
        lines.append("")
        lines.append(f"**At-the-money implied vol:** {_fmt_vol(atm_iv, '%')}  vs  "
                     f"**realized (30d):** {_fmt_vol(fair_vol, '%')}")
        if vrp is not None:
            direction = "premium is rich" if vrp > 0 else "premium is cheap (discounted)"
            lines.append(f"**VRP:** {_fmt_vol(vrp, ' pts')} — implied minus realized; {direction}.")
        lines.append("")
        lines.append(f"_Environment: {_regime_blurb(regime)}._")
    else:
        lines.append(f"**Status:** {status} — no usable metrics to interpret.")
        lines.append("")
        lines.append("The run produced a synthesis digest but no recognized metric groups. "
                     "This usually means the run dir lacks the companion result files "
                     "(e.g. only a vol scan with no options/var result).")

    # Edge strategy section
    lines.append("")
    lines.append("## Edges on the chain (from options-strategy)")
    lines.append("")
    if sells:
        lines.append(f"**Sell (IV rich — market paying up):** {len(sells)} strike(s)")
        for l in sells:
            lines.append(f"- {l}")
        lines.append("")
        lines.append("These wings trade above the fitted smile. Each is a candidate to "
                     "**sell** premium (short calls for the rich call wings) to harvest the "
                     "mispricing, sized against your realized-vol view.")
    if buys:
        lines.append(f"**Buy (IV cheap — market underpricing):** {len(buys)} strike(s)")
        for l in buys:
            lines.append(f"- {l}")
        lines.append("")
        lines.append("These wings trade below the fitted smile. Each is a candidate to "
                     "**buy** (long puts for cheap put wings) as cheap convexity — attractive "
                     "when the environment implies downside/upside you want long distance.")
    if not sells and not buys:
        lines.append("No edge candidates above the fit threshold in the cached chain scan.")
    elif score:
        lines.append("")
        lines.append(f"Net context: **{score}** edge candidate(s) flagged by the chain scanner.")

    if risks:
        lines.append("")
        lines.append("## Caveats")
        for r in risks:
            lines.append(f"- {r}")

    # ---- Assemble structured dict + persist ----
    interpretation = {
        "schema_version": 1,
        "ticker": header_ticker,
        "status": status,
        "verdict": signal,
        "regime": regime,
        "metrics": {"vol": {k: vol.get(k) for k in ("atm_iv", "fair_vol", "vrp", "signal", "score")}},
        "strategy": {
            "sell_candidates": sells,
            "buy_candidates": buys,
            "edge_count": score,
        },
        "risks": risks,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "interpretation_md": (summary.get("source_run_id") or run_dir.name) + "/" + INTERPRETATION_MD_FILENAME,
        "text": "\n".join(lines),
    }

    # Atomic-ish writes (plain files, not symlink-followed).
    md_text = "\n".join(lines) + "\n"
    _write_text(run_dir, INTERPRETATION_FILENAME, json.dumps(interpretation, indent=2, default=str) + "\n")
    _write_text(run_dir, INTERPRETATION_MD_FILENAME, md_text)
    return interpretation


def _write_text(run_dir: Path, filename: str, text: str) -> None:
    dest = run_dir / filename
    if dest.is_symlink():
        raise ValueError(f"publication destination must not be a symlink: {filename}")
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise
