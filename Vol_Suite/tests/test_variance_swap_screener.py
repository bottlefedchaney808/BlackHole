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
        # The vol-normalized tail mass the composite score actually reads.
        # Must be present: screen_ticker treats a missing/NaN tail_mass_z as
        # insufficient data rather than awarding the full tail term off a
        # stand-in, so a fixture that omits it silently forces every row to
        # INSUFFICIENT DATA.
        "tail_mass_z": 0.02,
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


class TestTailMassVolNormalization:
    """`tail_mass` uses a flat 0.5F/2.0F strike band, so it measures a
    different DISTANCE at every vol level -- the same defect
    replication_reference.py's range_truncation_score was already fixed for.
    `tail_mass_z` measures the band in sigma of the underlying's own
    log-return over the option's life instead.
    """

    @staticmethod
    def _flat_smile_chain(sigma, S0=100.0, T=0.25, r=0.04):
        import math

        import numpy as np
        from scipy.stats import norm

        from variance_swap_screener import ChainData

        def bs(S, K, T, r, sig, cp):
            d1 = (math.log(S / K) + (r + sig * sig / 2) * T) / (sig * math.sqrt(T))
            d2 = d1 - sig * math.sqrt(T)
            if cp:
                return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

        K = np.arange(40.0, 221.0, 2.5)
        return ChainData(
            expiry="20261016",
            strikes=K,
            call_mid=np.array([bs(S0, k, T, r, sigma, True) for k in K]),
            put_mid=np.array([bs(S0, k, T, r, sigma, False) for k in K]),
            call_iv=np.full(len(K), sigma),
            put_iv=np.full(len(K), sigma),
            r=r,
            q=0.0,
        )

    def test_flat_band_tail_mass_is_driven_by_vol_not_by_the_chain(self):
        """Characterization of the defect, so it cannot be 'fixed' unnoticed.

        The chain is IDENTICAL across these runs -- same strikes, same range.
        Only the vol level moves. A genuine tail/truncation diagnostic would
        be roughly unchanged; this swings by orders of magnitude.
        """
        from variance_swap_screener import compute_fair_variance_strike

        low = compute_fair_variance_strike(self._flat_smile_chain(0.15), 100.0, 0.25)
        high = compute_fair_variance_strike(self._flat_smile_chain(1.00), 100.0, 0.25)
        assert low["tail_mass"] < 0.001
        assert high["tail_mass"] > 0.01, (
            "if this stops holding, tail_mass may have been normalized -- "
            "update the score threshold discussion in PROJECT_AUDIT_AND_SPEC #5"
        )

    def test_normalized_tail_mass_is_stable_across_vol_on_the_same_chain(self):
        """The fix: identical chain shape => comparable tail_mass_z."""
        from variance_swap_screener import compute_fair_variance_strike

        vals = [
            compute_fair_variance_strike(self._flat_smile_chain(s), 100.0, 0.25)["tail_mass_z"]
            for s in (0.15, 0.25, 0.40)
        ]
        assert max(vals) - min(vals) < 0.005, f"tail_mass_z should be stable, got {vals}"

    def test_normalized_tail_mass_is_nan_when_atm_iv_is_missing(self):
        """A missing input must not read as 'no tail mass'."""
        import math

        import numpy as np

        from variance_swap_screener import compute_fair_variance_strike

        ch = self._flat_smile_chain(0.25)
        ch.call_iv = np.full(len(ch.strikes), float("nan"))
        ch.put_iv = np.full(len(ch.strikes), float("nan"))
        res = compute_fair_variance_strike(ch, 100.0, 0.25)
        assert math.isnan(res["tail_mass_z"])

    def test_score_uses_the_vol_normalized_metric(self):
        """The score must read tail_mass_z, not the flat-band tail_mass.

        tail_mass reads exactly 0.0000 for every name below ~40% vol, so its
        term handed a constant full 15/15 to most tickers and only ever
        penalised high-vol names -- a disguised volatility penalty. Pinned
        because reverting it would be silent: the score still computes, the
        signals still render, and every low-vol name just quietly gets its
        points back.
        """
        import inspect

        import variance_swap_screener as vss

        src = inspect.getsource(vss.screen_ticker)
        assert "TAIL_MASS_Z_SCORE_CAP" in src
        assert "tail_mass_z_for_score" in src
        assert "(0.20 - tail_mass)" not in src

    @pytest.mark.parametrize(
        "skew,expect_pts",
        [(0.00, 14.0), (0.10, 12.0), (0.20, 6.0), (0.45, 0.0)],
    )
    def test_tail_term_now_discriminates_on_skew(self, skew, expect_pts):
        """What tail_mass_z actually measures is how much of the fair value
        sits in the far-OTM wing -- i.e. smile skew -- which is the part of
        the strip a short-vol seller cannot hedge cheaply. The old metric
        could not see skew at all."""
        import math

        import numpy as np

        from variance_swap_screener import (
            TAIL_MASS_Z_SCORE_CAP,
            ChainData,
            compute_fair_variance_strike,
        )
        from scipy.stats import norm

        S0, T, r, atm = 100.0, 0.25, 0.04, 0.25

        def bs(S, K, T, r, sig, cp):
            d1 = (math.log(S / K) + (r + sig * sig / 2) * T) / (sig * math.sqrt(T))
            d2 = d1 - sig * math.sqrt(T)
            if cp:
                return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

        K = np.arange(40.0, 221.0, 2.5)
        mny = np.log(K / S0) / (atm * math.sqrt(T))
        iv = np.clip(atm * (1.0 - skew * mny), 0.02, 3.0)
        chain = ChainData(
            expiry="20261016", strikes=K,
            call_mid=np.array([bs(S0, k, T, r, s, True) for k, s in zip(K, iv)]),
            put_mid=np.array([bs(S0, k, T, r, s, False) for k, s in zip(K, iv)]),
            call_iv=iv, put_iv=iv, r=r, q=0.0,
        )
        tmz = compute_fair_variance_strike(chain, S0, T)["tail_mass_z"]
        pts = 15.0 * min(max((TAIL_MASS_Z_SCORE_CAP - tmz) / TAIL_MASS_Z_SCORE_CAP, 0.0), 1.0)
        assert pts == pytest.approx(expect_pts, abs=1.5), (
            f"skew={skew} gave tail_mass_z={tmz:.4f} -> {pts:.1f} pts"
        )
