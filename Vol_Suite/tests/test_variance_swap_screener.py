"""
Network-free tests for variance_swap_screener.py's missing-data handling.

variance_swap_screener.py had zero test coverage (flagged in README.md's
Phase 11 as a top-priority gap). These tests target the specific defect fixed
in FIX_PLAN_20260725.md issue 3: a ticker with insufficient price history used
to get its realized vol (and therefore its VRP) silently defaulted to 0.0 --
a missing input rendered as a real, neutral-looking reading -- which is how a
data gap produced a confident STRONG BUY/SELL signal and could land a ticker
on the "Top long/short candidates" list.

`screen_ticker` reaches out to ThetaData and yfinance/correlation_engine for
live data, so the network-facing pieces are patched here; everything under
test is the pure scoring/labeling logic downstream of those calls.
"""
import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import variance_swap_screener as vss


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------

class FakeTD:
    """Stand-in for ThetaDataController -- just enough surface for screen_ticker."""

    def __init__(self, spot=100.0, r=0.05, q=0.0):
        self._spot = spot
        self._r = r
        self._q = q

    def fetch_dividend_yield(self, ticker):
        return self._q

    def fetch_risk_free_rate(self, T):
        return self._r

    def fetch_spot_price(self, ticker):
        return self._spot

    def close(self):
        pass


def _fake_fair_variance_result(**overrides):
    base = {
        "fair_variance_swap_strike_vol_pct": 30.0,
        "atm_implied_vol_pct": 28.0,
        "convexity_premium_vol_pct": 2.0,
        "skew_bias": 0.5,
        "tail_mass": 0.05,
        "num_strikes_used": 40,
    }
    base.update(overrides)
    return base


def _patch_common(monkeypatch, fair_result=None, prices=None, resolve_expiration_return=("20261120", 0.25)):
    """Patch everything screen_ticker touches except the scoring/labeling math."""
    monkeypatch.setattr(vss, "ThetaDataController", lambda: FakeTD())
    monkeypatch.setattr(
        vss.expiry_selector, "resolve_expiration",
        lambda td, ticker, expiration, target_years: resolve_expiration_return,
    )
    monkeypatch.setattr(vss, "fetch_chain_thetadata", lambda td, ticker, expiration, r, q: object())
    monkeypatch.setattr(vss, "compute_fair_variance_strike", lambda chain, S0, T: (fair_result or _fake_fair_variance_result()))
    if prices is None:
        prices_df_col = np.array([])
    else:
        prices_df_col = np.asarray(prices)

    class FakeSeries:
        def __init__(self, values):
            self._values = values

        def values(self):
            return self._values

    # fetch_price_history returns a DataFrame-like object; screen_ticker does
    # `hist_df[ticker].values.flatten()`, so a plain dict-of-arrays with a
    # `.values` attribute per column is enough via a tiny fake.
    class FakeCol:
        def __init__(self, arr):
            self._arr = arr

        @property
        def values(self):
            return self._arr

    class FakeDF(dict):
        def __getitem__(self, key):
            return FakeCol(prices_df_col)

    monkeypatch.setattr(vss, "fetch_price_history", lambda tickers, period="2y": FakeDF())


# ---------------------------------------------------------------------------
# screen_ticker: insufficient price history
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_insufficient_price_history_yields_insufficient_data_signal(monkeypatch):
    """Fewer than 5 price points -> INSUFFICIENT DATA, not a scored BUY/SELL."""
    _patch_common(monkeypatch, prices=np.array([101.0, 100.5, 100.0]))  # only 3 points

    r = vss.screen_ticker("SPCX", 0.25)

    assert r is not None
    assert r.data_quality == "insufficient_price_history"
    assert r.signal == "INSUFFICIENT DATA"
    # The field must be NaN (unknown), never a fabricated 0.0 that reads as
    # "fair vol == realized vol, exactly balanced".
    assert math.isnan(r.vrp_pct)
    assert math.isnan(r.rv_30_pct)
    assert math.isnan(r.rv_60_pct)
    assert math.isnan(r.rv_90_pct)
    assert math.isnan(r.rv_match_pct)


