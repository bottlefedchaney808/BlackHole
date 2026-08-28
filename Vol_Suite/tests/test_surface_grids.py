"""Tests for surface_grids.py -- strike x expiry / strike x time grid
builders for the Surface Explorer tool.

Network-free: all ThetaData interaction goes through a fake stand-in, same
pattern as test_vol_surface_2d.py / test_expiry_book_production_contract.py.
"""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pytest
import surface_grids as sg

SPOT = 100.0


def _theta(k: float) -> int:
    return int(round(k * 1000))


def _smile_iv(strike: float, spot: float = SPOT) -> float:
    x = math.log(strike / spot)
    return max(0.20 + 0.30 * x * x, 0.05)


class _FakeTD:
    """Minimal stand-in exposing every ThetaDataController method
    surface_grids.py calls: fetch_spot_price, fetch_dividend_yield,
    list_expirations, option_bulk_greeks, option_bulk_oi,
    option_session_trades."""

    def __init__(self, spot=SPOT, n_expiries=4, strikes=None, with_trades=True):
        self._spot = spot
        self._strikes = strikes or list(range(70, 131, 5))
        today = date.today()
        self._exps = [
            (today + timedelta(days=14 * (i + 1))).strftime("%Y%m%d")
            for i in range(n_expiries)
        ]
        self._trades = None
        if with_trades and self._exps:
            base_ts = datetime.combine(today, datetime.min.time()).replace(
                hour=9, minute=30
            )
            self._trades = []
            for i, k in enumerate(self._strikes):
                self._trades.append(
                    {
                        "strike_price": _theta(k),
                        "trade_right": "C" if i % 2 == 0 else "P",
                        "expiration": self._exps[0],
                        "premium": 1000.0 + 10 * i,
                        "datetime": (base_ts + timedelta(minutes=i)).isoformat(),
                    }
                )

    def fetch_spot_price(self, ticker):
        return self._spot

    def fetch_dividend_yield(self, ticker):
        return 0.0

    def list_expirations(self, ticker):
        return list(self._exps)

    def option_bulk_greeks(self, ticker, expiry):
        rows = []
        for k in self._strikes:
            for right in ("C", "P"):
                rows.append(
                    {
                        "strike": _theta(k),
                        "right": right,
                        "implied_vol": _smile_iv(k, self._spot),
                    }
                )
        return rows

    def option_bulk_oi(self, ticker, expiry):
        return [
            {"strike": _theta(k), "right": right, "open_interest": 100}
            for k in self._strikes
            for right in ("C", "P")
        ]

    def option_session_trades(self, ticker, session):
        return list(self._trades or [])


# ---------------------------------------------------------------------------
# build_greek_surface
# ---------------------------------------------------------------------------
class TestBuildGreekSurface:
    def test_unknown_greek_raises(self):
        with pytest.raises(ValueError, match="unknown greek"):
            sg.build_greek_surface("AAPL", "theta", td=_FakeTD())

    def test_no_spot_raises(self):
        td = _FakeTD(spot=0.0)
        with pytest.raises(ValueError, match="no usable spot"):
            sg.build_greek_surface("AAPL", "gamma", td=td)

    def test_no_listed_expiries_raises(self):
        td = _FakeTD(n_expiries=0)
        with pytest.raises(ValueError, match="no listed expiries"):
            sg.build_greek_surface("AAPL", "gamma", td=td)

    def test_builds_rectangular_grid(self):
        td = _FakeTD(n_expiries=3)
        result = sg.build_greek_surface("AAPL", "gamma", td=td, max_expiries=3)
        assert result["ticker"] == "AAPL"
        assert result["greek"] == "gamma"
        assert result["spot"] == SPOT
        n_strikes = len(result["strikes"])
        assert n_strikes == sg.N_MONEYNESS
        assert len(result["expiries"]) == len(result["grid"])
        for row in result["grid"]:
            assert len(row) == n_strikes
        # dealer-frame gamma exposure should be finite everywhere
        flat = np.array(result["grid"]).ravel()
        assert np.all(np.isfinite(flat))

    def test_max_expiries_caps_rows(self):
        td = _FakeTD(n_expiries=6)
        result = sg.build_greek_surface("AAPL", "delta", td=td, max_expiries=2)
        assert len(result["expiries"]) <= 2


# ---------------------------------------------------------------------------
# build_market_iv_surface
# ---------------------------------------------------------------------------
class TestBuildMarketIvSurface:
    def test_builds_grid_within_smile_bounds(self):
        td = _FakeTD(n_expiries=3, strikes=list(range(60, 141, 2)))
        result = sg.build_market_iv_surface("AAPL", td=td, n_strikes=15, n_tenors=8)
        assert result["ticker"] == "AAPL"
        grid = np.array(result["grid"])
        assert grid.shape == (8, 15)
        assert np.all(grid >= 0.0)
        assert np.all(np.isfinite(grid))

    def test_insufficient_tenors_raises(self):
        td = _FakeTD(n_expiries=0)
        with pytest.raises(ValueError, match="could not build an IV surface"):
            sg.build_market_iv_surface("AAPL", td=td)


# ---------------------------------------------------------------------------
# build_flow_strike_time
# ---------------------------------------------------------------------------
class TestBuildFlowStrikeTime:
    def test_no_trades_raises(self):
        td = _FakeTD(with_trades=False)
        with pytest.raises(ValueError, match="no trades"):
            sg.build_flow_strike_time("AAPL", td=td)

    def test_builds_grid(self):
        td = _FakeTD(strikes=list(range(85, 116, 5)))
        result = sg.build_flow_strike_time(
            "AAPL", td=td, n_strike_bins=10, n_time_bins=6
        )
        grid = np.array(result["grid"])
        assert grid.shape == (6, 10)
        assert len(result["strike_edges"]) == 11
        assert len(result["time_labels"]) == 6
        # net premium should be nonzero somewhere (calls/puts alternate)
        assert np.any(grid != 0.0)


# ---------------------------------------------------------------------------
# build_flow_strike_expiry
# ---------------------------------------------------------------------------
class TestBuildFlowStrikeExpiry:
    def test_no_matching_expiry_raises(self):
        td = _FakeTD(with_trades=True)
        # trades are all tagged with self._exps[0]; ask for a DTE window that
        # excludes every listed expiry so none matches.
        with pytest.raises(ValueError, match="no listed expiries"):
            sg.build_flow_strike_expiry("AAPL", td=td, min_dte=9000, max_dte=9001)

    def test_builds_grid(self):
        td = _FakeTD(n_expiries=3, strikes=list(range(85, 116, 5)))
        result = sg.build_flow_strike_expiry(
            "AAPL", td=td, max_expiries=3, n_strike_bins=10
        )
        assert len(result["expiries"]) >= 1
        grid = np.array(result["grid"])
        assert grid.shape[1] == 10
        assert grid.shape[0] == len(result["expiries"])
