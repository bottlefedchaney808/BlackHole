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

A third, non-interactive entry point -- run_context_mode (`--context PATH
--context-out PATH`) -- runs that same `_run_core_analysis` pipeline with every
prompt answered from a validated suite_context.json instead of stdin, and
publishes a machine-readable `vol_result.json` (schema in shared/schemas.py,
`validate_vol_result`). This is the mode the root `orchestrator.py` drives.
Before it existed, the orchestrator had to *script Vol_Suite's stdin* -- a list
of blank lines and "y"/"n" answers coupled to the exact prompt order in
run_focus_workflow -- and, because nothing was written to `--context-out`, had
to synthesize a fake result payload by listing files in the output directory.
Both of those hacks are gone: the prompt order is now free to change, and
Vol_Suite reports its own vol surface / dealer positioning / gamma records
rather than having them inferred from filenames.
"""

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import index_membership as idxmem
from shared.artifact_paths import resolve_stored
from suite_context import (
    DEFAULT_OPTIONS_SUITE_ROOT,
    DEFAULT_SENTIMENT_SUITE_ROOT,
    DEFAULT_VAR_SUITE_ROOT,
    build_suite_context,
    read_suite_context,
    write_suite_context,
)
from vs_utils import collect_files, compose_pdf_report, timestamped_output_dir

JUMP_MODEL_DEFAULT = os.getenv("JUMP_MODEL_DEFAULT", "Bates")


def _calibrate_default_jump_model(ticker: str, expiration: str, target_years: float):
    """Calibrate JUMP_MODEL_DEFAULT against the focus ticker's chain at
    *expiration*, plus a cheap Merton fit for the GARCH jump-day filter
    (Task 7/8's tie 1 wants Merton specifically, independent of whichever
    model JUMP_MODEL_DEFAULT names -- see the design spec's model guide).
    Returns a dict shaped for suite_context.json's jump_diffusion key. On
    any failure returns {"status": "error", "error": <reason>} instead of
    raising (matches the GARCH call site's error-swallowing discipline at
    this same call level) -- the reason is also printed, but a live
    orchestrator run only ever persists a *successful* suite's stdout tail,
    so without this the failure reason was unrecoverable after the fact
    (confirmed live 2026-08-31: a null jump_diffusion block in a completed
    NVDA run with no way to tell what failed). Self-contained: fetches its
    own spot/rate/dividend/chain data, the same way
    garch_analysis.run_garch_module fetches its own price history, rather
    than depending on _run_core_analysis's internal variable soup.
    """
    from jump_diffusion.calibration import calibrate
    from jump_diffusion.models import ALL_MODELS, BatesModel, MertonModel
    from thetadata_client import ThetaDataController
    from variance_swap_live import fetch_chain_thetadata

    model_cls = next(
        (m for m in ALL_MODELS if m.name == JUMP_MODEL_DEFAULT), BatesModel
    )
    try:
        td = ThetaDataController()
        spot = float(td.fetch_spot_price(ticker))
        r = float(td.fetch_risk_free_rate(target_years))
        q = float(td.fetch_dividend_yield(ticker, spot))
        chain = fetch_chain_thetadata(td, ticker, expiration, r, q)
        result = calibrate(model_cls, chain, spot, target_years)

        jump_variance_share = None
        if model_cls is BatesModel:
            jump_variance_share = model_cls.from_array(
                [result.params[p] for p in model_cls.param_names]
            ).jump_variance_share(target_years)

        if model_cls is MertonModel:
            merton_sigma = result.params[
                "sigma"
            ]  # already fit, avoid a redundant calibration
        else:
            merton_result = calibrate(MertonModel, chain, spot, target_years)
            merton_sigma = merton_result.params["sigma"]

        return {
            "model_name": result.model_name,
            "params": result.params,
            "rmse_iv": result.rmse_iv,
            "jump_variance_share": jump_variance_share,
            "merton_sigma": merton_sigma,
        }
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        print(f"  [jump_diffusion] {JUMP_MODEL_DEFAULT} calibration failed: {reason}")
        return {"status": "error", "error": reason}


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


# ---------------------------------------------------------------------------
# Non-interactive overrides
# ---------------------------------------------------------------------------
# Populated from CLI flags in main() so the common single-ticker workflow can
# run headless without stdin.  Keys are unique substrings of the prompt text
# they bypass -- see _set_override calls in main().
_noninteractive: dict[str, str] = {}


def _set_override(key: str, value: str | None) -> None:
    if value is not None:
        _noninteractive[key] = str(value)


def _ni_input(prompt: str, default: str | None = None) -> str:
    """Like input(), but honours _noninteractive overrides keyed by prompt text.

    Keys that start with '_' are internal overrides not tied to a specific
    prompt -- they're read through _get_noninteractive() instead.
    """
    for key, val in _noninteractive.items():
        if key.startswith("_"):
            continue
        if key in prompt:
            return val
    return input(prompt) if default is None else input(prompt) or default


def _get_noninteractive(key: str, default: Any = None) -> Any:
    """Read an internal override (keys starting with '_') from the dict."""
    return _noninteractive.get(key, default)


def prompt_focus_ticker() -> str:
    """Prompt for the focus ticker, re-prompting on a symbol that doesn't
    resolve. A typo (e.g. NTFLX for NFLX) otherwise surfaces much later as
    'not found in any of the 15 tracked indices', which reads like a data-feed
    failure rather than a typo."""
    while True:
        t = _ni_input("Focus ticker (e.g. MSFT): ").strip().upper()
        if not t:
            return "MSFT"
        if _ticker_exists(t):
            return t
        print(f"  '{t}' doesn't resolve to a tradable symbol -- check the spelling.")
        retry = (
            _ni_input("  Enter a different ticker, or press Enter to use it anyway: ")
            .strip()
            .upper()
        )
        if not retry:
            return t
        if _ticker_exists(retry):
            return retry
        print(f"  '{retry}' doesn't resolve either -- continuing with it.")
        return retry


def _default_pack_manifest_path() -> str:
    root = Path(__file__).resolve().parent.parent
    return str(
        root
        / "sentiment-scanner"
        / "data"
        / "exports"
        / "highlighted_ticker_packs"
        / "latest_manifest.json"
    )


def _load_json_file(path: str) -> dict | None:
    """Load a JSON file, resolving the path through resolve_stored().
    
    This handles legacy Windows-style paths by converting them to the
    local host's absolute path if they point to an existing file.
    """
    resolved = resolve_stored(path)
    if resolved is None:
        return None
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _load_ticker_pack_interactive() -> dict | None:
    default_manifest = _default_pack_manifest_path()
    manifest_path = (
        _ni_input(f"Ticker-pack manifest path [default: {default_manifest}]: ").strip()
        or default_manifest
    )
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
    choice = _ni_input("Choose pack number (default 1): ").strip()
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
        print(
            f"  Could not load pack JSON at {pack_path}. Falling back to manual ticker entry."
        )
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
    sel = _ni_input("Focus ticker from pack (number, default 1): ").strip()
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


def prompt_index_choice(ticker: str) -> tuple[str, list[dict]]:
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
        choice = _ni_input(
            f"\nChoose an index by number, or type a ticker directly (default {default_idx}): "
        ).strip()
        if not choice:
            return default_idx, matches
        if choice.isdigit():
            i = int(choice) - 1
            if 0 <= i < len(matches):
                return matches[i]["index"], matches
        return choice.upper(), matches
    print(f"  {ticker} wasn't found in any tracked index/sector ETF.")
    # In non-interactive mode, fall back to the override or SPY rather than
    # blocking on a manual-entry prompt.
    override = _get_noninteractive("Choose an index") or _get_noninteractive(
        "Enter an index/sector ETF ticker manually"
    )
    if override:
        return str(override).upper(), matches
    print("  Falling back to SPY as the benchmark index.")
    return "SPY", matches


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
SHARE_CLASS_GROUPS: list[set] = [
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


def _dedupe_share_classes(
    constituents: list[tuple[str, float]], protect: str | None = None
) -> list[tuple[str, float]]:
    """Collapse dual-class listings to a single ticker per issuer.

    The surviving ticker carries the issuer's COMBINED index weight, since the
    economic exposure to that company really is the sum of both classes -- only
    the double-counting as two independent names is removed.

    `protect` (the focus ticker) always survives its group even if the other
    class carries more weight.
    """
    kept: list[tuple[str, float]] = []
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
        print(
            f"  [dedupe] {survivor} and {dropped} are share classes of one issuer "
            f"-> keeping {survivor} at combined weight {combined:.2f}%"
        )
        kept.append((survivor, combined))

    return kept


def _resolve_basket(
    ticker: str, chosen_index: str, top_n: int, known_weight: float | None = None
) -> tuple[list[str], list[float]]:
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
    ticker: str, pack_ctx: dict | None
) -> tuple[list[str], bool, str, float | None, int, list[dict]]:
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
        matches: list[dict] = []
        known_weight = None
        top_n = len(group_tickers)
        print(
            f"\nUsing the {len(group_tickers)}-ticker sentiment-scanner basket "
            f"directly (skipping the index-basket prompt). Benchmark index: "
            f"{chosen_index} (override with the VS_BENCHMARK_INDEX env var)."
        )
    else:
        chosen_index, matches = prompt_index_choice(ticker)
        known_weight = next(
            (m["weight"] for m in matches if m["index"] == chosen_index), None
        )
        n_input = _ni_input(
            "Basket size — number of index constituents to pull (default 10): "
        ).strip()
        top_n = int(n_input) if n_input else 10

    return group_tickers, use_pack_basket, chosen_index, known_weight, top_n, matches


def _build_basket(
    ticker: str,
    use_pack_basket: bool,
    group_tickers: list[str],
    chosen_index: str,
    top_n: int,
    known_weight: float | None,
) -> tuple[list[str], list[float]]:
    """The (tickers, weights) basket used for correlation stats AND, in the
    unified flow, the suite_context handoff -- computed once so both are
    backed by the same numbers instead of two independent (and potentially
    inconsistent) resolutions."""
    if use_pack_basket:
        print(
            f"\n[1/5] Using the {len(group_tickers)}-ticker sentiment-scanner "
            f"basket for correlation (not an index-derived basket)..."
        )
        return list(group_tickers), [1.0] * len(group_tickers)
    print(f"\n[1/5] Building basket from {chosen_index} constituents...")
    return _resolve_basket(ticker, chosen_index, top_n, known_weight=known_weight)


def _run_id_now() -> str:
    return datetime.utcnow().strftime("vsuite_%Y%m%d_%H%M%S")


def _choose_option_type() -> str:
    raw = (
        _ni_input("Option type for shared context (call/put, default call): ")
        .strip()
        .lower()
        or "call"
    )
    return "put" if raw == "put" else "call"


def _choose_optional_strike() -> float | None:
    raw = _ni_input(
        "Optional strike for shared context (press Enter to keep null): "
    ).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        print(f"  Invalid strike '{raw}', keeping strike=null.")
        return None


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    choice = (
        _ni_input(f"{prompt} (y/n, default {'y' if default else 'n'}): ")
        .strip()
        .lower()
    )
    if not choice:
        return default
    return choice == "y"


def _require_existing_dir(path_value: str, label: str) -> str:
    resolved = Path(path_value).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(
            f"{label} path does not exist or is not a directory: {resolved}"
        )
    return str(resolved)


def _collect_ranked_tickers_from_pack(pack_ctx: dict | None) -> list[str]:
    if not pack_ctx:
        return []
    rows = pack_ctx["pack"].get("tickers", [])
    ranked = sorted(
        rows,
        key=lambda t: (int(t.get("rank", 9999) or 9999), -float(t.get("cns", 0) or 0)),
    )
    out: list[str] = []
    for row in ranked:
        symbol = str((row or {}).get("symbol", "")).upper().strip()
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def _prompt_sign_model_and_options_chain(pack_ctx: dict | None) -> tuple[str, bool]:
    """The dealer-positioning sign-model choice and options-chain-scanner
    toggle -- asked identically by both run modes so mode 2 actually reaches
    v2 (vol_surface_replication) and the chain scanner instead of silently
    skipping them the way it used to. See FIX_PLAN_20260725.md, "mode two
    needs to run just like mode 1"."""
    # Production has one authoritative dealer engine; legacy sign selection is
    # intentionally unavailable outside the backtesting/comparison path.
    sign_model = "expiry_book"
    options_hint = True
    if pack_ctx:
        options_hint = bool(
            pack_ctx["pack"].get("downstream_hints", {}).get("options_suite", False)
        )
    options_default = "y" if options_hint else "n"
    run_options_chain = (
        _ni_input(f"Run Options Chain Scanner step? (y/n, default {options_default}): ")
        .strip()
        .lower()
        or options_default
    ) == "y"
    return sign_model, run_options_chain


def _run_production_dealer_positioning(
    ticker: str,
    target_years: float,
    output_dir: str,
    expiration: str,
    sign_model: str,
    jump_variance_share: float | None = None,
):
    """Run the authoritative expiry-book engine for the production suite."""
    from dealer_positioning import (
        plot_expiry_book_greek_exposure,
        plot_expiry_book_heatmap,
    )
    from expiry_book_production import fetch_production_result, format_production_interp
    from thetadata_client import ThetaDataController

    td = ThetaDataController()
    try:
        result = fetch_production_result(
            td, ticker, expiration, jump_variance_share=jump_variance_share
        )
    finally:
        td.close()
    interp = format_production_interp(result)
    files = [
        plot_expiry_book_greek_exposure(result, output_dir=output_dir),
        plot_expiry_book_heatmap(result, output_dir=output_dir),
    ]
    return files, interp, result


def _prompt_extra_analytics(pack_ctx: dict | None) -> tuple[bool, bool, bool]:
    """Toggles for the three optional analytics modules that sit alongside the
    core pipeline: the 2D (strike x tenor) vol surface, the VRP term
    structure (1-12mo), and -- only offered when a sentiment-scanner pack was
    actually loaded -- the CNS/war_score sentiment backtest. Asked identically
    by both run_focus_workflow and run_unified_flow, same pattern as
    _prompt_sign_model_and_options_chain."""
    run_vs2d = _prompt_yes_no(
        "Build 2D vol surface (strike x tenor) for the focus ticker?", default=False
    )
    run_vrp = _prompt_yes_no(
        "Run VRP term structure (1-12mo) for the focus ticker?", default=False
    )
    run_sent_bt = False
    if pack_ctx:
        run_sent_bt = _prompt_yes_no(
            "Run sentiment backtest (CNS/war_score vs forward returns) on the highlighted-pack history?",
            default=False,
        )
    return run_vs2d, run_vrp, run_sent_bt


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
) -> dict[str, Any]:
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

    print(
        f"  [{suite_name}] running {Path(entrypoint).name} "
        f"(timeout {_CHILD_SUITE_TIMEOUT_SEC}s)... output is captured, "
        f"so this will look idle until it finishes."
    )
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
            "stdout_tail": (e.stdout or b"").decode(errors="replace").splitlines()[-12:]
            and "\n".join((e.stdout or b"").decode(errors="replace").splitlines()[-12:])
            or "",
            "stderr_tail": f"TIMEOUT after {_CHILD_SUITE_TIMEOUT_SEC}s -- child killed.",
        }
    return {
        "suite": suite_name,
        "command": " ".join(command),
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-12:]) if proc.stdout else "",
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-12:]) if proc.stderr else "",
    }