@pytest.mark.unit
def test_sufficient_price_history_scores_normally(monkeypatch):
    """Control case: enough history -> a real score/signal, data_quality ok."""
    rng = np.linspace(100, 110, 300)  # 300 synthetic daily closes, mild uptrend
    _patch_common(monkeypatch, prices=rng)

    r = vss.screen_ticker("AAPL", 0.25)

    assert r is not None
    assert r.data_quality == "ok"
    assert r.signal != "INSUFFICIENT DATA"
    assert not math.isnan(r.vrp_pct)
    assert 0.0 <= r.score <= 100.0


@pytest.mark.unit
def test_insufficient_atm_iv_yields_insufficient_data_signal(monkeypatch):
    """Both call and put ATM IV are NaN -> INSUFFICIENT DATA, not a scored BUY/SELL.

    This tests FIX_PLAN issue 4: when neither call nor put IV is available at
    the ATM strike (thin/no liquidity), atm_iv must stay NaN instead of falling
    back to 0.0 (which fabricates a real-looking convexity number).
    """
    # Synthetic chain with both call and put IV as NaN at the ATM strike
    fake_result_nan_atm_iv = _fake_fair_variance_result(
        atm_implied_vol_pct=float('nan'),  # Both call/put IV missing
        convexity_premium_vol_pct=float('nan'),  # Naturally NaN when atm_iv is NaN
    )

    # Sufficient price history so the issue is purely ATM IV, not RV
    rng = np.linspace(100, 110, 300)  # 300 synthetic daily closes
    _patch_common(monkeypatch, fair_result=fake_result_nan_atm_iv, prices=rng)

    r = vss.screen_ticker("THIN", 0.25)

    assert r is not None
    # Signal must be INSUFFICIENT DATA even though we have good price history
    assert r.signal == "INSUFFICIENT DATA"
    # Both atm_iv_pct and convexity_pct must be NaN (unknown), not 0.0
    assert math.isnan(r.atm_iv_pct)
    assert math.isnan(r.convexity_pct)
    # But realized-vol fields should be fine (price history was sufficient)
    assert not math.isnan(r.rv_match_pct)


# ---------------------------------------------------------------------------
# run_variance_screener: flagged tickers must not reach candidate lists
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_flagged_ticker_excluded_from_buy_candidates(monkeypatch, tmp_path):
    """
    This is the exact shape of the SPCX bug: a ticker whose realized-vol read
    is missing must not be able to land on "Top long candidates" just because
    its score (computed with a VRP stand-in of 0.0) happened to fall low.
    """

    def fake_screen_ticker(ticker, target_years, expiration=None):
        if ticker == "GOODTICK":
            return vss.ScreenResult(
                ticker="GOODTICK", expiry="20261120", T_years=0.25, S0=100.0, F=101.0,
                fair_vol_pct=30.0, atm_iv_pct=28.0, convexity_pct=2.0, vrp_pct=5.0,
                rv_30_pct=25.0, rv_60_pct=24.0, rv_90_pct=23.0, rv_match_pct=25.0,
                skew_bias=0.5, tail_mass=0.05, num_strikes=40, score=15.0,
                signal="STRONG BUY", data_quality="ok",
            )
        # BROKENTICK: same low score, but driven by missing data, not a real
        # cheap-vol read -- must be excluded from "Top long candidates".
        return vss.ScreenResult(
            ticker="BROKENTICK", expiry="20261120", T_years=0.25, S0=115.0, F=115.0,
            fair_vol_pct=92.0, atm_iv_pct=84.0, convexity_pct=8.0, vrp_pct=float("nan"),
            rv_30_pct=float("nan"), rv_60_pct=float("nan"), rv_90_pct=float("nan"),
            rv_match_pct=float("nan"), skew_bias=0.61, tail_mass=0.12, num_strikes=31,
            score=7.5, signal="INSUFFICIENT DATA", data_quality="insufficient_price_history",
        )

    monkeypatch.setattr(vss, "screen_ticker", fake_screen_ticker)

    files, interp = vss.run_variance_screener(
        ["GOODTICK", "BROKENTICK"], target_years=0.25, output_dir=str(tmp_path)
    )

    assert "GOODTICK" in interp.split("Top long candidates:")[1].splitlines()[0]
    assert "BROKENTICK" not in interp.split("Top long candidates:")[1].splitlines()[0]
    assert "BROKENTICK" in interp  # still surfaced, just in the flagged note, not the candidate line


