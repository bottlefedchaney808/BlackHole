#!/usr/bin/env python3
"""Sentiment Scanner --- Contested Narrative Detector + 7 Options Scanners + Vol/Correlation.

Extended from the original StockTwits + CNS + deep-dive loop to run a full
suite of options scanners on every trending ticker: GEX, Unusual OI, IV Rank,
Skew, Max Pain, Vol Dispersion, and Earnings-Vol Premium — all piped into the
correlation engine for composite signals.
"""

import sys
from pathlib import Path

# Add root directory to path so `shared` module can be imported from anywhere
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import argparse
import dataclasses
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import scanner.youtube
from correlation.engine import CorrelationEngine
from scanner.narrative import score_messages
from scanner.options_scanner_base import close_td, get_td
from scanner.reddit import MCP_AVAILABLE, RedditScraper
from scanner.stocktwits import StockTwitsScraper
from scanner.swap_sdr import build_swap_snapshot
from scanner.theta_integration import build_oi_snapshot
from scanner.ticker_pack import export_alert_group
from scanner.youtube import format_scanner_line as yt_format
from scanner.youtube import scan_ticker as yt_scan_ticker

_reddit_scan = RedditScraper
_youtube_scan = scanner.youtube.scan_ticker
import config
from scanner.earnings_calendar import (
    fetch_earnings_calendar,
    format_earnings_digest,
    upcoming_earnings,
)
from scanner.earnings_scanner import EARNINGS_CALENDAR
from scanner.earnings_scanner import format_earnings_one as format_earnings_line
from scanner.earnings_scanner import scan_ticker as _scan_earnings_ticker
from scanner.report import ScannerReport


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
        venv_dir / "bin" / "python3",  # Linux / Mac
        venv_dir / "bin" / "python",  # Linux / Mac fallback
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    default = (
        venv_dir / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv_dir / "bin" / "python"
    )
    return str(default)


def _launch_vol_suite(pack_path: str) -> None:
    vol_suite_dir = Path(__file__).resolve().parent / ".." / "Vol_Suite"
    vol_suite_py = str(vol_suite_dir / "volatility_suite.py")
    venv_python = _find_vol_suite_python(vol_suite_dir)
    cmd = [venv_python, vol_suite_py, "--pack", pack_path]
    print(f"\n{'=' * 60}")
    print("  Launching Volatility Suite on pack...")
    print(f"  {' '.join(cmd)}")
    print(f"{'=' * 60}")
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
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _prompt_yes_no(question: str, skip: bool = False) -> bool:
    """Ask *question* as a y/N prompt.

    Returns False without prompting if *skip* is set or stdin isn't a
    TTY, so scheduled/cron/CI runs of main.py never hang on input().
    """
    if skip or not sys.stdin.isatty():
        return False
    answer = input(f"{question} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _launch_sector_rotation() -> None:
    """Launch sector_rotation_launcher.py as a foreground subprocess.

    Mirrors _launch_vol_suite's timeout/capture pattern (main.py:62-79) --
    a 15-ETF price-history fetch can be slow or hang if ThetaData is
    unreachable, so this must not block main.py indefinitely.
    """
    launcher_path = Path(__file__).resolve().parent / "sector_rotation_launcher.py"
    cmd = [sys.executable, str(launcher_path)]
    print(f"\n{'=' * 60}")
    print("  Launching Sector Rotation scanner...")
    print(f"{'=' * 60}")
    try:
        # encoding/errors match sector_rotation_launcher.py's stdout.reconfigure
        # to utf-8 (see sector_rotation_launcher.main) so captured output
        # decodes cleanly instead of mojibake-ing through the default locale
        # encoding (cp1252 on Windows).
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,
            encoding="utf-8",
            errors="replace",
        )
        print(proc.stdout)
        if proc.returncode != 0:
            print(f"  Sector Rotation stderr: {proc.stderr[-2000:]}")
    except subprocess.TimeoutExpired:
        print("  Sector Rotation scanner timed out after 30 min.")
    except FileNotFoundError as e:
        print(f"  Could not launch Sector Rotation scanner: {e}")


