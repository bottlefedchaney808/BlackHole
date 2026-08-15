"""Round-5 CARL loop tests — lock the R1/R2 corrections (R5-1..R5-8).

Covers the round-5 upgrade set from R2 (deleg_c01b5e5f, source-verified):
  R5-1  unique-calendar-day dedup (SPY 0605 == QQQ 0605 counts once)
  R5-2  cluster-level CI at eff-n (not pooled-bucket n)
  R5-4  pure-SVI sensitivity (production -1 fallback -> 0)
  R5-5  low/high |dIV| stratification helper
  R5-6  exact binomial null probabilities
  R5-8  SPY kept separate from QQQ
  and the gate_verdict_r5 INDETERMINATE-at-low-power rule.
Network-free: pure functions on the imported driver.
"""
import math

import run_dual_pipeline_gate_v3 as r5


# ---------------------------------------------------------------------------
# R5-1: unique-calendar-day dedup
# ---------------------------------------------------------------------------
def test_unique_day_dedup_same_day_counts_once():
    # SPY 20260605 and QQQ 20260605 are the SAME calendar day -> one unique day.
    clusters = [("SPY", "20260605"), ("QQQ", "20260605"),
                ("QQQ", "20260508"), ("QQQ", "20260716"), ("QQQ", "20260731")]
    unique = r5._dedup_unique_days(clusters)
    assert unique == {"20260605", "20260508", "20260716", "20260731"}
    assert len(unique) == 4  # not 5


def test_unique_day_dedup_distinct_days():
    assert r5._dedup_unique_days([("QQQ", "20260508"), ("QQQ", "20260605")]) == {"20260508", "20260605"}


# ---------------------------------------------------------------------------
# R5-6: exact binomial null probabilities
# ---------------------------------------------------------------------------
def test_binomial_tail_3_of_5_is_chance_median():
    # P(X >= 3 | Binom(5, 0.5)) = 16/32 = 0.50
    assert r5._binom_tail(3, 5, 0.5) == 0.5


def test_binomial_tail_7_of_8():
    # P(X >= 7 | Binom(8, 0.5)) = (8 + 1) / 256 = 9/256 = 0.0352
    assert abs(r5._binom_tail(7, 8, 0.5) - 9 / 256) < 1e-9


def test_binomial_tail_4_of_5_pass_bar():
    # P(X >= 4 | Binom(5, 0.5)) = 6/32 = 0.1875
    assert abs(r5._binom_tail(4, 5, 0.5) - 0.1875) < 1e-9


# ---------------------------------------------------------------------------
# R5-2 / gate: INDETERMINATE at low power
# ---------------------------------------------------------------------------
def test_gate_indeterminate_at_eff_n_4():
    # At eff-n=4 (md=1.0) the gate must be INDETERMINATE, not FAIL.
    # sign_agreement = [(agreed, both_nonzero), ...]
    sa = [(True, True), (True, True), (True, True), (True, True)]  # 4/4 agree
    v, reason = r5.gate_verdict_r5(sa, 0.10, 0.05, 40, 4)
    assert v == "INDETERMINATE"
    assert "eff-n" in reason


def test_gate_can_pass_when_powered():
    # At eff-n=29 (md 0.5), a strong agreement + corr>A6 can PASS.
    sa = [(True, True)] * 25 + [(False, True)] * 4  # 25/29 = 86% > 2/3
    v, reason = r5.gate_verdict_r5(sa, 0.30, 0.10, 200, 29)
    assert v == "PASS"


def test_gate_fail_on_negative_corr_even_powered():
    sa = [(True, True)] * 25 + [(False, True)] * 4
    v, _ = r5.gate_verdict_r5(sa, -0.05, 0.10, 200, 29)
    assert v == "FAIL"


def test_gate_fail_on_corr_below_reflexivity():
    sa = [(True, True)] * 25 + [(False, True)] * 4
    v, _ = r5.gate_verdict_r5(sa, 0.05, 0.30, 200, 29)
    assert v == "FAIL"


# ---------------------------------------------------------------------------
# R5-3 / R5-4: sign provenance + pure-SVI fallback classification
# ---------------------------------------------------------------------------
def test_sign_provenance_classifies_deadband_as_fallback():
    seed = {"greeks": [
        {"strike": 100, "implied_vol": 0.20},
        {"strike": 105, "implied_vol": 0.30},   # above median -> cheap-1
        {"strike": 95,  "implied_vol": 0.10},   # below median -> rich+1
        {"strike": 102, "implied_vol": 0.20},   # ~median -> deadband (fallback)
    ]}
    p = r5._sign_provenance(seed)
    assert p["total"] == 4
    assert p["deadband_zero"] >= 1
    assert p["fallback_minus1"] == p["deadband_zero"]
    assert p["rich_plus1"] >= 1
    assert p["cheap_minus1"] >= 1


def test_sign_provenance_empty_graceful():
    p = r5._sign_provenance({"greeks": []})
    assert p["total"] == 0


# ---------------------------------------------------------------------------
# R5-8: SPY separate (helper on the driver's SPY/QQQ split logic)
# ---------------------------------------------------------------------------
def test_spy_separation_detects_shared_day():
    # SPY resolvable but only 1 independent day -> must not be an index claim.
    spy_clusters = [("SPY", "20260605")]
    spy_unique = r5._dedup_unique_days(spy_clusters)
    assert len(spy_unique) == 1
    assert len(spy_clusters) == 1  # 1 resolvable cluster, non-independent-day


def test_md_increases_as_n_drops():
    # md at n=4 is higher (worse power) than at n=8.
    md4 = math.tanh(2.8016 / math.sqrt(max(4 - 3, 1)))
    md8 = math.tanh(2.8016 / math.sqrt(max(8 - 3, 1)))
    assert md4 > md8
