"""
Unit tests for pooled_panel_backtest.py -- the ticker-fixed-effects panel
regression of forward realized vol on net-gamma (the pooled PRIMARY test).

Same stage-1 discipline as test_backtest_stage3.py: prove the STATISTICAL
MACHINERY on synthetic data with a known, deliberately-embedded answer before
trusting it on real (weak-signal) data. Network-free -- builds DayRecords by
hand, never touches ThetaDataController.

Key properties verified:
  * a planted within-ticker negative net-gamma -> higher fwd vol relationship
    is recovered with coef < 0 and both permutation nulls significant;
  * a null relationship stays null (no false positive at alpha=0.05);
  * ticker fixed effects actually absorb cross-ticker LEVEL differences
    (a ticker whose vol level is higher across the board does not masquerade
    as a net-gamma effect once dummies are in);
  * degenerate small samples return NaN coefs, not garbage.
"""
import numpy as np
import pytest

import pooled_panel_backtest as ppb
from backtest_stage3 import DayRecord


def _panel(rng, n_per_ticker=60, tickers=("A", "B"), beta=0.0,
           ticker_vol_shift=None):
    """Build pooled (ticker, DayRecord) pairs.

    y = intercept + beta*net_gamma + 0.5*(atm_iv - 0.30) + noise,
    plus an optional per-ticker additive vol-level shift (the FE trap).
    """
    recs = []
    for t in tickers:
        shift = (ticker_vol_shift or {}).get(t, 0.0)
        for _ in range(n_per_ticker):
            ng = rng.normal(0.0, 1.0)
            iv = 0.30 + 0.10 * rng.normal()
            y = 0.40 + beta * ng + 0.5 * (iv - 0.30) + shift + rng.normal(0.0, 0.05)
            recs.append((t, DayRecord(
                date="d", spot=1.0,
                net_gamma_v1=ng, net_gamma_v2=ng, net_gamma_v3=ng, net_delta_oi=ng,
                regime_v1="short", regime_v2="short", regime_v3="short",
                fwd_realized_vol=y, atm_iv=iv, T=0.15)))
    return recs


def test_recovers_planted_negative_effect():
    """The pooled test must detect the relationship it was built to detect."""
    rng = np.random.default_rng(0)
    res = ppb._pooled_regression(_panel(rng, beta=-0.05), "net_gamma_v2")
    assert res["coef"] < 0
    assert res["perm_p_full"] < 0.05
    assert res["perm_p_block"] < 0.05


def test_null_stays_null():
    """No planted relationship -> no false positive under either null."""
    rng = np.random.default_rng(1)
    res = ppb._pooled_regression(_panel(rng, beta=0.0), "net_gamma_v1")
    assert abs(res["coef"]) < 0.02
    assert res["perm_p_full"] > 0.05
    assert res["perm_p_block"] > 0.05


def test_fixed_effects_absorb_ticker_levels():
    """A pure cross-ticker LEVEL difference must not read as a net-gamma
    effect. Ticker B trades at +0.25 vol higher every day, with NO
    within-ticker relationship: coef must stay ~0."""
    rng = np.random.default_rng(2)
    recs = _panel(rng, beta=0.0, ticker_vol_shift={"B": 0.25})
    res = ppb._pooled_regression(recs, "net_gamma_v2")
    assert abs(res["coef"]) < 0.03
    assert res["perm_p_block"] > 0.05


def test_small_sample_returns_nan():
    """Fewer than 10 usable rows -> NaN coefs, no crash (matches
    _summarize_regression's guard in backtest_stage3)."""
    rng = np.random.default_rng(3)
    recs = _panel(rng, n_per_ticker=3, tickers=("A", "B"))
    res = ppb._pooled_regression(recs, "net_gamma_v3")
    assert res["n"] < 10
    assert np.isnan(res["coef"])
    assert np.isnan(res["perm_p_full"])


def test_block_permutation_respects_ticker_identity():
    """Block (within-ticker) permutation must preserve the panel's power when
    the effect is genuinely within-ticker: it should agree with the full
    shuffle, not water it down."""
    rng = np.random.default_rng(4)
    res = ppb._pooled_regression(_panel(rng, beta=-0.08), "net_delta_oi")
    assert res["perm_p_block"] < 0.05
    assert res["perm_p_full"] < 0.05


# ---------------------------------------------------------------------------
# THE M2 JOINT TEST (battery-consolidated-20260811.md TEST 3, branch (b)):
# branch (b) Δvanna greek-drift (book_b) vs M2 net delta-OI.
# ---------------------------------------------------------------------------

def _panel_with_book_b(rng, n_per_ticker=60, tickers=("A", "B"), book_b_scale=1.0,
                       ticker_vol_shift=None):
    """_panel + a book_b column. M2 (net_delta_oi) = ng; book_b = book_b_scale*ng,
    so the sign relationship between book_b and M2 is controlled exactly:
    scale>0 => collinear (AGREE), scale<0 => OPPOSE."""
    recs = []
    for t in tickers:
        shift = (ticker_vol_shift or {}).get(t, 0.0)
        for _ in range(n_per_ticker):
            ng = rng.normal(0.0, 1.0)
            iv = 0.30 + 0.10 * rng.normal()
            y = 0.40 - 0.05 * ng + 0.5 * (iv - 0.30) + shift + rng.normal(0.0, 0.05)
            recs.append((t, DayRecord(
                date="d", spot=1.0,
                net_gamma_v1=ng, net_gamma_v2=ng, net_gamma_v3=ng, net_delta_oi=ng,
                book_b=book_b_scale * ng,
                regime_v1="short", regime_v2="short", regime_v3="short",
                fwd_realized_vol=y, atm_iv=iv, T=0.15)))
    return recs


def test_joint_book_b_collinear_detects_agreement():
    """collinear branch: book_b = +scale*M2 -> sign-agreement ~1, Spearman rho
    near +1. This is the pre-registered 'inversion ROBUST' mapping (>=80% / rho
    >=0.7). Also checks the bivariate regression emits a book_b coef."""
    rng = np.random.default_rng(9)
    res = ppb._pooled_joint_book_b(_panel_with_book_b(rng, book_b_scale=2.0),
                                   n_perm=300)
    assert res["sign_agreement"] >= 0.8
    assert res["spearman_rho"] > 0.9
    assert np.isfinite(res["coef"])
    assert res["coef"] < 0  # more book_b (positive scale) -> more dealer-short -> higher vol


def test_joint_book_b_oppose_detects_negative_agreement():
    """OPPOSE branch: book_b = -scale*M2 -> sign-agreement ~0, Spearman rho
    near -1. This is the pre-registered 'separate volga book' mapping (<=40% /
    rho<=-0.3)."""
    rng = np.random.default_rng(10)
    res = ppb._pooled_joint_book_b(_panel_with_book_b(rng, book_b_scale=-2.0),
                                   n_perm=200)
    assert res["sign_agreement"] <= 0.4
    assert res["spearman_rho"] <= -0.3


def test_joint_book_b_small_sample_returns_nan():
    """Fewer than 10 usable rows -> NaN coefs and rho, no crash (matches the
    existing _pooled_regression guard)."""
    rng = np.random.default_rng(11)
    res = ppb._pooled_joint_book_b(_panel_with_book_b(rng, n_per_ticker=3,
                                                      book_b_scale=1.0), n_perm=50)
    assert res["n"] < 10
    assert np.isnan(res["coef"])
    assert np.isnan(res["perm_p_block"])
