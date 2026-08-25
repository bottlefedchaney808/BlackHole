#!/usr/bin/env python3
"""backtest_stage3.py

Stage 3 of the dealer-positioning v2 validation plan
(DEALER_POSITIONING_V2_DESIGN.md §8) -- the realized-vol behavioral
backtest, and the ONLY stage that actually tests the core economic claim
(§2): does dealer gamma sign predict real subsequent price behavior
(short gamma -> dealers trade WITH the tape -> amplified realized vol;
long gamma -> dealers trade AGAINST it -> dampened realized vol), and does
v2's sign convention (vol_surface_replication, Layers 1a+1b) track that
relationship more consistently than v1's flat oi_heuristic does. Stages 1
(tests/test_variance_swap_replication.py) and 2 (replication_reference.py's
real-chain checks) only ever validated that the MACHINERY behaves sensibly
-- neither one says anything about whether the underlying assumption is
economically correct. This is the first module in the project that actually
tests that.

Empirical structure borrowed directly from the design doc's §8 citation
(Barbon & Buraschi "Gamma Fragility"; Bollen & Whaley): for each trading day
in the sample, classify the dealer book as net LONG or net SHORT gamma
(once under each sign convention), then look at REALIZED volatility over a
forward window starting the next day. If the "short gamma -> amplification"
hypothesis is right, short-gamma days should show higher forward realized
vol than long-gamma days -- and whichever sign convention shows a bigger,
more statistically significant gap is the one that's actually reading real
dealer behavior, not just producing a more sophisticated-looking chart.

Deliberate scope simplification (cost control): uses a SINGLE
near-dated expiry's chain per day, the same simplification
replication_reference.compute_accumulated_position already makes, not a
full multi-expiry aggregate the way dealer_positioning.py's LIVE snapshot
does. A live snapshot only ever pays the multi-expiry cost once; a
historical backtest would pay it once per expiry per day in the sample,
which multiplies fast. Single-expiry is enough to test whether the SIGN
CONVENTION itself carries signal; extending to a multi-expiry aggregate is
a natural follow-up once single-expiry proves the exercise is worth the
extra cost, not a prerequisite for a first result.

Reuses dealer_positioning._dealer_sign / _resolve_sign directly (not a
third reimplementation of the sign logic) and
replication_reference._otm_leg_weights / vol_surface_reference's fitting,
so this backtest exercises the EXACT SAME code path the live charts use --
if the live code changes, this backtest automatically tests the new
behavior instead of silently testing stale logic.

Split into a network-touching orchestrator (run_backtest) and a pure
function over already-fetched rows (_run_backtest_from_history), same
pattern as replication_reference.py's compute_accumulated_position /
_accumulate_from_history split -- the pure function is what
tests/test_backtest_stage3.py exercises directly with synthetic,
network-free data.
"""

import importlib.util
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import dealer_positioning
import expiry_selector
import implied_vol as implied_vol_mod
import numpy as np
import replication_reference
import vol_surface_reference
from scipy import stats as _scipy_stats
from thetadata_client import ThetaDataController, strike_from_theta, strike_to_theta

TRADING_DAYS_PER_YEAR = 252  # trading days/year, for realized-vol annualization from
# trading-day closes. NOT expiry_selector.DEFAULT_A (365,
# calendar days, used only for expiry-date resolution) --
# see variance_swap_live.py:25-36 for why these must stay
# separate; conflating them was already a bug fixed there once.

# Forward-window realized vol needs at least this many forward trading days
# of price data after a given day to be computed at all -- days too close to
# the end of the sample simply don't get a label (excluded, not zero-filled).
DEFAULT_FORWARD_WINDOW_DAYS = 5
DEFAULT_LOOKBACK_DAYS = 90

# Flat risk-free rate used for the forward-price approximation in the
# historical vol-surface fit -- no historical dividend-yield source is
# wired up, so this is a deliberate simplification. Gamma sign
# classification is not materially rate-sensitive; this only shifts where
# "OTM" is drawn by a small amount.
_BACKTEST_R = 0.04

# Flat dividend yield for the same reason. SPY's is ~1.2%; it matters only
# through the forward and through the IV inversion, both of which shift every
# strike on a given day together, while the gamma SIGN is decided by relative
# differences ACROSS strikes on that day.
_BACKTEST_Q = 0.012


@dataclass
class DayRecord:
    date: str
    spot: float
    net_gamma_v1: float
    net_gamma_v2: float
    regime_v1: str  # 'long' or 'short'
    regime_v2: str
    fwd_realized_vol: (
        float | None
    )  # annualized, None if too close to the end of the sample
    net_gamma_dealer: float = 0.0  # dealer_exposure_model net gex (dealer frame)
    regime_dealer_exposure: str | None = None  # 'long'/'short' when the engine ran


@dataclass
class BacktestResult:
    ticker: str
    expiry: str
    forward_window_days: int
    day_records: list[DayRecord] = field(default_factory=list)
    # v1 (oi_heuristic)
    v1_n_long: int = 0
    v1_n_short: int = 0
    v1_long_mean_vol: float = float("nan")
    v1_short_mean_vol: float = float("nan")
    v1_diff: float = float("nan")  # short_mean - long_mean; hypothesis predicts > 0
    v1_tstat: float = float("nan")
    v1_pvalue: float = float("nan")
    # v2 (vol_surface_replication)
    v2_n_long: int = 0
    v2_n_short: int = 0
    v2_long_mean_vol: float = float("nan")
    v2_short_mean_vol: float = float("nan")
    v2_diff: float = float("nan")
    v2_tstat: float = float("nan")
    v2_pvalue: float = float("nan")
    # dealer_exposure (dealer-frame greeks engine, sourced from the
    # Dealer-Exposure-Dev worktree -- NOT merged into master)
    dealer_exposure_n_long: int = 0
    dealer_exposure_n_short: int = 0
    dealer_exposure_long_mean_vol: float = float("nan")
    dealer_exposure_short_mean_vol: float = float("nan")
    dealer_exposure_diff: float = float("nan")
    dealer_exposure_tstat: float = float("nan")
    dealer_exposure_pvalue: float = float("nan")


