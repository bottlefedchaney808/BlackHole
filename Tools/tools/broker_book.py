"""broker_book.py -- aggregate chain-scan outputs into a convention-free broker-book
control corpus and backtest its predictive content against forward returns.

Convention rules (Jason 2026-08-16) -- DO NOT assign dealer direction:
  delta / vanna / charm : Net = sum(raw_greek * OI), take the greek's built-in sign
                          as-is (call delta +, put delta -, vanna moneyness-signed).
                          NO -1 dealer convention.
  gamma                 : raw gamma has NO sign (positive on both rights) so net_gamma
                          is always positive; net_gamma_gex (calls +, puts -) is an
                          IMPORTED reference only.
  theta / vega          : all-long proxies (raw long-option theta is -, vega is +).

Backtest arms (both reported; this is an EXPLORATORY, low-power study, not a
pre-registered falsifier):
  pooled        : Pearson r(net, forward return) per net x horizon, with nominal n AND
                  unique-(ticker,date) / unique-date effective-n reported so clustered
                  snapshots are never read as independent signal.
  cross_sectional: within each scan date, rank tickers by each net and compare the
                  mean forward return of the top vs bottom half -- escapes the
                  shared-underlying clustering of same-date snapshots.

All functions here are pure (take plain data, return plain data) so they are testable
network-free. The only I/O is `_fetch_closes_default`, used only when the tool runs for
real.
"""

from __future__ import annotations

import datetime as dt
import glob
import os
import re
from typing import Any, Dict, List, Optional

import pandas as pd

# Greeks whose sign is built into the raw value -> taken as-is, NO direction assigned.
GREEKS_ASIS = ["delta", "vanna", "charm"]
GREEKS_ALL = GREEKS_ASIS + ["gamma", "theta", "vega"]

NET_COLUMNS = [
    "net_delta",
    "net_vanna",
    "net_charm",
    "net_theta",
    "net_vega",
    "net_gamma",
    "net_gamma_gex",
]
HORIZONS = [1, 2, 5]

FILENAME_RE = re.compile(
    r"^(?P<ticker>[A-Z]+)_(?P<expiry>\d{8})_chain_scan_(?P<ts>\d{8}_\d{6})\.csv$"
)

DEFAULT_ROOTS = [
    r"C:/Users/bottl/FinancialDevelopment/orchestrator_output",
    r"C:/Users/bottl/FinancialDevelopment/Vol_Suite/outputs",
]


# --------------------------------------------------------------------------- #
# Aggregation (pure)
# --------------------------------------------------------------------------- #
def parse_chain_filename(path: str) -> Optional[Dict[str, Any]]:
    m = FILENAME_RE.match(os.path.basename(path))
    if not m:
        return None
    return {
        "ticker": m.group("ticker"),
        "expiry": m.group("expiry"),
        "scan_dt": dt.datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S"),
    }


def compute_nets(df: pd.DataFrame) -> Dict[str, float]:
    """Compute convention-free broker-book nets from one chain-scan frame.

    delta/vanna/charm: sum(raw_greek * OI) as-is (no direction assigned).
    gamma            : sum(gamma * OI) (always positive) + net_gamma_gex = call - put.
    theta / vega     : all-long proxies.
    Rows without OI are excluded; rows where a greek is missing are skipped per greek.
    """
    oi = pd.to_numeric(df.get("oi"), errors="coerce").fillna(0)
    has_oi = oi > 0
    rights = df.get("right")
    is_call = None
    if rights is not None:
        is_call = rights.map(lambda r: str(r).strip().upper() == "C")

    out: Dict[str, float] = {}
    for g in GREEKS_ALL:
        col = df.get(g)
        if col is None:
            out[f"net_{g}"] = float("nan")
            continue
        gv = pd.to_numeric(col, errors="coerce")
        mask = has_oi & gv.notna()
        out[f"net_{g}"] = float((gv[mask] * oi[mask]).sum())
        if g == "gamma" and is_call is not None:
            cmask = mask & is_call
            pmask = mask & ~is_call
            out["net_gamma_gex"] = float(
                (gv[cmask] * oi[cmask]).sum() - (gv[pmask] * oi[pmask]).sum()
            )
    if "net_gamma_gex" not in out:
        out["net_gamma_gex"] = float("nan")
    return out


