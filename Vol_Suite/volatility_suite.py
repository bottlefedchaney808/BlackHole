#!/usr/bin/env python3
"""volatility_suite.py
Front-end CLI for the volatility suite -- focus-ticker-driven workflow.

Replaces the old "pick modules off a menu, run each on whatever ticker(s) you
type in separately" flow. Every module now shares a single context built once
at the top:

    focus ticker -> candidate index/sector (real membership, via
    index_membership.py) -> index-weighted constituent basket (real weights,
    via correlation_engine.build basket / index_membership) -> basket stats
    (correlation matrix, heatmap, dispersion score) -> variance-swap
    replication on ONLY the chosen index and the focus ticker (not the full
    basket -- full-constituent replication is deferred) -> an opportunities
    read comparing the two legs against the basket's dispersion/correlation
    picture -> GARCH, screener score, and dealer positioning, all scoped to
    the focus ticker.

ThetaData is the primary data source everywhere in this suite; yfinance is
used only as a per-ticker fallback when ThetaData is unavailable (see
thetadata_client.py and correlation_engine.fetch_price_history).

Two run modes share one analysis pipeline (_run_core_analysis, below):
run_focus_workflow (mode 1) runs it standalone and can compile a PDF;
run_unified_flow (mode 2) runs the SAME pipeline and additionally writes a
suite_context.json handoff and can launch Options_Suite / VaR_Tools_Simulations
as child processes. The two modes used to diverge -- mode 2 wrote a context
and, unless a child suite was explicitly requested, ran no analysis at all --
which is exactly why "Compile outputs into single PDF? y" in mode 2 used to
produce nothing: there were no sections to compile. See FIX_PLAN_20260725.md.
"""
import os
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from vs_utils import timestamped_output_dir, collect_files, compose_pdf_report
import index_membership as idxmem
from suite_context import (
    DEFAULT_OPTIONS_SUITE_ROOT,
    DEFAULT_SENTIMENT_SUITE_ROOT,
    DEFAULT_VAR_SUITE_ROOT,
    build_suite_context,
    write_suite_context,
)


def _ticker_exists(ticker: str) -> bool:
    """Cheap sanity check that a symbol is real before the suite spends 15
    index lookups and several data fetches on it. Returns True on any
    inconclusive result -- this should never block a valid run just because
    the check itself failed. Uses PotatoHedge/ThetaData (yahoo purged): a valid
    symbol returns a positive spot from the snapshot quote."""
    try:
        from thetadata_client import ThetaDataController
        td = ThetaDataController()
        try:
            return td.fetch_spot_price(ticker) > 0
        finally:
            td.close()
    except Exception:
        return True


def prompt_focus_ticker() -> str:
    """Prompt for the focus ticker, re-prompting on a symbol that doesn't
    resolve. A typo (e.g. NTFLX for NFLX) otherwise surfaces much later as
    'not found in any of the 15 tracked indices', which reads like a data-feed
    failure rather than a typo."""
    while True:
        t = input("Focus ticker (e.g. MSFT): ").strip().upper()
        if not t:
            return "MSFT"
        if _ticker_exists(t):
            return t
        print(f"  '{t}' doesn't resolve to a tradable symbol -- check the spelling.")
        retry = input("  Enter a different ticker, or press Enter to use it anyway: ").strip().upper()
        if not retry:
            return t
        if _ticker_exists(retry):
            return retry
        print(f"  '{retry}' doesn't resolve either -- continuing with it.")
        return retry


def _default_pack_manifest_path() -> str:
    root = Path(__file__).resolve().parent.parent
    return str(root / "sentiment-scanner" / "data" / "exports" / "highlighted_ticker_packs" / "latest_manifest.json")


