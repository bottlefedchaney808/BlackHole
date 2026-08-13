"""
Regression test for the Heston LSM over-discounting bug.

The backward-induction loop in `heston_lsm_price` / `_heston_lsm_price_crn`
already applies a single-step discount `exp(-r*(T/steps))` once per step
(t = steps .. 1, i.e. steps-1 discounts), so the value held in `payoff`
after the loop is the option value at t=1 in t=1 dollars. The final factor
previously discounted a FULL period `exp(-r*T)` instead of ONE step
`exp(-r*(T/steps))`, biasing prices LOW by ~exp(-r*T*(1-1/steps)) -- for
r=0.03, T=1, steps=50 that's ~2.9%, enough to push an American price below
its European-analytic lower bound.

American options are worth at least as much as their European
counterparts (early-exercise premium >= 0), so a healthy LSM price must
never sit meaningfully below the European Heston analytic price.
"""

import pytest

from Options_Suite.MCHestonLSM import heston_european_call_price, heston_lsm_price


@pytest.mark.unit
def test_heston_lsm_price_not_over_discounted():
    """LSM (American) price must not fall below the European-analytic limit."""
    S, K, T, r, q = 100.0, 100.0, 1.0, 0.03, 0.0
    V0, kappa, theta = 0.04, 1.5, 0.04
    xi, rho = 0.01, 0.0

    sims, steps = 200_000, 50

    lsm = heston_lsm_price(
        S0=S,
        K=K,
        T=T,
        r=r,
        q=q,
        V0=V0,
        kappa=kappa,
        theta=theta,
        vol_sigma=xi,
        rho=rho,
        sims=sims,
        steps=steps,
        option='call',
        seed=42,
    )
    european = heston_european_call_price(S, K, T, r, q, V0, kappa, theta, xi, rho)

    assert european > 0, f"European reference unexpectedly non-positive: {european}"
    assert lsm >= european * 0.985, (
        f"American LSM price {lsm:.6f} is {100 * (european - lsm) / european:.2f}% "
        f"below the European-analytic price {european:.6f} -- the final "
        f"exp(-r*T) discount likely over-discounts (should be one step, "
        f"exp(-r*(T/steps)))."
    )