def aggregate_corpus(
    root_dirs: Optional[List[str]] = None, max_files: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Glob every *chain_scan*.csv under root_dirs and compute nets per snapshot."""
    root_dirs = root_dirs or DEFAULT_ROOTS
    files: List[str] = []
    for root in root_dirs:
        files.extend(
            glob.glob(os.path.join(root, "**", "*chain_scan*.csv"), recursive=True)
        )
    files = sorted(files)
    if max_files:
        files = files[:max_files]

    rows: List[Dict[str, Any]] = []
    for path in files:
        meta = parse_chain_filename(path)
        if not meta:
            continue
        try:
            df = pd.read_csv(path)
        except Exception:  # noqa: BLE001 -- a bad file must not kill the whole corpus
            continue
        if "oi" not in df.columns:
            continue
        rec = {"path": path, **meta, "date": meta["scan_dt"].date(), **compute_nets(df)}
        rows.append(rec)
    return rows


def forward_returns_from_closes(
    closes: Dict[Any, float], date, horizons: List[int] = HORIZONS
) -> Dict[int, Optional[float]]:
    """Given {date: close} sorted, return {h: close[h-days-after]/close[date] - 1}.

    Reference close is the close ON `date` (daily-frequency convention); forward h is the
    close h trading days later. Returns None where insufficient future data exists.
    """
    dates = sorted(closes.keys())
    if date not in closes:
        return {h: None for h in horizons}
    ref_idx = dates.index(date)
    ref = closes[date]
    out: Dict[int, Optional[float]] = {}
    for h in horizons:
        j = ref_idx + h
        out[h] = (closes[dates[j]] / ref - 1) if j < len(dates) else None
    return out


# --------------------------------------------------------------------------- #
# Backtest stats (pure)
# --------------------------------------------------------------------------- #
def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 4 or len(a) != len(b):
        return None
    s = pd.Series(a).corr(pd.Series(b))
    return None if pd.isna(s) else float(s)


def run_pooled(
    rows: List[Dict[str, Any]],
    nets: Optional[List[str]] = None,
    horizons: List[int] = HORIZONS,
) -> Dict[str, Any]:
    """Pooled Pearson r(net, forward) per net x horizon, with independence counts."""
    nets = nets or [c for c in NET_COLUMNS if c in (rows[0] if rows else {})]
    df = pd.DataFrame(rows)
    n_nominal = len(df)
    n_unique_td = df[["ticker", "date"]].drop_duplicates().shape[0] if n_nominal else 0
    n_unique_dates = df["date"].nunique() if n_nominal else 0

    cells = []
    for h in horizons:
        for n in nets:
            sub = df[[n, f"fwd_{h}"]].dropna()
            r = _pearson(sub[n].tolist(), sub[f"fwd_{h}"].tolist())
            cells.append({"net": n, "horizon": h, "r": r, "n": int(len(sub))})

    return {
        "kind": "pooled",
        "nominal_snapshots": n_nominal,
        "unique_ticker_date": n_unique_td,
        "unique_dates": n_unique_dates,
        "cells": cells,
    }


def run_cross_sectional(
    rows: List[Dict[str, Any]], nets: Optional[List[str]] = None, horizon: int = 1
) -> Dict[str, Any]:
    """Within each scan date, rank tickers by each net; compare top vs bottom half
    mean forward return. Aggregates over dates (a date needs >=2 signable tickers)."""
    nets = nets or [c for c in NET_COLUMNS if c in (rows[0] if rows else {})]
    df = pd.DataFrame(rows)
    fwd_col = f"fwd_{horizon}"

    per_date: Dict[Any, Dict[str, Any]] = {}
    for date, g in df.groupby("date"):
        usable = g.dropna(subset=[fwd_col])
        if len(usable) < 2:
            continue
        for n in nets:
            cell = usable.dropna(subset=[n])
            if len(cell) < 2:
                continue
            cell = cell.sort_values(n, ascending=True)
            k = len(cell) // 2
            if k == 0:
                continue
            top = cell.tail(k)
            bot = cell.head(k)
            diff = top[fwd_col].mean() - bot[fwd_col].mean()
            per_date.setdefault(n, []).append(
                {"date": str(date), "n": int(len(cell)), "diff": float(diff)}
            )

    result = {"kind": "cross_sectional", "horizon": horizon, "nets": {}}
    for n in nets:
        arr = per_date.get(n, [])
        result["nets"][n] = {
            "dates": len(arr),
            "mean_diff": float(pd.Series([a["diff"] for a in arr]).mean())
            if arr
            else None,
            "positive_dates": sum(1 for a in arr if a["diff"] > 0),
            "rows": arr,
        }
    return result


# --------------------------------------------------------------------------- #
# Orchestration (network only here, injectable for tests)
# --------------------------------------------------------------------------- #
def _fetch_closes_default(ticker: str, start: str, end: str) -> Dict[Any, float]:
    from shared.thetadata import ThetaDataController

    td = ThetaDataController()
    try:
        rows = td.hist_stock_eod(ticker, start, end)
    finally:
        td.close()
    closes: Dict[Any, float] = {}
    for r in rows:
        dv = r.get("created") or r.get("last_trade") or r.get("date")
        if not dv:
            continue
        d = pd.to_datetime(str(dv)).date()
        v = r.get("close")
        if v is not None and float(v) > 0:
            closes[d] = float(v)
    return closes


def run_backtest(context: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate the chain-scan corpus, join forward returns, run both arms.

    context keys (all optional):
      roots       : list of dirs to glob chain scans (default broker-book roots)
      max_files   : cap the number of chain scans scanned (test/debug)
      horizons    : forward horizons (default [1,2,5])
      fetch_closes: injectable Callable[[ticker,start,end], {date: close}] (tests)
    """
    roots = context.get("roots") or DEFAULT_ROOTS
    max_files = context.get("max_files")
    horizons = [int(h) for h in context.get("horizons", HORIZONS)]
    fetch_closes = context.get("fetch_closes") or _fetch_closes_default

    rows = aggregate_corpus(roots, max_files=max_files)
    if not rows:
        return _empty_result("no chain-scan snapshots found to aggregate")

    tickers = sorted({r["ticker"] for r in rows})
    dates = [r["date"] for r in rows]
    start = (min(dates) - dt.timedelta(days=2)).strftime("%Y%m%d")
    end = dt.date.today().strftime("%Y%m%d")

    closes = {}
    for t in tickers:
        try:
            closes[t] = fetch_closes(t, start, end)
        except Exception:  # noqa: BLE001 -- a missing ticker must not kill the run
            closes[t] = {}

    for r in rows:
        fr = forward_returns_from_closes(
            closes.get(r["ticker"], {}), r["date"], horizons
        )
        for h in horizons:
            r[f"fwd_{h}"] = fr.get(h)

    pooled = run_pooled(rows, horizons=horizons)
    cross_1 = run_cross_sectional(rows, horizon=1)
    cross_5 = run_cross_sectional(rows, horizon=5)

    return {
        "mode": "broker_book_accuracy",
        "n_snapshots": len(rows),
        "n_tickers": len(tickers),
        "date_range": f"{min(dates)}..{max(dates)}",
        "pooled": pooled,
        "cross_sectional": {"fwd_1d": cross_1, "fwd_5d": cross_5},
        "report": format_report(pooled, cross_1, cross_5),
    }


def _empty_result(reason: str) -> Dict[str, Any]:
    return {
        "mode": "broker_book_accuracy",
        "n_snapshots": 0,
        "n_tickers": 0,
        "error": reason,
        "report": reason,
        "pooled": {
            "cells": [],
            "nominal_snapshots": 0,
            "unique_ticker_date": 0,
            "unique_dates": 0,
        },
        "cross_sectional": {},
    }


def format_report(
    pooled: Dict[str, Any], cross_1: Dict[str, Any], cross_5: Dict[str, Any]
) -> str:
    lines = []
    lines.append(
        f"Broker-book control accuracy  "
        f"(n={pooled['nominal_snapshots']} snapshots, "
        f"{pooled['unique_dates']} unique dates, "
        f"{pooled['unique_ticker_date']} unique ticker-days)"
    )
    lines.append("")
    lines.append("POOLED  Pearson r(net, forward return)")
    header = f"{'net':14s}" + "".join(f"  fwd_{h}d (n)" for h in HORIZONS)
    lines.append(header)
    net_order = []
    for c in pooled["cells"]:
        if c["net"] not in net_order:
            net_order.append(c["net"])
    for n in net_order:
        row = f"{n:14s}"
        for h in HORIZONS:
            cell = next(
                (c for c in pooled["cells"] if c["net"] == n and c["horizon"] == h),
                None,
            )
            if cell and cell["r"] is not None:
                row += f"  {cell['r']:+.3f} ({cell['n']:2d})"
            else:
                row += "  -"
        lines.append(row)
    lines.append("")
    lines.append(
        "CROSS-SECTIONAL  top vs bottom half, mean fwd-return diff "
        "(positive = higher net -> higher forward return)"
    )
    for label, cx in (("fwd_1d", cross_1), ("fwd_5d", cross_5)):
        lines.append(f"  {label}:")
        for n, info in cx["nets"].items():
            if info["dates"]:
                lines.append(
                    f"    {n:14s} mean_diff={info['mean_diff']:+.4f} "
                    f"({info['positive_dates']}/{info['dates']} dates positive, "
                    f"n_dates={info['dates']})"
                )
            else:
                lines.append(f"    {n:14s} (no date had >=2 signable tickers)")
    lines.append("")
    lines.append(
        "NOTE: exploratory, low-power. unique_dates is the effective independence, "
        "NOT nominal snapshots."
    )
    return "\n".join(lines)


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    return run_backtest(context)