def _net_gamma_v1(
    gamma_map: dict[tuple[float, str], float], oi_map: dict[tuple[float, str], int]
) -> float:
    """v1 (oi_heuristic): flat call=+/put=- across the WHOLE chain, no
    moneyness restriction -- exactly dealer_positioning._resolve_sign's
    'oi_heuristic' branch.
    """
    total = 0.0
    for (k, right), gamma in gamma_map.items():
        oi = oi_map.get((k, right), 0)
        if oi <= 0 or gamma == 0:
            continue
        total += dealer_positioning._dealer_sign(right) * gamma * oi
    return total


def _net_gamma_v2(
    gamma_map: dict[tuple[float, str], float],
    oi_map: dict[tuple[float, str], int],
    chain_iv: dict[tuple[float, str], float],
    spot: float,
    forward: float,
    T: float,
) -> float:
    """v2 (vol_surface_replication): OTM-restricted, Layer 1a-flippable sign
    -- same call dealer_positioning.py's per-expiry loop makes for this sign
    model, just against a historical day's chain instead of a live one.
    """
    otm_strikes = set(replication_reference._otm_leg_weights(chain_iv, spot, T).keys())
    vol_surface_ref = vol_surface_reference.compute_vol_surface_reference(
        "BACKTEST", chain_iv, spot, forward=forward, T=T
    )

    total = 0.0
    for (k, right), gamma in gamma_map.items():
        oi = oi_map.get((k, right), 0)
        if oi <= 0 or gamma == 0:
            continue
        sign = dealer_positioning._resolve_sign(
            right, k, "vol_surface_replication", otm_strikes, vol_surface_ref
        )
        total += sign * gamma * oi
    return total


# ---------------------------------------------------------------------------
# v3 (vol_surface_replication_weighted) -- BACKTEST-ONLY experiment, not
# wired into dealer_positioning.py's live/validated sign models
# (VALID_SIGN_MODELS is untouched). Motivated by the cross-ticker batch run
# 2026-08-04 (docs/PROJECT_AUDIT_AND_SPEC.md Part 4): v2 trended
# CORRECTLY-signed on several tickers (GME +0.029, V +0.026, SMCI +0.213)
# without reaching significance, while being significantly WRONG-signed on
# others (NVDA, AMD) -- a pattern consistent with a real but noisy signal
# being diluted, not a signal that's simply absent.
#
# vol_surface_reference.resolve_vol_surface_sign flips the FULL +/-1 sign on
# ANY nonzero deviation from the SABR reference curve, with no regard for
# the fit's own uncertainty -- a deviation of 0.0003 vol points (pure fit
# noise / bid-ask wiggle) counts exactly as much as 0.05 (a real overwriting
# program). The SABR fit already computes an RMSE (sabr_params['rmse']) but
# nothing downstream ever uses it. v3 uses it two ways:
#   1. Materiality gate: a deviation smaller than _V3_NOISE_FLOOR_MULT times
#      the fit's own RMSE is treated as unreadable (falls through to Layer
#      1b's flat -1 default) instead of flipping the sign on noise.
#   2. Magnitude weighting: a deviation that DOES clear the floor is scaled
#      by how many multiples of the floor it clears (capped at
#      _V3_MAX_WEIGHT), so a strongly-evidenced strike outweighs one that
#      barely qualifies, instead of every included strike voting +/-1 flat.
# Falls back to plain 'replication' behavior (flat -1) when there's no SABR
# fit to compute a noise floor from (quadratic-only ref, or no ref at all)
# -- same fallback dealer_positioning._resolve_sign uses for missing data.
# ---------------------------------------------------------------------------

_V3_NOISE_FLOOR_MULT = (
    0.5  # multiples of SABR fit RMSE below which a deviation is ignored
)
_V3_MAX_WEIGHT = (
    3.0  # cap on how many multiples of the floor one strike's weight can carry
)


def _resolve_sign_weighted(
    right: str, strike: float, otm_strikes: set | None, vol_surface_ref
) -> float:
    """Materiality-gated, magnitude-weighted variant of
    dealer_positioning._resolve_sign's 'vol_surface_replication' branch --
    see module-level comment above for the rationale. Still gated by Layer
    1b's OTM classification (`otm_strikes`) exactly like v2; only the
    MAGNITUDE/threshold of the Layer 1a flip changes.
    """
    if otm_strikes is None or (strike, right) not in otm_strikes:
        return 0.0
    if (
        vol_surface_ref is None
        or vol_surface_ref.fitter != "sabr"
        or not vol_surface_ref.sabr_params
    ):
        return (
            -1.0
        )  # no SABR fit -> no noise floor to gate on; plain replication default
    dev = vol_surface_ref.deviation_by_strike.get((strike, right))
    if dev is None:
        return -1.0
    rmse = vol_surface_ref.sabr_params.get("rmse") or 0.0
    if rmse <= 0:
        return -1.0 if dev >= 0 else 1.0
    z = abs(dev) / rmse
    if z < _V3_NOISE_FLOOR_MULT:
        return -1.0  # not material enough to override Layer 1b's default
    weight = min(z / _V3_NOISE_FLOOR_MULT, _V3_MAX_WEIGHT)
    return -weight if dev > 0 else weight


def _net_gamma_v3(
    gamma_map: dict[tuple[float, str], float],
    oi_map: dict[tuple[float, str], int],
    chain_iv: dict[tuple[float, str], float],
    spot: float,
    forward: float,
    T: float,
) -> float:
    """v3 (vol_surface_replication_weighted): same OTM gating as v2, but the
    Layer 1a sign uses _resolve_sign_weighted instead of the flat +/-1 flip.
    """
    otm_strikes = set(replication_reference._otm_leg_weights(chain_iv, spot, T).keys())
    vol_surface_ref = vol_surface_reference.compute_vol_surface_reference(
        "BACKTEST", chain_iv, spot, forward=forward, T=T
    )

    total = 0.0
    for (k, right), gamma in gamma_map.items():
        oi = oi_map.get((k, right), 0)
        if oi <= 0 or gamma == 0:
            continue
        weight = _resolve_sign_weighted(right, k, otm_strikes, vol_surface_ref)
        total += weight * gamma * oi
    return total


