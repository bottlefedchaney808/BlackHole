#!/usr/bin/env python3
"""Sentiment Scanner --- Contested Narrative Detector + 6 Options Scanners + Vol/Correlation.

Extended from the original StockTwits + CNS + deep-dive loop to run a full
suite of options scanners on every trending ticker: GEX, Unusual OI, IV Rank,
Skew, Max Pain, and Vol Dispersion — all piped into the correlation engine
for composite signals.
"""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from scanner.stocktwits import StockTwitsScraper
from scanner.narrative import score_messages
from scanner.swap_sdr import build_swap_snapshot
from scanner.reddit import MCP_AVAILABLE
from scanner.theta_integration import build_oi_snapshot
from scanner.ticker_pack import export_alert_group
from scanner.options_scanner_base import close_td
from correlation.engine import CorrelationEngine
from scanner.youtube import scan_ticker as yt_scan_ticker
from scanner.youtube import format_scanner_line as yt_format
import config


def _find_vol_suite_python(vol_suite_dir: Path) -> str:
    """Locate Vol_Suite's own venv interpreter, cross-platform.

    Vol_Suite has its own venv (each suite's deps -- arch, statsmodels, etc.
    -- aren't necessarily installed in sentiment-scanner's own venv), unlike
    Ubuntu's single shared Financial_Dev_Env root venv. Windows venvs place
    the interpreter at `.venv/Scripts/python.exe`; POSIX venvs (Linux/Mac)
    place it at `.venv/bin/python`.
    """
    venv_dir = vol_suite_dir / ".venv"
    candidates = [
        venv_dir / "Scripts" / "python.exe",  # Windows
        venv_dir / "bin" / "python3",         # Linux / Mac
        venv_dir / "bin" / "python",          # Linux / Mac fallback
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    default = venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python"
    return str(default)


def _launch_vol_suite(pack_path: str) -> None:
    vol_suite_dir = Path(__file__).resolve().parent / ".." / "Vol_Suite"
    vol_suite_py = str(vol_suite_dir / "volatility_suite.py")
    venv_python = _find_vol_suite_python(vol_suite_dir)
    cmd = [venv_python, vol_suite_py, "--pack", pack_path]
    print(f"\n{'='*60}")
    print(f"  Launching Volatility Suite on pack...")
    print(f"  {' '.join(cmd)}")
    print(f"{'='*60}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        print(proc.stdout)
        if proc.returncode != 0:
            print(f"  Vol Suite stderr: {proc.stderr[-2000:]}")
    except subprocess.TimeoutExpired:
        print("  Vol Suite timed out after 30 min.")
    except FileNotFoundError as e:
        print(f"  Could not launch Vol Suite: {e}")


def _make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_context_export(path: str, run_id: str, pack: dict) -> None:
    payload = {
        "schema_version": 2,
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sentiment": {
            "manifest_path": pack.get("manifest_path", ""),
            "pack_json_path": pack.get("pack_json_path") or pack.get("json_path", ""),
            "group_id": pack.get("group_id", ""),
            "ranked_tickers": pack.get("ranked_tickers", []),
        },
    }
    output_path = os.path.abspath(path)
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    from shared.schemas import validate_sentiment_context
    validate_sentiment_context(payload)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    print(f"  Context export: {output_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sentiment scanner + 6 options scanners + correlation."
    )
    parser.add_argument(
        "--export-context",
        dest="export_context_path",
        help="Write suite_context-compatible sentiment metadata JSON to this path.",
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Run one scan pass and exit.",
    )
    parser.add_argument(
        "--benchmark",
        default="SPY",
        help="Benchmark ticker for vol dispersion scan (default SPY).",
    )
    parser.add_argument(
        "--skip-gex",
        action="store_true",
        help="Skip GEX scan (more expensive — pulls many expiries).",
    )
    parser.add_argument(
        "--skip-youtube",
        action="store_true",
        help="Skip YouTube transcript sentiment scan.",
    )
    parser.add_argument(
        "--launch-vol-suite",
        action="store_true",
        help="After scan, launch Volatility Suite on the highlight pack.",
    )
    return parser.parse_args()


# ---- Lazy imports for the 6 options scanners ----
def _import_scanners():
    from scanner.gex_scanner import scan_gex, format_gex
    from scanner.unusual_oi_scanner import scan_unusual_oi, format_unusual_oi
    from scanner.iv_rank_scanner import scan_iv_rank, format_iv_rank
    from scanner.skew_scanner import scan_skew, format_skew
    from scanner.max_pain_scanner import scan_max_pain, format_max_pain
    from scanner.vol_dispersion_scanner import scan_vol_dispersion, format_dispersion
    return (scan_gex, format_gex, scan_unusual_oi, format_unusual_oi,
            scan_iv_rank, format_iv_rank, scan_skew, format_skew,
            scan_max_pain, format_max_pain,
            scan_vol_dispersion, format_dispersion)


# ---- Core scan functions ----
def scan_ticker(st, ticker, engine):
    messages = st.get_ticker_stream(ticker, max_pages=2)
    if not messages:
        return None
    scores = score_messages(messages)
    engine.record_narrative(ticker, scores)
    cns = scores["contested_narrative_score"]
    print(f"  {ticker:6s} | CNS: {cns:3d} | War: {scores['war_score']:.2f} | "
          f"Vol: {scores['volume']:3d} | Thesis: {scores['thesis_ratio']:.0%}")
    if cns >= config.CNS_THRESHOLD:
        return {**scores, "ticker": ticker, "timestamp": datetime.now().isoformat()}
    return None


def run_options_scanners(ticker, engine, benchmark="SPY", skip_gex=False):
    """Run all 6 options scanners on a ticker and pipe results into the engine."""
    (scan_gex, fmt_gex, scan_oi, fmt_oi,
     scan_iv, fmt_iv, scan_skew, fmt_skew,
     scan_pain, fmt_pain,
     scan_disp, fmt_disp) = _import_scanners()

    results = []

    # 1. GEX (most expensive — skip if flagged)
    if not skip_gex:
        try:
            gex = scan_gex(ticker)
            engine.record_gex(ticker, gex)
            results.append(fmt_gex(gex))
        except Exception as e:
            results.append(f"  {ticker:6s} | GEX: ERROR — {e}")

    # 2. Unusual OI
    try:
        oi = scan_oi(ticker)
        engine.record_oi(ticker, oi)
        results.append(fmt_oi(oi))
    except Exception as e:
        results.append(f"  {ticker:6s} | OI: ERROR — {e}")

    # 3. IV Rank
    try:
        iv = scan_iv(ticker)
        engine.record_iv(ticker, iv)
        results.append(fmt_iv(iv))
    except Exception as e:
        results.append(f"  {ticker:6s} | IV: ERROR — {e}")

    # 4. Skew
    try:
        skew = scan_skew(ticker)
        engine.record_skew(ticker, skew)
        results.append(fmt_skew(skew))
    except Exception as e:
        results.append(f"  {ticker:6s} | SKEW: ERROR — {e}")

    # 5. Max Pain
    try:
        pain = scan_pain(ticker)
        engine.record_pain(ticker, pain)
        results.append(fmt_pain(pain))
    except Exception as e:
        results.append(f"  {ticker:6s} | PAIN: ERROR — {e}")

    # 6. Vol Dispersion
    try:
        disp = scan_disp(ticker, benchmark=benchmark)
        engine.record_dispersion(ticker, disp)
        results.append(fmt_disp(disp))
    except Exception as e:
        results.append(f"  {ticker:6s} | DISP: ERROR — {e}")

    return results


def run_youtube_scanner(ticker, engine):
    try:
        yt = yt_scan_ticker(ticker)
        if yt:
            engine.record_narrative(ticker, yt)
            return yt_format(yt)
    except Exception:
        pass
    return None


def scan_trending(st, engine, benchmark="SPY", skip_gex=False, skip_youtube=False):
    trending = st.get_trending()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Trending: {len(trending)} symbols")
    alerts = []
    for t in trending[:config.MAX_TICKERS_TO_SCAN]:
        ticker = t["symbol"]
        result = scan_ticker(st, ticker, engine)
        if result:
            alerts.append(result)
        # Run options scanners on EVERY trending ticker, not just above-threshold
        scanner_lines = run_options_scanners(ticker, engine, benchmark, skip_gex)
        for line in scanner_lines:
            print(line)
        if not skip_youtube:
            yt_line = run_youtube_scanner(ticker, engine)
            if yt_line:
                print(yt_line)
        time.sleep(0.5)  # brief pause between tickers
    return alerts


def deep_dive(ticker):
    print(f"\n{'='*60}")
    print(f"DEEP DIVE: {ticker}")
    print(f"{'='*60}")
    print(f"\n[Options Chain]")
    oi = build_oi_snapshot(ticker)
    if "error" not in oi:
        print(f"  Spot: ${oi['spot']:.2f}")
        print(f"  ATM IV: {oi['atm_iv']*100:.2f}%")
        print(f"  IV Skew: {oi.get('skew_vol_pts', 0):.2f} vol pts")
        print(f"  Strikes: {oi['num_strikes']}")
    else:
        print(f"  Error: {oi.get('error')}")

    print(f"\n[Swap Data]")
    swap = build_swap_snapshot(ticker, lookback_days=config.SWAP_LOOKBACK_DAYS)
    print(f"  Swaps: {swap['swap_activity']} | Notional: ${swap['total_notional_usd']:,.0f}")

    if MCP_AVAILABLE:
        from scanner.reddit import RedditScraper
        rs = RedditScraper()
        reddit_posts = rs.get_hot_posts("wallstreetbets", limit=10)
        print(f"  Reddit WSB hot posts: {len(reddit_posts)}")
        rs.close()

    return {"oi": oi, "swap": swap}


def print_correlation_summary(engine, tickers):
    """Print composite signals for all tickers that had scanners run."""
    print(f"\n{'='*60}")
    print("COMPOSITE SIGNALS")
    print(f"{'='*60}")
    for ticker in sorted(set(tickers)):
        summary = engine.get_scanner_summary(ticker)
        signals = engine.correlate_with_oi(ticker, {})
        sig_list = signals.get("signals", [])
        severity = signals.get("severity", "LOW")

        if sig_list:
            print(f"  {ticker:6s} [{severity}] " + " | ".join(sig_list))
        else:
            # Even without signals, show a one-liner of scanner health
            statuses = []
            for name, data in summary.items():
                s = data.get("status", "?")
                if s == "ok":
                    statuses.append(f"{name}:✓")
                elif s.startswith("error"):
                    statuses.append(f"{name}:✗")
            print(f"  {ticker:6s} [——] " + " ".join(statuses))


def main():
    args = _parse_args()
    print("="*60)
    print("CONTESTED NARRATIVE SCANNER + OPTIONS SUITE v0.2")
    print("="*60)
    print("Sources: StockTwits | ThetaData (options) | CME SDR (swaps) | YouTube")
    print(f"Options Scanners: GEX | Unusual OI | IV Rank | Skew | Max Pain | Vol Dispersion")
    print("="*60)

    st = StockTwitsScraper()
    engine = CorrelationEngine()
    try:
        run_id = _make_run_id()
        alerts = scan_trending(st, engine, args.benchmark, args.skip_gex, args.skip_youtube)
        pack = {}

        if alerts:
            pack = export_alert_group(
                alerts,
                group_name="cns-threshold-alerts",
                source_run_id=run_id,
                social_sources=["stocktwits"],
            )
            print(f"\n🚨 ALERTS: {len(alerts)} tickers above CNS threshold")
            if pack:
                print(f"  Highlight pack: {pack['json_path']}")
            for a in alerts:
                print(f"  {a['ticker']} — CNS: {a['contested_narrative_score']} | "
                      f"War: {a['war_score']:.2f} | Bull: {a['bullish_pct']:.0f}% Bear: {a['bearish_pct']:.0f}%")
            for a in alerts[:3]:
                deep_dive(a["ticker"])

        # Print correlation composite signals for ALL scanned tickers
        scanned_tickers = [t["symbol"] for t in st.get_trending()[:config.MAX_TICKERS_TO_SCAN]]
        print_correlation_summary(engine, scanned_tickers)

        if args.export_context_path:
            _write_context_export(args.export_context_path, run_id, pack)

        if args.launch_vol_suite and pack and pack.get("json_path"):
            _launch_vol_suite(pack["json_path"])
        elif args.launch_vol_suite:
            print("  No highlight pack — skipping Vol Suite launch.")

        if args.no_loop:
            return

        while True:
            print(f"\n--- Next scan in {config.SCAN_INTERVAL_MINUTES} min ---")
            time.sleep(config.SCAN_INTERVAL_MINUTES * 60)
            run_id = _make_run_id()
            alerts = scan_trending(st, engine, args.benchmark, args.skip_gex, args.skip_youtube)
            pack = {}
            if alerts:
                pack = export_alert_group(
                    alerts,
                    group_name="cns-threshold-alerts",
                    source_run_id=run_id,
                    social_sources=["stocktwits"],
                )
                if pack:
                    print(f"  Highlight pack: {pack['json_path']}")
                for a in alerts[:3]:
                    deep_dive(a["ticker"])
            if args.export_context_path:
                _write_context_export(args.export_context_path, run_id, pack)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        st.close()
        close_td()


if __name__ == "__main__":
    main()