def _raw_result_to_dict(name: str, result: object) -> dict:
    """Convert a scanner result object (or None) into a plain dict for
    ScannerReport.add_ticker_results(). Report cards read fields via
    dict.get(), so dataclass instances are converted with dataclasses.asdict(),
    which recurses through nested dataclasses and lists of them (e.g.
    UnusualOiScan.top_strikes: List[OiStrike] — see CARL R1-F1). A plain
    vars()/dict() shallow copy would leave nested dataclass fields as
    objects instead of dicts, breaking report.py's .get(...) calls on them.
    """
    if result is None:
        return {"error": "no_data"}
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.asdict(result)
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    return dict(result)


def _maybe_build_report(engine, cycle_raw: dict, skip: bool = False) -> None:
    """Prompt to build a PDF report for the most recently completed cycle.

    Called once at shutdown (CARL R1-F5 / user decision "option B"), not
    after every cycle — matching _launch_sector_rotation's cadence so a
    user who leaves an open terminal running never finds the loop
    blocked on an unattended input() mid-session.
    """
    if not cycle_raw:
        return
    if not _prompt_yes_no("Generate PDF report for the last completed run?", skip=skip):
        return

    tickers = sorted(cycle_raw.keys())
    report = ScannerReport(title="Sentiment Scanner Report", tickers=tickers)
    for ticker, raw in cycle_raw.items():
        for name, result in raw.items():
            report.add_ticker_results(ticker, name, _raw_result_to_dict(name, result))
        signals = engine.correlate_with_oi(ticker, {})
        report.add_signals(
            ticker, signals.get("signals", []), signals.get("severity", "LOW")
        )

    report.save(out_dir=config.OUTPUT_DIR)


def _write_context_export(path: str, run_id: str, pack: dict) -> None:
    payload = {
        "schema_version": 2,
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
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
        description="Sentiment scanner + 7 options scanners + correlation."
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
        "--skip-reddit",
        action="store_true",
        help="Skip Reddit Atom-feed sentiment scan.",
    )
    parser.add_argument(
        "--skip-sector-prompt",
        action="store_true",
        help="Don't prompt to launch the Sector Rotation scanner at startup/shutdown.",
    )
    parser.add_argument(
        "--skip-report-prompt",
        action="store_true",
        help="Don't prompt to generate a PDF report at shutdown.",
    )
    parser.add_argument(
        "--launch-vol-suite",
        action="store_true",
        help="After scan, launch Volatility Suite on the highlight pack.",
    )
    parser.add_argument(
        "--universe",
        default=None,
        help="Comma-separated ticker list (e.g. SPY,QQQ,NVDA). Runs one directional "
        "scan pass (narrative + 6 options scanners [no GEX] + real OI snapshot "
        "+ composite signals) over exactly this list instead of StockTwits's "
        "trending symbols, writes results to outputs/directional_scan_*.json, "
        "and exits -- no looping, no sector-rotation/PDF prompts.",
    )
    return parser.parse_args()


# ---- Lazy imports for the 6 options scanners ----
def _import_scanners():
    from scanner.gex_scanner import format_gex, scan_gex
    from scanner.iv_rank_scanner import format_iv_rank, scan_iv_rank
    from scanner.max_pain_scanner import format_max_pain, scan_max_pain
    from scanner.skew_scanner import format_skew, scan_skew
    from scanner.unusual_oi_scanner import format_unusual_oi, scan_unusual_oi
    from scanner.vol_dispersion_scanner import format_dispersion, scan_vol_dispersion

    return (
        scan_gex,
        format_gex,
        scan_unusual_oi,
        format_unusual_oi,
        scan_iv_rank,
        format_iv_rank,
        scan_skew,
        format_skew,
        scan_max_pain,
        format_max_pain,
        scan_vol_dispersion,
        format_dispersion,
    )