def _load_json_file(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _load_ticker_pack_interactive() -> Optional[dict]:
    default_manifest = _default_pack_manifest_path()
    manifest_path = input(f"Ticker-pack manifest path [default: {default_manifest}]: ").strip() or default_manifest
    manifest = _load_json_file(manifest_path)
    if not manifest:
        print("  Could not load manifest file. Falling back to manual ticker entry.")
        return None
    packs = manifest.get("packs", [])
    if not packs:
        print("  Manifest has no packs yet. Falling back to manual ticker entry.")
        return None
    print("\nAvailable highlighted ticker packs:")
    for i, p in enumerate(packs[:15], start=1):
        gid = p.get("group_id", "unknown")
        gname = p.get("group_name", "group")
        count = p.get("ticker_count", 0)
        updated = p.get("last_updated_at") or p.get("created_at") or ""
        print(f"  {i:2d}. {gname} | {count} tickers | {gid} | {updated}")
    choice = input("Choose pack number (default 1): ").strip()
    idx = 0
    if choice.isdigit():
        n = int(choice) - 1
        if 0 <= n < min(15, len(packs)):
            idx = n
    selected = packs[idx]
    pack_path = selected.get("json_path", "")
    if not pack_path:
        print("  Pack entry missing json_path. Falling back to manual ticker entry.")
        return None
    pack = _load_json_file(pack_path)
    if not pack:
        print(f"  Could not load pack JSON at {pack_path}. Falling back to manual ticker entry.")
        return None
    tickers = pack.get("tickers", [])
    if not tickers:
        print("  Pack has no tickers. Falling back to manual ticker entry.")
        return None
    print(f"\nLoaded pack: {pack.get('group_id', 'unknown')} ({len(tickers)} tickers)")
    ranked = sorted(
        tickers,
        key=lambda t: (int(t.get("rank", 9999) or 9999), -float(t.get("cns", 0) or 0)),
    )
    for i, t in enumerate(ranked[:20], start=1):
        sym = str(t.get("symbol", "")).upper()
        cns = t.get("cns", 0)
        conf = t.get("confidence", 0.0)
        print(f"  {i:2d}. {sym:6s} | CNS={cns:>3} | conf={float(conf):.2f}")
    sel = input("Focus ticker from pack (number, default 1): ").strip()
    sel_idx = 0
    if sel.isdigit():
        v = int(sel) - 1
        if 0 <= v < len(ranked):
            sel_idx = v
    focus_ticker = str(ranked[sel_idx].get("symbol", "")).upper()
    if not focus_ticker:
        print("  Selected row has no symbol. Falling back to manual ticker entry.")
        return None
    return {
        "focus_ticker": focus_ticker,
        "pack": pack,
        "manifest_path": manifest_path,
    }


def prompt_index_choice(ticker: str) -> Tuple[str, List[dict]]:
    """Look up which tracked indices/sector ETFs the focus ticker belongs to
    and let the user choose one. Weight is the decision-support data point:
    a higher weight means the ticker is a more meaningful driver of that
    index's variance, which is what makes it a more relevant dispersion
    counterpart (vs. one where the ticker is a rounding error)."""
    print(f"\nLooking up index/sector membership for {ticker}...")
    matches = idxmem.find_indices_for_ticker(ticker)
    idxmem.print_index_choices(ticker, matches)
    if matches:
        default_idx = matches[0]["index"]
        choice = input(f"\nChoose an index by number, or type a ticker directly (default {default_idx}): ").strip()
        if not choice:
            return default_idx, matches
        if choice.isdigit():
            i = int(choice) - 1
            if 0 <= i < len(matches):
                return matches[i]["index"], matches
        return choice.upper(), matches
    print(f"  {ticker} wasn't found in any tracked index/sector ETF.")
    manual = input("Enter an index/sector ETF ticker manually (e.g. SPY, QQQ, XLK): ").strip().upper()
    return (manual or "SPY"), matches


# Dual-class listings: the same issuer trading under two tickers. Both classes
# sit in the index at their own weights, so a naive top-N basket can spend a
# third of its weight on one company while the correlation stats count them as
# two independent names -- inflating the diversification ratio and understating
# single-issuer concentration. Their return series are near-identical (GOOGL vs
# GOOG measured 0.9971 in testing), so the second class adds no information.
#
# This is an explicit list rather than a prefix heuristic on purpose: matching
# by shared prefix would collapse genuinely unrelated pairs (MA/MAA are
# Mastercard and Mid-America Apartment; CAT/CATY are Caterpillar and Cathay).
# Add to it as needed -- an unlisted pair degrades to current behaviour, it
# doesn't break anything.
SHARE_CLASS_GROUPS: List[set] = [
    {"GOOGL", "GOOG"},
    {"BRK.A", "BRK.B", "BRK-A", "BRK-B"},
    {"FOX", "FOXA"},
    {"NWS", "NWSA"},
    {"UA", "UAA"},
    {"LEN", "LEN.B"},
    {"HEI", "HEI.A"},
    {"CWEN", "CWEN.A"},
    {"BF.A", "BF.B"},
    {"MOG.A", "MOG.B"},
    {"CENT", "CENTA"},
    {"PARA", "PARAA"},
]


def _dedupe_share_classes(constituents: List[Tuple[str, float]],
                          protect: Optional[str] = None) -> List[Tuple[str, float]]:
    """Collapse dual-class listings to a single ticker per issuer.

    The surviving ticker carries the issuer's COMBINED index weight, since the
    economic exposure to that company really is the sum of both classes -- only
    the double-counting as two independent names is removed.

    `protect` (the focus ticker) always survives its group even if the other
    class carries more weight.
    """
    kept: List[Tuple[str, float]] = []
    handled: set = set()
    protect = (protect or "").upper()

    for sym, weight in constituents:
        if sym in handled:
            continue
        group = next((g for g in SHARE_CLASS_GROUPS if sym.upper() in g), None)
        if group is None:
            kept.append((sym, weight))
            continue

        siblings = [(s, w) for s, w in constituents if s.upper() in group]
        handled.update(s for s, _ in siblings)
        if len(siblings) < 2:
            kept.append((sym, weight))
            continue

        combined = sum(w for _, w in siblings)
        if protect and any(s.upper() == protect for s, _ in siblings):
            survivor = next(s for s, _ in siblings if s.upper() == protect)
        else:
            survivor = max(siblings, key=lambda x: x[1])[0]
        dropped = ", ".join(s for s, _ in siblings if s != survivor)
        print(f"  [dedupe] {survivor} and {dropped} are share classes of one issuer "
              f"-> keeping {survivor} at combined weight {combined:.2f}%")
        kept.append((survivor, combined))

    return kept


def _resolve_basket(ticker: str, chosen_index: str, top_n: int,
                    known_weight: Optional[float] = None) -> Tuple[List[str], List[float]]:
    """Build the index-weighted basket, making sure the focus ticker itself
    is always in it (even if its weight is too small to land in the default
    top_n cut) -- the whole point of the basket is to compare the focus
    ticker against its index and peers."""
    # Over-fetch, then dedupe, then cut to top_n -- so collapsing a dual-class
    # pair pulls the next real constituent in rather than leaving a short basket.
    raw = idxmem.get_index_constituents(chosen_index, top_n=max(top_n * 3, top_n + 10))
    if not raw:
        # Every holdings source failed. Say so plainly instead of falling
        # through to a one-name "basket" -- see the guard in main().
        print(f"  WARNING: could not retrieve any constituents for {chosen_index}.")
    constituents = _dedupe_share_classes(raw, protect=ticker)[:top_n]
    tickers = [sym for sym, _w in constituents]
    weights = [w for _sym, w in constituents]
    if ticker not in tickers:
        tw = known_weight
        if tw is None:
            full = idxmem.get_index_constituents(chosen_index, top_n=100000)
            tw = next((w for sym, w in full if sym == ticker), None)
        if tw is None:
            tw = min(weights) if weights else 1.0
        tickers = [ticker] + tickers
        weights = [tw] + weights
    return tickers, weights


def _resolve_ticker_universe(
    ticker: str, pack_ctx: Optional[dict]
) -> Tuple[List[str], bool, str, Optional[float], int, List[dict]]:
    """Decide what basket/benchmark universe this run uses.

    If a sentiment-scanner pack with 2+ tickers was loaded, that pack IS the
    basket -- the index-membership prompt is skipped entirely and a
    benchmark-only index (default SPY, override via VS_BENCHMARK_INDEX) is
    used just for the two-leg vol-spread/beta comparison, not for basket
    composition. Otherwise falls back to the interactive index-membership
    prompt to build an index-weighted basket.

    Shared by run_focus_workflow and run_unified_flow so the two entry
    points can't drift apart on this decision the way they used to. See
    FIX_PLAN_20260725.md, issue 1.

    Returns (group_tickers, use_pack_basket, chosen_index, known_weight, top_n, matches).
    """
    group_tickers = _collect_ranked_tickers_from_pack(pack_ctx)
    use_pack_basket = bool(pack_ctx) and len(group_tickers) >= 2

    if use_pack_basket:
        chosen_index = os.environ.get("VS_BENCHMARK_INDEX", "SPY")
        matches: List[dict] = []
        known_weight = None
        top_n = len(group_tickers)
        print(f"\nUsing the {len(group_tickers)}-ticker sentiment-scanner basket "
              f"directly (skipping the index-basket prompt). Benchmark index: "
              f"{chosen_index} (override with the VS_BENCHMARK_INDEX env var).")
    else:
        chosen_index, matches = prompt_index_choice(ticker)
        known_weight = next((m["weight"] for m in matches if m["index"] == chosen_index), None)
        n_input = input("Basket size — number of index constituents to pull (default 10): ").strip()
        top_n = int(n_input) if n_input else 10

    return group_tickers, use_pack_basket, chosen_index, known_weight, top_n, matches


def _build_basket(
    ticker: str, use_pack_basket: bool, group_tickers: List[str],
    chosen_index: str, top_n: int, known_weight: Optional[float],
) -> Tuple[List[str], List[float]]:
    """The (tickers, weights) basket used for correlation stats AND, in the
    unified flow, the suite_context handoff -- computed once so both are
    backed by the same numbers instead of two independent (and potentially
    inconsistent) resolutions."""
    if use_pack_basket:
        print(f"\n[1/5] Using the {len(group_tickers)}-ticker sentiment-scanner "
              f"basket for correlation (not an index-derived basket)...")
        return list(group_tickers), [1.0] * len(group_tickers)
    print(f"\n[1/5] Building basket from {chosen_index} constituents...")
    return _resolve_basket(ticker, chosen_index, top_n, known_weight=known_weight)


def _run_id_now() -> str:
    return datetime.utcnow().strftime("vsuite_%Y%m%d_%H%M%S")


def _choose_option_type() -> str:
    raw = (input("Option type for shared context (call/put, default call): ").strip().lower() or "call")
    return "put" if raw == "put" else "call"


def _choose_optional_strike() -> Optional[float]:
    raw = input("Optional strike for shared context (press Enter to keep null): ").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        print(f"  Invalid strike '{raw}', keeping strike=null.")
        return None


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    choice = input(f"{prompt} (y/n, default {'y' if default else 'n'}): ").strip().lower()
    if not choice:
        return default
    return choice == "y"


def _require_existing_dir(path_value: str, label: str) -> str:
    resolved = Path(path_value).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(f"{label} path does not exist or is not a directory: {resolved}")
    return str(resolved)


def _collect_ranked_tickers_from_pack(pack_ctx: Optional[dict]) -> List[str]:
    if not pack_ctx:
        return []
    rows = pack_ctx["pack"].get("tickers", [])
    ranked = sorted(
        rows,
        key=lambda t: (int(t.get("rank", 9999) or 9999), -float(t.get("cns", 0) or 0)),
    )
    out: List[str] = []
    for row in ranked:
        symbol = str((row or {}).get("symbol", "")).upper().strip()
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def _prompt_sign_model_and_options_chain(pack_ctx: Optional[dict]) -> Tuple[str, bool]:
    """The dealer-positioning sign-model choice and options-chain-scanner
    toggle -- asked identically by both run modes so mode 2 actually reaches
    v2 (vol_surface_replication) and the chain scanner instead of silently
    skipping them the way it used to. See FIX_PLAN_20260725.md, "mode two
    needs to run just like mode 1"."""
    sign_choice = input(
        "Dealer positioning sign model -- (1) OI heuristic, (2) Replication "
        "(Layer 1b), (3) Vol-Surface + Replication (Layer 1a+1b) [default 3]: "
    ).strip()
    sign_model = {'1': 'oi_heuristic', '2': 'replication'}.get(sign_choice, 'vol_surface_replication')
    options_hint = True
    if pack_ctx:
        options_hint = bool(pack_ctx["pack"].get("downstream_hints", {}).get("options_suite", False))
    options_default = "y" if options_hint else "n"
    run_options_chain = (input(f"Run Options Chain Scanner step? (y/n, default {options_default}): ").strip().lower() or options_default) == "y"
    return sign_model, run_options_chain


def _child_entrypoint_for_suite(suite_root: str, suite_name: str) -> str:
    root = Path(suite_root)
    candidates = {
        "options": [
            "options_suite.py",
            "volatility_suite.py",
            "main.py",
        ],
        "var": [
            "var_tools_simulations.py",
            "var_suite.py",
            "main.py",
        ],
    }[suite_name]
    for rel in candidates:
        p = root / rel
        if p.exists() and p.is_file():
            return str(p.resolve())
    raise FileNotFoundError(
        f"Could not find a launch script for {suite_name} suite in {root}. "
        f"Tried: {', '.join(candidates)}"
    )


# A child suite that blocks forever (waiting on stdin it will never get, or a
# network call with no timeout of its own) must not take the parent with it.
# 30 minutes is far longer than any child legitimately needs -- Options_Suite's
# LSM Monte Carlo is the slowest and runs in minutes -- so hitting this means
# something is genuinely stuck, not merely slow.
_CHILD_SUITE_TIMEOUT_SEC = int(os.environ.get("SUITE_CHILD_TIMEOUT_SEC", "1800"))


def _run_child_suite(
    *,
    suite_name: str,
    suite_root: str,
    context_path: str,
    output_dir: str,
) -> Dict[str, Any]:
    entrypoint = _child_entrypoint_for_suite(suite_root, suite_name)
    context_out = os.path.join(output_dir, f"{suite_name}_result.json")
    command = [
        sys.executable,
        entrypoint,
        "--context",
        context_path,
        "--context-out",
        context_out,
    ]
    env = os.environ.copy()
    env["SUITE_CONTEXT_PATH"] = context_path
    env["SUITE_CONTEXT_MODE"] = "1"
    env["VS_OUTPUT_DIR"] = output_dir

    print(f"  [{suite_name}] running {Path(entrypoint).name} "
          f"(timeout {_CHILD_SUITE_TIMEOUT_SEC}s)... output is captured, "
          f"so this will look idle until it finishes.")
    try:
        proc = subprocess.run(
            command,
            cwd=str(Path(suite_root).resolve()),
            env=env,
            capture_output=True,
            text=True,
            # stdin closed, not inherited: if a child ever falls back to an
            # interactive prompt it gets EOF and dies with an error we can
            # read, instead of silently blocking forever on a terminal whose
            # output we've captured and aren't showing.
            stdin=subprocess.DEVNULL,
            timeout=_CHILD_SUITE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "suite": suite_name,
            "command": " ".join(command),
            "returncode": -1,
            "stdout_tail": (e.stdout or b"").decode(errors="replace").splitlines()[-12:] and
                           "\n".join((e.stdout or b"").decode(errors="replace").splitlines()[-12:]) or "",
            "stderr_tail": f"TIMEOUT after {_CHILD_SUITE_TIMEOUT_SEC}s -- child killed.",
        }
    return {
        "suite": suite_name,
        "command": " ".join(command),
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-12:]) if proc.stdout else "",
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-12:]) if proc.stderr else "",
    }


