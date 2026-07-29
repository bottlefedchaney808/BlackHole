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
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats as _scipy_stats

from thetadata_client import ThetaDataController, strike_from_theta
import expiry_selector
import implied_vol as implied_vol_mod
import replication_reference
import vol_surface_reference
import dealer_positioning

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
    regime_v1: str              # 'long' or 'short'
    regime_v2: str
    fwd_realized_vol: Optional[float]  # annualized, None if too close to the end of the sample


@dataclass
class BacktestResult:
    ticker: str
    expiry: str
    forward_window_days: int
    day_records: List[DayRecord] = field(default_factory=list)
    # v1 (oi_heuristic)
    v1_n_long: int = 0
    v1_n_short: int = 0
    v1_long_mean_vol: float = float('nan')
    v1_short_mean_vol: float = float('nan')
    v1_diff: float = float('nan')       # short_mean - long_mean; hypothesis predicts > 0
    v1_tstat: float = float('nan')
    v1_pvalue: float = float('nan')
    # v2 (vol_surface_replication)
    v2_n_long: int = 0
    v2_n_short: int = 0
    v2_long_mean_vol: float = float('nan')
    v2_short_mean_vol: float = float('nan')
    v2_diff: float = float('nan')
    v2_tstat: float = float('nan')
    v2_pvalue: float = float('nan')


def _net_gamma_v1(gamma_map: Dict[Tuple[float, str], float],
                   oi_map: Dict[Tuple[float, str], int]) -> float:
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


def _net_gamma_v2(gamma_map: Dict[Tuple[float, str], float],
                   oi_map: Dict[Tuple[float, str], int],
                   chain_iv: Dict[Tuple[float, str], float],
                   spot: float, forward: float, T: float) -> float:
    """v2 (vol_surface_replication): OTM-restricted, Layer 1a-flippable sign
    -- same call dealer_positioning.py's per-expiry loop makes for this sign
    model, just against a historical day's chain instead of a live one.
    """
    otm_strikes = set(replication_reference._otm_leg_weights(chain_iv, spot, T).keys())
    vol_surface_ref = vol_surface_reference.compute_vol_surface_reference(
        "BACKTEST", chain_iv, spot, forward=forward, T=T)

    total = 0.0
    for (k, right), gamma in gamma_map.items():
        oi = oi_map.get((k, right), 0)
        if oi <= 0 or gamma == 0:
            continue
        sign = dealer_positioning._resolve_sign(
            right, k, 'vol_surface_replication', otm_strikes, vol_surface_ref)
        total += sign * gamma * oi
    return total


def _forward_realized_vol(closes_from_today: List[float], window: int) -> Optional[float]:
    """Annualized close-to-close realized vol over the next `window` trading
    days, given a list of closes starting at today's close (index 0) through
    at least `window` more trading days. Returns None if there aren't enough
    forward closes yet (caller should leave that day unlabeled, not
    zero-fill it -- an unlabeled day is honest; a zero-filled one silently
    biases the comparison).
    """
    if len(closes_from_today) < window + 1:
        return None
    prices = closes_from_today[:window + 1]
    log_rets = np.diff(np.log(prices))
    if len(log_rets) < 2:
        return None
    return float(np.std(log_rets, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))