# ---------------------------------------------------------------------------
# whale (whale-flow) -- BACKTEST-ONLY experiment, ported from the whale-flow
# leg of an external devnotes research package (see
# docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md for
# the port rationale and what was deliberately left out: the other 4
# "Direction" signals, the NO_CALL live-report gate, and the AMD/SPY
# sign-caveat registry calibrated on a different environment's data).
# Not wired into dealer_positioning.py's live/validated sign models
# (VALID_SIGN_MODELS is untouched).
# ---------------------------------------------------------------------------


def _net_gamma_whale(
    gamma_map: dict[tuple[float, str], float],
    oi_map: dict[tuple[float, str], int],
    chain_iv: dict[tuple[float, str], float],
    spot: float,
    T: float,
    whale_bias: str,
) -> float:
    """whale (whale-flow): same OTM gating as v2, but applies ONE uniform
    sign for the whole day (from that day's whale-flow bias) instead of a
    per-strike sign. 'bullish' -> customers bought call convexity / sold
    puts -> dealer short calls (-1), long puts (+1); 'bearish' is the
    mirror; 'neutral' -> 0 contribution everywhere (no signal, no trade --
    the caller leaves regime_whale as None for these days rather than
    folding them into a default sign).
    """
    if whale_bias == "neutral":
        return 0.0
    otm_strikes = set(replication_reference._otm_leg_weights(chain_iv, spot, T).keys())
    direction_bias = 1.0 if whale_bias == "bullish" else -1.0

    total = 0.0
    for (k, right), gamma in gamma_map.items():
        if (k, right) not in otm_strikes:
            continue
        oi = oi_map.get((k, right), 0)
        if oi <= 0 or gamma == 0:
            continue
        leg_direction = 1.0 if right == "C" else -1.0
        sign = -direction_bias * leg_direction
        total += sign * gamma * oi
    return total


def _forward_realized_vol(closes_from_today: list[float], window: int) -> float | None:
    """Annualized close-to-close realized vol over the next `window` trading
    days, given a list of closes starting at today's close (index 0) through
    at least `window` more trading days. Returns None if there aren't enough
    forward closes yet (caller should leave that day unlabeled, not
    zero-fill it -- an unlabeled day is honest; a zero-filled one silently
    biases the comparison).
    """
    if len(closes_from_today) < window + 1:
        return None
    prices = closes_from_today[: window + 1]
    log_rets = np.diff(np.log(prices))
    if len(log_rets) < 2:
        return None
    return float(np.std(log_rets, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))


# ---------------------------------------------------------------------------
# dealer_exposure_model -- the dealer-frame greeks engine. As of 2026-08-20 it
# is MERGED into the main tree (Vol_Suite/expiry_book_exposure.py); the
# Dealer-Exposure-Dev worktree is where it was developed before promotion.
# This study prefers the in-tree copy (the live model) and only falls back to
# the dev worktree if the in-tree file is absent (a tree where the merge has
# not landed yet). Failing that, a clear error -- never a silent skip.
# ---------------------------------------------------------------------------
_IN_TREE_EXPIRY_EXPOSURE = Path(__file__).resolve().parent / "expiry_book_exposure.py"
_DEV_WORKTREE_EXPIRY_EXPOSURE = (
    Path(__file__).resolve().parent.parent
    / ".worktrees"
    / "dealer-exposure-dev"
    / "Vol_Suite"
    / "expiry_book_exposure.py"
)


def _dealer_exposure_engine_path() -> Path | None:
    if _IN_TREE_EXPIRY_EXPOSURE.is_file():
        return _IN_TREE_EXPIRY_EXPOSURE
    if _DEV_WORKTREE_EXPIRY_EXPOSURE.is_file():
        return _DEV_WORKTREE_EXPIRY_EXPOSURE
    return None


def _dealer_exposure_engine_available() -> bool:
    return _dealer_exposure_engine_path() is not None


