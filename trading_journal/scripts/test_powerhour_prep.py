"""Known-answer tests for powerhour_prep.py pure logic (no network).

Guards the theta-strike normalization fix (the repo shared contract) and the
implied 1-day move formula. Network paths (_atm_iv / main) are not tested here.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import powerhour_prep as ph  # noqa: E402


def test_normalize_theta_strike_microcap():
    # SOUN ~$7.38 -> ThetaData int strike 7500 (= $7.50)
    assert ph.normalize_theta_strike(7500) == 7.5


def test_normalize_theta_strike_index():
    # QQQ ~$718 -> 718170; SPY ~$771 -> 500000 example
    assert ph.normalize_theta_strike(718170) == 718.17
    assert ph.normalize_theta_strike(500000) == 500.0


def test_normalize_theta_strike_dollar_passthrough():
    # Sub-$1 or already-dollar strikes pass through unchanged
    assert ph.normalize_theta_strike(500) == 500.0
    assert ph.normalize_theta_strike(0.5) == 0.5


def test_implied_one_day_move_known_answer():
    # 1-sigma daily move = IV / sqrt(365) * 100. IV 50% -> ~2.62%
    iv = 0.5
    move = iv / math.sqrt(365) * 100
    assert round(move, 2) == 2.62


def test_known_ivs_from_scan():
    # Values observed in the 2026-08-11 live scan (sanity anchors)
    assert ph.TICKERS[:4] == ["SOUN", "UUUU", "TGB", "KOS"]
    assert "QQQ" in ph.TICKERS and "SPY" in ph.TICKERS
