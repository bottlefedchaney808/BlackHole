"""whale_scanner.py

Pure classification of large single-contract ("whale") options premium into
a bullish/bearish/neutral bias for ONE day's chain. Ported from the
whale-flow leg of an external devnotes research package (2026-08-06 CARL
debate, `Direction/whale_scanner.py`) -- see
docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md for
what was ported, what was deliberately left out (the other 4 "Direction"
signals, the live-report NO_CALL gate, the AMD/SPY sign-caveat registry),
and why.

No network I/O here, unlike the source package's version: this repo's
backtest_stage3.py already fetches per-contract volume/close from the same
route it uses for greeks/IV (option_bulk_hist_eod), so this module only
classifies rows the caller already has -- zero new network calls.
"""
import os
from typing import Dict, Iterable, Optional, Tuple

WHALE_THRESHOLD = 25_000.0  # legacy absolute $ premium bar (volume * close * 100)
_CONTRACT_MULTIPLIER = 100  # options are 100 shares per contract
_CALL_PUT_RATIO = 1.2       # one side's premium must exceed the other's by this multiple to call a direction


def _effective_threshold(min_premium: float, threshold_bps: Optional[float],
                          price: Optional[float]) -> float:
    """Resolve the premium filter bar.

    `threshold_bps` (or the WHALE_THRESHOLD_BPS env var, when threshold_bps
    is None) switches to a premium-RELATIVE bar when it clears 0 AND a
    price is available: bar = bps/10_000 * price * 100 (bps of one
    contract's ATM notional). 0/unset, or a missing price, keeps the
    legacy absolute `min_premium` bar unchanged -- this mirrors the source
    package's E4 fix for the $25K bar being price-level correlated (easier
    to clear on richly-priced names).
    """
    bps = (float(os.environ.get("WHALE_THRESHOLD_BPS", "0") or 0)
           if threshold_bps is None else float(threshold_bps))
    if bps > 0 and price:
        return bps / 10_000.0 * float(price) * _CONTRACT_MULTIPLIER
    return min_premium


def classify_whale_bias(rows: Iterable[dict], min_premium: float = WHALE_THRESHOLD,
                         threshold_bps: Optional[float] = None,
                         price: Optional[float] = None) -> Tuple[str, Dict]:
    """Classify one day's chain rows into a whale-flow bias.

    `rows`: iterable of {'strike': float, 'right': 'C'|'P', 'volume': float,
    'close': float} for a SINGLE trading day -- a snapshot, not a
    cumulative window. Callers are responsible for pre-filtering to one day
    (backtest_stage3.py does this via its existing per-date grouping).

    Returns (bias, stats): bias is 'bullish' | 'bearish' | 'neutral';
    stats carries whale_calls/whale_puts/call_premium/put_premium for
    diagnostics. 'bullish' when call premium exceeds put premium by more
    than _CALL_PUT_RATIO, 'bearish' for the inverse, else 'neutral' --
    including when nothing in the chain clears the premium bar at all.
    Malformed rows (non-numeric volume/close) are skipped, never raise.
    """
    eff_threshold = _effective_threshold(min_premium, threshold_bps, price)

    whale_calls = whale_puts = 0
    call_premium = put_premium = 0.0
    for row in rows:
        try:
            volume = float(row.get('volume') or 0)
            close = float(row.get('close') or 0)
        except (TypeError, ValueError):
            continue
        premium = volume * close * _CONTRACT_MULTIPLIER
        if premium < eff_threshold:
            continue
        right = str(row.get('right', '')).upper()[:1]
        if right == 'C':
            whale_calls += 1
            call_premium += premium
        elif right == 'P':
            whale_puts += 1
            put_premium += premium

    if call_premium > put_premium * _CALL_PUT_RATIO:
        bias = 'bullish'
    elif put_premium > call_premium * _CALL_PUT_RATIO:
        bias = 'bearish'
    else:
        bias = 'neutral'

    stats = {
        'whale_calls': whale_calls, 'whale_puts': whale_puts,
        'call_premium': call_premium, 'put_premium': put_premium,
    }
    return bias, stats