def _load_dealer_exposure_engine():
    """Load expiry_book_exposure.py -- in-tree (merged) first, dev worktree
    as a fallback."""
    path = _dealer_exposure_engine_path()
    if path is None:
        raise FileNotFoundError(
            "dealer_exposure_model requires expiry_book_exposure.py (the live "
            "dealer-frame greeks engine); it is not in the main tree "
            f"({_IN_TREE_EXPIRY_EXPOSURE}) nor the Dealer-Exposure-Dev worktree "
            f"({_DEV_WORKTREE_EXPIRY_EXPOSURE})."
        )
    existing = sys.modules.get("expiry_book_exposure")
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location("expiry_book_exposure", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["expiry_book_exposure"] = mod
    spec.loader.exec_module(mod)
    return mod


def _build_day_records(
    ticker: str,
    expiry: str,
    hist_greek_rows: list[dict],
    hist_oi_rows: list[dict],
    hist_price_rows: list[dict],
    forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
    accumulated_position: dict | None = None,
    use_dealer_exposure: bool = False,
) -> list[DayRecord]:
    """Pure function over already-fetched historical rows -- the part
    tests/test_backtest_stage3.py exercises directly with synthetic data,
    same split as replication_reference._accumulate_from_history.
    """
    expiry_date = datetime.strptime(expiry, "%Y%m%d")

    close_by_date_pre: dict[str, float] = {}
    for row in hist_price_rows:
        d = row.get("date") or replication_reference._parse_hist_date(row)
        try:
            c = float(row.get("close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if d and c > 0:
            close_by_date_pre[d] = c

    gamma_by_date: dict[str, dict[tuple[float, str], float]] = defaultdict(dict)
    iv_by_date: dict[str, dict[tuple[float, str], float]] = defaultdict(dict)
    n_derived = n_vendor = n_unrecoverable = 0

    for row in hist_greek_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            # hist/option/eod (the LIVE route hist_greek_rows normally comes
            # from) echoes strike in plain dollar form ("650.000"), NOT
            # theta-scaled -- unlike option_bulk_hist_oi_by_day below, which
            # does return theta-scaled integers. Running a plain-dollar
            # strike through strike_from_theta() silently divided it by
            # another 1000 (e.g. 650 -> 0.65), pushing every real market
            # price outside implied_vol()'s no-arbitrage bounds and making
            # every row "unrecoverable" -- confirmed live, 2026-08-04 (see
            # docs/PROJECT_AUDIT_AND_SPEC.md Part 5).
            #
            # BUT this function has a SECOND real producer now: the cached
            # seed_data_*.json payload (seed_data_maker.py) stores greek-row
            # strikes THETA-SCALED ("650000"), the same convention oi_by_date
            # already uses. Treating those as plain dollars leaves gamma_map
            # keyed at (650000.0, 'C') while oi_map is keyed at (650.0, 'C')
            # -- they never match, every row is skipped, and net_gamma_v1 is
            # silently exactly 0.0 for every day, every ticker (the SAME
            # observable failure the 08-04 incident hit, opposite direction
            # -- found running the pooled falsifier against real cached data,
            # see test_gamma_map_matches_oi_map_when_greek_rows_use_theta_scaled_strikes).
            # Auto-detect instead of assuming one producer: a real listed
            # equity/index strike is never >= 10,000 dollars, so a raw value
            # that large can only be theta-scaled.
            raw_k = float(row["strike"])
            k = strike_from_theta(int(round(raw_k))) if raw_k >= 10000 else raw_k
            # Also normalize `right` to a single uppercase char here: the
            # live route returns the full word ("CALL"/"PUT"), while
            # oi_by_date below is keyed on ThetaData's normal single-char
            # "C"/"P" -- left unnormalized, every (k, right) lookup into
            # oi_map in _net_gamma_v1/_v2 would silently miss.
            right = str(row["right"]).upper()[:1]
        except (KeyError, TypeError, ValueError):
            continue

        iv = float(row.get("implied_vol", 0) or 0)
        gamma = float(row.get("gamma", 0) or 0)

        # Rows from hist/option/eod carry prices but no greeks -- that route
        # is the only per-contract one that honors a date range, which is why
        # we buy prices and reconstruct the rest. Vendor greeks are still
        # used verbatim when present, so a mixed source (or a future fix to
        # the greeks route) needs no change here.
        #
        # IMPORTANT: iv and gamma are handled as SEPARATE gates, not one
        # combined `if iv <= 0 or gamma <= 0` condition (that was a real bug,
        # found running the accumulation falsifier against the WSL handoff's
        # cached "dense EOD, IV solved, vanna injected" seed_data payloads --
        # see tests/test_backtest_stage3.py::test_gamma_derived_from_an_already_solved_iv_without_bid_ask).
        # Those rows carry an already-solved, perfectly good `implied_vol`
        # but no `gamma`, `bid`, or `ask` at all (`close` is legitimately
        # '0.00' for illiquid strikes). The combined condition discarded the
        # good IV and tried to re-derive it from bid/ask/close, which fails
        # on that data shape -- silently dropping ~78% of rows and crushing
        # 150 days of history down to ~14-21 usable days. Re-solving IV from
        # price is ONLY needed when iv itself is missing/invalid; deriving
        # gamma from an already-good iv never needs bid/ask at all.
        if iv <= 0:
            spot = close_by_date_pre.get(d)
            if not spot:
                n_unrecoverable += 1
                continue
            T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
            mark = implied_vol_mod.mid_price(
                row.get("bid"), row.get("ask"), row.get("close")
            )
            solved = implied_vol_mod.implied_vol(
                mark, spot, k, T, _BACKTEST_R, _BACKTEST_Q, right
            )
            if solved is None:
                # Deliberately NOT zero-filled. A strike whose price carries
                # no recoverable vol is missing information, and imputing a
                # number here would put fabricated points into the smile that
                # v2's whole sign convention is fitted to.
                n_unrecoverable += 1
                continue
            iv = solved
            n_derived += 1
        else:
            n_vendor += 1

        if gamma <= 0 and iv > 0:
            spot = close_by_date_pre.get(d)
            if spot:
                T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
                gamma = dealer_positioning.bs_gamma(
                    spot, k, T, _BACKTEST_R, _BACKTEST_Q, iv
                )

        if iv > 0:
            iv_by_date[d][(k, right)] = iv
        if gamma > 0:
            gamma_by_date[d][(k, right)] = gamma

    if n_derived or n_unrecoverable:
        total = n_derived + n_vendor + n_unrecoverable
        print(
            f"  [backtest_stage3] IV/gamma source: {n_derived} derived from price, "
            f"{n_vendor} vendor, {n_unrecoverable} unrecoverable "
            f"({100.0 * n_unrecoverable / total:.1f}% dropped) of {total} rows"
        )

    oi_by_date: dict[str, dict[tuple[float, str], int]] = defaultdict(dict)
    for row in hist_oi_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row["strike"])))
            right = row["right"]
            oi = int(float(row.get("open_interest", 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue
        oi_by_date[d][(k, right)] = oi

    close_by_date: dict[str, float] = {}
    for row in hist_price_rows:
        d = row.get("date") or replication_reference._parse_hist_date(row)
        try:
            close = float(row.get("close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if d and close > 0:
            close_by_date[d] = close

    # A day is only usable if ALL FOUR inputs line up on it. That
    # intersection is where a run quietly turns into nothing: an earlier
    # backtest returned exactly 2 usable days out of a 30-day window and
    # looked, from the outside, like a clean successful run -- every
    # contract reported "done", no errors, and the report just happened to
    # be built on two data points. Report the per-input date counts so the
    # size of the intersection is always attributable to a specific missing
    # input rather than a mystery.
    trading_dates = sorted(
        d
        for d in gamma_by_date
        if d in oi_by_date and d in iv_by_date and d in close_by_date
    )
    print(
        f"  [backtest_stage3] {ticker} {expiry} date coverage: "
        f"gamma={len(gamma_by_date)} iv={len(iv_by_date)} oi={len(oi_by_date)} "
        f"close={len(close_by_date)} -> {len(trading_dates)} usable days"
    )
    if trading_dates and len(trading_dates) < min(
        len(gamma_by_date), len(oi_by_date), len(close_by_date)
    ):
        missing_oi = sorted(set(gamma_by_date) - set(oi_by_date))[:5]
        missing_close = sorted(set(gamma_by_date) - set(close_by_date))[:5]
        if missing_oi:
            print(
                f"  [backtest_stage3] dates with greeks but no OI (first 5): {missing_oi}"
            )
        if missing_close:
            print(
                f"  [backtest_stage3] dates with greeks but no close (first 5): {missing_close}"
            )

    records: list[DayRecord] = []
    _dealer_engine = None
    if use_dealer_exposure:
        _dealer_engine = _load_dealer_exposure_engine()
    for i, d in enumerate(trading_dates):
        spot = close_by_date[d]
        T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
        forward = spot * math.exp((_BACKTEST_R - _BACKTEST_Q) * T)

        gamma_map = gamma_by_date[d]
        oi_map = oi_by_date[d]
        chain_iv = iv_by_date[d]

        net_v1 = _net_gamma_v1(gamma_map, oi_map)
        if accumulated_position:
            # v2_live accumulation: the accumulated position is a SIGNED dealer
            # book (sign already baked in), so classify the v2 regime from it
            # with pass-through sign=1.0 -- mirrors dealer_positioning's
            # accumulate branch (position_by_strike, applied_sign=1.0).
            net_v2 = sum(
                gamma * accumulated_position.get((k, right), 0.0)
                for (k, right), gamma in gamma_map.items()
            )
        else:
            net_v2 = _net_gamma_v2(gamma_map, oi_map, chain_iv, spot, forward, T)

        net_dealer = 0.0
        regime_dealer = None
        if _dealer_engine is not None:
            # Dealer-frame net exposure for this day, from real spot/strike/T/IV
            # via the dev-worktree greeks engine (build_net_exposure). GEX sign
            # (dollar-gamma-per-1%) drives the long/short regime.
            dealer_rows = [
                {
                    "strike": k,
                    "right": right,
                    "oi": oi,
                    "implied_vol": chain_iv.get((k, right)),
                }
                for (k, right), oi in oi_map.items()
                if oi > 0 and chain_iv.get((k, right), 0) > 0
            ]
            if dealer_rows:
                ne = _dealer_engine.build_net_exposure(
                    dealer_rows, spot, ticker, expiry, T=T
                )
                net_dealer = float(ne.gex())
                regime_dealer = "long" if net_dealer > 0 else "short"

        # Forward realized vol uses ANY available future close (not just the
        # dates that happen to have a full option chain snapshot), since
        # price history is denser than chain-snapshot history and there's no
        # reason to throw away real trading days just because that
        # particular day wasn't also an OI/greeks history date.
        future_closes = [spot] + [
            close_by_date[fd] for fd in sorted(close_by_date) if fd > d
        ][:forward_window_days]
        fwd_vol = _forward_realized_vol(future_closes, forward_window_days)

        records.append(
            DayRecord(
                date=d,
                spot=spot,
                net_gamma_v1=net_v1,
                net_gamma_v2=net_v2,
                regime_v1="long" if net_v1 > 0 else "short",
                regime_v2="long" if net_v2 > 0 else "short",
                fwd_realized_vol=fwd_vol,
                net_gamma_dealer=net_dealer,
                regime_dealer_exposure=regime_dealer,
            )
        )

    return records


def _summarize(records: list[DayRecord], regime_attr: str) -> dict:
    """Welch's two-sample t-test (unequal variance) between forward realized
    vol on 'short' vs 'long' gamma days for one model's regime
    classification. Welch's, not Student's, because there's no reason to
    assume the two regimes have equal variance -- and a regime with very
    unequal counts (which these are, especially v1's flat heuristic) is
    exactly the case where that assumption would matter most.
    """
    long_vols = [
        r.fwd_realized_vol
        for r in records
        if getattr(r, regime_attr) == "long" and r.fwd_realized_vol is not None
    ]
    short_vols = [
        r.fwd_realized_vol
        for r in records
        if getattr(r, regime_attr) == "short" and r.fwd_realized_vol is not None
    ]

    out = {
        "n_long": len(long_vols),
        "n_short": len(short_vols),
        "long_mean_vol": float(np.mean(long_vols)) if long_vols else float("nan"),
        "short_mean_vol": float(np.mean(short_vols)) if short_vols else float("nan"),
        "diff": float("nan"),
        "tstat": float("nan"),
        "pvalue": float("nan"),
    }
    if len(long_vols) >= 2 and len(short_vols) >= 2:
        out["diff"] = out["short_mean_vol"] - out["long_mean_vol"]
        t_res = _scipy_stats.ttest_ind(short_vols, long_vols, equal_var=False)
        out["tstat"] = float(t_res.statistic)
        out["pvalue"] = float(t_res.pvalue)
    return out


def _run_backtest_from_history(
    ticker: str,
    expiry: str,
    hist_greek_rows: list[dict],
    hist_oi_rows: list[dict],
    hist_price_rows: list[dict],
    forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
    accumulated_position: dict | None = None,
    use_dealer_exposure: bool = False,
) -> BacktestResult:
    records = _build_day_records(
        ticker,
        expiry,
        hist_greek_rows,
        hist_oi_rows,
        hist_price_rows,
        forward_window_days,
        accumulated_position=accumulated_position,
        use_dealer_exposure=use_dealer_exposure,
    )
    if not records:
        raise ValueError(
            f"No overlapping greeks/OI/price history for {ticker} {expiry} -- "
            f"nothing to backtest."
        )

    v1 = _summarize(records, "regime_v1")
    v2 = _summarize(records, "regime_v2")
    dealer = _summarize(records, "regime_dealer_exposure")

    return BacktestResult(
        ticker=ticker,
        expiry=expiry,
        forward_window_days=forward_window_days,
        day_records=records,
        v1_n_long=v1["n_long"],
        v1_n_short=v1["n_short"],
        v1_long_mean_vol=v1["long_mean_vol"],
        v1_short_mean_vol=v1["short_mean_vol"],
        v1_diff=v1["diff"],
        v1_tstat=v1["tstat"],
        v1_pvalue=v1["pvalue"],
        v2_n_long=v2["n_long"],
        v2_n_short=v2["n_short"],
        v2_long_mean_vol=v2["long_mean_vol"],
        v2_short_mean_vol=v2["short_mean_vol"],
        v2_diff=v2["diff"],
        v2_tstat=v2["tstat"],
        v2_pvalue=v2["pvalue"],
        dealer_exposure_n_long=dealer["n_long"],
        dealer_exposure_n_short=dealer["n_short"],
        dealer_exposure_long_mean_vol=dealer["long_mean_vol"],
        dealer_exposure_short_mean_vol=dealer["short_mean_vol"],
        dealer_exposure_diff=dealer["diff"],
        dealer_exposure_tstat=dealer["tstat"],
        dealer_exposure_pvalue=dealer["pvalue"],
    )


def run_backtest(
    ticker: str,
    expiration: str | None = None,
    target_years: float = 0.25,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
    accumulate: bool = False,
    sign_model: str = "all",
) -> BacktestResult:
    """Network-touching orchestrator: resolves the target expiry, pulls
    historical greeks/OI/price straight from ThetaData, and runs the pure
    backtest over it.

    Single near-dated expiry only -- see module docstring for why.

    No caching layer (removed 2026-07-24). There was a SQLite cache here;
    it cost more than it saved. Its central rule -- "a date that's been
    checked is never checked again" -- is only safe if a failed fetch can
    never be mistaken for an empty one, and on a proxy this transient that
    turned out to be a losing bet: one bad response would mark a whole date
    range permanently complete, and every later run would then read the
    resulting hole straight out of SQLite without ever touching the network
    to notice. Debugging a bad result meant deleting the DB file first, on
    every single iteration, which made every experiment slower rather than
    faster. Fetching directly is slower per run and always correct.
    """
    td = ThetaDataController()
    try:
        expiry, _ = expiry_selector.resolve_expiration(
            td, ticker, expiration, target_years
        )

        # End at yesterday, not today: today's EOD greeks don't exist
        # server-side until the session closes, so asking for them
        # guarantees a wall of errors for every contract in the chain.
        # Stage 3 only needs closed days anyway -- today couldn't carry a
        # forward-realized-vol label yet regardless (see
        # _forward_realized_vol's "leave it unlabeled" rule).
        end_date = datetime.now() - timedelta(days=1)
        # Generous calendar-day pad: lookback_days TRADING days, plus
        # forward_window_days of extra price history so the LAST lookback
        # day can still get a forward-vol label, plus weekend/holiday slack.
        pad_days = int((lookback_days + forward_window_days) * 1.6) + 10
        start_date = end_date - timedelta(days=pad_days)
        start_str, end_str = start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")

        # Route choice is a cost decision, not a preference. Measured
        # 2026-07-24 (diagnostics/diagnose_range_route_hunt.py):
        #   option_bulk_hist_greeks     -> ~47,000 requests (one DAY per call)
        #   option_bulk_hist_eod        ->     ~430 requests (full range per call)
        #   option_bulk_hist_eod_greeks -> one request per expiry, dense range,
        #                                   OHLC + implied_vol + full greeks
        #   option_bulk_hist_oi_by_day  ->    ~110 requests (whole chain per day)
        # Since 2026-08-16 we use option_bulk_hist_eod_greeks (the "untapped"
        # dense route) instead of option_bulk_hist_eod: it returns real
        # implied_vol + gamma over the full range in one request per expiry,
        # which _build_day_records uses verbatim (falling back to price
        # inversion only when IV is missing) and which the v2_live accumulation
        # needs to seed + accumulate -- option_bulk_hist_eod prices carry no
        # IV, so feeding them to the accumulation left its IV map empty and it
        # could not build a position. Same convention as oi_by_day (string
        # YYYYMMDD 'date', right 'C'/'P', strike cents-int); _build_day_records
        # auto-detects strike scale and normalizes right.
        hist_greek_rows = td.option_bulk_hist_eod_greeks(
            ticker, expiry, start_str, end_str
        )
        hist_oi_rows = td.option_bulk_hist_oi_by_day(ticker, expiry, start_str, end_str)
        hist_price_rows = td.hist_stock_eod(ticker, start_str, end_str)
    finally:
        td.close()

    accumulated_position = None
    if accumulate:
        # v2_live: classify the v2 regime from the accumulated SIGNED dealer
        # book (the live model runs with accumulation on) rather than the
        # same-day OI snapshot. Route through dealer_positioning -- the single
        # live model -- so there is no second, separate accumulation path.
        accumulated_position = dealer_positioning.compute_accumulated_position(
            ticker,
            expiry,
            lookback_days=lookback_days,
            seed_mode="replication",
            hist_rows=(hist_greek_rows, hist_oi_rows, hist_price_rows),
        )
        if not accumulated_position:
            raise ValueError(
                f"v2_live accumulation produced no position for {ticker} "
                f"{expiry}; refusing to fall back to a same-day snapshot."
            )

    # The study's default ('all') now runs all THREE of Jason's live models
    # together: v1 (oi_heuristic), v2_live (accumulated, via `accumulate`), and
    # dealer_exposure (dealer-frame engine). There is no per-model selector.
    use_dealer_exposure = sign_model in ("all", "dealer_exposure", "live")
    if use_dealer_exposure and not _dealer_exposure_engine_available():
        raise ValueError(
            "dealer_exposure_model requires the merged expiry_book_exposure.py "
            "in the main tree (Vol_Suite/expiry_book_exposure.py); it was not "
            "found in the in-tree location or the dev worktree."
        )

    return _run_backtest_from_history(
        ticker,
        expiry,
        hist_greek_rows,
        hist_oi_rows,
        hist_price_rows,
        forward_window_days,
        accumulated_position=accumulated_position,
        use_dealer_exposure=use_dealer_exposure,
    )


def format_backtest_report(result: BacktestResult) -> str:
    lines = [
        f"Stage 3 backtest -- {result.ticker} {result.expiry} "
        f"({len(result.day_records)} days, {result.forward_window_days}d forward window)",
        "",
        f"{'':20s}{'v1 (oi_heuristic)':>22s}{'v2_live (accumulated)':>32s}{'dealer_exposure':>28s}",
        f"{'long-gamma days':20s}{result.v1_n_long:>22d}{result.v2_n_long:>32d}{result.dealer_exposure_n_long:>28d}",
        f"{'short-gamma days':20s}{result.v1_n_short:>22d}{result.v2_n_short:>32d}{result.dealer_exposure_n_short:>28d}",
        f"{'mean vol | long':20s}{result.v1_long_mean_vol:>22.4f}{result.v2_long_mean_vol:>32.4f}{result.dealer_exposure_long_mean_vol:>28.4f}",
        f"{'mean vol | short':20s}{result.v1_short_mean_vol:>22.4f}{result.v2_short_mean_vol:>32.4f}{result.dealer_exposure_short_mean_vol:>28.4f}",
        f"{'short - long':20s}{result.v1_diff:>22.4f}{result.v2_diff:>32.4f}{result.dealer_exposure_diff:>28.4f}",
        f"{'t-stat':20s}{result.v1_tstat:>22.3f}{result.v2_tstat:>32.3f}{result.dealer_exposure_tstat:>28.3f}",
        f"{'p-value':20s}{result.v1_pvalue:>22.4f}{result.v2_pvalue:>32.4f}{result.dealer_exposure_pvalue:>28.4f}",
        "",
        "Hypothesis: short-gamma days should show HIGHER forward realized vol "
        "(dealers trade with the tape) -- a positive, statistically significant "
        "diff supports the model. v1 is the conventional GEX (oi_heuristic) sign; "
        "v2_live is the accumulated dealer book (vol_surface_replication with "
        "multi-day accumulation); dealer_exposure is the LIVE dealer-frame "
        "greeks engine (expiry_book_exposure, merged into the main tree) -- "
        "v1/v2_live are retained as legacy comparison arms only.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# General strategy P&L backtester
#
# A second, independent capability bolted onto this module: given a
# multi-leg options strategy recommendation (strategy_recommender.py's
# StrategyRecommendation, or the equivalent JSON dict shape produced by its
# _strategy_to_dict / carried in a suite_context.json "strategies" list --
# see suite_context.validate_suite_context's strategies-schema section for
# the canonical dict contract), simulate the strategy's P&L from an entry
# date to an exit date (default: hold to expiration).
#
# Same network/pure-function split as the Stage 3 code above:
# run_strategy_backtest is the orchestrator that resolves theta strikes and
# pulls per-contract historical EOD prices (and, if the exit falls at/after
# expiration, the underlying's spot history for an intrinsic-value payoff);
# _run_strategy_backtest_from_history is the pure function tested directly
# with synthetic entry/exit price maps in tests/test_strategy_backtest.py.
#
# This is intentionally independent of DayRecord/BacktestResult and every
# function above it -- it does not modify, call, or depend on any of the
# Stage 3 dealer-gamma-sign validation code, and nothing above this comment
# has been touched.
# ---------------------------------------------------------------------------


@dataclass
class StrategyBacktestResult:
    strategy_type: str
    legs: list[dict]
    entry_date: str
    exit_date: str
    entry_cost: float  # net debit paid (positive) or credit received (negative) to open
    exit_value: (
        float  # net value received (positive) or owed (negative) to close/settle
    )
    pnl: float  # exit_value - entry_cost
    pnl_pct: float  # pnl / abs(entry_cost) * 100; nan if entry_cost == 0
    contract_multiplier: float = 100.0


def _leg_key(leg: dict) -> tuple[float, str]:
    """(strike, instrument_type) identity for a leg -- matches both
    StrategyLeg (via dataclasses.asdict/_strategy_to_dict) and a plain dict
    read straight out of a suite_context.json "strategies" entry, since both
    shapes carry 'strike' and 'instrument_type' fields.
    """
    return (float(leg["strike"]), leg["instrument_type"])


def _leg_intrinsic_value(strike: float, instrument_type: str, spot: float) -> float:
    """Payoff at/after expiration: max(S-K, 0) for a call, max(K-S, 0) for a
    put -- the option's only remaining value once there's no time left.
    """
    if instrument_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def _run_strategy_backtest_from_history(
    strategy: dict,
    entry_date: str,
    exit_date: str,
    expiry: str,
    entry_prices: dict[tuple[float, str], float],
    exit_prices: dict[tuple[float, str], float],
    exit_spot: float | None = None,
    contract_multiplier: float = 100.0,
) -> StrategyBacktestResult:
    """Pure function over already-resolved per-leg prices -- the part
    tests/test_strategy_backtest.py exercises directly with synthetic data,
    same split as _run_backtest_from_history above.

    entry_prices / exit_prices: {(strike, instrument_type): premium} maps,
    one quote per leg, in the SAME per-share units as `strike` (the caller
    is responsible for handing over a market mid/close, not a raw bid or
    ask). Entry always prices off entry_prices (a strategy is always opened
    while the contract is still alive). Exit prices off exit_prices UNLESS
    exit_date is at or after expiry (lexicographic YYYYMMDD comparison,
    same convention _build_day_records uses), in which case the leg is
    already expired and is priced at INTRINSIC value off exit_spot instead
    -- there is no market quote for a dead contract.

    Quantity sign (positive = long, negative = short, per StrategyLeg) is
    respected on both sides: entry_cost = sum(qty * entry_price) is a net
    debit (positive) for a net-long strategy or a net credit (negative,
    i.e. money received) for a net-short one; exit_value is the same sum
    computed against exit prices, so pnl = exit_value - entry_cost is
    always "what you'd have in hand at the end" minus "what it cost to
    get in", with the correct sign for both long and short legs.
    """
    legs = strategy["legs"]
    if not legs:
        raise ValueError(
            f"strategy {strategy.get('strategy_type')!r} has no legs to backtest"
        )

    expired = exit_date >= expiry

    entry_cost = 0.0
    exit_value = 0.0
    missing_entry: list[tuple[float, str]] = []
    missing_exit: list[tuple[float, str]] = []

    for leg in legs:
        key = _leg_key(leg)
        qty = int(leg["quantity"])

        entry_px = entry_prices.get(key)
        if entry_px is None:
            missing_entry.append(key)
            continue
        entry_cost += qty * entry_px * contract_multiplier

        if expired:
            if exit_spot is None:
                raise ValueError(
                    f"exit_date {exit_date} is at/past expiry {expiry} for leg {key}, "
                    f"but no exit_spot was supplied to price its intrinsic value."
                )
            exit_px = _leg_intrinsic_value(key[0], key[1], exit_spot)
        else:
            exit_px = exit_prices.get(key)
            if exit_px is None:
                missing_exit.append(key)
                continue
        exit_value += qty * exit_px * contract_multiplier

    if missing_entry:
        raise ValueError(
            f"Missing entry price(s) for leg(s) {missing_entry} on {entry_date} -- "
            f"cannot compute entry cost."
        )
    if missing_exit:
        raise ValueError(
            f"Missing exit price(s) for leg(s) {missing_exit} on {exit_date} -- "
            f"cannot compute exit value."
        )

    pnl = exit_value - entry_cost
    pnl_pct = (pnl / abs(entry_cost) * 100.0) if entry_cost != 0 else float("nan")

    return StrategyBacktestResult(
        strategy_type=strategy["strategy_type"],
        legs=legs,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_cost=entry_cost,
        exit_value=exit_value,
        pnl=pnl,
        pnl_pct=pnl_pct,
        contract_multiplier=contract_multiplier,
    )


def run_strategy_backtest(
    strategy: dict,
    ticker: str,
    expiry: str,
    entry_date: str,
    exit_date: str | None = None,
    contract_multiplier: float = 100.0,
) -> StrategyBacktestResult:
    """Network-touching orchestrator: pulls each leg's per-contract EOD
    price history from ThetaData (option_hist_eod_single, same route
    _build_day_records' price-reconstruction path relies on for mid
    pricing), plus the underlying's spot history if the exit falls at/after
    expiration, and hands everything to the pure function above.

    `expiry`: the strategy's expiration date (YYYYMMDD) -- not carried on
    the strategy dict itself (strategy_recommender._strategy_to_dict has no
    expiry field; format_strategies_artifact carries it one level up, as
    top-level 'expiration_date'), so the caller supplies it explicitly.

    `exit_date`: configurable horizon parameter. Defaults to None, meaning
    "hold to expiration" -- exit_date is then set to `expiry` and every leg
    is settled at intrinsic value off the underlying's closing spot.
    """
    exit_date = exit_date or expiry
    expired = exit_date >= expiry

    fmt = "%Y%m%d"
    # Same calendar-day slack idea as run_backtest's pad_days: entry_date or
    # exit_date landing on a weekend/holiday shouldn't cause a miss, so pad
    # a few days on each side of the requested window.
    start_dt = datetime.strptime(entry_date, fmt) - timedelta(days=5)
    end_anchor = min(datetime.strptime(exit_date, fmt), datetime.strptime(expiry, fmt))
    end_dt = end_anchor + timedelta(days=5)
    start_str, end_str = start_dt.strftime(fmt), end_dt.strftime(fmt)

    right_map = {"call": "C", "put": "P"}

    td = ThetaDataController()
    try:
        entry_prices: dict[tuple[float, str], float] = {}
        exit_prices: dict[tuple[float, str], float] = {}

        for leg in strategy["legs"]:
            strike = float(leg["strike"])
            instrument_type = leg["instrument_type"]
            right = right_map.get(instrument_type, str(instrument_type)[:1].upper())
            k_theta = strike_to_theta(strike)

            rows = td.option_hist_eod_single(
                ticker, expiry, k_theta, right, start_str, end_str
            )
            by_date: dict[str, float] = {}
            for row in rows:
                d = replication_reference._parse_hist_date(row)
                if not d:
                    continue
                px = implied_vol_mod.mid_price(
                    row.get("bid"), row.get("ask"), row.get("close")
                )
                if px is not None:
                    by_date[d] = px

            key = (strike, instrument_type)
            if entry_date in by_date:
                entry_prices[key] = by_date[entry_date]
            if not expired and exit_date in by_date:
                exit_prices[key] = by_date[exit_date]

        exit_spot = None
        if expired:
            spot_rows = td.hist_stock_eod(ticker, start_str, end_str)
            spot_by_date: dict[str, float] = {}
            for row in spot_rows:
                d = row.get("date") or replication_reference._parse_hist_date(row)
                try:
                    c = float(row.get("close", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if d and c > 0:
                    spot_by_date[d] = c
            exit_spot = spot_by_date.get(exit_date)
            if exit_spot is None and spot_by_date:
                # Expiration day itself may not have printed an EOD close
                # yet (or landed on a non-trading day) -- fall back to the
                # last available close AT OR BEFORE exit_date rather than
                # failing outright.
                eligible = [d for d in spot_by_date if d <= exit_date]
                if eligible:
                    exit_spot = spot_by_date[max(eligible)]
    finally:
        td.close()

    return _run_strategy_backtest_from_history(
        strategy,
        entry_date,
        exit_date,
        expiry,
        entry_prices,
        exit_prices,
        exit_spot=exit_spot,
        contract_multiplier=contract_multiplier,
    )


def format_strategy_backtest_report(result: StrategyBacktestResult) -> str:
    leg_lines = []
    for leg in result.legs:
        qty = int(leg["quantity"])
        side = "long" if qty > 0 else "short"
        leg_lines.append(
            f"    {side:5s} {abs(qty)}x {leg['instrument_type']:4s} @ {float(leg['strike']):.2f}"
        )

    lines = [
        f"Strategy backtest -- {result.strategy_type} "
        f"({result.entry_date} -> {result.exit_date})",
        "",
        "  legs:",
        *leg_lines,
        "",
        f"{'entry cost':20s}{result.entry_cost:>15.2f}",
        f"{'exit value':20s}{result.exit_value:>15.2f}",
        f"{'P&L':20s}{result.pnl:>15.2f}",
        f"{'P&L %':20s}{result.pnl_pct:>14.2f}%",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LOOKBACK_DAYS
    result = run_backtest(ticker, lookback_days=lookback)
    print(format_backtest_report(result))