# ---- Core scan functions ----
def scan_ticker(st, ticker, engine):
    messages = st.get_ticker_stream(ticker, max_pages=2)
    if not messages:
        return None
    scores = score_messages(messages)
    engine.record_narrative(ticker, scores)
    cns = scores["contested_narrative_score"]
    print(
        f"  {ticker:6s} | CNS: {cns:3d} | War: {scores['war_score']:.2f} | "
        f"Vol: {scores['volume']:3d} | Thesis: {scores['thesis_ratio']:.0%}"
    )
    if cns >= config.CNS_THRESHOLD:
        return {**scores, "ticker": ticker, "timestamp": datetime.now().isoformat()}
    return None


def run_options_scanners(ticker, engine, benchmark="SPY", skip_gex=False, scanners=None):
    """Run selected options scanners via registry (Phase 4).

    scanners: list of slugs or None (all except gex if skip_gex).
    Keeps engine.record_* and raw dict for compat.
    --skip-gex kept as deprecated alias.
    """
    import importlib.util
    from pathlib import Path

    # Load THIS suite's registry by absolute path -- scanner.options_scanner_base
    # pushes Vol_Suite onto sys.path[0], so a bare `import module_registry` would
    # resolve to Vol_Suite/module_registry.py instead of the sentiment-scanner one.
    _mreg_path = Path(__file__).resolve().parent / "module_registry.py"
    _spec = importlib.util.spec_from_file_location("ss_module_registry", _mreg_path)
    _mreg = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mreg)
    resolve_modules = _mreg.resolve_modules

    if scanners is None:
        all_slugs = ["gex", "unusual_oi", "iv_rank", "skew", "max_pain", "vol_dispersion", "earnings"]
        if skip_gex:
            scanners = [s for s in all_slugs if s != "gex"]
        else:
            scanners = all_slugs

    selected = resolve_modules(scanners)

    results = []
    raw = {s: None for s in ["gex", "unusual_oi", "iv_rank", "skew", "max_pain", "dispersion", "earnings"]}

    slug_to_record = {
        "gex": ("record_gex", None),
        "unusual_oi": ("record_oi", None),
        "iv_rank": ("record_iv", None),
        "skew": ("record_skew", None),
        "max_pain": ("record_pain", None),
        "vol_dispersion": ("record_dispersion", None),
        "earnings": ("record_earnings", None),
    }

    for spec in selected:
        slug = spec.slug
        try:
            res = spec.run({"ticker": ticker, "benchmark": benchmark})
            raw_key = slug if slug != "vol_dispersion" else "dispersion"
            raw[raw_key] = res.context_patch.get(slug + "_result") or res
            # call engine if possible
            rec_name, _ = slug_to_record.get(slug, (None, None))
            if rec_name and hasattr(engine, rec_name):
                val = raw[raw_key]
                getattr(engine, rec_name)(ticker, val)
            # format not wired here (legacy fns still used in other paths); results list simplified
            results.append(f"  {ticker:6s} | {slug}: ok via registry")
        except Exception as e:
            results.append(f"  {ticker:6s} | {slug}: ERROR — {e}")

    return results, raw


def run_youtube_scanner(ticker, engine):
    try:
        yt = yt_scan_ticker(ticker)
        if yt:
            engine.record_narrative(ticker, yt)
            return yt_format(yt)
    except Exception:
        pass
    return None