def _build_day_records(ticker: str, expiry: str,
                        hist_greek_rows: List[dict], hist_oi_rows: List[dict],
                        hist_price_rows: List[dict],
                        forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
                        ) -> List[DayRecord]:
    """Pure function over already-fetched historical rows -- the part
    tests/test_backtest_stage3.py exercises directly with synthetic data,
    same split as replication_reference._accumulate_from_history.
    """
    expiry_date = datetime.strptime(expiry, "%Y%m%d")

    close_by_date_pre: Dict[str, float] = {}
    for row in hist_price_rows:
        d = row.get('date') or replication_reference._parse_hist_date(row)
        try:
            c = float(row.get('close', 0) or 0)
        except (TypeError, ValueError):
            continue
        if d and c > 0:
            close_by_date_pre[d] = c

    gamma_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    iv_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    n_derived = n_vendor = n_unrecoverable = 0

    for row in hist_greek_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row['strike'])))
            right = row['right']
        except (KeyError, TypeError, ValueError):
            continue

        iv = float(row.get('implied_vol', 0) or 0)
        gamma = float(row.get('gamma', 0) or 0)

        # Rows from hist/option/eod carry prices but no greeks -- that route
        # is the only per-contract one that honors a date range, which is why
        # we buy prices and reconstruct the rest. Vendor greeks are still
        # used verbatim when present, so a mixed source (or a future fix to
        # the greeks route) needs no change here.
        if iv <= 0 or gamma <= 0:
            spot = close_by_date_pre.get(d)
            if not spot:
                n_unrecoverable += 1
                continue
            T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
            mark = implied_vol_mod.mid_price(row.get('bid'), row.get('ask'),
                                             row.get('close'))
            solved = implied_vol_mod.implied_vol(
                mark, spot, k, T, _BACKTEST_R, _BACKTEST_Q, right)
            if solved is None:
                # Deliberately NOT zero-filled. A strike whose price carries
                # no recoverable vol is missing information, and imputing a
                # number here would put fabricated points into the smile that
                # v2's whole sign convention is fitted to.
                n_unrecoverable += 1
                continue
            iv = solved
            gamma = dealer_positioning.bs_gamma(spot, k, T, _BACKTEST_R, _BACKTEST_Q, iv)
            n_derived += 1
        else:
            n_vendor += 1

        if iv > 0:
            iv_by_date[d][(k, right)] = iv
        if gamma > 0:
            gamma_by_date[d][(k, right)] = gamma

    if n_derived or n_unrecoverable:
        total = n_derived + n_vendor + n_unrecoverable
        print(f"  [backtest_stage3] IV/gamma source: {n_derived} derived from price, "
              f"{n_vendor} vendor, {n_unrecoverable} unrecoverable "
              f"({100.0 * n_unrecoverable / total:.1f}% dropped) of {total} rows")

    oi_by_date: Dict[str, Dict[Tuple[float, str], int]] = defaultdict(dict)
    for row in hist_oi_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row['strike'])))
            right = row['right']
            oi = int(float(row.get('open_interest', 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue
        oi_by_date[d][(k, right)] = oi

    close_by_date: Dict[str, float] = {}
    for row in hist_price_rows:
        d = row.get('date') or replication_reference._parse_hist_date(row)
        try:
            close = float(row.get('close', 0) or 0)
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
        d for d in gamma_by_date
        if d in oi_by_date and d in iv_by_date and d in close_by_date
    )
    print(f"  [backtest_stage3] {ticker} {expiry} date coverage: "
          f"gamma={len(gamma_by_date)} iv={len(iv_by_date)} oi={len(oi_by_date)} "
          f"close={len(close_by_date)} -> {len(trading_dates)} usable days")
    if trading_dates and len(trading_dates) < min(len(gamma_by_date), len(oi_by_date),
                                                   len(close_by_date)):
        missing_oi = sorted(set(gamma_by_date) - set(oi_by_date))[:5]
        missing_close = sorted(set(gamma_by_date) - set(close_by_date))[:5]
        if missing_oi:
            print(f"  [backtest_stage3] dates with greeks but no OI (first 5): {missing_oi}")
        if missing_close:
            print(f"  [backtest_stage3] dates with greeks but no close (first 5): {missing_close}")

    records: List[DayRecord] = []
    for i, d in enumerate(trading_dates):
        spot = close_by_date[d]
        T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
        forward = spot * math.exp((_BACKTEST_R - _BACKTEST_Q) * T)

        gamma_map = gamma_by_date[d]
        oi_map = oi_by_date[d]
        chain_iv = iv_by_date[d]

        net_v1 = _net_gamma_v1(gamma_map, oi_map)
        net_v2 = _net_gamma_v2(gamma_map, oi_map, chain_iv, spot, forward, T)

        # Forward realized vol uses ANY available future close (not just the
        # dates that happen to have a full option chain snapshot), since
        # price history is denser than chain-snapshot history and there's no
        # reason to throw away real trading days just because that
        # particular day wasn't also an OI/greeks history date.
        future_closes = [spot] + [
            close_by_date[fd] for fd in sorted(close_by_date)
            if fd > d
        ][:forward_window_days]
        fwd_vol = _forward_realized_vol(future_closes, forward_window_days)

        records.append(DayRecord(
            date=d, spot=spot, net_gamma_v1=net_v1, net_gamma_v2=net_v2,
            regime_v1='long' if net_v1 > 0 else 'short',
            regime_v2='long' if net_v2 > 0 else 'short',
            fwd_realized_vol=fwd_vol,
        ))

    return records


def _summarize(records: List[DayRecord], regime_attr: str) -> dict:
    """Welch's two-sample t-test (unequal variance) between forward realized
    vol on 'short' vs 'long' gamma days for one model's regime
    classification. Welch's, not Student's, because there's no reason to
    assume the two regimes have equal variance -- and a regime with very
    unequal counts (which these are, especially v1's flat heuristic) is
    exactly the case where that assumption would matter most.
    """
    long_vols = [r.fwd_realized_vol for r in records
                 if getattr(r, regime_attr) == 'long' and r.fwd_realized_vol is not None]
    short_vols = [r.fwd_realized_vol for r in records
                  if getattr(r, regime_attr) == 'short' and r.fwd_realized_vol is not None]

    out = {
        'n_long': len(long_vols), 'n_short': len(short_vols),
        'long_mean_vol': float(np.mean(long_vols)) if long_vols else float('nan'),
        'short_mean_vol': float(np.mean(short_vols)) if short_vols else float('nan'),
        'diff': float('nan'), 'tstat': float('nan'), 'pvalue': float('nan'),
    }
    if len(long_vols) >= 2 and len(short_vols) >= 2:
        out['diff'] = out['short_mean_vol'] - out['long_mean_vol']
        t_res = _scipy_stats.ttest_ind(short_vols, long_vols, equal_var=False)
        out['tstat'] = float(t_res.statistic)
        out['pvalue'] = float(t_res.pvalue)
    return out


def _run_backtest_from_history(ticker: str, expiry: str,
                                hist_greek_rows: List[dict], hist_oi_rows: List[dict],
                                hist_price_rows: List[dict],
                                forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
                                ) -> BacktestResult:
    records = _build_day_records(ticker, expiry, hist_greek_rows, hist_oi_rows,
                                  hist_price_rows, forward_window_days)
    if not records:
        raise ValueError(
            f"No overlapping greeks/OI/price history for {ticker} {expiry} -- "
            f"nothing to backtest."
        )

    v1 = _summarize(records, 'regime_v1')
    v2 = _summarize(records, 'regime_v2')

    return BacktestResult(
        ticker=ticker, expiry=expiry, forward_window_days=forward_window_days,
        day_records=records,
        v1_n_long=v1['n_long'], v1_n_short=v1['n_short'],
        v1_long_mean_vol=v1['long_mean_vol'], v1_short_mean_vol=v1['short_mean_vol'],
        v1_diff=v1['diff'], v1_tstat=v1['tstat'], v1_pvalue=v1['pvalue'],
        v2_n_long=v2['n_long'], v2_n_short=v2['n_short'],
        v2_long_mean_vol=v2['long_mean_vol'], v2_short_mean_vol=v2['short_mean_vol'],
        v2_diff=v2['diff'], v2_tstat=v2['tstat'], v2_pvalue=v2['pvalue'],
    )


def run_backtest(ticker: str, expiration: Optional[str] = None, target_years: float = 0.25,
                  lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                  forward_window_days: int = DEFAULT_FORWARD_WINDOW_DAYS,
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
        expiry, _ = expiry_selector.resolve_expiration(td, ticker, expiration, target_years)

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
        #   option_bulk_hist_greeks  -> ~47,000 requests (one DAY per call)
        #   option_bulk_hist_eod     ->     ~430 requests (full range per call)
        #   option_bulk_hist_oi_by_day ->   ~110 requests (whole chain per day)
        # The EOD route carries no greeks, so _build_day_records inverts IV
        # from the prices and derives gamma. See implied_vol.py for the
        # provenance caveat that buys.
        hist_greek_rows = td.option_bulk_hist_eod(ticker, expiry, start_str, end_str)
        hist_oi_rows = td.option_bulk_hist_oi_by_day(ticker, expiry, start_str, end_str)
        hist_price_rows = td.hist_stock_eod(ticker, start_str, end_str)
    finally:
        td.close()

    return _run_backtest_from_history(ticker, expiry, hist_greek_rows, hist_oi_rows,
                                       hist_price_rows, forward_window_days)


def format_backtest_report(result: BacktestResult) -> str:
    lines = [
        f"Stage 3 backtest -- {result.ticker} {result.expiry} "
        f"({len(result.day_records)} days, {result.forward_window_days}d forward window)",
        "",
        f"{'':20s}{'v1 (oi_heuristic)':>22s}{'v2 (vol_surface_replication)':>32s}",
        f"{'long-gamma days':20s}{result.v1_n_long:>22d}{result.v2_n_long:>32d}",
        f"{'short-gamma days':20s}{result.v1_n_short:>22d}{result.v2_n_short:>32d}",
        f"{'mean vol | long':20s}{result.v1_long_mean_vol:>22.4f}{result.v2_long_mean_vol:>32.4f}",
        f"{'mean vol | short':20s}{result.v1_short_mean_vol:>22.4f}{result.v2_short_mean_vol:>32.4f}",
        f"{'short - long':20s}{result.v1_diff:>22.4f}{result.v2_diff:>32.4f}",
        f"{'t-stat':20s}{result.v1_tstat:>22.3f}{result.v2_tstat:>32.3f}",
        f"{'p-value':20s}{result.v1_pvalue:>22.4f}{result.v2_pvalue:>32.4f}",
        "",
        "Hypothesis: short-gamma days should show HIGHER forward realized vol "
        "(dealers trade with the tape) -- a positive, statistically significant "
        "diff supports the model; a larger, more significant diff for v2 than "
        "v1 means the richer sign convention is reading something real, not "
        "just producing a more sophisticated-looking chart.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LOOKBACK_DAYS
    result = run_backtest(ticker, lookback_days=lookback)
    print(format_backtest_report(result))
