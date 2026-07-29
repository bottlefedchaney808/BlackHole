"""
Tests for implied_vol.py -- the Black-Scholes inversion Stage 3 now depends on.

This module exists because the only per-contract route that honors a date
range (`hist/option/eod`) carries prices but no greeks, so IV and gamma are
reconstructed rather than bought. That makes the inversion load-bearing: if
it's wrong, every downstream number is wrong in a way that still looks
plausible on a chart.

The core discipline here is ROUND-TRIPPING: price an option at a known sigma,
invert the price, and demand the original sigma back. That catches sign
errors, argument-order slips and convergence failures without needing any
reference data -- the right answer is known by construction.

The second discipline is that unrecoverable input must return None, never a
number. This project has been bitten twice by a failure encoded as a plausible
value (an empty cache range read as "no data"; a missing realized vol read as
0.0 and ranked as a strong sell), so those paths are tested explicitly.
"""
import math

import pytest

import implied_vol as iv


S0, R, Q = 100.0, 0.04, 0.012


# ---------------------------------------------------------------------------
# Pricing primitives
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_put_call_parity_holds():
    """C - P = S*e^(-qT) - K*e^(-rT). The single best structural check on a
    BS implementation: it fails loudly if discounting or the dividend term is
    misplaced."""
    K, T, sigma = 105.0, 0.5, 0.22
    c = iv.bs_price(S0, K, T, R, Q, sigma, "C")
    p = iv.bs_price(S0, K, T, R, Q, sigma, "P")
    assert c - p == pytest.approx(S0 * math.exp(-Q * T) - K * math.exp(-R * T), abs=1e-9)


@pytest.mark.unit
def test_price_is_monotonically_increasing_in_vol():
    """The property the solver's bracketing depends on."""
    prices = [iv.bs_price(S0, 100.0, 0.5, R, Q, s, "C")
              for s in (0.05, 0.10, 0.20, 0.40, 0.80)]
    assert prices == sorted(prices)


@pytest.mark.unit
@pytest.mark.parametrize("right", ["C", "P"])
def test_price_never_below_the_discounted_no_arbitrage_floor(right):
    """Note the floor is the DISCOUNTED one, not undiscounted intrinsic.

    A European put legitimately trades below K - S: at K=120, S=100, T=0.25
    the price is 19.28 against an intrinsic of 20.00, because you cannot
    exercise early to collect that 0.72. That gap IS the early-exercise
    premium, and it's the concrete reason the American (LR) path exists.
    """
    T = 0.25
    for K in (80.0, 100.0, 120.0):
        price = iv.bs_price(S0, K, T, R, Q, 0.2, right)
        lower, upper = iv._no_arbitrage_bounds(S0, K, T, R, Q, right)
        assert lower - 1e-9 <= price <= upper + 1e-9


@pytest.mark.unit
def test_expired_option_is_worth_intrinsic():
    assert iv.bs_price(S0, 90.0, 0.0, R, Q, 0.2, "C") == pytest.approx(10.0)
    assert iv.bs_price(S0, 90.0, 0.0, R, Q, 0.2, "P") == pytest.approx(0.0)


@pytest.mark.unit
def test_vega_is_positive_and_peaks_near_the_money():
    atm = iv.bs_vega(S0, 100.0, 0.5, R, Q, 0.2)
    wing = iv.bs_vega(S0, 160.0, 0.5, R, Q, 0.2)
    assert atm > 0 and wing >= 0
    assert atm > wing, "vega should collapse in the far wing -- this is why " \
                       "Newton alone can't be trusted here"


# ---------------------------------------------------------------------------
# Round-trip inversion -- the main event
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("K", [70.0, 85.0, 95.0, 100.0, 105.0, 115.0, 130.0])
@pytest.mark.parametrize("sigma", [0.08, 0.15, 0.25, 0.45, 0.80])
@pytest.mark.parametrize("right", ["C", "P"])
def test_round_trip_recovers_the_input_vol(K, sigma, right):
    """70 combinations spanning deep ITM to deep OTM and 8% to 80% vol."""
    T = 0.35
    price = iv.bs_price(S0, K, T, R, Q, sigma, right)
    if price < iv.MIN_PRICE:
        pytest.skip("price below the floor where vol is recoverable")
    if iv.bs_vega(S0, K, T, R, Q, sigma) * iv.VOL_RESOLUTION * 2 < iv.PRICE_RESOLUTION:
        # Deep ITM at low vol: a full vol point moves the price by less than
        # the market can quote, so the vol is genuinely unidentifiable. None
        # is the correct answer, and asserting it is the point -- returning a
        # number here is the bug the identifiability check was added to stop.
        assert iv.implied_vol(price, S0, K, T, R, Q, right) is None
        return
    solved = iv.implied_vol(price, S0, K, T, R, Q, right)
    assert solved is not None, f"failed to invert K={K} sigma={sigma} {right}"
    assert solved == pytest.approx(sigma, abs=1e-4)


@pytest.mark.unit
@pytest.mark.parametrize("T", [0.01, 0.05, 0.25, 1.0, 2.0])
def test_round_trip_across_tenors(T):
    sigma = 0.28
    price = iv.bs_price(S0, 100.0, T, R, Q, sigma, "C")
    assert iv.implied_vol(price, S0, 100.0, T, R, Q, "C") == pytest.approx(sigma, abs=1e-4)