def run_reddit_scanner(ticker, engine):
    """Run Reddit Arctic Shift scanner for a ticker.

    Uses Arctic Shift API to search posts mentioning $TICKER and get recent comments.
    Returns (formatted_line, raw_result) tuple or (None, None) if no data or error.
    """
    try:
        rs = _reddit_scan()

        # Search posts for this ticker in wallstreetbets
        ticker_posts = rs.search_posts_arctic("wallstreetbets", ticker, limit=25)

        # Get recent comments and filter for ticker mentions
        all_comments = rs.get_recent_comments("wallstreetbets", limit=50)
        ticker_comments = [c for c in all_comments if ticker.lower() in c.get("body", "").lower()]

        rs.close()

        # Combine posts and comments for analysis
        all_ticker_mentions = ticker_posts + ticker_comments

        if all_ticker_mentions:
            # Calculate sentiment using score-weighted approach
            bullish_keywords = ["buy", "long", "call", "moon", "rocket", "hodl", "bull"]
            bearish_keywords = ["sell", "short", "put", "bear", "drop", "crash", "bearish"]

            # Score-weighted counting: posts use their score, comments use their score
            bullish_score = 0.0
            bearish_score = 0.0
            post_count = len(ticker_posts)
            comment_count = len(ticker_comments)

            for p in ticker_posts:
                score = p.get("score", 0) or 0
                text = (p.get("title", "") or "") + " " + (p.get("selftext", "") or "")
                is_bullish = any(kw in text.lower() for kw in bullish_keywords)
                is_bearish = any(kw in text.lower() for kw in bearish_keywords)
                if is_bullish and not is_bearish:
                    bullish_score += score
                elif is_bearish and not is_bullish:
                    bearish_score += score

            for c in ticker_comments:
                score = c.get("score", 0) or 0
                body = c.get("body", "") or ""
                is_bullish = any(kw in body.lower() for kw in bullish_keywords)
                is_bearish = any(kw in body.lower() for kw in bearish_keywords)
                if is_bullish and not is_bearish:
                    bullish_score += score
                elif is_bearish and not is_bullish:
                    bearish_score += score

            total_score = abs(bullish_score) + abs(bearish_score)
            if total_score > 0:
                bullish_pct = bullish_score / total_score if bullish_score >= 0 else 0
                bearish_pct = bearish_score / total_score if bearish_score >= 0 else 0
            else:
                bullish_pct = 0.5
                bearish_pct = 0.5

            # Ensure percentages are in valid range
            bullish_pct = max(0.0, min(1.0, bullish_pct))
            bearish_pct = max(0.0, min(1.0, bearish_pct))

            result = {
                "post_count": post_count,
                "comment_count": comment_count,
                "bullish_score": round(bullish_score, 2),
                "bearish_score": round(bearish_score, 2),
                "bullish_pct": round(bullish_pct, 3),
                "bearish_pct": round(bearish_pct, 3),
                "total_mentions": post_count + comment_count,
                "ticker": ticker,
            }

            # Record to engine with Reddit sentiment data
            engine.record_narrative(ticker, {
                "narrative_score": 0,
                "bullish_pct": round(bullish_pct * 100, 1),
                "bearish_pct": round(bearish_pct * 100, 1),
                "volume": post_count + comment_count,
                "contested_narrative_score": 0,
                "war_score": 0,
                "thesis_ratio": 0.5,
            })

            return format_reddit_line(result), result
    except Exception:
        pass
    return None, None


def format_reddit_line(result):
    """Format Reddit scanner result as a line for console output (mirrors stocktwits style)."""
    if not result:
        return None
    return (
        f"  {result['ticker']:6s} | Reddit: {result.get('post_count', 0):<3d} posts / {result.get('comment_count', 0):<3d} comments | "
        f"Bull: {result.get('bullish_pct', 0):.0%} | Bear: {result.get('bearish_pct', 0):.0%}"
    )