# ---------------------------------------------------------------------------
# vol_result.json -- the machine-readable side of a Vol_Suite run
#
# Everything below turns the objects the pipeline already computes (variance
# swap result dicts, a DealerPositioningResult, correlation BasketStats) into
# JSON that another process can rely on. The formal contract lives in
# shared/schemas.py::validate_vol_result; these helpers are the producer side
# of it.
# ---------------------------------------------------------------------------

VOL_RESULT_SCHEMA_VERSION = 1

# gamma_records is genuinely large -- a liquid name's chain runs to several
# thousand rows, which is a ~500 KB JSON blob nobody downstream reads in full.
# The full set is already written to CSV by dealer_positioning; the JSON keeps
# the highest-|dollar gamma| rows (the ones that actually drive the hedging
# picture) and says how many it dropped. Set to 0 to disable the cap.
_MAX_GAMMA_RECORDS = int(os.environ.get("VS_VOL_RESULT_MAX_GAMMA_RECORDS", "500"))


def _iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    """Coerce pipeline values into something json.dump can emit *and* another
    process can parse.

    Two hazards, both routine in this codebase and both silent:

    - NaN/Infinity. Every module here returns float('nan') for "not available"
      (see GammaRecord.delta, _extract_greek_field, the RV lookbacks). Python's
      json writes those as bare `NaN`/`Infinity` tokens, which are not JSON --
      strict parsers in any other language reject the whole file. They become
      null.
    - numpy scalars and arrays. DealerPositioningResult is half np.ndarray and
      its scalars are np.float64, which json.dump raises TypeError on. Handled
      without importing numpy at module scope (this module deliberately does
      its heavy imports lazily inside functions).
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    item = getattr(value, "item", None)  # numpy scalar
    if callable(item) and getattr(value, "shape", None) == ():
        try:
            return _json_safe(item())
        except Exception:
            pass
    tolist = getattr(value, "tolist", None)  # numpy array
    if callable(tolist):
        try:
            return _json_safe(tolist())
        except Exception:
            pass
    return str(value)


_VARIANCE_LEG_FIELDS = (
    "S0",
    "F",
    "T_years",
    "fair_variance_annualized",
    "fair_variance_swap_strike_vol",
    "fair_variance_swap_strike_vol_pct",
    "atm_strike",
    "atm_implied_vol",
    "atm_implied_vol_pct",
    "convexity_premium_vol_pct",
    "num_strikes_used",
    "K_min",
    "K_max",
)


def _variance_leg_summary(ticker: str, result: dict | None) -> dict[str, Any] | None:
    """One replication leg, flattened for the handoff.

    Deliberately drops `strike_table` -- it is a dict of parallel numpy arrays
    over every strike used, which is both enormous and already exported to CSV
    by variance_swap_live.export_csv. Consumers wanting per-strike detail read
    that file; this block is the scalar summary.
    """
    if not isinstance(result, dict):
        return None
    summary: dict[str, Any] = {"ticker": str(ticker).upper()}
    for key in _VARIANCE_LEG_FIELDS:
        if key in result:
            summary[key] = _json_safe(result[key])
    # Stable short alias for the one number every consumer wants, so nobody
    # has to spell fair_variance_swap_strike_vol_pct to get at it.
    summary["fair_vol_pct"] = summary.get("fair_variance_swap_strike_vol_pct")
    return summary


_DEALER_SCALAR_FIELDS = (
    "ticker",
    "spot",
    "forward",
    "dividend_yield",
    "total_net_gamma",
    "total_net_dollar_gamma",
    "hedge_requirement",
    "gamma_flip_level",
    "highest_gamma_strike",
    "total_gamma_exposure",
    "num_expiries",
    "num_records",
    "greek_days_window",
    "has_delta_data",
    "has_vanna_data",
    "has_charm_data",
    "hedge_equiv_option_strike",
    "hedge_equiv_option_right",
    "hedge_equiv_option_contracts",
)


def _dealer_positioning_summary(
    result: Any, sign_model: str, csv_path: str | None = None
) -> dict[str, Any]:
    """The scalar dealer-positioning read, plus an explicit `available` flag.

    `available` is the load-bearing field: dealer positioning is the step most
    likely to come back empty (no chain, no greeks, a ThetaData outage), and
    without a flag saying so, a block of zeros is indistinguishable from a
    genuinely flat book. Consumers branch on `available`, never on whether the
    numbers look plausible.
    """
    payload: dict[str, Any] = {
        "available": result is not None,
        "sign_model": str(sign_model),
        "gamma_records_csv": csv_path,
    }
    if result is None:
        return payload
    for key in _DEALER_SCALAR_FIELDS:
        if hasattr(result, key):
            payload[key] = _json_safe(getattr(result, key))
    # Historical comparison objects may carry a retired label; production
    # artifacts must identify the authoritative engine, never the test object's
    # legacy metadata.
    payload["sign_model"] = str(sign_model)
    return payload


_GAMMA_RECORD_FIELDS = (
    "strike",
    "expiry",
    "right",
    "oi",
    "gamma",
    "dollar_gamma",
    "iv",
    "tte",
    "bid",
    "ask",
    "delta",
    "vanna",
    "charm",
)


def _gamma_records_payload(result: Any) -> tuple[list[dict[str, Any]], int, bool]:
    """(records, total_before_truncation, was_truncated)."""
    raw = (
        list(getattr(result, "gamma_records", None) or []) if result is not None else []
    )
    total = len(raw)
    truncated = False
    if _MAX_GAMMA_RECORDS > 0 and total > _MAX_GAMMA_RECORDS:

        def _abs_dollar_gamma(rec: Any) -> float:
            val = _json_safe(getattr(rec, "dollar_gamma", None))
            return abs(float(val)) if isinstance(val, (int, float)) else 0.0

        raw = sorted(raw, key=_abs_dollar_gamma, reverse=True)[:_MAX_GAMMA_RECORDS]
        truncated = True
    records = [
        {field: _json_safe(getattr(rec, field, None)) for field in _GAMMA_RECORD_FIELDS}
        for rec in raw
    ]
    return records, total, truncated


def _build_vol_surface(artifacts: dict[str, Any]) -> dict[str, Any]:
    """The `vol_surface` block of vol_result.json.

    "Vol surface" here means what this suite actually measures: the two
    replication legs (index and focus ticker) at one shared, pinned expiry,
    the spread between them, and the basket correlation context that decides
    whether that spread is a dispersion signal or just a spread. It is not a
    strike x expiry IV grid -- Vol_Suite computes those inside
    vol_surface_reference/dealer_positioning as an intermediate, and they are
    published as plots/CSV, not as JSON.
    """
    vs = artifacts.get("variance_swap", {})
    return {
        "focus_ticker": artifacts.get("focus_ticker"),
        "index_ticker": artifacts.get("index_ticker"),
        "expiration": artifacts.get("expiration"),
        "target_years": artifacts.get("target_years"),
        "focus": vs.get("focus"),
        "index": vs.get("index"),
        "vol_spread_pts": vs.get("vol_spread_pts"),
        "garch_conditional_vol": artifacts.get("garch_conditional_vol"),
        "basket": artifacts.get("basket", {}),
        "opportunities": artifacts.get("opportunities", ""),
    }


def _build_vol_result(
    *,
    artifacts: dict[str, Any],
    context: dict[str, Any] | None,
    output_dir: str,
    produced: list[str],
    summary: str = "",
) -> dict[str, Any]:
    """Assemble a schema-valid vol_result payload from a completed run."""
    vol_surface = _build_vol_surface(artifacts)
    dealer = artifacts.get(
        "dealer_positioning",
        {"available": False, "sign_model": artifacts.get("sign_model", "")},
    )

    # 'ok' means "at least one of the three blocks carries real content". A run
    # where every leg failed is reported as an error even though the process
    # exited cleanly -- otherwise the orchestrator books a total data outage as
    # a successful stage.
    produced_something = bool(
        vol_surface.get("focus") or vol_surface.get("index") or dealer.get("available")
    )
    status = "ok" if produced_something else "error"

    payload: dict[str, Any] = {
        "schema_version": VOL_RESULT_SCHEMA_VERSION,
        "suite": "vol",
        "status": status,
        "ticker": str(artifacts.get("focus_ticker") or ""),
        "run_id": (context or {}).get("run_id"),
        "output_dir": output_dir,
        "timestamp": _iso_utc_now(),
        "vol_surface": vol_surface,
        "dealer_positioning": dealer,
        "vol_surface_2d": artifacts.get("vol_surface_2d", {"available": False}),
        "vrp_term_structure": artifacts.get("vrp_term_structure", {"available": False}),
        "sentiment_backtest": artifacts.get("sentiment_backtest", {"available": False}),
        "gamma_records": artifacts.get("gamma_records", []),
        "gamma_records_total": int(artifacts.get("gamma_records_total", 0) or 0),
        "gamma_records_truncated": bool(
            artifacts.get("gamma_records_truncated", False)
        ),
        "gamma_records_csv": artifacts.get("gamma_records_csv"),
        "sign_model": artifacts.get("sign_model"),
        "produced_files": [os.path.basename(f) for f in (produced or [])],
        "summary": summary,
        "errors": artifacts.get("errors", []),
    }
    if status == "error":
        steps = (
            ", ".join(e.get("step", "?") for e in payload["errors"])
            or "all pipeline steps"
        )
        payload["error"] = (
            "Vol_Suite produced no vol surface and no dealer positioning; "
            f"failed steps: {steps}"
        )
    return _json_safe(payload)


def _error_vol_result(
    *, ticker: str, error: str, output_dir: str | None = None, run_id: str | None = None
) -> dict[str, Any]:
    """A schema-valid vol_result for a run that never got as far as analysis.

    The empty blocks are present, not omitted, precisely so the consumer's
    validator passes and it can branch on `status` rather than on a
    KeyError.
    """
    return {
        "schema_version": VOL_RESULT_SCHEMA_VERSION,
        "suite": "vol",
        "status": "error",
        "ticker": str(ticker or ""),
        "run_id": run_id,
        "output_dir": output_dir,
        "timestamp": _iso_utc_now(),
        "vol_surface": {},
        "dealer_positioning": {"available": False, "sign_model": ""},
        "gamma_records": [],
        "gamma_records_total": 0,
        "gamma_records_truncated": False,
        "gamma_records_csv": None,
        "produced_files": [],
        "summary": "",
        "errors": [],
        "error": str(error),
    }


def _import_shared_schemas():
    """shared/schemas.py lives at the repo root, one level above this suite.

    Returned as None when unavailable rather than raised: schema validation is
    a correctness check on our own output, and a missing sibling package must
    not be the reason a completed analysis fails to publish. The orchestrator
    validates independently on the consuming side.
    """
    try:
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from shared import schemas

        return schemas
    except Exception:
        return None


def _apply_instrument_resolver(payload: dict[str, Any]) -> dict[str, Any]:
    """Enrich a vol_result payload with cross-source instrument identifiers,
    per instrument_resolver.py's own stated integration point (see that
    module's docstring: "This module can be imported by volatility_suite.py
    to add ... Automatic identifier normalization in analysis pipeline ...
    Cross-source instrument tracking in vol_result.json").

    Gated on VS_INSTRUMENT_RESOLVER (set from --instrument-resolver in
    main()) so a run with no resolver requested pays zero cost and the
    payload is unchanged. Failures here must not cost the run its
    vol_result.json, so they are caught and reported rather than raised.
    """
    resolver_name = os.environ.get("VS_INSTRUMENT_RESOLVER")
    if not resolver_name:
        return payload
    try:
        import instrument_resolver as ir

        resolver = ir.get_resolver_from_args(resolver_name)
        normalizer = ir.Vol_SuiteInstrumentNormalizer(resolver)
        payload = normalizer.add_instrument_identifiers_to_result(payload)
        if isinstance(payload.get("vol_surface"), dict):
            payload["vol_surface"] = normalizer.normalize_instrument_in_vol_surface(
                payload["vol_surface"]
            )
    except Exception as e:
        print(f"  [instrument_resolver] enrichment failed: {e}", file=sys.stderr)
    return payload


def _write_vol_result(path: str, payload: dict[str, Any]) -> str:
    """Validate then write. A payload that fails its own schema is REPLACED by
    an error payload that says so, so the file on disk is always readable by a
    consumer that trusts the contract -- rather than being a subtly malformed
    'success' the orchestrator then has to reject."""
    schemas = _import_shared_schemas()
    if schemas is not None and hasattr(schemas, "validate_vol_result"):
        try:
            schemas.validate_vol_result(payload)
        except ValueError as exc:
            print(f"  [vol_result] self-validation failed: {exc}", file=sys.stderr)
            payload = _error_vol_result(
                ticker=str(payload.get("ticker") or ""),
                error=f"vol_result failed shared.schemas.validate_vol_result: {exc}",
                output_dir=payload.get("output_dir"),
                run_id=payload.get("run_id"),
            )

    out_path = os.path.abspath(path)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, allow_nan=False)
        f.write("\n")
    return out_path


def _resolve_context_out_path(
    context_path: str | None, context: dict[str, Any] | None, context_out: str | None
) -> str:
    """Where vol_result.json goes: --context-out if given, else the context's
    own output_dir, else next to the context file. Same precedence
    Options_Suite/main.py::_resolve_context_out_path uses, so all three
    children behave identically when the orchestrator omits the flag."""
    if context_out:
        return os.path.abspath(context_out)
    output_dir = (context or {}).get("output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        return os.path.join(output_dir, "vol_result.json")
    base = (
        os.path.dirname(os.path.abspath(context_path)) if context_path else os.getcwd()
    )
    return os.path.join(base, "vol_result.json")


def _run_core_analysis(
    *,
    ticker: str,
    pack_ctx: dict | None,
    group_tickers: list[str],
    use_pack_basket: bool,
    tickers: list[str],
    weights: list[float],
    chosen_index: str,
    target_years: float,
    expiration: str,
    sign_model: str,
    run_options_chain: bool,
    out_root: str,
    run_group_screener: bool | None = None,
    run_vol_surface_2d: bool = False,
    run_vrp_term_structure: bool = False,
    run_sentiment_backtest: bool = False,
) -> tuple[list[str], list[dict], dict[str, Any]]:
    """Runs the full Vol_Suite analysis pipeline: group screener (if a pack
    basket is present), basket correlation/dispersion, variance-swap
    replication on the index + focus ticker, an opportunities read, GARCH,
    a screener recheck (skipped if redundant with the group screener),
    dealer positioning, and (optionally) the options chain scanner.

    Shared by run_focus_workflow, run_unified_flow and run_context_mode so
    "running the suite" means exactly the same thing regardless of entry point
    -- the modes differ only in what happens BEFORE this (how
    ticker/basket/expiry get chosen: prompts, or a suite_context.json) and
    AFTER (whether a context handoff gets written, whether sibling suites get
    launched, whether a vol_result.json gets published), never in what analysis
    actually runs. This is what used to be missing: mode 2 wrote a context and,
    unless a child suite was explicitly requested, ran none of this at all. See
    FIX_PLAN_20260725.md.

    `run_group_screener` decides the one prompt this function owns. None (the
    default) keeps the historical behaviour -- ask, defaulting to yes -- and is
    what both interactive modes pass. A bool answers it without touching stdin,
    which is what makes this function safe to call from context mode: a single
    stray input() there would block on a closed stdin and kill the run.

    Returns (produced_files, pdf_sections, artifacts). `artifacts` is the
    structured, JSON-serializable record of what the pipeline actually
    computed -- the raw material for vol_result.json. It is built here rather
    than reconstructed by the caller from filenames, so a step that fails
    reports itself as failed instead of being invisible.
    """
    produced: list[str] = []
    sections: list[dict] = []

    artifacts: dict[str, Any] = {
        "focus_ticker": ticker,
        "index_ticker": chosen_index,
        "expiration": expiration,
        "target_years": float(target_years),
        "sign_model": sign_model,
        "basket": {
            "tickers": list(tickers),
            "weights": [float(w) for w in weights],
            "source": "sentiment_pack" if use_pack_basket else "index_constituents",
            "degenerate": len(tickers) < 2,
            "dispersion_score": None,
            "betas": {},
        },
        "variance_swap": {"index": None, "focus": None, "vol_spread_pts": None},
        "opportunities": "",
        "dealer_positioning": {"available": False, "sign_model": sign_model},
        "gamma_records": [],
        "gamma_records_total": 0,
        "gamma_records_truncated": False,
        "gamma_records_csv": None,
        "group_screener_ran": False,
        "garch_ran": False,
        "chain_scan": None,
        "vol_surface_2d": {"available": False},
        "vrp_term_structure": {"available": False},
        "sentiment_backtest": {"available": False},
        # Per-step failures. A failing module prints and continues (a broken
        # GARCH fit must not cost you the dealer-positioning run), which used
        # to mean the failure left no trace anywhere a machine could read.
        "errors": [],
    }

    def _note_error(step: str, exc: Exception) -> None:
        artifacts["errors"].append(
            {"step": step, "error": f"{type(exc).__name__}: {exc}"}
        )

    group_screener_ran = False
    if pack_ctx or run_group_screener:
        if run_group_screener is None:
            run_pack_screen = (
                _ni_input(
                    "Run variance screener on full highlighted group first? (y/n, default y): "
                )
                .strip()
                .lower()
                or "y"
            ) == "y"
        else:
            run_pack_screen = bool(run_group_screener)
        if run_pack_screen and group_tickers:
            print(
                f"\n[0/5] Running group screener on {len(group_tickers)} highlighted tickers..."
            )
            try:
                import variance_swap_screener as vss

                files, interp = vss.run_variance_screener(
                    group_tickers, target_years, output_dir=out_root
                )
                produced.extend(files)
                group_label = (pack_ctx or {}).get("pack", {}).get(
                    "group_id"
                ) or "highlighted-pack"
                sections.append(
                    {
                        "title": f"Group Screener: {group_label}",
                        "text": interp or "",
                        "images": [f for f in files if f.lower().endswith(".png")],
                    }
                )
                group_screener_ran = True
                artifacts["group_screener_ran"] = True
            except Exception as e:
                print(f"  Group screener failed: {e}")
                _note_error("group_screener", e)

    # ---- Optional: sentiment backtest (CNS/war_score vs forward returns) ----
    # Only meaningful when a sentiment-scanner pack was actually loaded this
    # run -- the backtest measures the pack signal's historical predictive
    # power, not anything about the focus ticker itself.
    if run_sentiment_backtest:
        if pack_ctx:
            print("\n[Running] Sentiment Backtest (CNS/war_score vs forward returns)")
            try:
                import sentiment_backtest as sbt

                manifest_path = (
                    pack_ctx.get("manifest_path") or _default_pack_manifest_path()
                )
                # manifest_path = .../sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json
                # run_sentiment_backtest wants the "data" dir three levels up.
                data_dir = os.path.dirname(
                    os.path.dirname(os.path.dirname(manifest_path))
                )
                bt_result = sbt.run_sentiment_backtest(data_dir, forward_days=5)
                interp = (
                    f"packs_analyzed={bt_result.total_packs_analyzed}  "
                    f"signals={bt_result.total_signals}  "
                    f"hit_rate(top-bottom)={bt_result.hit_rate_top_vs_bottom:+.4f}  "
                    f"sharpe(long-only top quartile)={bt_result.sharpe_long_only}  "
                    f"cns_return_corr={bt_result.cns_return_correlation}"
                )
                sections.append(
                    {
                        "title": "Sentiment Backtest (CNS/war_score vs forward returns)",
                        "text": interp,
                        "images": [],
                    }
                )
                artifacts["sentiment_backtest"] = {
                    "available": True,
                    "start_date": bt_result.start_date,
                    "end_date": bt_result.end_date,
                    "total_packs_analyzed": bt_result.total_packs_analyzed,
                    "total_signals": bt_result.total_signals,
                    "top_quartile_cns_names": list(bt_result.top_quartile_cns_names),
                    "bottom_quartile_cns_names": list(
                        bt_result.bottom_quartile_cns_names
                    ),
                    "hit_rate_top_vs_bottom": _json_safe(
                        bt_result.hit_rate_top_vs_bottom
                    ),
                    "sharpe_long_only": _json_safe(bt_result.sharpe_long_only),
                    "cns_return_correlation": _json_safe(
                        bt_result.cns_return_correlation
                    ),
                    "forward_days": bt_result.forward_days,
                }
            except Exception as e:
                print(f"  Sentiment backtest failed: {e}")
                _note_error("sentiment_backtest", e)
        else:
            print(
                "\n[Skipping] Sentiment Backtest -- no highlighted ticker pack was "
                "loaded this run (needs the ticker-pack input mode)."
            )

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
        print(
            "           Fix the constituent feed before trusting any dispersion read."
        )

    basket_stats = None
    try:
        basket_files, basket_interp, basket_stats = ce.run_correlation_engine(
            tickers,
            weights=weights,
            market=chosen_index,
            period="2y",
            output_dir=out_root,
        )
        produced.extend(basket_files)
        pack_group_id = (pack_ctx or {}).get("pack", {}).get(
            "group_id"
        ) or "highlighted-pack"
        basket_label = (
            f"sentiment basket ({pack_group_id})"
            if use_pack_basket
            else f"{chosen_index} constituents"
        )
        sections.append(
            {
                "title": f"Basket Statistics — {basket_label}",
                "text": basket_interp or "",
                "images": [f for f in basket_files if f.lower().endswith(".png")],
            }
        )
        # dispersion_score is only information when there were pairs to
        # correlate; a degenerate basket's 0.000 is an artifact (see above), so
        # it is published as null rather than as a low-correlation reading.
        if not basket_is_degenerate:
            artifacts["basket"]["dispersion_score"] = _json_safe(
                getattr(basket_stats, "dispersion_score", None)
            )
        artifacts["basket"]["betas"] = _json_safe(
            dict(getattr(basket_stats, "individual_betas", {}) or {})
        )
        # Realized annualized vol per basket ticker and the pairwise
        # correlation matrix were computed here (compute_basket_stats) but
        # previously only ever reached disk as a CSV/PNG artifact -- nothing
        # downstream (VaR's context-mode) could read them, so every VaR run
        # fell back to a flat 0.25 vol and an identity correlation matrix
        # regardless of what this basket actually showed. Publish both in
        # vol_result.json, keyed by the same tickers list already in the
        # basket block, so the orchestrator can thread real numbers into
        # VaR's context instead of leaving it to guess.
        artifacts["basket"]["individual_vols"] = _json_safe(
            dict(getattr(basket_stats, "individual_vols", {}) or {})
        )
        corr_matrix = getattr(basket_stats, "correlation_matrix", None)
        if corr_matrix is not None:
            artifacts["basket"]["correlation_matrix"] = _json_safe(corr_matrix)
            artifacts["basket"]["correlation_tickers"] = list(
                getattr(basket_stats, "tickers", tickers)
            )
    except Exception as e:
        print(f"  Basket/correlation engine failed: {e}")
        _note_error("correlation_engine", e)

    # ---- Step 2: variance-swap replication on ONLY the index + focus ticker ----
    # Full-constituent replication is explicitly deferred -- this is a
    # 2-leg read (index vs. the one ticker we're profiling), not a basket-wide run.
    print(
        f"\n[2/5] Running variance-swap replication on {chosen_index} (index) and {ticker} (focus)..."
    )
    import variance_swap_live as vsl

    index_result = None
    ticker_result = None
    try:
        idx_files, idx_interp, index_result = vsl.run_variance_swap_live(
            chosen_index, target_years, output_dir=out_root, expiration=expiration
        )
        produced.extend(idx_files)
        sections.append(
            {
                "title": f"Variance Swap: {chosen_index} (index)",
                "text": idx_interp or "",
                "images": [f for f in idx_files if f.lower().endswith(".png")],
            }
        )
        artifacts["variance_swap"]["index"] = _variance_leg_summary(
            chosen_index, index_result
        )
    except Exception as e:
        print(f"  Index replication failed: {e}")
        _note_error("variance_swap_index", e)

    try:
        tk_files, tk_interp, ticker_result = vsl.run_variance_swap_live(
            ticker, target_years, output_dir=out_root, expiration=expiration
        )
        produced.extend(tk_files)
        sections.append(
            {
                "title": f"Variance Swap: {ticker} (focus)",
                "text": tk_interp or "",
                "images": [f for f in tk_files if f.lower().endswith(".png")],
            }
        )
        artifacts["variance_swap"]["focus"] = _variance_leg_summary(
            ticker, ticker_result
        )
    except Exception as e:
        print(f"  Focus ticker replication failed: {e}")
        _note_error("variance_swap_focus", e)

    # ---- Step 3: opportunities read ----
    print("\n[3/5] Dispersion / vol opportunity read...")
    opp_lines = []
    if index_result and ticker_result:
        idx_fv = index_result.get("fair_variance_swap_strike_vol_pct")
        tk_fv = ticker_result.get("fair_variance_swap_strike_vol_pct")
        beta = basket_stats.individual_betas.get(ticker) if basket_stats else None
        # Only treat the dispersion score as real information if the basket
        # actually had pairs to correlate.
        avg_corr = (
            basket_stats.dispersion_score
            if (basket_stats and not basket_is_degenerate)
            else None
        )
        opp_lines.append(
            f"{chosen_index} fair vol: {idx_fv:.2f}%  |  {ticker} fair vol: {tk_fv:.2f}%"
        )
        if beta is not None:
            opp_lines.append(f"{ticker} beta vs {chosen_index}: {beta:.2f}")
        if avg_corr is not None:
            opp_lines.append(
                f"Basket avg pairwise correlation (dispersion score): {avg_corr:.3f}"
            )
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
                "  -> HIGH basket correlation: weak dispersion environment; "
                "index-level short-vol is likely more efficient than a single-name dispersion leg."
            )
        elif basket_is_degenerate:
            opp_lines.append(
                "  -> Basket correlation unavailable (fewer than 2 names resolved), so no "
                "dispersion read is possible. The vol spread above is still a valid "
                f"two-leg comparison of {ticker} vs {chosen_index}, but nothing here "
                "supports or rejects a dispersion trade."
            )
        else:
            opp_lines.append(
                "  -> Mixed signal; no strong dispersion edge from correlation alone."
            )
        if basket_stats:
            other_betas = sorted(
                (
                    (t, b)
                    for t, b in basket_stats.individual_betas.items()
                    if t != ticker
                ),
                key=lambda x: abs(x[1]),
                reverse=True,
            )[:5]
            if other_betas:
                opp_lines.append(
                    "Other basket names with the highest beta to "
                    + chosen_index
                    + " (worth a look too): "
                    + ", ".join(f"{t} ({b:.2f})" for t, b in other_betas)
                )
    else:
        opp_lines.append(
            "Could not compute an opportunity read -- one or both replication legs failed."
        )
    opp_text = "\n".join(opp_lines)
    print(opp_text)
    sections.append({"title": "Opportunities", "text": opp_text, "images": []})
    artifacts["opportunities"] = opp_text

    idx_leg = artifacts["variance_swap"]["index"] or {}
    focus_leg = artifacts["variance_swap"]["focus"] or {}
    if (
        idx_leg.get("fair_vol_pct") is not None
        and focus_leg.get("fair_vol_pct") is not None
    ):
        artifacts["variance_swap"]["vol_spread_pts"] = _json_safe(
            float(focus_leg["fair_vol_pct"]) - float(idx_leg["fair_vol_pct"])
        )

    # ---- Step 4: rest of the suite, scoped to the focus ticker ----
    print(f"\n[4/5] Running remaining modules for {ticker}...")

    print("\n[Running] Jump-Diffusion Calibration")
    jump_diffusion_result = _calibrate_default_jump_model(
        ticker, expiration, target_years
    )
    artifacts["jump_diffusion"] = jump_diffusion_result

    print("\n[Running] GARCH Analysis")
    garch_conditional_vol = None
    try:
        import garch_analysis as ga

        garch_result = ga.run_garch_module(
            ticker,
            output_dir=out_root,
            merton_sigma=(jump_diffusion_result or {}).get("merton_sigma"),
            jump_variance_share=(jump_diffusion_result or {}).get(
                "jump_variance_share"
            ),
        )
        files, interp, garch_conditional_vol = garch_result
        produced.extend(files)
        sections.append(
            {
                "title": f"GARCH Analysis: {ticker}",
                "text": interp or "",
                "images": [f for f in files if f.lower().endswith(".png")],
            }
        )
        # run_garch_module swallows a failing fit so one dead module does not
        # cost us dealer positioning, and reports it out-of-band via .error.
        # Without this check the failure would leave no machine-readable trace
        # and garch_ran would claim a clean run. Note a *successful* fit can
        # still legitimately yield garch_conditional_vol=None (empty vol
        # series), so .error -- not the None -- is the failure signal.
        garch_error = getattr(garch_result, "error", None)
        if garch_error is not None:
            raise garch_error
        artifacts["garch_ran"] = True
    except Exception as e:
        print(f"  GARCH failed: {e}")
        _note_error("garch", e)
    artifacts["garch_conditional_vol"] = garch_conditional_vol

    if run_vol_surface_2d:
        print("\n[Running] 2D Vol Surface (strike x tenor)")
        try:
            import vol_surface_2d as vs2d
            from thetadata_client import ThetaDataController

            td_vs2d = ThetaDataController()
            try:
                surface = vs2d.build_surface(ticker, td_vs2d)
            finally:
                td_vs2d.close()
            if surface is not None:
                ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
                surf_path = os.path.join(
                    out_root, f"{ticker}_vol_surface_2d_{ts_tag}.png"
                )
                vs2d.plot(surface, surf_path)
                produced.append(surf_path)
                tenors = surface.fitted_params.get("tenors", [])
                spot = surface.fitted_params.get("spot")
                sections.append(
                    {
                        "title": f"2D Vol Surface: {ticker}",
                        "text": (
                            f"Fitted {len(tenors)} tenor(s) via quadratic-per-expiry smile "
                            f"+ linear total-variance interpolation; spot={spot:.2f}."
                        ),
                        "images": [surf_path],
                    }
                )
                artifacts["vol_surface_2d"] = {
                    "available": True,
                    "tenors": _json_safe(tenors),
                    "spot": _json_safe(spot),
                    "chart_path": surf_path,
                }
            else:
                print(
                    f"  Could not build a 2D vol surface for {ticker} "
                    f"(insufficient expiries/data)."
                )
        except Exception as e:
            print(f"  2D vol surface failed: {e}")
            _note_error("vol_surface_2d", e)

    if run_vrp_term_structure:
        print("\n[Running] VRP Term Structure (1-12mo)")
        try:
            import vrp_term_structure as vts
            from jump_diffusion.models import ALL_MODELS, BatesModel
            from thetadata_client import ThetaDataController

            vrp_jump_model_cls = next(
                (m for m in ALL_MODELS if m.name == JUMP_MODEL_DEFAULT), BatesModel
            )
            td_vrp = ThetaDataController()
            try:
                vrp_spot = td_vrp.fetch_spot_price(ticker)
                vrp_q = td_vrp.fetch_dividend_yield(ticker)
                vrp_r = td_vrp.fetch_risk_free_rate(0.25) or 0.05
                vrp_result = vts.compute_vrp_term_structure(
                    ticker,
                    td_vrp,
                    vrp_spot,
                    vrp_r,
                    vrp_q,
                    jump_model_cls=vrp_jump_model_cls,
                )
            finally:
                td_vrp.close()
            # Print the table on the suite's own console block. Until this
            # existed the term structure only ever reached the PDF section
            # and vol_result.json, so a headless run's log gave no way to
            # tell a computed term structure from a skipped one.
            print(
                f"\n  {'Tenor':<6} {'Expiry':<10} {'FairVol%':<10} "
                f"{'ATM IV%':<10} {'VRP%':<10} {'RV30%':<10}"
            )
            print("  " + "-" * 58)

            def _fmt(v, spec=".2f"):
                # Failed tenors come back as NaN by design (see
                # VrpTermPoint) -- render the gap, don't print 'nan'.
                return format(v, spec) if not math.isnan(v) else "N/A"

            for p in vrp_result.points:
                print(
                    f"  {p.expiry_label:<6} {p.expiry_date:<10} "
                    f"{_fmt(p.fair_vol_pct):<10} {_fmt(p.atm_iv_pct):<10} "
                    f"{_fmt(p.vrp_pct, '+.2f'):<10} {_fmt(p.rv_30d_pct):<10}"
                )
            print(f"  Term structure shape: {vrp_result.shape}")

            ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            vrp_path = os.path.join(
                out_root, f"{ticker}_vrp_term_structure_{ts_tag}.png"
            )
            vts.plot_vrp_term_structure(vrp_result, vrp_path)
            produced.append(vrp_path)
            sections.append(
                {
                    "title": f"VRP Term Structure: {ticker} (shape={vrp_result.shape})",
                    "text": "\n".join(
                        f"{p.expiry_label}: fair={p.fair_vol_pct:.2f}% atm={p.atm_iv_pct:.2f}% "
                        f"vrp={p.vrp_pct:+.2f}pp rv30={p.rv_30d_pct:.2f}%"
                        for p in vrp_result.points
                    ),
                    "images": [vrp_path],
                }
            )
            artifacts["vrp_term_structure"] = {
                "available": True,
                "shape": vrp_result.shape,
                "points": [_json_safe(vars(p)) for p in vrp_result.points],
                "chart_path": vrp_path,
            }
        except Exception as e:
            print(f"  VRP term structure failed: {e}")
            _note_error("vrp_term_structure", e)

    # This is a SEPARATE, narrower step from the Group Screener above (Step
    # 0): it only ever screens the one focus ticker, so its table always has
    # one row. That's by design, not a bug -- but rendered under the generic
    # "Screener Score" label it reads as the group screener silently breaking
    # down to one ticker. Labeled explicitly as a recheck, and skipped
    # entirely when the focus ticker's score is already sitting in the group
    # screener's table above. See FIX_PLAN_20260725.md, issue 2.
    if group_screener_ran and ticker in group_tickers:
        print(
            f"\n[Skipping] Focus-Ticker Screener Recheck for {ticker} -- "
            f"already scored in the Group Screener table above."
        )
        sections.append(
            {
                "title": f"Screener Score: {ticker} (see Group Screener above)",
                "text": f"{ticker} was already scored as part of the Group Screener "
                f"run in this session; see that section's table for its "
                f"row rather than re-running a single-ticker screen.",
                "images": [],
            }
        )
    else:
        print("\n[Running] Focus-Ticker Screener Recheck (post-basket)")
        try:
            import variance_swap_screener as vss

            r = vss.screen_ticker(ticker, target_years, expiration=expiration)
            if r:
                vss.print_screener_table([r])
                interp = (
                    f"{ticker}: Score={r.score:.1f} ({r.signal}), VRP={r.vrp_pct:+.1f}pp, "
                    f"Convexity={r.convexity_pct:.1f}pp, Skew={r.skew_bias:.2f}, Tail={r.tail_mass:.1%}"
                )
                sections.append(
                    {
                        "title": f"Focus-Ticker Screener Recheck: {ticker}",
                        "text": interp,
                        "images": [],
                    }
                )
            else:
                print(f"  No screener result for {ticker}.")
        except Exception as e:
            print(f"  Screener failed: {e}")
            _note_error("screener_recheck", e)

    print(f"\n[Running] Dealer Positioning (sign_model={sign_model})")
    dp_result = None
    dealer_positioning_ok = False
    try:
        files, interp, dp_result = _run_production_dealer_positioning(
            ticker=ticker,
            target_years=target_years,
            output_dir=out_root,
            expiration=expiration,
            sign_model=sign_model,
            jump_variance_share=(jump_diffusion_result or {}).get(
                "jump_variance_share"
            ),
        )
        produced.extend(files)
        sections.append(
            {
                "title": f"Dealer Positioning: {ticker} (sign_model={sign_model})",
                "text": interp or "",
                "images": [f for f in files if f.lower().endswith(".png")],
            }
        )
        if hasattr(dp_result, "snapshot"):
            total_net_dollar_gamma = dp_result.snapshot.gex()
            artifacts["dealer_positioning"] = {
                "available": dp_result.status == "available",
                "engine": "expiry_book",
                "sign_model": "expiry_book",
                "units": dp_result.units,
                "provenance": dp_result.provenance,
                "spot": dp_result.spot,
                "expiry": dp_result.expiry,
                "gex": total_net_dollar_gamma,
                "dex": dp_result.snapshot.dex(),
                "execution_locus": vars(dp_result.execution_locus),
                "structural": vars(dp_result.structural),
                # shared/schemas.py::validate_vol_result's dealer_positioning
                # contract predates the expiry_book engine and still requires
                # these four scalar keys when available=True -- without them
                # self-validation below fails and _error_vol_result() discards
                # this entire payload (vol_surface/correlation/GARCH included),
                # not just the dealer-positioning block. total_net_gamma is the
                # raw (non-dollarized) net gamma; hedge_requirement matches the
                # legacy dealer_positioning.py convention (CLAUDE.md: "hedge_
                # requirement = abs(net_dollar_gamma * 0.01)"); gamma_flip_level
                # is the execution-locus zero-gamma crossing already computed
                # as local_gamma_boundary.
                "total_net_gamma": dp_result.snapshot.net("gamma"),
                "total_net_dollar_gamma": total_net_dollar_gamma,
                "hedge_requirement": abs(total_net_dollar_gamma * 0.01),
                "gamma_flip_level": dp_result.execution_locus.local_gamma_boundary,
                "residual_vanna_inventory": dp_result.residual_vanna_inventory,
                "vanna_flow_7d": dp_result.vanna_flow_live,
                "vanna_flow_provenance": dp_result.vanna_flow_provenance,
                "d_iv_used": dp_result.d_iv_used,
                "flow_provenance": getattr(dp_result, "flow_provenance", None),
                "flow_volume_rows": getattr(dp_result, "flow_volume_rows", 0),
            }
            artifacts["gamma_records"] = []
            artifacts["gamma_records_total"] = 0
            artifacts["gamma_records_truncated"] = False
            artifacts["gamma_records_csv"] = None
        else:
            # Test/comparison adapters may still supply the historical result
            # shape. Normalize it here without making that shape a production
            # dependency or reopening the legacy live caller.
            gamma_csv = next(
                (f for f in files if str(f).lower().endswith(".csv")), None
            )
            artifacts["dealer_positioning"] = _dealer_positioning_summary(
                dp_result, "expiry_book", csv_path=gamma_csv
            )
            records, total, truncated = _gamma_records_payload(dp_result)
            artifacts["gamma_records"] = records
            artifacts["gamma_records_total"] = total
            artifacts["gamma_records_truncated"] = truncated
            artifacts["gamma_records_csv"] = gamma_csv
        dealer_positioning_ok = True
    except Exception as e:
        print(f"  Dealer positioning failed: {e}")
        _note_error("dealer_positioning", e)

    # ---- Step 5: options chain scanner on the resolved expiry ----
    if run_options_chain and not dealer_positioning_ok:
        # Step 4 failed (see the except block above) -- dp_result may be None
        # or may hold a partial value, but either way there's no trustworthy
        # shared dealer-engine result. run_chain_scanner would happily make
        # its OWN independent fetch_production_result call when
        # dealer_result=None, which defeats
        # the whole point of sharing step 4's result (see the "two-vanna" bug
        # note below) and can render a misleading vanna chart -- e.g. a
        # single-strike bar -- that looks legitimate even though the primary
        # dealer-positioning step already failed for this ticker/expiry.
        # Skip it and report the dependency plainly instead of masking the
        # upstream failure.
        print(
            "\n[5/5] Skipping Options Chain Scanner: dealer positioning "
            "(step 4) failed, so no shared dealer-engine result is available "
            "to plot the vanna panel from."
        )
        artifacts["chain_scan"] = {
            "available": False,
            "error": "dealer_positioning_unavailable",
        }
    elif run_options_chain:
        print(f"\n[5/5] Running Options Chain Scanner for {ticker} @ {expiration}...")
        try:
            import options_chain_scanner as ocs

            # Share the dealer-positioning engine's own result (step 4 just
            # succeeded, since dp_result is not None here) so the scanner's
            # vanna panel matches the 4-panel dealer chart exactly instead of
            # computing a second, independent vanna series -- see
            # options_chain_scanner.compute_vanna_positioning's docstring for
            # the "two-vanna" bug this closes.
            files, interp, scan_result = ocs.run_chain_scanner(
                ticker,
                target_years,
                expiration=expiration,
                output_dir=out_root,
                dealer_result=dp_result,
                jump_risk_signal=(
                    # jump_variance_share is only populated when
                    # JUMP_MODEL_DEFAULT is Bates (see
                    # _calibrate_default_jump_model); None otherwise, and
                    # StrategyRecommender's bonus math assumes a numeric
                    # jump_variance_share when a signal dict is passed at
                    # all, so omit the signal entirely rather than pass None.
                    {
                        "jump_variance_share": jump_diffusion_result[
                            "jump_variance_share"
                        ]
                    }
                    if (jump_diffusion_result or {}).get("jump_variance_share")
                    is not None
                    else None
                ),
            )
            produced.extend(files)
            sections.append(
                {
                    "title": f"Options Chain Scan: {ticker} {expiration} ({scan_result.verdict})",
                    "text": interp or "",
                    "images": [f for f in files if f.lower().endswith(".png")],
                }
            )
            artifacts["chain_scan"] = {
                "verdict": _json_safe(getattr(scan_result, "verdict", None)),
                "expiration": expiration,
                "strategies": getattr(
                    scan_result, "strategies", []
                ),  # Add strategies from scan
            }

            # Add Strategy Recommendations section to PDF
            strategies = getattr(scan_result, "strategies", [])
            if strategies:
                strat_text = (
                    f"Strategy Recommendations for {ticker} ({scan_result.verdict})\n"
                )
                strat_text += "=" * 60 + "\n\n"
                for i, strat in enumerate(strategies, 1):
                    strat_text += (
                        f"{i}. {strat.get('strategy_type', 'Unknown').upper()}\n"
                    )
                    strat_text += f"   Vol Regime: {strat.get('vol_regime', 'N/A')}\n"
                    strat_text += f"   Rank Score: {strat.get('rank_score', 0):.2f}\n"
                    strat_text += f"   Rationale: {strat.get('rationale', 'N/A')}\n"

                    # Add legs
                    legs = strat.get("legs", [])
                    if legs:
                        strat_text += "   Legs:\n"
                        for leg in legs:
                            qty_str = (
                                f"+{leg.get('quantity')}"
                                if leg.get("quantity", 0) > 0
                                else f"{leg.get('quantity')}"
                            )
                            strat_text += f"     {qty_str} {leg.get('instrument_type', '?').upper()} @ ${leg.get('strike', 0):.2f}\n"

                    # Add Greeks
                    greeks = strat.get("greeks_summary", {})
                    if greeks:
                        strat_text += f"   Greeks: Δ={greeks.get('delta', 0):.3f}, Γ={greeks.get('gamma', 0):.4f}, "
                        strat_text += f"Θ={greeks.get('theta', 0):.3f}, V={greeks.get('vega', 0):.3f}\n"
                    strat_text += "\n"

                sections.append(
                    {
                        "title": "Strategy Recommendations",
                        "text": strat_text,
                        "images": [],
                    }
                )

        except Exception as e:
            print(f"  Chain scanner failed: {e}")
            _note_error("options_chain_scanner", e)
    else:
        print("\n[5/5] Skipping Options Chain Scanner (disabled for this run).")

    artifacts["produced_files"] = list(produced)
    return produced, sections, artifacts


def run_unified_flow():
    print("=" * 60)
    print("  VOLATILITY SUITE — Unified Cross-Suite Run")
    print("=" * 60)

    load_mode = (
        _get_noninteractive("_load_mode")
        or _ni_input(
            "Input mode: (1) manual focus ticker, (2) highlighted ticker pack [default 1]: "
        ).strip()
    )
    if str(load_mode).strip() == "2":
        pack_ctx = _load_ticker_pack_interactive()
    else:
        pack_ctx = None
    ticker = (pack_ctx or {}).get("focus_ticker") or prompt_focus_ticker()
    if pack_ctx:
        print(f"\nUsing focus ticker from pack: {ticker}")

    group_tickers, use_pack_basket, chosen_index, known_weight, top_n, _matches = (
        _resolve_ticker_universe(ticker, pack_ctx)
    )

    import expiry_selector
    from thetadata_client import ThetaDataController

    print()
    td_for_expiry = ThetaDataController()
    try:
        if _get_noninteractive("_expiry") or _get_noninteractive("_target_years"):
            expiration, target_years = expiry_selector.choose_expiry_noninteractive(
                td_for_expiry,
                ticker,
                expiry=_get_noninteractive("_expiry"),
                target_years=float(_get_noninteractive("_target_years"))
                if _get_noninteractive("_target_years")
                else None,
            )
        else:
            expiration, target_years = expiry_selector.choose_expiry_interactive(
                td_for_expiry, ticker
            )
    finally:
        td_for_expiry.close()

    # Same prompts run_focus_workflow asks, so "unified" actually runs the
    # same analysis mode 1 does. This mode used to skip these entirely and
    # silently default the dealer-positioning sign model to v1 and the
    # options chain scanner to off. See FIX_PLAN_20260725.md.
    sign_model, run_options_chain = _prompt_sign_model_and_options_chain(pack_ctx)
    run_vol_surface_2d, run_vrp_term_structure, run_sentiment_backtest = (
        _prompt_extra_analytics(pack_ctx)
    )

    option_type = _choose_option_type()
    strike = _choose_optional_strike()
    run_options_suite = _prompt_yes_no(
        "Run Options_Suite after writing context?", default=False
    )
    run_var_suite = _prompt_yes_no(
        "Run VaR_Tools_Simulations after writing context?", default=False
    )
    compile_pdf = _prompt_yes_no("Compile outputs into single PDF?", default=False)

    out_root = timestamped_output_dir()
    os.environ["VS_OUTPUT_DIR"] = out_root
    print(f"\nOutputs will be written to: {out_root}")

    tickers, weights = _build_basket(
        ticker, use_pack_basket, group_tickers, chosen_index, top_n, known_weight
    )

    # ---- Run the SAME analysis pipeline mode 1 runs ----
    produced, sections, artifacts = _run_core_analysis(
        ticker=ticker,
        pack_ctx=pack_ctx,
        group_tickers=group_tickers,
        use_pack_basket=use_pack_basket,
        tickers=tickers,
        weights=weights,
        chosen_index=chosen_index,
        target_years=target_years,
        expiration=expiration,
        sign_model=sign_model,
        run_options_chain=run_options_chain,
        out_root=out_root,
        run_vol_surface_2d=run_vol_surface_2d,
        run_vrp_term_structure=run_vrp_term_structure,
        run_sentiment_backtest=run_sentiment_backtest,
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
        sentiment_manifest_path=(pack_ctx or {}).get("manifest_path")
        or _default_pack_manifest_path(),
        sentiment_pack_json_path=sentiment_pack_json,
        sentiment_group_id=sentiment_group_id,
        sentiment_ranked_tickers=group_tickers,
        garch_conditional_vol=artifacts.get("garch_conditional_vol"),
        jump_diffusion=artifacts.get("jump_diffusion"),
        run_options_suite=run_options_suite,
        run_var_suite=run_var_suite,
        compile_pdf=compile_pdf,
        options_suite_root=options_root,
        var_suite_root=var_root,
        sentiment_suite_root=sentiment_root,
    )

    # Populate strategies from chain scan into shared context (Task 3 integration)
    if (
        isinstance(artifacts.get("chain_scan"), dict)
        and "strategies" in artifacts["chain_scan"]
    ):
        context["strategies"] = artifacts["chain_scan"]["strategies"]

    context_path = write_suite_context(
        context, os.path.join(out_root, "suite_context.json")
    )
    print(f"Wrote shared handoff context: {context_path}")

    child_results: list[dict[str, Any]] = []
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
            pdf_path = os.path.join(
                out_root,
                f"volatility_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            )
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
        status = (
            "ok" if result["returncode"] == 0 else f"failed(rc={result['returncode']})"
        )
        summary_lines.append(f"{result['suite']}_suite={status}")
    summary_text = "\n".join(summary_lines)

    summary_path = os.path.join(out_root, "unified_run_summary.txt")
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(summary_text + "\n\n")
    print("\nUnified run summary:")
    print(summary_text)
    print(f"Summary file: {summary_path}")

    # Mode 2 publishes the same vol_result.json context mode does. The
    # interactive unified flow and the headless orchestrator therefore leave
    # behind an identical, validated artifact, so downstream tooling never has
    # to care which one produced the run.
    try:
        vol_result_path = _write_vol_result(
            os.path.join(out_root, "vol_result.json"),
            _apply_instrument_resolver(
                _build_vol_result(
                    artifacts=artifacts,
                    context=context,
                    output_dir=out_root,
                    produced=produced,
                    summary=summary_text,
                )
            ),
        )
        print(f"Wrote vol result: {vol_result_path}")
    except Exception as e:
        print(f"Could not write vol_result.json: {e}", file=sys.stderr)

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

    load_mode = (
        _get_noninteractive("_load_mode")
        or _ni_input(
            "Input mode: (1) manual focus ticker, (2) highlighted ticker pack [default 1]: "
        ).strip()
    )
    if str(load_mode).strip() == "2":
        pack_ctx = _load_ticker_pack_interactive()
    else:
        pack_ctx = None
    ticker = (pack_ctx or {}).get("focus_ticker") or prompt_focus_ticker()
    if pack_ctx:
        print(f"\nUsing focus ticker from pack: {ticker}")

    group_tickers, use_pack_basket, chosen_index, known_weight, top_n, _matches = (
        _resolve_ticker_universe(ticker, pack_ctx)
    )

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
        if _get_noninteractive("_expiry") or _get_noninteractive("_target_years"):
            expiration, target_years = expiry_selector.choose_expiry_noninteractive(
                td_for_expiry,
                ticker,
                expiry=_get_noninteractive("_expiry"),
                target_years=float(_get_noninteractive("_target_years"))
                if _get_noninteractive("_target_years")
                else None,
            )
        else:
            expiration, target_years = expiry_selector.choose_expiry_interactive(
                td_for_expiry, ticker
            )
    finally:
        td_for_expiry.close()

    # Dealer-positioning sign model + options chain scanner toggle: same
    # prompts run_unified_flow now asks too (see
    # _prompt_sign_model_and_options_chain), so the two entry points can't
    # drift on this again.
    sign_model, run_options_chain = _prompt_sign_model_and_options_chain(pack_ctx)
    run_vol_surface_2d, run_vrp_term_structure, run_sentiment_backtest = (
        _prompt_extra_analytics(pack_ctx)
    )

    pdf_choice = (
        _ni_input("Compile outputs into single PDF? (y/n, default n): ").strip().lower()
        or "n"
    )

    out_root = timestamped_output_dir()
    print(f"\nOutputs will be written to: {out_root}")
    os.environ["VS_OUTPUT_DIR"] = out_root

    tickers, weights = _build_basket(
        ticker, use_pack_basket, group_tickers, chosen_index, top_n, known_weight
    )

    produced, sections, _artifacts = _run_core_analysis(
        ticker=ticker,
        pack_ctx=pack_ctx,
        group_tickers=group_tickers,
        use_pack_basket=use_pack_basket,
        tickers=tickers,
        weights=weights,
        chosen_index=chosen_index,
        target_years=target_years,
        expiration=expiration,
        sign_model=sign_model,
        run_options_chain=run_options_chain,
        out_root=out_root,
        run_vol_surface_2d=run_vol_surface_2d,
        run_vrp_term_structure=run_vrp_term_structure,
        run_sentiment_backtest=run_sentiment_backtest,
    )

    # ---- Summary / optional PDF ----
    print("\nRun complete.")
    all_files = collect_files(os.environ["VS_OUTPUT_DIR"])
    print(f"Files in output folder ({os.environ['VS_OUTPUT_DIR']}):")
    for f in all_files:
        print(f"  {f}")

    if pdf_choice == "y":
        try:
            pdf_path = os.path.join(
                os.environ["VS_OUTPUT_DIR"],
                f"volatility_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            )
            compose_pdf_report(pdf_path, sections)
            print(f"Compiled PDF: {pdf_path}")
        except Exception as e:
            print(f"PDF compilation failed: {e}")

    print("\nDone.")


# ---------------------------------------------------------------------------
# Non-interactive context mode
# ---------------------------------------------------------------------------

_VALID_SIGN_MODELS = {"expiry_book"}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


# Task 6 (Modularization Overhaul, Phase 3): fixed slug mapping from
# context["modules"] entries to _run_core_analysis's five gated-step
# booleans. These slugs are NOT independently runnable ModuleSpecs in the
# usual Task 3/4/5 sense (see module_registry.py's group_screener/
# vol_surface_2d/vrp_term_structure/sentiment_backtest entries, which exist
# purely for --list-modules/dashboard discoverability and raise
# NotImplementedError if .run() is called directly) -- their real execution
# only happens inside _run_core_analysis's shared pipeline, which is why
# run_context_mode resolves them by direct slug-string membership below
# rather than through shared.module_registry.
_CORE_ANALYSIS_MODULE_SLUGS = {
    "chain_scanner": "run_options_chain",
    "group_screener": "run_group_screener",
    "vol_surface_2d": "run_vol_surface_2d",
    "vrp_term_structure": "run_vrp_term_structure",
    "sentiment_backtest": "run_sentiment_backtest",
}


def _resolve_core_analysis_flags(context: dict[str, Any]) -> dict[str, bool]:
    """Resolve the five _run_core_analysis gated-step booleans for
    run_context_mode.

    Back-compat (non-negotiable, see Task 6 brief): when context["modules"]
    is absent or an empty list, this returns EXACTLY the env-var-driven
    values run_context_mode has always used -- same _env_flag() reads, same
    defaults (VS_RUN_VRP_TERM_STRUCTURE defaults True; the other four
    default False). An empty list is treated the same as absent (falls back
    to env vars), not as "select nothing".

    When context["modules"] is a non-empty list, every one of the five
    booleans is resolved purely from slug membership in that list -- env
    vars are not consulted at all for this call, and nothing not explicitly
    selected runs, including vrp_term_structure. This is a deliberate
    choice (see Task 6 brief report): context["modules"] is an explicit
    selection API (the same one Task 2's run_selected_modules / the
    dashboard checkbox UI use), and "selected modules run, nothing else
    does" is the least surprising contract for that API -- a caller who
    wants the VRP default under module-selection should list
    "vrp_term_structure" explicitly, the same way they'd list any other
    slug.
    """
    modules = context.get("modules")
    if not modules:
        return {
            "run_options_chain": _env_flag("VS_RUN_CHAIN_SCANNER", False),
            "run_group_screener": _env_flag("VS_RUN_GROUP_SCREENER", False),
            "run_vol_surface_2d": _env_flag("VS_RUN_VOL_SURFACE_2D", False),
            "run_vrp_term_structure": _env_flag("VS_RUN_VRP_TERM_STRUCTURE", True),
            "run_sentiment_backtest": _env_flag("VS_RUN_SENTIMENT_BACKTEST", False),
        }
    selected = {str(m).strip().lower() for m in modules}
    return {
        flag_name: slug in selected
        for slug, flag_name in _CORE_ANALYSIS_MODULE_SLUGS.items()
    }


def _compact_expiry(iso_or_compact: str) -> str:
    """suite_context stores expiration_date as ISO 'YYYY-MM-DD' (see
    suite_context._normalize_expiration). Every analysis module in this suite
    speaks ThetaData's compact 'YYYYMMDD'. Convert at exactly this boundary
    rather than letting either format leak into the other side."""
    raw = str(iso_or_compact).strip()
    return raw.replace("-", "")


def run_context_mode(context_path: str, context_out: str | None = None) -> int:
    """Run the full analysis pipeline with zero prompts, driven by a
    suite_context.json, and publish vol_result.json.

    Return codes match the other suites' context modes: 0 success, 2 the
    context itself was unusable, 1 the run failed. In every case a schema-valid
    vol_result is written first, because the caller's failure path reads that
    file -- exiting non-zero with nothing on disk is what made the old
    stdin-scripted integration so hard to diagnose.

    Child suites are deliberately NOT launched here even when
    `controls.run_options_suite` / `run_var_suite` are true. In context mode the
    caller is an orchestrator that runs Options_Suite and VaR itself off the
    same context; honouring those flags here would run each of them twice. The
    interactive unified flow, where nothing else is going to launch them, still
    does.
    """
    context: dict[str, Any] | None = None
    try:
        context = read_suite_context(context_path)
    except Exception as exc:
        out_path = _resolve_context_out_path(context_path, None, context_out)
        _write_vol_result(
            out_path,
            _error_vol_result(
                ticker="", error=f"Unusable context {context_path}: {exc}"
            ),
        )
        print(f"Unusable context {context_path}: {exc}", file=sys.stderr)
        return 2

    # Task C3: When context lacks run_id or output_dir (standalone --context
    # mode), mint fresh values using the same format as run_selected_modules.
    # This ensures all files land in one run directory regardless of how the
    # context was created.
    import datetime as dt
    import secrets
    if "run_id" not in context or "output_dir" not in context:
        now = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        ran = secrets.token_hex(2)
        # Honor an existing run_id when only output_dir is missing, so all
        # files land in the SAME run directory (card requirement).
        run_id = context.get("run_id") or f"{now}-{ran}"
        context["run_id"] = run_id

        # Use the same output directory logic as run_selected_modules
        from shared.artifact_paths import repo_root
        out_dir = repo_root() / "outputs" / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        context["output_dir"] = str(out_dir)

    focus = context["focus"]
    basket = context["basket"]
    ticker = str(focus["ticker"]).upper()
    target_years = float(focus["target_years"])
    expiration = _compact_expiry(focus["expiration_date"])

    out_root = (
        context.get("output_dir")
        or os.environ.get("VS_OUTPUT_DIR")
        or timestamped_output_dir()
    )
    os.makedirs(out_root, exist_ok=True)
    os.environ["VS_OUTPUT_DIR"] = out_root

    out_path = _resolve_context_out_path(context_path, context, context_out)

    tickers = [str(t).upper() for t in basket["tickers"]]
    weights = [float(w) for w in basket["weights"]]
    chosen_index = str(basket["index_ticker"]).upper()
    group_tickers = [
        str(t).upper()
        for t in (context.get("sentiment", {}).get("ranked_tickers") or [])
    ]

    # The prompts mode 1/2 ask become environment knobs here. Defaults are the
    # ones the interactive flow defaults to, except the two that cost a lot of
    # wall-clock time in a headless batch (the group screener re-runs
    # replication for every ranked ticker; the chain scanner pulls another full
    # chain), which are opt-in.
    # Production sign-model selection is locked to the expiry-book engine.
    sign_model = "expiry_book"
    # Task 6 (Modularization Overhaul, Phase 3): when context["modules"] is a
    # non-empty list, these five booleans come from slug membership in that
    # list instead of the VS_RUN_* env vars -- see
    # _resolve_core_analysis_flags's docstring for the back-compat contract
    # (absent/empty "modules" is byte-identical to the historical env-var
    # path, including VS_RUN_VRP_TERM_STRUCTURE's True default) and the
    # VRP-under-selection judgment call.
    _core_flags = _resolve_core_analysis_flags(context)
    run_options_chain = _core_flags["run_options_chain"]
    run_group_screener = _core_flags["run_group_screener"]
    run_vol_surface_2d = _core_flags["run_vol_surface_2d"]
    run_vrp_term_structure = _core_flags["run_vrp_term_structure"]
    run_sentiment_backtest = _core_flags["run_sentiment_backtest"]

    print("=" * 60)
    print("  VOLATILITY SUITE — context mode (non-interactive)")
    print("=" * 60)
    print(f"  context      : {context_path}")
    print(f"  run_id       : {context['run_id']}")
    print(
        f"  focus        : {ticker} @ {focus['expiration_date']} (T={target_years:.4f}yr)"
    )
    print(f"  index/basket : {chosen_index} / {len(tickers)} names")
    print(f"  sign_model   : {sign_model}")
    print(f"  output_dir   : {out_root}")
    print(f"  context_out  : {out_path}")

    try:
        produced, sections, artifacts = _run_core_analysis(
            ticker=ticker,
            # pack_ctx=None keeps every pack-specific prompt out of the code
            # path; the pack's ranked tickers still arrive via group_tickers.
            pack_ctx=None,
            group_tickers=group_tickers,
            # The basket came from the context, already resolved by whoever
            # built it -- do not re-derive it from index membership here.
            use_pack_basket=False,
            tickers=tickers,
            weights=weights,
            chosen_index=chosen_index,
            target_years=target_years,
            expiration=expiration,
            sign_model=sign_model,
            run_options_chain=run_options_chain,
            out_root=out_root,
            run_group_screener=run_group_screener,
            run_vol_surface_2d=run_vol_surface_2d,
            run_vrp_term_structure=run_vrp_term_structure,
            run_sentiment_backtest=run_sentiment_backtest,
        )
    except Exception as exc:
        _write_vol_result(
            out_path,
            _error_vol_result(
                ticker=ticker,
                error=f"{type(exc).__name__}: {exc}",
                output_dir=out_root,
                run_id=context.get("run_id"),
            ),
        )
        print(f"Context-mode run failed: {exc}", file=sys.stderr)
        return 1

    if context.get("controls", {}).get("compile_pdf"):
        try:
            pdf_path = os.path.join(
                out_root,
                f"volatility_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            )
            compose_pdf_report(pdf_path, sections)
            produced.append(pdf_path)
            print(f"Compiled PDF: {pdf_path}")
        except Exception as e:
            print(f"PDF compilation failed: {e}", file=sys.stderr)

    summary = "\n".join(
        [
            f"run_id={context['run_id']}",
            f"focus={ticker} {focus['expiration_date']} {focus['option_type']}",
            f"analysis_output_files={len(produced)}",
            f"failed_steps={len(artifacts.get('errors', []))}",
        ]
    )

    payload = _apply_instrument_resolver(
        _build_vol_result(
            artifacts=artifacts,
            context=context,
            output_dir=out_root,
            produced=produced,
            summary=summary,
        )
    )
    written = _write_vol_result(out_path, payload)
    print(f"\nWrote vol result: {written}")
    print(summary)
    return 0 if payload.get("status") == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="volatility_suite.py",
        description="Volatility suite: interactive focus/unified workflows, or a "
        "non-interactive context-mode run driven by suite_context.json.",
    )
    parser.add_argument(
        "--context",
        default=None,
        help="Path to a suite_context.json. Supplying it selects non-interactive "
        "context mode: every prompt is answered from the context and nothing "
        "is read from stdin.",
    )
    parser.add_argument(
        "--context-out",
        default=None,
        help="Where to write vol_result.json (default: <context output_dir>/vol_result.json).",
    )
    loop_group = parser.add_mutually_exclusive_group()
    loop_group.add_argument(
        "--no-loop",
        dest="loop",
        action="store_false",
        default=False,
        help="Run one workflow and exit. This is the default and always has "
        "been; the flag exists so every suite accepts the same headless "
        "flag set, and so a caller can state the intent explicitly rather "
        "than depending on the default. Context mode is unconditionally "
        "single-pass, so it is a no-op there.",
    )
    loop_group.add_argument(
        "--loop",
        dest="loop",
        action="store_true",
        help="Return to the run-mode menu after each workflow instead of "
        "exiting; 'q' quits.",
    )
    parser.add_argument(
        "--mode",
        choices=["1", "2"],
        default=None,
        help="Preselect the interactive run mode (1 = focus workflow, "
        "2 = unified cross-suite run) instead of being prompted for it.",
    )
    parser.add_argument(
        "--instrument-resolver",
        default=None,
        help="Name of an IdentifierResolver (see instrument_resolver.py) used to "
        "enrich vol_result.json with cross-source normalized instrument "
        "identifiers for the focus/index tickers. Unset (default) skips "
        "enrichment entirely -- vol_result.json is unchanged.",
    )
    # ------------------------------------------------------------------
    # Non-interactive workflow overrides
    # ------------------------------------------------------------------
    ni = parser.add_argument_group(
        "non-interactive workflow overrides",
        "When any of these flags is supplied, the corresponding prompt is "
        "bypassed and the flag value is used instead.  Supplying enough of "
        "them makes the run fully headless -- no stdin required.",
    )
    ni.add_argument(
        "--ticker", default=None, help="Focus ticker (bypasses 'Focus ticker' prompt)."
    )
    ni.add_argument(
        "--index",
        default=None,
        help="Benchmark/sector ETF ticker (bypasses index choice prompt).",
    )
    ni.add_argument(
        "--basket-size",
        type=int,
        default=None,
        help="Number of index constituents to pull (bypasses basket size prompt).",
    )
    ni.add_argument(
        "--expiry",
        default=None,
        help="Expiration date as YYYYMMDD or YYYY-MM-DD.  When supplied, "
        "the interactive expiry picker is skipped and target-years is "
        "computed from today.",
    )
    ni.add_argument(
        "--target-years",
        type=float,
        default=None,
        help="Target time-to-expiry in years (e.g. 0.25).  Used with "
        "--expiry or to auto-select the nearest expiry.",
    )
    ni.add_argument(
        "--option-type",
        default=None,
        choices=["call", "put"],
        help="Option type for shared context (bypasses option-type prompt).",
    )
    ni.add_argument(
        "--strike",
        type=float,
        default=None,
        help="Optional strike for shared context (bypasses strike prompt).",
    )
    pack_group = ni.add_mutually_exclusive_group()
    pack_group.add_argument(
        "--pack",
        action="store_true",
        default=None,
        help="Use the highlighted ticker pack (selects pack mode).",
    )
    pack_group.add_argument(
        "--no-pack",
        action="store_false",
        dest="pack",
        help="Skip the highlighted ticker pack (manual ticker mode, default).",
    )
    ni.add_argument(
        "--pack-manifest-path",
        default=None,
        help="Path to the ticker-pack manifest JSON (bypasses manifest prompt).",
    )
    ni.add_argument(
        "--pack-index",
        type=int,
        default=None,
        help="1-based index into the manifest's packs list (bypasses pack-choice prompt).",
    )
    ni.add_argument(
        "--run-chain-scanner",
        action="store_true",
        default=None,
        help="Run the Options Chain Scanner step (y to the prompt).",
    )
    ni.add_argument(
        "--no-chain-scanner",
        action="store_false",
        dest="run_chain_scanner",
        help="Skip the Options Chain Scanner step (n to the prompt).",
    )
    ni.add_argument(
        "--run-group-screener",
        action="store_true",
        default=None,
        help="Run the variance screener on the full highlighted group first.",
    )
    ni.add_argument(
        "--no-group-screener",
        action="store_false",
        dest="run_group_screener",
        help="Skip the variance screener on the full highlighted group.",
    )
    ni.add_argument(
        "--run-2d-surface",
        action="store_true",
        default=None,
        help="Build the 2D vol surface (strike x tenor).",
    )
    ni.add_argument(
        "--no-2d-surface",
        action="store_false",
        dest="run_2d_surface",
        help="Skip the 2D vol surface.",
    )
    ni.add_argument(
        "--run-vrp",
        action="store_true",
        default=None,
        help="Run the VRP term structure (1-12mo).",
    )
    ni.add_argument(
        "--no-vrp",
        action="store_false",
        dest="run_vrp",
        help="Skip the VRP term structure.",
    )
    ni.add_argument(
        "--run-sentiment-backtest",
        action="store_true",
        default=None,
        help="Run the sentiment backtest on the highlighted-pack history.",
    )
    ni.add_argument(
        "--no-sentiment-backtest",
        action="store_false",
        dest="run_sentiment_backtest",
        help="Skip the sentiment backtest.",
    )
    ni.add_argument(
        "--compile-pdf",
        action="store_true",
        default=None,
        help="Compile outputs into a single PDF.",
    )
    ni.add_argument(
        "--no-compile-pdf",
        action="store_false",
        dest="compile_pdf",
        help="Skip PDF compilation.",
    )
    ni.add_argument(
        "--run-options-suite",
        action="store_true",
        default=None,
        help="Launch Options_Suite after writing context.",
    )
    ni.add_argument(
        "--no-options-suite",
        action="store_false",
        dest="run_options_suite",
        help="Skip launching Options_Suite.",
    )
    ni.add_argument(
        "--run-var-suite",
        action="store_true",
        default=None,
        help="Launch VaR_Tools_Simulations after writing context.",
    )
    ni.add_argument(
        "--no-var-suite",
        action="store_false",
        dest="run_var_suite",
        help="Skip launching VaR_Tools_Simulations.",
    )
    ni.add_argument(
        "--yes",
        action="store_true",
        default=None,
        help="Accept all yes/no defaults (equivalent to --run-chain-scanner "
        "--run-group-screener --run-2d-surface --run-vrp --compile-pdf "
        "--run-options-suite --run-var-suite).",
    )

    args = parser.parse_args(argv)

    # Apply --yes before individual flags so explicit flags can override it.
    if args.yes:
        for attr in (
            "run_chain_scanner",
            "run_group_screener",
            "run_2d_surface",
            "run_vrp",
            "compile_pdf",
            "run_options_suite",
            "run_var_suite",
        ):
            if getattr(args, attr) is None:
                setattr(args, attr, True)

    if args.instrument_resolver:
        os.environ["VS_INSTRUMENT_RESOLVER"] = args.instrument_resolver

    # Populate the non-interactive override dict so every prompt in the
    # workflow can check _ni_input() instead of blocking on stdin.
    # Keyed on the full "(e.g. MSFT)" prompt text, not just "Focus ticker" --
    # that shorter substring also matches _load_ticker_pack_interactive's
    # "Focus ticker from pack (number, default 1): " prompt, which expects a
    # numeric selection, not a ticker string.
    _set_override("Focus ticker (e.g. MSFT)", args.ticker)
    _set_override("Choose an index", args.index)
    _set_override("Enter an index/sector ETF ticker manually", args.index)
    _set_override(
        "Basket size", str(args.basket_size) if args.basket_size is not None else None
    )
    _set_override("Option type", args.option_type)
    _set_override(
        "Optional strike", str(args.strike) if args.strike is not None else None
    )
    _set_override("Ticker-pack manifest path", args.pack_manifest_path)
    _set_override(
        "Choose pack number",
        str(args.pack_index) if args.pack_index is not None else None,
    )
    _set_override(
        "Run Options Chain Scanner step",
        "y"
        if args.run_chain_scanner
        else "n"
        if args.run_chain_scanner is False
        else None,
    )
    _set_override(
        "Run variance screener",
        "y"
        if args.run_group_screener
        else "n"
        if args.run_group_screener is False
        else None,
    )
    _set_override(
        "Build 2D vol surface",
        "y" if args.run_2d_surface else "n" if args.run_2d_surface is False else None,
    )
    _set_override(
        "Run VRP term structure",
        "y" if args.run_vrp else "n" if args.run_vrp is False else None,
    )
    _set_override(
        "Run sentiment backtest",
        "y"
        if args.run_sentiment_backtest
        else "n"
        if args.run_sentiment_backtest is False
        else None,
    )
    _set_override(
        "Compile outputs into single PDF",
        "y" if args.compile_pdf else "n" if args.compile_pdf is False else None,
    )
    _set_override(
        "Run Options_Suite after writing context",
        "y"
        if args.run_options_suite
        else "n"
        if args.run_options_suite is False
        else None,
    )
    _set_override(
        "Run VaR_Tools_Simulations after writing context",
        "y" if args.run_var_suite else "n" if args.run_var_suite is False else None,
    )
    # Internal overrides (not tied to a specific prompt string).
    if args.expiry:
        _noninteractive["_expiry"] = args.expiry
    if args.target_years is not None:
        _noninteractive["_target_years"] = str(args.target_years)
    if args.pack is not None:
        _noninteractive["_load_mode"] = "2" if args.pack else "1"
    # The mode prompt is handled by args.mode; no override needed.

    # The orchestrator sets SUITE_CONTEXT_PATH/SUITE_CONTEXT_MODE in the child's
    # environment as well as passing the flags. Honouring the environment means
    # a caller that sets only the env vars still gets context mode rather than
    # silently blocking on the run-mode prompt with a closed stdin.
    context_path = args.context
    if not context_path and os.environ.get("SUITE_CONTEXT_MODE") == "1":
        context_path = os.environ.get("SUITE_CONTEXT_PATH") or None

    if context_path:
        return run_context_mode(context_path, args.context_out)

    if args.context_out:
        parser.error("--context-out requires --context (or SUITE_CONTEXT_MODE=1).")

    prompt = "Run mode: (1) standard focus workflow, (2) unified cross-suite run " + (
        "[default 1, q to quit]: " if args.loop else "[default 1]: "
    )
    while True:
        mode = args.mode if args.mode is not None else input(prompt).strip()
        if mode.lower() in {"q", "quit", "exit"}:
            return 0
        if mode == "2":
            run_unified_flow()
        else:
            run_focus_workflow()
        if not args.loop or args.mode is not None:
            return 0
        print()


if __name__ == "__main__":
    raise SystemExit(main())