# ---------------------------------------------------------------------------
# print_screener_table: must not crash on NaN fields, marker matches signal
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_print_screener_table_handles_insufficient_data_row(capsys):
    ok = vss.ScreenResult(
        ticker="OK1", expiry="20261120", T_years=0.25, S0=100.0, F=101.0,
        fair_vol_pct=30.0, atm_iv_pct=28.0, convexity_pct=2.0, vrp_pct=5.0,
        rv_30_pct=25.0, rv_60_pct=24.0, rv_90_pct=23.0, rv_match_pct=25.0,
        skew_bias=0.5, tail_mass=0.05, num_strikes=40, score=85.0,
        signal="STRONG SELL", data_quality="ok",
    )
    flagged = vss.ScreenResult(
        ticker="FLAGGED", expiry="20261120", T_years=0.25, S0=115.0, F=115.0,
        fair_vol_pct=92.0, atm_iv_pct=84.0, convexity_pct=8.0, vrp_pct=float("nan"),
        rv_30_pct=float("nan"), rv_60_pct=float("nan"), rv_90_pct=float("nan"),
        rv_match_pct=float("nan"), skew_bias=0.61, tail_mass=0.12, num_strikes=31,
        score=7.5, signal="INSUFFICIENT DATA", data_quality="insufficient_price_history",
    )

    vss.print_screener_table([ok, flagged])  # must not raise

    out = capsys.readouterr().out
    assert "[SELL]" in out
    assert "[ ?? ]" in out
    assert "missing RV history" in out


# ---------------------------------------------------------------------------
# FIX 5: DDKZ boundary correction alignment between screener and live
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fair_variance_consistency_with_boundary_correction():
    """
    FIX_PLAN_20260728 issue 5: variance_swap_screener.compute_fair_variance_strike
    must match variance_swap_live.compute_fair_variance_strike exactly, including
    the DDKZ boundary correction term.

    This test creates a synthetic option chain and verifies both functions
    produce identical fair_variance results.
    """
    import variance_swap_live as vsl

    # Synthetic chain: spot=100, forward ~102, rates/dividend typical
    S0 = 100.0
    T_years = 0.25  # 3 months
    r = 0.05
    q = 0.01

    # Create synthetic option chain:
    # Strikes: 80, 85, 90, 95, 100, 105, 110, 115, 120
    strikes = np.array([80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0])

    # Synthetic OTM prices (typically call prices above forward, put prices below)
    # Using simple price model: e.g., call = exp(-r*T) * max(0, F - K)
    F = vss.compute_forward_price(S0, r, q, T_years)  # ~102.5

    # Simple synthetic prices: OTM put prices below forward, OTM call prices above
    call_mid = np.array([0.1, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0, 3.5, 5.5])
    put_mid = np.array([17.0, 13.0, 9.5, 6.5, 4.0, 2.0, 1.0, 0.5, 0.2])

    # IV values (relatively flat smile)
    call_iv = np.array([0.22, 0.21, 0.205, 0.20, 0.20, 0.20, 0.205, 0.21, 0.22])
    put_iv = np.array([0.22, 0.21, 0.205, 0.20, 0.20, 0.20, 0.205, 0.21, 0.22])

    # Construct ChainData objects (same for both)
    chain = vss.ChainData(
        expiry="20261225",
        strikes=strikes,
        call_mid=call_mid,
        put_mid=put_mid,
        r=r,
        q=q,
        call_iv=call_iv,
        put_iv=put_iv,
    )

    # Compute fair variance using screener's function
    result_screener = vss.compute_fair_variance_strike(chain, S0, T_years)
    fair_var_screener = result_screener["fair_variance_annualized"]
    fair_vol_screener = result_screener["fair_variance_swap_strike_vol"]

    # Compute fair variance using live's function
    result_live = vsl.compute_fair_variance_strike(chain, S0, T_years)
    fair_var_live = result_live["fair_variance_annualized"]
    fair_vol_live = result_live["fair_variance_swap_strike_vol"]

    # Assert both produce the same result (accounting for floating-point rounding)
    # The DDKZ boundary correction should make them identical.
    assert abs(fair_var_screener - fair_var_live) < 1e-10, (
        f"Fair variance mismatch: screener={fair_var_screener}, live={fair_var_live}, "
        f"diff={abs(fair_var_screener - fair_var_live)}"
    )
    assert abs(fair_vol_screener - fair_vol_live) < 1e-10, (
        f"Fair vol mismatch: screener={fair_vol_screener}, live={fair_vol_live}, "
        f"diff={abs(fair_vol_screener - fair_vol_live)}"
    )