def scan_trending(st, engine, benchmark="SPY", skip_gex=False, skip_youtube=False, skip_reddit=False):
    """Scan all trending tickers, running sentiment + options scanners on each.

    Returns (alerts, cycle_raw) where alerts are CNS-threshold alert dicts
    and cycle_raw maps ticker -> that ticker's raw scanner-result dict from
    run_options_scanners, for downstream reporting.
    """
    trending = st.get_trending()
    print(
        f"\n[{datetime.now().strftime('%H:%M:%S')}] Trending: {len(trending)} symbols"
    )
    entries = upcoming_earnings(days=7, static_fallback=EARNINGS_CALENDAR)
    live = bool(fetch_earnings_calendar())
    print(format_earnings_digest(entries, days=7, live=live))
    alerts = []
    cycle_raw = {}
    for t in trending[: config.MAX_TICKERS_TO_SCAN]:
        ticker = t["symbol"]
        result = scan_ticker(st, ticker, engine)
        if result:
            alerts.append(result)
        # Run options scanners on EVERY trending ticker, not just above-threshold
        scanner_lines, scanner_raw = run_options_scanners(
            ticker, engine, benchmark, skip_gex
        )
        for line in scanner_lines:
            print(line)
        cycle_raw[ticker] = scanner_raw
        if not skip_youtube:
            try:
                yt_result = _youtube_scan(ticker)
            except Exception as e:
                print(f"  {ticker:6s} | YT: ERROR — {e}")
                yt_result = None
            if yt_result:
                scanner_raw["youtube"] = yt_result
                yt_line = yt_format(yt_result)
                if yt_line:
                    print(yt_line)
        if not skip_reddit:
            try:
                reddit_line, reddit_result = run_reddit_scanner(ticker, engine)
            except Exception as e:
                print(f"  {ticker:6s} | Reddit: ERROR — {e}")
                reddit_result = None
            if reddit_result:
                scanner_raw["reddit"] = reddit_result
                print(reddit_line)
        time.sleep(0.5)  # brief pause between tickers
    return alerts, cycle_raw


# Signal names that, on their own, indicate a narrative-corroborated setup --
# used by run_directional_scan's strong-signal gate below.
_HIGH_CONVICTION_SIGNALS = (
    "DIRECTIONAL_BET_FORMING",
    "VOL_EVENT_DETECTED",
    "GAMMA_SQUEEZE_RISK",
    "OI_SURGE_WITH_NARRATIVE",
    "RICH_VOL_PLUS_NARRATIVE",
    "EXTREME_SKEW_PLUS_NARRATIVE",
    "PIN_ACTION_WITH_NARRATIVE",
    "FAR_FROM_PAIN_PLUS_NARRATIVE",
    "DISPERSION_SETUP_PLUS_NARRATIVE",
    "EARNINGS_VOL_PLUS_NARRATIVE",
)


