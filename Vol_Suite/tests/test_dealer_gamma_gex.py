"""v1 (oi_heuristic) must match the conventional GEX sign convention.

The conventional GEX convention (SqueezeMetrics / SpotGamma style, documented
on dealer_positioning._dealer_sign) is: call open interest contributes
POSITIVE dealer gamma exposure, put open interest NEGATIVE. Net gamma =
sum(call_gamma*call_OI) - sum(put_gamma*put_OI). v1 (_net_gamma_v1) must
honor this exactly -- a call-only book reads net long gamma, a put-only book
net short gamma.
"""
import backtest_stage3 as bs3
from dealer_positioning import _dealer_sign


def test_dealer_sign_convention():
    assert _dealer_sign("C") == 1.0   # call OI -> positive gamma exposure
    assert _dealer_sign("P") == -1.0  # put OI -> negative gamma exposure


def test_v1_call_only_book_is_net_long_gamma():
    gamma_map = {(100.0, "C"): 0.05}
    oi_map = {(100.0, "C"): 10}
    assert bs3._net_gamma_v1(gamma_map, oi_map) > 0.0


def test_v1_put_only_book_is_net_short_gamma():
    gamma_map = {(100.0, "P"): 0.04}
    oi_map = {(100.0, "P"): 10}
    assert bs3._net_gamma_v1(gamma_map, oi_map) < 0.0


def test_v1_call_and_put_offset():
    gamma_map = {(100.0, "C"): 0.05, (100.0, "P"): 0.05}
    oi_map = {(100.0, "C"): 1, (100.0, "P"): 1}
    assert bs3._net_gamma_v1(gamma_map, oi_map) == 0.0
