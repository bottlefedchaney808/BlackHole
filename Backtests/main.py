"""main.py — Backtest Tournament CLI.

Three harnesses, one report:
  H1 pricing     -- model price vs market mid (common IV input)
  H2 greeks      -- two levels: first-order (delta/gamma/theta/vega/rho) and
                    second-order (vanna/charm/vomma/speed/color) vs market
  H3 signals     -- sentiment packs vs forward returns (head-to-head)

`run_tournament(...)` is the reusable engine (also called by the Tools
registry's strategy_pnl mode). Output: <out>/backtest_tournament_<ts>.txt
(human report) + <out>/backtest_summary_<ts>.json (machine). Diagnostics on
stderr; the compact human summary goes to stdout. Use --json to emit the
full summary JSON to stdout instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Direct `python Backtests\main.py` execution starts with Backtests/, so add
# the repository root before importing the shared package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.thetadata import ThetaDataController
from Backtests.backtest_greeks import render_greeks_report, run_greeks
from Backtests.backtest_pricing import render_pricing_report, run_pricing
from Backtests.backtest_signals import render_signals_report, run_signal_tournament
from Backtests.core import write_json, write_txt
from Backtests.data import fetch_chain_days, pick_expiry, recent_calendar_dates


def log(msg: str) -> None:
    print(f"[backtests] {msg}", file=sys.stderr)


def _import_harnesses():
    return {
        "pricing": (run_pricing, render_pricing_report),
        "greeks": (run_greeks, render_greeks_report),
        "signals": (run_signal_tournament, render_signals_report),
    }


def _default_expiry(td: Any, ticker: str, lookback_days: int) -> Optional[str]:
    min_days = 5 if lookback_days <= 1 else 21
    return pick_expiry(td, ticker, min_days)


def _fetch_context(td: Any, ticker: str) -> Dict[str, float]:
    r = float(td.fetch_risk_free_rate() or 0.05)
    q = float(td.fetch_dividend_yield(ticker) or 0.0)
    return {"r": r, "q": q}


def run_tournament(
    tickers: Sequence[str],
    harnesses: Sequence[str],
    expiry: str = "",
    lookback_days: int = 1,
    out_dir: str = "Backtests/outputs",
    packs_dir: str = "sentiment-scanner/data/exports/highlighted_ticker_packs",
    forward_days: int = 5,
    td: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the tournament and return the summary dict.

    tickers: e.g. ['SPY','QQQ']; harnesses: subset of
    {pricing, greeks, signals}. Writes .txt + .json artifacts into out_dir.
    A ThetaDataController is created/closed internally unless `td` is given.
    """
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    harnesses = [h for h in harnesses if h in {"pricing", "greeks", "signals"}]
    if not harnesses:
        raise ValueError("run_tournament: harnesses must be non-empty")
    want_pricing = "pricing" in harnesses
    want_greeks = "greeks" in harnesses
    want_signals = "signals" in harnesses

    _own = td is None
    if _own:
        td = ThetaDataController()
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_lines: List[str] = []
        summary: Dict[str, Any] = {
            "generated": ts,
            "tickers": tickers,
            "lookback_days": lookback_days,
            "harnesses": list(harnesses),
        }
        har = _import_harnesses()

        chains_by_ticker: Dict[str, List[Any]] = {}
        expiries: Dict[str, str] = {}
        for ticker in tickers:
            log(f"ticker {ticker}: fetching context (r, q)")
            ctx = _fetch_context(td, ticker)
            exp = expiry or _default_expiry(td, ticker, lookback_days)
            if not exp:
                log(f"ticker {ticker}: no expiry available, skipping chain harnesses")
                continue
            expiries[ticker] = exp
            log(f"ticker {ticker}: expiry {exp}, lookback {lookback_days}d")
            if want_pricing or want_greeks:
                dates = recent_calendar_dates(lookback_days)
                chains = fetch_chain_days(td, ticker, exp, dates, ctx["r"], ctx["q"])
                log(f"ticker {ticker}: {len(chains)} chain day(s) fetched")
                chains_by_ticker[ticker] = chains

        if want_pricing or want_greeks:
            header = (f"Chains: {', '.join(f'{t}@{expiries.get(t)}' for t in tickers if t in expiries)}")
            report_lines.append(header)
            report_lines.append("")
            summary["chains"] = header

        if want_pricing:
            all_chains: List[Any] = []
            for t in tickers:
                all_chains.extend(chains_by_ticker.get(t, []))
            log("running H1 pricing accuracy")
            pres = har["pricing"][0](all_chains)
            summary["pricing"] = pres
            report_lines += har["pricing"][1](pres)

        if want_greeks:
            all_chains = []
            for t in tickers:
                all_chains.extend(chains_by_ticker.get(t, []))
            log("running H2 greeks accuracy (L1 + L2)")
            gres = har["greeks"][0](all_chains)
            summary["greeks"] = gres
            report_lines += har["greeks"][1](gres)

        if want_signals:
            log(f"running H3 signal tournament (packs: {packs_dir})")
            sres = har["signals"][0](td, packs_dir, forward_days=forward_days)
            summary["signals"] = sres
            report_lines += har["signals"][1](sres)

        txt_path = write_txt(f"{out_dir}/backtest_tournament_{ts}.txt", report_lines)
        json_path = write_json(f"{out_dir}/backtest_summary_{ts}.json", summary)
        summary["artifacts"] = {"txt": txt_path, "json": json_path}
        summary["report"] = report_lines
        return summary
    finally:
        if _own:
            td.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest Tournament (pricing / greeks / signals)")
    ap.add_argument("--harness", default="all",
                    help="comma-separated: pricing,greeks,signals (default all)")
    ap.add_argument("--ticker", default="SPY,QQQ",
                    help="comma-separated tickers (default SPY,QQQ)")
    ap.add_argument("--expiry", default="",
                    help="YYYYMMDD expiry; default auto (next-week for snapshot, >=21d for lookback)")
    ap.add_argument("--lookback-days", type=int, default=1,
                    help="calendar days of history (1 = snapshot tournament; >1 uses per-day bulk greeks)")
    ap.add_argument("--out", default="Backtests/outputs",
                    help="artifact directory")
    ap.add_argument("--packs-dir", default="sentiment-scanner/data/exports/highlighted_ticker_packs",
                    help="sentiment pack directory for H3")
    ap.add_argument("--forward-days", type=int, default=5,
                    help="H3 forward return horizon (trading days)")
    ap.add_argument("--json", action="store_true",
                    help="emit full summary JSON to stdout instead of the human table")
    args = ap.parse_args(argv)

    tickers = [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    _valid = {"pricing", "greeks", "signals"}
    harnesses = [h.strip() for h in args.harness.split(",") if h.strip()]
    if "all" in harnesses:
        harnesses = list(_valid)
    harnesses = [h for h in harnesses if h in _valid]
    if not harnesses:
        ap.error("--harness must include one of pricing,greeks,signals,all")

    summary = run_tournament(
        tickers=tickers,
        harnesses=harnesses,
        expiry=args.expiry,
        lookback_days=args.lookback_days,
        out_dir=args.out,
        packs_dir=args.packs_dir,
        forward_days=args.forward_days,
    )
    report_lines = summary.get("report", [])
    if args.json:
        _summary = {k: v for k, v in summary.items() if k != "report"}
        print(json.dumps(_summary, indent=2, default=str))
    else:
        print(f"Backtest Tournament complete -> {summary.get('artifacts', {}).get('txt', '?')}")
        print(f"  json: {summary.get('artifacts', {}).get('json', '?')}")
        for ln in report_lines:
            print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
