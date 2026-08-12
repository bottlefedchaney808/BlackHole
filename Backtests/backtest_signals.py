"""backtest_signals.py — H3: signal tournament (the lot vs the lot).

Replays historical signal packs (sentiment-scanner CNS highlight packs) and
scores each strategy's predictive power over forward N-day returns:
top-quartile vs bottom-quartile hit rate, long-only Sharpe, Pearson corr.
Strategies here: CNS score, WAR score, RANK (inverted rank).

Pattern per the signal-backtesting skill: packs on disk -> (date, ticker,
score) signals -> forward returns from the price source -> quartile
bucketing -> per-strategy tournament table.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from Backtests.core import parse_yyyymmdd, summarize_errors

STRATEGIES = ["CNS", "WAR", "RANK"]


def _score_for(pack_ticker: Dict[str, Any], strategy: str) -> Optional[float]:
    if strategy == "CNS":
        v = pack_ticker.get("cns")
        return float(v) if v is not None else None
    if strategy == "WAR":
        v = pack_ticker.get("war_score")
        return float(v) if v is not None else None
    if strategy == "RANK":
        v = pack_ticker.get("rank")
        return -float(v) if v is not None else None  # lower rank = better = higher score
    return None


def load_packs(pack_dir: str) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Return [(date_str, symbol, pack_ticker)] deduped per (date, symbol)."""
    out: List[Tuple[str, str, Dict[str, Any]]] = []
    seen = set()
    if not os.path.isdir(pack_dir):
        return out
    for entry in sorted(os.listdir(pack_dir)):
        day_dir = os.path.join(pack_dir, entry)
        if not (os.path.isdir(day_dir) and len(entry) == 8 and entry.isdigit()):
            continue
        for fn in sorted(os.listdir(day_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(day_dir, fn), "r", encoding="utf-8") as f:
                    pack = json.load(f)
            except Exception:
                continue
            created = pack.get("created_at") or pack.get("last_updated_at")
            d = parse_yyyymmdd(str(created).split("T")[0]) if created else None
            if d is None:
                continue
            date_str = d.strftime("%Y%m%d")
            for t in pack.get("tickers", []):
                sym = str(t.get("symbol", "")).upper().strip()
                if not sym:
                    continue
                key = (date_str, sym)
                if key in seen:
                    continue
                seen.add(key)
                out.append((date_str, sym, t))
    return out


def _closes_for(td: Any, symbol: str, start: str, end: str) -> List[Tuple[str, float]]:
    """[(date, close)] sorted ascending from the price source.

    ThetaData stock EOD rows carry no per-row 'date' field — the trading day
    is in 'created' (ISO datetime string); close is a string.
    """
    try:
        rows = td.hist_stock_eod(symbol, start, end)
    except Exception:
        return []
    out: List[Tuple[str, float]] = []
    for r in rows:
        d_raw = r.get("date") or r.get("created")
        d = parse_yyyymmdd(d_raw) if d_raw else None
        c = r.get("close")
        if d is None or c is None:
            continue
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out.append((d.strftime("%Y%m%d"), c))
    out.sort()
    return out


def forward_return(td: Any, symbol: str, signal_date: str, forward_days: int) -> Optional[float]:
    """Return from first close >= signal_date to the close `forward_days`
    trading sessions later. None if insufficient history."""
    start_dt = parse_yyyymmdd(signal_date)
    if start_dt is None:
        return None
    start = start_dt.strftime("%Y%m%d")
    end = (start_dt + timedelta(days=forward_days * 2 + 10)).strftime("%Y%m%d")
    closes = _closes_for(td, symbol, start, end)
    if len(closes) < forward_days + 1:
        return None
    ref = closes[0][1]
    fwd = closes[forward_days][1]
    if ref <= 0:
        return None
    return fwd / ref - 1.0


def run_signal_tournament(
    td: Any,
    pack_dir: str,
    forward_days: int = 5,
    controller_factory: Optional[Any] = None,
) -> Dict[str, Any]:
    """Evaluate every strategy over the pack history on disk.

    controller_factory: lazy price-source constructor (tests pass a fake).
    When None, `td` is used directly.
    """
    packs = load_packs(pack_dir)
    price_src = td
    if controller_factory is not None:
        price_src = None  # created lazily on first need

    results: Dict[str, Dict[str, Any]] = {}
    for strategy in STRATEGIES:
        signals: List[Tuple[str, str, float, Optional[float]]] = []
        for date_str, sym, pack_t in packs:
            score = _score_for(pack_t, strategy)
            if score is None:
                continue
            src = price_src if price_src is not None else controller_factory()  # type: ignore[misc]
            fwd = forward_return(src, sym, date_str, forward_days)
            signals.append((date_str, sym, score, fwd))

        valid = [(s, f) for _, _, s, f in signals if f is not None]
        if len(valid) < 4:
            results[strategy] = {"n_signals": len(signals), "n_valid": len(valid),
                                 "n_top": 0, "n_bottom": 0,
                                 "hit_rate": None, "sharpe": None, "corr": None,
                                 "degenerate": True}
            continue

        scores = [s for s, _ in valid]
        scores_sorted = sorted(scores)
        q2 = scores_sorted[len(scores_sorted) // 2]
        q3 = scores_sorted[int(round(len(scores_sorted) * 0.75)) - 1]
        top = [f for s, f in valid if s >= q3]
        bottom = [f for s, f in valid if s < q2]
        hit_rate = (sum(top) / len(top) - sum(bottom) / len(bottom)) if top and bottom else None
        sharpe = None
        if len(top) >= 2:
            m = sum(top) / len(top)
            sd = math.sqrt(sum((x - m) ** 2 for x in top) / (len(top) - 1))
            if sd > 0:
                sharpe = (m / sd) * math.sqrt(252.0 / forward_days)
        corr = None
        if len(valid) >= 3:
            from Backtests.core import pearson
            corr = pearson([(s, f) for s, f in valid])
        results[strategy] = {
            "n_signals": len(signals),
            "n_valid": len(valid),
            "n_top": len(top),
            "n_bottom": len(bottom),
            "hit_rate": hit_rate,
            "sharpe": sharpe,
            "corr": corr,
            "degenerate": False,
        }

    return {
        "strategies": results,
        "forward_days": forward_days,
        "n_pack_signals": len(packs),
        "pack_dir": pack_dir,
    }


def render_signals_report(result: Dict[str, Any]) -> List[str]:
    lines = [
        "== H3 Signal tournament: scored signals vs forward returns ==",
        f"pack dir: {result['pack_dir']}",
        f"signals: {result['n_pack_signals']}   forward days: {result['forward_days']}",
        "",
        "strategy   n_valid   n_top  n_bottom   hit_rate   sharpe(ann)   corr",
        "-------------------------------------------------------------------",
    ]
    for strategy in STRATEGIES:
        r = result["strategies"][strategy]
        hit = f"{r['hit_rate'] * 100:+.2f}%" if r.get("hit_rate") is not None else "-"
        sh = f"{r['sharpe']:.2f}" if r.get("sharpe") is not None else "-"
        co = f"{r['corr']:.3f}" if r.get("corr") is not None else "-"
        deg = "  (degenerate)" if r.get("degenerate") else ""
        lines.append(
            f"{strategy:<10} {r['n_valid']:>7} {r.get('n_top', 0):>7} {r.get('n_bottom', 0):>9} "
            f"{hit:>10} {sh:>13} {co:>6}{deg}"
        )
    lines.append("")
    lines.append("hit_rate = mean(top-quartile forward return) - mean(bottom-quartile);")
    lines.append("sharpe = long-only top-quartile, annualised by sqrt(252/forward_days);")
    lines.append("corr = Pearson(signal score, forward return), n>=3 required.")
    lines.append("")
    return lines