def _run_core_analysis(
    *,
    ticker: str,
    pack_ctx: Optional[dict],
    group_tickers: List[str],
    use_pack_basket: bool,
    tickers: List[str],
    weights: List[float],
    chosen_index: str,
    target_years: float,
    expiration: str,
    sign_model: str,
    run_options_chain: bool,
    out_root: str,
) -> Tuple[List[str], List[dict]]:
    """Runs the full Vol_Suite analysis pipeline: group screener (if a pack
    basket is present), basket correlation/dispersion, variance-swap
    replication on the index + focus ticker, an opportunities read, GARCH,
    a screener recheck (skipped if redundant with the group screener),
    dealer positioning, and (optionally) the options chain scanner.

    Shared by run_focus_workflow and run_unified_flow so "running the suite"
    means exactly the same thing regardless of entry point -- the two modes
    differ only in what happens BEFORE this (how ticker/basket/expiry get
    chosen) and AFTER (whether a suite_context.json gets written and sibling
    suites get launched), never in what analysis actually runs. This is what
    used to be missing: mode 2 wrote a context and, unless a child suite was
    explicitly requested, ran none of this at all. See FIX_PLAN_20260725.md.

    Returns (produced_files, pdf_sections).
    """
    produced: List[str] = []
    sections: List[dict] = []
    group_screener_ran = False
    if pack_ctx:
        run_pack_screen = (input("Run variance screener on full highlighted group first? (y/n, default y): ").strip().lower() or "y") == "y"
        if run_pack_screen and group_tickers:
            print(f"\n[0/5] Running group screener on {len(group_tickers)} highlighted tickers...")
            try:
                import variance_swap_screener as vss
                files, interp = vss.run_variance_screener(group_tickers, target_years, output_dir=out_root)
                produced.extend(files)
                sections.append({
                    "title": f"Group Screener: {pack_ctx['pack'].get('group_id', 'highlighted-pack')}",
                    "text": interp or "",
                    "images": [f for f in files if f.lower().endswith('.png')]
                })
                group_screener_ran = True
            except Exception as e:
                print(f"  Group screener failed: {e}")

    # ---- Step 1: basket stats (tickers/weights already resolved by caller) ----
    import correlation_engine as ce
    print(f"  Basket ({len(tickers)}): {', '.join(tickers)}")

    # A basket of one has no pairwise correlations, so the correlation engine
    # reports dispersion_score = 0.000 and diversification ratio = 1.000 by
    # construction. Those are artifacts of an empty pair set, NOT a measured
    # low-correlation environment -- and downstream they read as a green light
    # for a dispersion trade. Track it and suppress that read below.
    basket_is_degenerate = len(tickers) < 2
    if basket_is_degenerate:
        print("  WARNING: basket has fewer than 2 names. Correlation/dispersion")
        print("           statistics below are structurally empty, not a signal.")
        print("           Fix the constituent feed before trusting any dispersion read.")

    basket_stats = None
    try:
        basket_files, basket_interp, basket_stats = ce.run_correlation_engine(
            tickers, weights=weights, market=chosen_index, period='2y', output_dir=out_root
        )
        produced.extend(basket_files)
        basket_label = (
            f"sentiment basket ({pack_ctx['pack'].get('group_id', 'highlighted-pack')})"
            if use_pack_basket else f"{chosen_index} constituents"
        )
        sections.append({
            "title": f"Basket Statistics — {basket_label}",
            "text": basket_interp or "",
            "images": [f for f in basket_files if f.lower().endswith('.png')]
        })
    except Exception as e:
        print(f"  Basket/correlation engine failed: {e}")

    # ---- Step 2: variance-swap replication on ONLY the index + focus ticker ----
    # Full-constituent replication is explicitly deferred -- this is a
    # 2-leg read (index vs. the one ticker we're profiling), not a basket-wide run.
    print(f"\n[2/5] Running variance-swap replication on {chosen_index} (index) and {ticker} (focus)...")
    import variance_swap_live as vsl
    index_result = None
    ticker_result = None
    try:
        idx_files, idx_interp, index_result = vsl.run_variance_swap_live(chosen_index, target_years, output_dir=out_root, expiration=expiration)
        produced.extend(idx_files)
        sections.append({
            "title": f"Variance Swap: {chosen_index} (index)",
            "text": idx_interp or "",
            "images": [f for f in idx_files if f.lower().endswith('.png')]
        })
    except Exception as e:
        print(f"  Index replication failed: {e}")

    try:
        tk_files, tk_interp, ticker_result = vsl.run_variance_swap_live(ticker, target_years, output_dir=out_root, expiration=expiration)
        produced.extend(tk_files)
        sections.append({
            "title": f"Variance Swap: {ticker} (focus)",
            "text": tk_interp or "",
            "images": [f for f in tk_files if f.lower().endswith('.png')]
        })
    except Exception as e:
        print(f"  Focus ticker replication failed: {e}")

    # ---- Step 3: opportunities read ----
    print("\n[3/5] Dispersion / vol opportunity read...")
    opp_lines = []
    if index_result and ticker_result:
        idx_fv = index_result.get('fair_variance_swap_strike_vol_pct')
        tk_fv = ticker_result.get('fair_variance_swap_strike_vol_pct')
        beta = basket_stats.individual_betas.get(ticker) if basket_stats else None
        # Only treat the dispersion score as real information if the basket
        # actually had pairs to correlate.
        avg_corr = basket_stats.dispersion_score if (basket_stats and not basket_is_degenerate) else None
        opp_lines.append(f"{chosen_index} fair vol: {idx_fv:.2f}%  |  {ticker} fair vol: {tk_fv:.2f}%")
        if beta is not None:
            opp_lines.append(f"{ticker} beta vs {chosen_index}: {beta:.2f}")
        if avg_corr is not None:
            opp_lines.append(f"Basket avg pairwise correlation (dispersion score): {avg_corr:.3f}")
        spread = tk_fv - idx_fv
        opp_lines.append(f"Vol spread (ticker - index): {spread:+.2f} vol pts")
        if avg_corr is not None and avg_corr < 0.3 and spread > 0:
            opp_lines.append(
                f"  -> LOW basket correlation + {ticker} priced rich vs {chosen_index}: "
                f"classic dispersion setup (long {ticker} vol / short {chosen_index} vol), "
                f"or short {ticker} vol outright if you don't want the index leg."
            )
        elif avg_corr is not None and avg_corr >= 0.6:
            opp_lines.append(
                f"  -> HIGH basket correlation: weak dispersion environment; "
                f"index-level short-vol is likely more efficient than a single-name dispersion leg."
            )
        elif basket_is_degenerate:
            opp_lines.append(
                "  -> Basket correlation unavailable (fewer than 2 names resolved), so no "
                "dispersion read is possible. The vol spread above is still a valid "
                f"two-leg comparison of {ticker} vs {chosen_index}, but nothing here "
                "supports or rejects a dispersion trade."
            )
        else:
            opp_lines.append("  -> Mixed signal; no strong dispersion edge from correlation alone.")
        if basket_stats:
            other_betas = sorted(
                ((t, b) for t, b in basket_stats.individual_betas.items() if t != ticker),
                key=lambda x: abs(x[1]), reverse=True
            )[:5]
            if other_betas:
                opp_lines.append("Other basket names with the highest beta to " + chosen_index + " (worth a look too): " +
                                 ", ".join(f"{t} ({b:.2f})" for t, b in other_betas))
    else:
        opp_lines.append("Could not compute an opportunity read -- one or both replication legs failed.")
    opp_text = "\n".join(opp_lines)
    print(opp_text)
    sections.append({"title": "Opportunities", "text": opp_text, "images": []})

    # ---- Step 4: rest of the suite, scoped to the focus ticker ----
    print(f"\n[4/5] Running remaining modules for {ticker}...")

    print("\n[Running] GARCH Analysis")
    try:
        import garch_analysis as ga
        files, interp = ga.run_garch_module(ticker, output_dir=out_root)
        produced.extend(files)
        sections.append({
            "title": f"GARCH Analysis: {ticker}", "text": interp or "",
            "images": [f for f in files if f.lower().endswith('.png')]
        })
    except Exception as e:
        print(f"  GARCH failed: {e}")

    # This is a SEPARATE, narrower step from the Group Screener above (Step
    # 0): it only ever screens the one focus ticker, so its table always has
    # one row. That's by design, not a bug -- but rendered under the generic
    # "Screener Score" label it reads as the group screener silently breaking
    # down to one ticker. Labeled explicitly as a recheck, and skipped
    # entirely when the focus ticker's score is already sitting in the group
    # screener's table above. See FIX_PLAN_20260725.md, issue 2.
    if group_screener_ran and ticker in group_tickers:
        print(f"\n[Skipping] Focus-Ticker Screener Recheck for {ticker} -- "
              f"already scored in the Group Screener table above.")
        sections.append({
            "title": f"Screener Score: {ticker} (see Group Screener above)",
            "text": f"{ticker} was already scored as part of the Group Screener "
                    f"run in this session; see that section's table for its "
                    f"row rather than re-running a single-ticker screen.",
            "images": []
        })
    else:
        print("\n[Running] Focus-Ticker Screener Recheck (post-basket)")
        try:
            import variance_swap_screener as vss
            r = vss.screen_ticker(ticker, target_years, expiration=expiration)
            if r:
                vss.print_screener_table([r])
                interp = (f"{ticker}: Score={r.score:.1f} ({r.signal}), VRP={r.vrp_pct:+.1f}pp, "
                         f"Convexity={r.convexity_pct:.1f}pp, Skew={r.skew_bias:.2f}, Tail={r.tail_mass:.1%}")
                sections.append({"title": f"Focus-Ticker Screener Recheck: {ticker}", "text": interp, "images": []})
            else:
                print(f"  No screener result for {ticker}.")
        except Exception as e:
            print(f"  Screener failed: {e}")

    print(f"\n[Running] Dealer Positioning (sign_model={sign_model})")
    try:
        import dealer_positioning as dp
        files, interp, _ = dp.run_dealer_positioning(ticker, target_years, output_dir=out_root, save_csv=True,
                                                       expiration=expiration, sign_model=sign_model)
        produced.extend(files)
        sections.append({
            "title": f"Dealer Positioning: {ticker} (sign_model={sign_model})", "text": interp or "",
            "images": [f for f in files if f.lower().endswith('.png')]
        })
    except Exception as e:
        print(f"  Dealer positioning failed: {e}")

    # ---- Step 5: options chain scanner on the resolved expiry ----
    if run_options_chain:
        print(f"\n[5/5] Running Options Chain Scanner for {ticker} @ {expiration}...")
        try:
            import options_chain_scanner as ocs
            files, interp, scan_result = ocs.run_chain_scanner(ticker, target_years, expiration=expiration, output_dir=out_root)
            produced.extend(files)
            sections.append({
                "title": f"Options Chain Scan: {ticker} {expiration} ({scan_result.verdict})",
                "text": interp or "",
                "images": [f for f in files if f.lower().endswith('.png')]
            })
        except Exception as e:
            print(f"  Chain scanner failed: {e}")
    else:
        print("\n[5/5] Skipping Options Chain Scanner (disabled for this run).")

    return produced, sections