def run_directional_scan(tickers, engine, benchmark="SPY"):
    """Run the narrative + 6-scanner (GEX skipped -- expensive) + real-OI +
    composite-signal pipeline over an explicit ticker universe, and write a
    JSON results file. One-shot, not looped -- for "screen exactly this
    watchlist" runs rather than StockTwits's trending-symbol discovery.

    Returns (results, out_path): results maps ticker -> its result dict,
    out_path is the JSON file written to outputs/.
    """
    st = StockTwitsScraper()
    results = {}
    start = time.time()
    try:
        for i, ticker in enumerate(tickers):
            t0 = time.time()
            row = {
                "ticker": ticker,
                "narrative": None,
                "scanners": {},
                "oi": {},
                "signals": [],
                "severity": "LOW",
                "errors": [],
            }

            # 1. Narrative (CNS / war) -- feeds the signal rules
            try:
                msgs = st.get_ticker_stream(ticker, max_pages=2)
                if msgs:
                    scores = score_messages(msgs)
                    engine.record_narrative(ticker, scores)
                    row["narrative"] = {
                        "cns": scores.get("contested_narrative_score"),
                        "war": round(scores.get("war_score", 0), 3),
                        "bullish_pct": scores.get("bullish_pct"),
                        "bearish_pct": scores.get("bearish_pct"),
                        "volume": scores.get("volume"),
                    }
                else:
                    row["errors"].append("no_narrative_messages")
            except Exception as e:
                row["errors"].append(f"narrative:{e}")

            # 2. Options scanners (GEX skipped -- expensive; 6 remain)
            try:
                _, raw = run_options_scanners(
                    ticker, engine, benchmark=benchmark, skip_gex=True
                )
                for name, r in raw.items():
                    if r is None:
                        continue
                    err = getattr(r, "error", None)
                    if err:
                        row["scanners"][name] = {"status": "error", "error": str(err)}
                    else:
                        d = {"status": "ok"}
                        for attr in (
                            "surge_detected",
                            "regime",
                            "skew_signal",
                            "near_pin",
                            "max_pain_strike",
                            "price_vs_pain_pct",
                            "dispersion_signal",
                            "premium_pct",
                            "atm_iv",
                            "iv_rank",
                            "total_oi",
                            "call_oi",
                            "put_oi",
                        ):
                            v = getattr(r, attr, None)
                            if v is not None and not callable(v):
                                d[attr] = round(v, 4) if isinstance(v, float) else v
                        row["scanners"][name] = d
            except Exception as e:
                row["errors"].append(f"scanners:{e}")

            # 3. Real OI snapshot (feeds the OI signal rules)
            try:
                oi = build_oi_snapshot(ticker)
                row["oi"] = oi if "error" not in oi else {"error": oi.get("error")}
            except Exception as e:
                row["oi"] = {"error": str(e)}

            # 4. Composite signals
            try:
                sig = engine.correlate_with_oi(ticker, row["oi"])
                row["signals"] = sig.get("signals", [])
                row["severity"] = sig.get("severity", "LOW")
            except Exception as e:
                row["errors"].append(f"correlate:{e}")

            # 5. Strong-signal gate for downstream unified runs
            n_high = sum(
                1
                for s in row["signals"]
                if s.endswith("_PLUS_NARRATIVE") or s in _HIGH_CONVICTION_SIGNALS
            )
            cns = (row["narrative"] or {}).get("cns") or 0
            row["strong"] = (
                (row["severity"] == "HIGH")
                or (len(row["signals"]) >= 2)
                or (n_high >= 1 and cns >= 40)
            )
            row["elapsed_s"] = round(time.time() - t0, 1)
            results[ticker] = row

            print(
                f"[{i + 1}/{len(tickers)}] {ticker:6s} | sev={row['severity']:6s} | "
                f"signals={len(row['signals']):2d} | CNS={cns:3d} | "
                f"strong={row['strong']} | {row['elapsed_s']}s",
                flush=True,
            )
            time.sleep(0.5)
    finally:
        st.close()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir, f"directional_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now().isoformat(),
                "universe_size": len(tickers),
                "results": results,
            },
            f,
            indent=2,
            default=str,
        )

    strong = [t for t, r in results.items() if r["strong"]]
    print(
        f"\nDONE in {time.time() - start:.0f}s -- {len(strong)} strong: {', '.join(strong)}"
    )
    print(f"OUTPUT {out_path}")
    return results, out_path


def deep_dive(ticker):
    print(f"\n{'=' * 60}")
    print(f"DEEP DIVE: {ticker}")
    print(f"{'=' * 60}")
    print("\n[Options Chain]")
    oi = build_oi_snapshot(ticker)
    if "error" not in oi:
        print(f"  Spot: ${oi['spot']:.2f}")
        print(f"  ATM IV: {oi['atm_iv'] * 100:.2f}%")
        print(f"  IV Skew: {oi.get('skew_vol_pts', 0):.2f} vol pts")
        print(f"  Strikes: {oi['num_strikes']}")
    else:
        print(f"  Error: {oi.get('error')}")

    print("\n[Swap Data]")
    swap = build_swap_snapshot(ticker, lookback_days=config.SWAP_LOOKBACK_DAYS)
    print(
        f"  Swaps: {swap['swap_activity']} | Notional: ${swap['total_notional_usd']:,.0f}"
    )

    if MCP_AVAILABLE:
        from scanner.reddit import RedditScraper

        rs = RedditScraper()
        reddit_posts = rs.get_hot_posts("wallstreetbets", limit=10)
        print(f"  Reddit WSB hot posts: {len(reddit_posts)}")
        rs.close()

    return {"oi": oi, "swap": swap}