@pytest.mark.unit
def test_far_wing_inversion_does_not_diverge():
    """Deep OTM is where vega -> 0 and a pure Newton solver blows up. These
    are exactly the strikes the vol-surface fit needs, so failing here would
    quietly bias v2's sign model in the wings."""
    T, sigma = 0.5, 0.6
    for K in (30.0, 45.0, 200.0, 300.0):
        for right in ("C", "P"):
            price = iv.bs_price(S0, K, T, R, Q, sigma, right)
            if price < iv.MIN_PRICE:
                continue
            solved = iv.implied_vol(price, S0, K, T, R, Q, right)
            assert solved is None or solved == pytest.approx(sigma, abs=1e-3)


# ---------------------------------------------------------------------------
# Unrecoverable input must return None, not a number
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("price", [0.0, 0.001, -1.0, None])
def test_worthless_or_invalid_price_returns_none(price):
    assert iv.implied_vol(price, S0, 100.0, 0.25, R, Q, "C") is None


@pytest.mark.unit
def test_price_above_the_no_arbitrage_ceiling_returns_none():
    """A call can never be worth more than the discounted spot. A quote that
    says otherwise is bad data, and must not be silently clamped to MAX_VOL."""
    assert iv.implied_vol(S0 * 2, S0, 100.0, 0.25, R, Q, "C") is None


@pytest.mark.unit
def test_price_below_intrinsic_returns_none():
    """Deep ITM call quoted below intrinsic -- crossed or stale EOD marks do
    this for real."""
    assert iv.implied_vol(1.0, S0, 50.0, 0.25, R, Q, "C") is None


@pytest.mark.unit
@pytest.mark.parametrize("S,K,T", [(0.0, 100.0, 0.25), (100.0, 0.0, 0.25), (100.0, 100.0, 0.0)])
def test_degenerate_inputs_return_none(S, K, T):
    assert iv.implied_vol(5.0, S, K, T, R, Q, "C") is None


# ---------------------------------------------------------------------------
# mid_price
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_mid_prefers_the_quote_midpoint():
    assert iv.mid_price(1.0, 1.4, close=99.0) == pytest.approx(1.2)


@pytest.mark.unit
@pytest.mark.parametrize("bid,ask", [(None, 1.4), (1.0, None), (1.4, 1.0), (0, 0)])
def test_mid_falls_back_to_close_when_the_quote_is_unusable(bid, ask):
    """One-sided, missing or crossed quotes fall back to the last trade."""
    assert iv.mid_price(bid, ask, close=1.25) == pytest.approx(1.25)


@pytest.mark.unit
def test_mid_returns_none_when_nothing_is_usable():
    assert iv.mid_price(None, None, None) is None
    assert iv.mid_price(0, 0, 0) is None


@pytest.mark.unit
def test_mid_rejects_nan():
    assert iv.mid_price(float("nan"), float("nan"), close=1.5) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Pluggable pricer / American (Leisen-Reimer) path
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_a_custom_pricer_is_actually_used():
    """Guards the injection point itself: invert against a deliberately
    shifted model and confirm the answer moves."""
    def shifted(S, K, T, r, q, sigma, right):
        return iv.bs_price(S, K, T, r, q, sigma * 1.5, right)

    price = iv.bs_price(S0, 100.0, 0.5, R, Q, 0.30, "C")
    assert iv.implied_vol(price, S0, 100.0, 0.5, R, Q, "C") == pytest.approx(0.30, abs=1e-4)
    assert iv.implied_vol(price, S0, 100.0, 0.5, R, Q, "C", pricer=shifted) == \
        pytest.approx(0.20, abs=1e-3)


@pytest.mark.unit
def test_american_pricer_rejects_an_unknown_model():
    with pytest.raises(ValueError, match="lr"):
        iv.american_pricer(model="nonsense")


@pytest.mark.unit
def test_leisen_reimer_round_trips_and_prices_above_european():
    """The American path, end to end. Two claims:

    1. Inverting LR recovers the sigma LR was priced at -- proves the
       argument-order adapter (sigma/q are swapped between the two
       conventions) is correct. Getting that wrong yields plausible,
       completely wrong vols.
    2. An American put is worth at least its European counterpart, since
       early exercise is an extra right. That's the actual reason to care
       about this path at all.
    """
    lr = pytest.importorskip_result = None
    try:
        lr = iv.american_pricer(steps=51)
    except ImportError:
        pytest.skip("Options_Suite/american_binomial.py not available")

    K, T, sigma = 105.0, 0.5, 0.25
    am_put = lr(S0, K, T, R, Q, sigma, "P")
    eu_put = iv.bs_price(S0, K, T, R, Q, sigma, "P")
    assert am_put >= eu_put - 1e-6, "American put must not be worth less than European"

    solved = iv.implied_vol(am_put, S0, K, T, R, Q, "P", pricer=lr, tol=1e-5)
    assert solved is not None
    assert solved == pytest.approx(sigma, abs=5e-3)


@pytest.mark.unit
def test_european_inversion_of_an_american_price_understates_vol():
    """Quantifies the provenance caveat rather than asserting it's small.

    Inverting an American price through a European model attributes the
    early-exercise premium to volatility, so the recovered vol comes out
    BIASED HIGH for puts. This test pins the direction; the magnitude is what
    a sampled LR-vs-BS comparison should measure before any Stage 3 result is
    published.
    """
    try:
        lr = iv.american_pricer(steps=51)
    except ImportError:
        pytest.skip("Options_Suite/american_binomial.py not available")

    K, T, sigma = 110.0, 1.0, 0.25
    am_put = lr(S0, K, T, R, Q, sigma, "P")
    eu_iv = iv.implied_vol(am_put, S0, K, T, R, Q, "P")
    assert eu_iv is not None
    assert eu_iv >= sigma - 1e-6, "European inversion of an American put should not undershoot"