def run_unified_flow():
    print("=" * 60)
    print("  VOLATILITY SUITE — Unified Cross-Suite Run")
    print("=" * 60)

    load_mode = input("Input mode: (1) manual focus ticker, (2) highlighted ticker pack [default 1]: ").strip()
    pack_ctx = _load_ticker_pack_interactive() if load_mode == "2" else None
    ticker = (pack_ctx or {}).get("focus_ticker") or prompt_focus_ticker()
    if pack_ctx:
        print(f"\nUsing focus ticker from pack: {ticker}")

    group_tickers, use_pack_basket, chosen_index, known_weight, top_n, _matches = \
        _resolve_ticker_universe(ticker, pack_ctx)

    import expiry_selector
    from thetadata_client import ThetaDataController
    print()
    td_for_expiry = ThetaDataController()
    try:
        expiration, target_years = expiry_selector.choose_expiry_interactive(td_for_expiry, ticker)
    finally:
        td_for_expiry.close()

    # Same prompts run_focus_workflow asks, so "unified" actually runs the
    # same analysis mode 1 does. This mode used to skip these entirely and
    # silently default the dealer-positioning sign model to v1 and the
    # options chain scanner to off. See FIX_PLAN_20260725.md.
    sign_model, run_options_chain = _prompt_sign_model_and_options_chain(pack_ctx)

    option_type = _choose_option_type()
    strike = _choose_optional_strike()
    run_options_suite = _prompt_yes_no("Run Options_Suite after writing context?", default=False)
    run_var_suite = _prompt_yes_no("Run VaR_Tools_Simulations after writing context?", default=False)
    compile_pdf = _prompt_yes_no("Compile outputs into single PDF?", default=False)

    out_root = timestamped_output_dir()
    os.environ["VS_OUTPUT_DIR"] = out_root
    print(f"\nOutputs will be written to: {out_root}")

    tickers, weights = _build_basket(ticker, use_pack_basket, group_tickers, chosen_index, top_n, known_weight)

    # ---- Run the SAME analysis pipeline mode 1 runs ----
    produced, sections = _run_core_analysis(
        ticker=ticker, pack_ctx=pack_ctx, group_tickers=group_tickers,
        use_pack_basket=use_pack_basket, tickers=tickers, weights=weights,
        chosen_index=chosen_index, target_years=target_years, expiration=expiration,
        sign_model=sign_model, run_options_chain=run_options_chain, out_root=out_root,
    )

    sentiment_pack_json = None
    sentiment_group_id = None
    if pack_ctx:
        sentiment_pack_json = str(pack_ctx["pack"].get("json_path") or "") or None
        sentiment_group_id = str(pack_ctx["pack"].get("group_id") or "") or None

    options_root = DEFAULT_OPTIONS_SUITE_ROOT
    var_root = DEFAULT_VAR_SUITE_ROOT
    sentiment_root = DEFAULT_SENTIMENT_SUITE_ROOT

    context = build_suite_context(
        output_dir=out_root,
        run_id=_run_id_now(),
        ticker=ticker,
        option_type=option_type,
        strike=strike,
        target_years=target_years,
        expiration_date=expiration,
        index_ticker=chosen_index,
        basket_tickers=tickers,
        basket_weights=weights,
        sentiment_manifest_path=(pack_ctx or {}).get("manifest_path") or _default_pack_manifest_path(),
        sentiment_pack_json_path=sentiment_pack_json,
        sentiment_group_id=sentiment_group_id,
        sentiment_ranked_tickers=group_tickers,
        run_options_suite=run_options_suite,
        run_var_suite=run_var_suite,
        compile_pdf=compile_pdf,
        options_suite_root=options_root,
        var_suite_root=var_root,
        sentiment_suite_root=sentiment_root,
    )
    context_path = write_suite_context(context, os.path.join(out_root, "suite_context.json"))
    print(f"Wrote shared handoff context: {context_path}")

    child_results: List[Dict[str, Any]] = []
    before_files = set(collect_files(out_root))

    if run_options_suite:
        _require_existing_dir(options_root, "Options_Suite root")
        print("\n[Unified] Launching Options_Suite in context mode...")
        child_results.append(
            _run_child_suite(
                suite_name="options",
                suite_root=options_root,
                context_path=context_path,
                output_dir=out_root,
            )
        )

    if run_var_suite:
        _require_existing_dir(var_root, "VaR_Tools_Simulations root")
        print("\n[Unified] Launching VaR_Tools_Simulations in context mode...")
        child_results.append(
            _run_child_suite(
                suite_name="var",
                suite_root=var_root,
                context_path=context_path,
                output_dir=out_root,
            )
        )

    after_files = set(collect_files(out_root))
    new_child_files = sorted(after_files - before_files)

    # This is the actual fix for the dead PDF prompt: mode 2 now has real
    # sections to compile, because _run_core_analysis just produced them.
    if compile_pdf:
        try:
            pdf_path = os.path.join(out_root, f"volatility_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
            compose_pdf_report(pdf_path, sections)
            print(f"Compiled PDF: {pdf_path}")
        except Exception as e:
            print(f"PDF compilation failed: {e}")

    summary_lines = [
        f"run_id={context['run_id']}",
        f"focus={context['focus']['ticker']} {context['focus']['expiration_date']} {context['focus']['option_type']}",
        f"context_file={context_path}",
        # Renamed from the old "new_output_files" -- that name read as "how
        # many files did this run produce", when it only ever measured files
        # added by child suites. analysis_output_files is the real total.
        f"analysis_output_files={len(produced)}",
        f"child_suites_requested={int(run_options_suite) + int(run_var_suite)}",
        f"child_suite_new_files={len(new_child_files)}",
    ]
    for result in child_results:
        status = "ok" if result["returncode"] == 0 else f"failed(rc={result['returncode']})"
        summary_lines.append(f"{result['suite']}_suite={status}")
    summary_text = "\n".join(summary_lines)

    summary_path = os.path.join(out_root, "unified_run_summary.txt")
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(summary_text + "\n\n")
    print("\nUnified run summary:")
    print(summary_text)
    print(f"Summary file: {summary_path}")

    if child_results:
        for result in child_results:
            if result["returncode"] != 0:
                print(f"\n[{result['suite']}] command: {result['command']}")
                if result["stdout_tail"]:
                    print(f"[{result['suite']}] stdout tail:\n{result['stdout_tail']}")
                if result["stderr_tail"]:
                    print(f"[{result['suite']}] stderr tail:\n{result['stderr_tail']}")

    print("\nRun complete.")
    all_files = collect_files(out_root)
    print(f"Files in output folder ({out_root}):")
    for f in all_files:
        print(f"  {f}")
    print("Done.")


def run_focus_workflow():
    print("=" * 60)
    print("  VOLATILITY SUITE — Focus-Ticker Workflow")
    print("=" * 60)

    load_mode = input("Input mode: (1) manual focus ticker, (2) highlighted ticker pack [default 1]: ").strip()
    pack_ctx = None
    if load_mode == "2":
        pack_ctx = _load_ticker_pack_interactive()
    ticker = (pack_ctx or {}).get("focus_ticker") or prompt_focus_ticker()
    if pack_ctx:
        print(f"\nUsing focus ticker from pack: {ticker}")

    group_tickers, use_pack_basket, chosen_index, known_weight, top_n, _matches = \
        _resolve_ticker_universe(ticker, pack_ctx)

    # Expiry selection: still asks for a target-years number the same way it
    # always has (0.33 / 0.15 / 0.25 / whatever), but no longer silently
    # carries that number forward into whatever single expiry happens to be
    # numerically closest. Instead it resolves to the closest WEEKLY expiry
    # and the closest MONTHLY/OPEX expiry to that target date (e.g. a target
    # landing near 10/2 but with OPEX that month on 10/16 gets offered both),
    # and the user picks the specific date. That one resolved expiration is
    # then threaded through every downstream module below instead of each
    # module independently re-deriving its own nearest expiry from
    # target_years -- which is what let different modules silently land on
    # different actual dates for "the same" target before (see
    # expiry_selector.py's module docstring for the 252-vs-365-day-count bug
    # this also fixes).
    import expiry_selector
    from thetadata_client import ThetaDataController
    print()
    td_for_expiry = ThetaDataController()
    try:
        expiration, target_years = expiry_selector.choose_expiry_interactive(td_for_expiry, ticker)
    finally:
        td_for_expiry.close()

    # Dealer-positioning sign model + options chain scanner toggle: same
    # prompts run_unified_flow now asks too (see
    # _prompt_sign_model_and_options_chain), so the two entry points can't
    # drift on this again.
    sign_model, run_options_chain = _prompt_sign_model_and_options_chain(pack_ctx)

    pdf_choice = input("Compile outputs into single PDF? (y/n, default n): ").strip().lower() or 'n'

    out_root = timestamped_output_dir()
    print(f"\nOutputs will be written to: {out_root}")
    os.environ['VS_OUTPUT_DIR'] = out_root

    tickers, weights = _build_basket(ticker, use_pack_basket, group_tickers, chosen_index, top_n, known_weight)

    produced, sections = _run_core_analysis(
        ticker=ticker, pack_ctx=pack_ctx, group_tickers=group_tickers,
        use_pack_basket=use_pack_basket, tickers=tickers, weights=weights,
        chosen_index=chosen_index, target_years=target_years, expiration=expiration,
        sign_model=sign_model, run_options_chain=run_options_chain, out_root=out_root,
    )

    # ---- Summary / optional PDF ----
    print("\nRun complete.")
    all_files = collect_files(os.environ['VS_OUTPUT_DIR'])
    print(f"Files in output folder ({os.environ['VS_OUTPUT_DIR']}):")
    for f in all_files:
        print(f"  {f}")

    if pdf_choice == 'y':
        try:
            pdf_path = os.path.join(os.environ['VS_OUTPUT_DIR'], f"volatility_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
            compose_pdf_report(pdf_path, sections)
            print(f"Compiled PDF: {pdf_path}")
        except Exception as e:
            print(f"PDF compilation failed: {e}")

    print("\nDone.")


def main():
    mode = input("Run mode: (1) standard focus workflow, (2) unified cross-suite run [default 1]: ").strip()
    if mode == "2":
        run_unified_flow()
        return
    run_focus_workflow()


if __name__ == '__main__':
    main()