def print_correlation_summary(engine, tickers):
    """Print composite signals for all tickers that had scanners run."""
    print(f"\n{'=' * 60}")
    print("COMPOSITE SIGNALS")
    print(f"{'=' * 60}")
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
    # Windows consoles default to cp1252, which can't encode many characters
    # this module and the scanner modules print (em-dashes, arrows, emoji,
    # box-drawing separators) -- see sector_rotation_launcher.py's
    # UnicodeEncodeError crash for the same root cause. Reconfigure stdout to
    # UTF-8 up front rather than patching every individual string.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = _parse_args()

    if args.universe:
        tickers = [t.strip().upper() for t in args.universe.split(",") if t.strip()]
        if not tickers:
            print("--universe was given but contained no tickers.")
            return
        engine = CorrelationEngine()
        try:
            run_directional_scan(tickers, engine, benchmark=args.benchmark)
        finally:
            close_td()
        return

    print("=" * 60)
    print("CONTESTED NARRATIVE SCANNER + OPTIONS SUITE v0.2")
    print("=" * 60)
    print("Sources: StockTwits | ThetaData (options) | CME SDR (swaps) | YouTube | Reddit")
    print(
        "Options Scanners: GEX | Unusual OI | IV Rank | Skew | Max Pain | Vol Dispersion | Earnings"
    )
    print("=" * 60)

    if _prompt_yes_no(
        "Launch Sector Rotation scanner now?",
        skip=args.skip_sector_prompt,
    ):
        _launch_sector_rotation()

    st = StockTwitsScraper()
    engine = CorrelationEngine()
    cycle_raw = {}
    try:
        run_id = _make_run_id()
        alerts, cycle_raw = scan_trending(
            st, engine, args.benchmark, args.skip_gex, args.skip_youtube, args.skip_reddit
        )
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
                print(
                    f"  {a['ticker']} — CNS: {a['contested_narrative_score']} | "
                    f"War: {a['war_score']:.2f} | Bull: {a['bullish_pct']:.0f}% Bear: {a['bearish_pct']:.0f}%"
                )
            for a in alerts[:3]:
                deep_dive(a["ticker"])

        # Print correlation composite signals for ALL scanned tickers
        scanned_tickers = [
            t["symbol"] for t in st.get_trending()[: config.MAX_TICKERS_TO_SCAN]
        ]
        print_correlation_summary(engine, scanned_tickers)

        if args.export_context_path:
            _write_context_export(args.export_context_path, run_id, pack)

        if args.launch_vol_suite and pack and pack.get("json_path"):
            _launch_vol_suite(pack["json_path"])
        elif args.launch_vol_suite:
            print("  No highlight pack — skipping Vol Suite launch.")

        if args.no_loop:
            if _prompt_yes_no(
                "Launch Sector Rotation scanner before exiting?",
                skip=args.skip_sector_prompt,
            ):
                _launch_sector_rotation()
            _maybe_build_report(engine, cycle_raw, skip=args.skip_report_prompt)
            return

        while True:
            print(f"\n--- Next scan in {config.SCAN_INTERVAL_MINUTES} min ---")
            time.sleep(config.SCAN_INTERVAL_MINUTES * 60)
            run_id = _make_run_id()
            alerts, cycle_raw = scan_trending(
                st, engine, args.benchmark, args.skip_gex, args.skip_youtube, args.skip_reddit
            )
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
        if _prompt_yes_no(
            "Launch Sector Rotation scanner before exiting?",
            skip=args.skip_sector_prompt,
        ):
            _launch_sector_rotation()
        _maybe_build_report(engine, cycle_raw, skip=args.skip_report_prompt)
    finally:
        st.close()
        close_td()


if __name__ == "__main__":
    main()
