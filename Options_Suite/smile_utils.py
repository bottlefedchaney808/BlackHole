"""
smile_utils.py

Shared market-implied-vol-smile fetch, used by both SABRCalibrator
(SABRModel.py) and HestonCalibrator (MCHestonLSM.py). Both previously carried
their own near-identical copy of this fetch-and-parse-by-strike logic, and --
more importantly -- neither one computed an IV of its own when ThetaData's
vendor `implied_vol` field came back missing/null for a strike (common on
short-dated weeklies, e.g. a T=0.074y NVDA expiry): both just gave up on that
strike, and if too few strikes survived, fell all the way to a flat,
no-skew synthetic smile. That threw away real information sitting right in
the same bulk-greeks row (bid/ask) -- exactly what Standard/Leisen-Reimer's
own single-strike IV solvers already invert successfully. This module runs
that same American-price inversion (brute_force_lr) across every OTM strike
in the chain, so a sparse vendor IV field degrades to a real, skew-bearing
smile instead of a flat one whenever we still have a tradeable price to
invert.
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

from bruteforceimpliedvol import brute_force_lr

try:
    from thetadata_controller import ThetaDataController, strike_from_theta
    _THETADATA_AVAILABLE = True
except Exception:
    _THETADATA_AVAILABLE = False


def _row_mid_price(row: dict) -> Optional[float]:
    """Best available market price for one bulk-greeks row: mid of bid/ask,
    falling back to a lone bid, then to close/mark/last fields if the live
    quote itself is missing (illiquid deep-OTM strikes on some names)."""
    try:
        bid = row.get('bid')
        ask = row.get('ask')
        bid_f = float(bid) if bid not in (None, '') else None
        ask_f = float(ask) if ask not in (None, '') else None
        if bid_f and ask_f and bid_f > 0 and ask_f > 0:
            return (bid_f + ask_f) / 2.0
        if bid_f and bid_f > 0:
            return bid_f
    except (TypeError, ValueError):
        pass
    for key in ('close', 'mark', 'last'):
        try:
            v = row.get(key)
            if v not in (None, ''):
                v = float(v)
                if v > 0:
                    return v
        except (TypeError, ValueError):
            continue
    return None


# Above this relative bid-ask spread, a row's quote is untrustworthy enough
# that neither its vendor implied_vol field nor a price solved off its mid
# should be trusted -- this is the actual mechanism by which garbage IVs
# (e.g. 100%+ prints on a normally-30%-vol name) get into a calibration:
# a thin/no-market deep-OTM strike with a wide or stale bid-ask, not a
# genuine market move. (Confirmed live: ORCL, spot=115, strikes fetched out
# to 510 -- 4.4x spot -- with a reported IV range of 32%-133% and median
# 94%, which fed SABR a nonsensical ATM target and blew up its free-beta fit.
# ORCL does not trade 100%+ IV under normal conditions; illiquid deep-OTM
# marks do this routinely.) This is a liquidity filter on the INPUT data,
# not a fallback -- it drops untrustworthy quotes instead of calibrating
# against them and hoping for the best, or substituting a fabricated value
# for them.
MAX_RELATIVE_SPREAD = 0.60  # (ask - bid) / mid


def _quote_is_liquid(row: dict) -> bool:
    try:
        bid = row.get('bid')
        ask = row.get('ask')
        bid_f = float(bid) if bid not in (None, '') else None
        ask_f = float(ask) if ask not in (None, '') else None
    except (TypeError, ValueError):
        return True  # no bid/ask fields at all -- can't judge width, don't reject on this basis
    if not bid_f or not ask_f or bid_f <= 0 or ask_f <= 0:
        return True  # missing/zero quote -- handled (or dropped) elsewhere, not this filter's job
    mid = (bid_f + ask_f) / 2.0
    if mid <= 0:
        return True
    return ((ask_f - bid_f) / mid) <= MAX_RELATIVE_SPREAD


# A SECOND, complementary filter to the spread check above. Confirmed live
# (MU, spot=920.95, forward=926.35): the 60% relative-spread filter alone
# dropped only 28 of the far-wing strikes, leaving 238 strikes spanning
# K=5..2500 (moneyness 0.005x to 2.7x) and a reported IV range of 50%-480%
# feeding straight into SABR/Heston calibration. The spread filter misses
# these because a deep-OTM contract priced at, say, $0.05 bid / $0.07 ask
# LOOKS like a tight relative spread (28% of mid) while still being a
# minimum-tick, essentially-never-trades quote -- the price itself is so
# close to zero that inverting it for an implied vol is numerically
# ill-conditioned (BS/tree price is nearly flat in sigma way out there), so
# small quote noise maps to enormous swings in "implied vol". Spread-as-%%-
# of-mid cannot see that; distance from the forward can. This does not
# discard real skew (equity skew legitimately needs strikes well outside
# the ATM neighborhood to show its shape) -- it discards strikes so far
# outside the money that no reasonable liquid market exists on them at all.
MIN_MONEYNESS = 0.30   # K / forward
MAX_MONEYNESS = 3.00   # K / forward


def _moneyness_is_sane(k: float, forward: float) -> bool:
    if forward <= 0:
        return True
    m = k / forward
    return MIN_MONEYNESS <= m <= MAX_MONEYNESS


def fetch_market_smile(ticker: str, expiry: str, S: float, T: float, r: float, q: float = 0.0,
                        max_strikes: int = None, steps: int = 150
                        ) -> Tuple[np.ndarray, np.ndarray, List[str], float, np.ndarray, List[str]]:
    """
    Market IV smile for `ticker`/`expiry`: one IV per strike, OTM side only
    (put for K<=forward, call for K>forward -- the same convention
    SABRModel.py and HestonCalibrator already use, since OTM quotes are
    consistently more liquid/reliable than ITM ones on the same strike).

    Uses the FULL observed OTM chain by default (max_strikes=None) -- not a
    "nearest N to forward" slice. BUG FIX: an earlier version of this
    function capped this at max_strikes=15, which every caller (SABR,
    Vanna-Volga's own atm/rr/bf reads, Heston) inherited. That silently threw
    away the entire chain outside a narrow ATM band -- confirmed live on
    TSLA: SABR calibrated against strikes 280-350 only, then was asked to
    price a 225 put, ~28% outside everything it had ever seen, and predictably
    diverged (Hagan SABR extrapolates badly past its fitted range). The
    ORIGINAL SABRModel.py (before smile_utils.py existed) used every strike in
    the chain with no cap at all -- this restores that, and applies it to
    Heston too (which previously capped at 9, for an unrelated historical
    reason: an old off-center "middle index" strike selection, not a
    real need for fewer points -- using the whole chain makes that moot,
    since there's no subset-selection step left to be off-center). A caller
    can still pass an explicit max_strikes to cap it, but nothing does by
    default -- the whole point of a smile model is the whole chain.

    PREFERS ThetaData's own `implied_vol` field per strike -- that vendor
    number IS "the market IV" (what the user is asking to see when this
    function is described as the "Market" curve/calibration target), so it's
    used directly whenever present, with no re-derivation through this
    suite's own solver. Only solves its own IV (via brute_force_lr, off that
    row's own bid/ask mid) as a genuine LAST RESORT, for a strike where the
    vendor field is missing/null but a usable price still exists -- e.g. some
    short-dated weeklies -- so a sparse vendor field degrades to a real,
    skew-bearing smile point instead of just being dropped.
    #
    # REVERTED (previous session had flipped this to "always solve, vendor
    # only if no price at all" -- confirmed live to be the wrong default: on
    # a real BA chain, that meant nearly every near-the-money strike showed
    # as "self-solved" while only the illiquid deep wings fell back to
    # vendor, which is backwards from what a "Market IV" reference curve
    # should show, and made the plotted "Market" line tautologically
    # coincide with the CRR/Leisen-Reimer/Newton-Raphson chain-wide curves
    # -- all four were inverting the SAME price through the SAME-family
    # American solver, so of course they overlapped; that's circularity, not
    # independent verification). "Market IV" should be the market's own
    # quoted IV, full stop -- this suite's own solve stays available (that's
    # what CRR/Leisen-Reimer/Newton-Raphson/MC's own chain-wide curves ARE,
    # each inverting this same row's price through ITS OWN pricer) but no
    # longer overwrites the vendor field as the default "Market" reference.
    #
    # This is purely a REFERENCE/calibration-target construction -- every
    # model on the smile chart (CRR, Leisen-Reimer, Newton-Raphson, MC via
    # their own chain-wide solves; SABR/Vanna-Volga/Heston via their own
    # calibrated formulas) still produces its curve entirely from its own
    # solve; nothing here feeds a model's curve directly, only what it's
    # being compared/calibrated against.

    Returns (strikes, ivs, sources, forward, prices, rights): sources[i] is
    'vendor' or 'solved', matching strikes[i]/ivs[i], so callers/reports can
    show which points are genuinely vendor-quoted vs. reconstructed here.
    prices[i] is that strike's own observed market price (bid/ask mid) --
    exposed so a caller can independently re-solve IV at that SAME price
    with a different solver (e.g. CRR/Newton-Raphson/MC's own IV solvers,
    for an apples-to-apples chain-wide comparison curve, not just a single
    flat sigma at one strike). rights[i] is 'C' or 'P', exposed so a caller
    can tell where the OTM-put/OTM-call convention switches over.

    Returns empty arrays (not an exception) if ThetaData is unavailable or
    the chain can't be fetched at all -- callers should treat that the same
    as "no smile data available" (i.e. keep their own flat-vol fallback),
    not a new failure mode layered on top of the ones they already handle.
    """
    forward = float(S) * float(np.exp((r - q) * T))
    empty = (np.array([]), np.array([]), [], forward, np.array([]), [])
    if not _THETADATA_AVAILABLE or T <= 0:
        return empty

    td = None
    try:
        td = ThetaDataController()
        rows = td.option_bulk_greeks(ticker, expiry)
    except Exception:
        return empty
    finally:
        if td is not None:
            td.close()

    if not rows:
        return empty

    by_strike_iv: Dict[float, float] = {}
    by_strike_price: Dict[float, float] = {}
    by_strike_right: Dict[float, str] = {}
    n_dropped_wide = 0
    n_dropped_moneyness = 0
    for row in rows:
        try:
            k = strike_from_theta(int(row.get('strike')))
            right = str(row.get('right', '')).upper()
            if right not in ('C', 'P'):
                continue
            wants_call = k > forward
            if wants_call and right != 'C':
                continue
            if not wants_call and right != 'P':
                continue

            if not _moneyness_is_sane(k, forward):
                n_dropped_moneyness += 1
                continue

            if not _quote_is_liquid(row):
                n_dropped_wide += 1
                continue

            price = _row_mid_price(row)
            if price is not None:
                by_strike_price[k] = price
            by_strike_right[k] = right

            iv = row.get('implied_vol') or row.get('impliedVolatility') or row.get('impliedvol')
            try:
                ivf = float(iv) if iv is not None else None
            except (TypeError, ValueError):
                ivf = None
            if ivf is not None and 0.0 < ivf <= 5.0:
                by_strike_iv[k] = ivf
        except Exception:
            continue

    if n_dropped_wide:
        print(f"[smile_utils] Dropped {n_dropped_wide} strike(s) with bid-ask spread > "
              f"{MAX_RELATIVE_SPREAD:.0%} of mid (untrustworthy quote, not used for vendor IV or price-solve).")
    if n_dropped_moneyness:
        print(f"[smile_utils] Dropped {n_dropped_moneyness} strike(s) outside {MIN_MONEYNESS:.2f}x-{MAX_MONEYNESS:.2f}x "
              f"moneyness (too deep OTM to have a real, invertible market price -- not a skew truncation).")

    # DIAGNOSTIC: if not a single row in the whole chain produced a usable
    # price (_row_mid_price found no bid/ask/close/mark/last on ANY strike),
    # that's suspicious enough to print raw evidence rather than silently
    # falling back to 100% vendor IV -- especially since a single-strike
    # lookup (fetch_option_iv, used for the priced K) routinely DOES find a
    # real bid/ask off this same bulk-greeks endpoint. Either this ticker's
    # chain genuinely has no live 2-sided quotes anywhere except the one
    # strike being priced (possible for a thin name), or something about the
    # bulk response's field names/shape differs from what's expected here --
    # printing one raw row's keys settles which, instead of guessing.
    if rows and not by_strike_price:
        sample = rows[0]
        print(f"[smile_utils] WARNING: 0 of {len(rows)} rows produced a usable price "
              f"(_row_mid_price found no bid/ask/close/mark/last anywhere) -- every point below is "
              f"vendor implied_vol only, unverified against this suite's own solver. "
              f"Sample row keys/values: { {k: sample.get(k) for k in ('strike','right','bid','ask','close','mark','last','implied_vol')} }")

    candidate_strikes = sorted(set(by_strike_iv) | set(by_strike_price))
    if not candidate_strikes:
        return empty

    # Full chain by default (see docstring). Only truncate if a caller
    # explicitly asked for a cap -- and even then, keep the strikes nearest
    # the forward rather than an arbitrary slice.
    candidate_strikes = sorted(candidate_strikes, key=lambda k: abs(k - forward))
    if max_strikes is not None:
        candidate_strikes = candidate_strikes[:max_strikes]

    out_strikes, out_ivs, out_sources, out_prices, out_rights = [], [], [], [], []
    for k in candidate_strikes:
        right = by_strike_right.get(k, 'C' if k > forward else 'P')
        cp = (right == 'C')
        price = by_strike_price.get(k)
        vendor_iv = by_strike_iv.get(k)

        # PREFER the vendor's own implied_vol field -- that IS "the market
        # IV" for this row; don't re-derive it through this suite's own
        # solver when we already have it (see docstring: reverted from an
        # earlier "always solve" flip that made the plotted "Market" curve
        # tautologically identical to the CRR/Leisen-Reimer/Newton-Raphson
        # chain-wide curves, which invert the same price through the same
        # family of solver -- confirmed live on BA to show almost every
        # near-the-money strike as "self-solved" and only the illiquid deep
        # wings as vendor, backwards from what a reference curve should do).
        # Solving from the observed price is now only a fallback for a
        # strike where the vendor field is missing/null but a usable price
        # still exists (e.g. some short-dated weeklies) -- a genuine last
        # resort, not the default.
        if vendor_iv is not None:
            iv = vendor_iv
            source = 'vendor'
        elif price is not None and price > 0:
            try:
                iv = brute_force_lr(price, S, k, T, r, cp, q=q, steps=steps)
                source = 'solved'
            except Exception:
                continue
        else:
            continue

        out_strikes.append(k)
        out_ivs.append(iv)
        out_sources.append(source)
        out_prices.append(price if price is not None else float('nan'))
        out_rights.append(right)

    if not out_strikes:
        return empty

    order = np.argsort(out_strikes)
    strikes_arr = np.array(out_strikes)[order]
    ivs_arr = np.array(out_ivs)[order]
    sources_arr = [out_sources[i] for i in order]
    prices_arr = np.array(out_prices)[order]
    rights_arr = [out_rights[i] for i in order]
    return strikes_arr, ivs_arr, sources_arr, forward, prices_arr, rights_arr
