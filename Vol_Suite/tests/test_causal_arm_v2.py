"""R10.2 causal-arm driver — production-grade network-free tests (L8).

Supersedes the simplistic sequential-projection helpers in
test_causal_arm_r10_helpers.py (the reference warns those are NOT a valid
multivariate regression). These tests exercise the ACTUAL production driver
`run_causal_arm_v2` (numpy lstsq, rank/condition/VIF diagnostics,
NOT-IDENTIFIABLE disposition) plus the pre-event cutoff, unique-day dedup,
locked-h, family-rule, and BOTH-CLOCK-CONFIRMED machinery.

Covers (all L8 requirements):
  1. rank-deficiency handling -> β NOT-IDENTIFIABLE, not a junk number
  2. exact unique-day effective-n (same-day SPY/QQQ = 1 unit)
  3. same-day family dedup in power/CI/gates
  4. locked-horizon enforcement (a lag sweep cannot change the locked h)
  5. pre-event cutoff integrity (no post-treatment quantity leaks in)
  6. β unavailable/zero when collinear
  7. both-clock-confirmed + family opposite-sign rule
  8. FWL equivalence of the direct multivariate fit vs explicit residualization

No network, no acquisition, deterministic.
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic in-memory fixtures
# ---------------------------------------------------------------------------
def _day(ticker, day, pre_vanna, div, resp, **kw):
    """One day-record in the shape run_causal_arm consumes."""
    fam_spy = pre_vanna if ticker == "SPY" else 0.0
    fam_qqq = pre_vanna if ticker == "QQQ" else 0.0
    record = {
        "day": day,
        "date": day,
        "ticker": ticker,
        "families": [ticker],
        "pre_window": {"pre_vanna_timestamp": 0.0, "pre_window_end": 0.0,
                       "breach_window_start": 1000.0, "cutoff_pass": True},
        "l2": {
            "pre_vanna_exposure": pre_vanna,
            "delta_iv": kw.get("div", div),
            "gamma_burst": kw.get("gamma", 0.0),
            "delta_s": kw.get("ds", 0.0),
            "market": kw.get("mkt", 0.0),
            "event": kw.get("event", 0),
            "a6_reflexivity": kw.get("a6", 0.0),
            "cross_family_spillover": kw.get("spill", 0.0),
            "family_interaction_spy": fam_spy,
            "family_interaction_qqq": fam_qqq,
            "forward_return_h": resp,
        },
        "clock": {
            "daily": {"return": kw.get("daily_ret"), "eligible": kw.get("daily_eligible", False)},
            "from_breach": {"return": kw.get("breach_ret"), "eligible": kw.get("breach_eligible", False)},
        },
    }
    return record


def _genuine_vanna_fixture(n_days=40):
    """Genuine vanna signal: forward return scales with pre_vanna x ΔIV, plus
    independent control noise. Unique days 20260101.. Each day one family (SPY),
    so n_eff = n_days. Controls (gamma, ΔS, market, A6, spill, event) are seeded
    continuous random values so the design is genuinely full-rank (deterministic
    periodic control patterns become linearly dependent at moderate n)."""
    recs = []
    rng = np.random.RandomState(42)
    pre_v = rng.uniform(0.5, 2.0, n_days)          # varying exposure
    div = rng.choice([-0.02, -0.01, 0.01, 0.02], n_days)
    gamma = rng.normal(0.0, 0.05, n_days)
    ds = rng.normal(0.0, 0.002, n_days)
    mkt = rng.normal(0.0, 0.002, n_days)
    event = rng.binomial(1, 0.15, n_days).astype(float)
    a6 = rng.normal(0.0, 0.03, n_days)             # varying A6 reflexivity baseline
    spill = rng.normal(0.0, 0.02, n_days)          # varying cross-family spillover
    noise = rng.normal(0.0, 0.2, n_days)
    for i in range(n_days):
        day = f"2026{100 + i:03d}"
        resp = 3.0 * pre_v[i] * div[i] + 0.3 * gamma[i] + noise[i]   # beta=3
        recs.append(_day("SPY", day, pre_v[i], div[i], resp, gamma=gamma[i],
                         ds=ds[i], mkt=mkt[i], event=event[i], a6=a6[i], spill=spill[i],
                         daily_ret=-0.01, daily_eligible=True,
                         breach_ret=0.02, breach_eligible=True))
    return recs


# ---------------------------------------------------------------------------
# 1. rank deficiency -> NOT-IDENTIFIABLE, not a junk number
# ---------------------------------------------------------------------------
def test_rank_deficient_design_beta_not_identifiable():
    # pre_vanna constant -> pre_vanna x ΔIV is exactly collinear with ΔIV main effect
    n = 30
    recs = []
    for i in range(n):
        day = f"2026{200 + i:03d}"
        recs.append(_day("SPY", day, 1.0, 0.02, 0.5))   # constant pre_vanna
    t = ca.run_causal_arm(recs)
    assert t["beta_status"] == "NOT-IDENTIFIABLE"
    assert math.isnan(t["beta"]), "collinear target must NOT yield a junk beta"
    assert t["beta_unavailable_reason"] is not None
    assert "rank" in t["beta_unavailable_reason"] or "cond" in t["beta_unavailable_reason"] \
        or "non-finite" in t["beta_unavailable_reason"]
    assert t["beta_power_available"] is False
    assert t["beta_reach_80"] is False


# ---------------------------------------------------------------------------
# 2 + 3. unique-day effective-n + family dedup
# ---------------------------------------------------------------------------
def test_unique_day_dedup_same_day_spy_qqq_one_unit():
    obs = [("SPY", "20260605"), ("QQQ", "20260605"),
           ("SPY", "20260617"), ("QQQ", "20260617"),
           ("SPY", "20260728"), ("SPY", "20260413")]
    assert ca.unique_day_count(
        [{"day": d, "date": d} for _t, d in obs]) == 4


def test_run_causal_arm_effective_n_is_unique_days():
    # 6 (ticker,day) records but only 4 unique days; n_eff must be 4
    recs = [
        _day("SPY", "20260605", 1.0, 0.02, 0.1),
        _day("QQQ", "20260605", 1.2, 0.02, 0.2),
        _day("SPY", "20260617", 1.0, -0.02, -0.1),
        _day("QQQ", "20260617", 1.2, -0.02, -0.2),
        _day("SPY", "20260728", 1.0, 0.02, 0.3),
        _day("SPY", "20260413", 1.0, -0.02, 0.05),
    ]
    t = ca.run_causal_arm(recs)
    assert t["effective_n_unique_days"] == 4


def test_family_dedup_no_double_count_in_rows():
    # same-day SPY+QQQ -> ONE row, families merged
    recs = [
        _day("SPY", "20260605", 1.0, 0.02, 0.1),
        _day("QQQ", "20260605", 1.2, 0.02, 0.2),
        _day("SPY", "20260617", 1.0, -0.02, -0.1),
    ]
    t = ca.run_causal_arm(recs)
    assert t["effective_n_unique_days"] == 2
    assert len(t["rows"]) == 2
    day_0605 = [r for r in t["rows"] if r["unique_day"] == "20260605"][0]
    assert set(day_0605["families"]) == {"SPY", "QQQ"}


def test_unique_day_target_not_reached_no_80_claim():
    # 10 unique days < 29 target -> reach_80 False regardless of signal
    recs = []
    for i in range(10):
        day = f"2026{300 + i:03d}"
        recs.append(_day("SPY", day, 1.0 + 0.5 * (i % 4), 0.02, 3.0))
    t = ca.run_causal_arm(recs)
    assert t["reach_unique_day_target"] is False
    assert t["beta_reach_80"] is False


def test_29_unique_days_can_reach_md_beta_power():
    # >=29 unique days is necessary (not sufficient) for an 80% power claim
    recs = _genuine_vanna_fixture(40)
    t = ca.run_causal_arm(recs)
    assert t["effective_n_unique_days"] >= 29
    assert t["reach_unique_day_target"] is True


# ---------------------------------------------------------------------------
# 4. locked-horizon enforcement (no best-lag)
# ---------------------------------------------------------------------------
def test_enforce_locked_horizon_rejects_best_lag():
    assert ca.enforce_locked_horizon(1, 1) is True
    assert ca.enforce_locked_horizon(2, 1) is False   # a lag sweep must not change locked h


def test_run_causal_arm_horizon_sweep_cannot_change_locked_h():
    recs = _genuine_vanna_fixture(30)
    # config passes a candidate sweep incl a non-locked horizon
    t = ca.run_causal_arm(recs, config={"locked_h_breach": 1, "horizon_sweep": [1, 2, 3]})
    assert t["horizon_locked"] is False  # the sweep tried to move it -> enforcement fires
    assert t["locked_h_breach"] == 1     # the driver still used the locked value
    t2 = ca.run_causal_arm(recs, config={"locked_h_breach": 1, "horizon_sweep": [1]})
    assert t2["horizon_locked"] is True


# ---------------------------------------------------------------------------
# 5. pre-event cutoff integrity (no post-treatment leakage)
# ---------------------------------------------------------------------------
def test_cutoff_rejects_contemporaneous_post_window():
    # breach at 1000ms, cutoff at 1000ms -> NOT strictly before -> cutoff fails
    import expiry_book_exposure as ebe
    rows = [{"strike": 100.0, "right": "C", "oi": 1000, "implied_vol": 0.25, "ms_of_day": 1000}]
    res = ca.compute_pre_event_vanna_exposure(rows, spot=100.0, T=0.05,
                                              cutoff_ts=1000.0, breach_ts=1000.0)
    assert res["cutoff_pass"] is False
    assert math.isnan(res["pre_event_vanna_exposure"])


def test_cutoff_uses_only_pre_window_rows():
    import expiry_book_exposure as ebe
    rows = [
        {"strike": 100.0, "right": "C", "oi": 1000, "implied_vol": 0.25, "ms_of_day": 500},
        {"strike": 105.0, "right": "C", "oi": 2000, "implied_vol": 0.26, "ms_of_day": 1500},  # post-cutoff
    ]
    res = ca.compute_pre_event_vanna_exposure(rows, spot=100.0, T=0.05,
                                              cutoff_ts=1000.0, breach_ts=1001.0)
    assert res["cutoff_pass"] is True
    assert res["n_rows_used"] == 1        # only the 500ms row used
    assert res["excluded_post_cutoff"] == 1


def test_run_causal_arm_cutoff_integrity_flag():
    recs = [
        _day("SPY", "20260605", 1.0, 0.02, 0.1),
    ]
    recs[0]["pre_window"]["cutoff_pass"] = False
    t = ca.run_causal_arm(recs)
    assert t["cutoff_integrity_ok"] is False


# ---------------------------------------------------------------------------
# 6. β unavailable / zero when collinear (no fabrication)
# ---------------------------------------------------------------------------
def test_beta_unavailable_when_target_collinear_with_controls():
    # constant exposure x varying ΔIV -> target == constant*ΔIV == collinear with ΔIV main effect
    n = 30
    recs = []
    for i in range(n):
        day = f"2026{400 + i:03d}"
        recs.append(_day("SPY", day, 1.0, 0.02 * (1 if i % 2 else -1), 0.3))
    t = ca.run_causal_arm(recs)
    assert t["beta_status"] == "NOT-IDENTIFIABLE"
    assert math.isnan(t["beta"])


# ---------------------------------------------------------------------------
# FWL equivalence: direct multivariate fit == explicit residualization
# ---------------------------------------------------------------------------
def test_fwl_equivalence_direct_vs_residualized():
    n = 40
    recs = _genuine_vanna_fixture(n)
    uniq = ca.deduplicate_family_day(recs)
    des = ca.build_design(uniq, family_l2=True)
    X, y = des["X"], des["y"]
    cols = des["col_names"]
    target = "pre_vanna_x_delta_iv"
    t_idx = cols.index(target)
    control_cols = [j for j in range(len(cols)) if j != t_idx]

    # direct fit
    fit = ca._fit_ols(X, y, target, cols)
    beta_direct = fit["beta"]

    # FWL: residualize x and y on controls, then regress residuals
    Xc = np.hstack([np.ones((n, 1)), X[:, control_cols]])
    b_x, *_ = np.linalg.lstsq(Xc, X[:, t_idx], rcond=None)
    res_x = X[:, t_idx] - Xc @ b_x
    b_y, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    res_y = y - Xc @ b_y
    beta_fwl = float((res_x @ res_y) / (res_x @ res_x))

    assert math.isfinite(beta_direct)
    assert abs(beta_direct - beta_fwl) < 1e-6


def test_genuine_vanna_beta_survives_controls():
    recs = _genuine_vanna_fixture(40)
    t = ca.run_causal_arm(recs)
    assert t["beta_status"] == "IDENTIFIABLE"
    assert t["beta"] > 1.0   # ~3 survives gamma/ΔIV/ΔS/market/A6/spill controls


# ---------------------------------------------------------------------------
# 7. both-clock-confirmed + family opposite-sign rule
# ---------------------------------------------------------------------------
def test_both_clock_confirmed_direct():
    res = ca._both_clock_confirmed(True, -0.05, True, 0.10)
    assert res["value"] == "BOTH-CLOCK-CONFIRMED"
    assert res["confirmed"] is True


def test_both_clock_not_confirmed():
    res = ca._both_clock_confirmed(True, -0.05, False, 0.10)
    assert res["value"] == "NOT-CONFIRMED"
    assert res["confirmed"] is False


def test_family_opposite_sign_rule():
    assert ca.family_claim_allowed(0.3, -0.4)["allowed"] is True
    assert ca.family_claim_allowed(0.3, 0.4)["allowed"] is False      # same sign
    assert ca.family_claim_allowed(-0.3, 0.4)["allowed"] is True       # opposite
    assert ca.family_claim_allowed(None, 0.4)["allowed"] is False      # missing
    assert ca.family_claim_allowed(0.0, 0.4)["allowed"] is False       # zero
    assert ca.family_claim_allowed(0.3, 0.3)["allowed"] is False       # duplicate/same


def test_both_clock_cell_in_decision_table():
    # A single-family day can be both-clock SIGN-confirmed (n_confirmed_days>=1)
    # but the RE-ADMISSION cell is NOT-CONFIRMED because the opposite-sign family
    # rule requires BOTH distinct families present (single family cannot pass).
    recs = [
        _day("SPY", "20260605", 1.0, 0.02, 0.1,
             daily_ret=-0.01, daily_eligible=True,
             breach_ret=0.02, breach_eligible=True),
    ]
    t = ca.run_causal_arm(recs)
    assert t["both_clock"]["n_eligible_days"] == 1
    assert t["both_clock"]["n_confirmed_days"] == 1        # clock signs both confirm
    assert t["both_clock"]["value"] == "NOT-CONFIRMED"     # family rule blocks re-admission
    assert t["both_clock"]["confirmed"] is False
    assert t["both_clock"]["family_rule_passed"] is False  # no QQQ family present


def test_both_clock_rejected_when_family_signs_equal():
    # Two families both-clock confirmed but SAME-sign interactions => re-admission
    # blocked (opposite-sign rule). Constructed with both families present so the
    # design is identifiable; the family-interaction columns are made equal-sign.
    recs = [
        _day("SPY", "20260605", 1.0, 0.02, 0.1,
             daily_ret=-0.01, daily_eligible=True,
             breach_ret=0.02, breach_eligible=True),
        _day("QQQ", "20260605", 1.2, 0.02, 0.2,
             daily_ret=-0.01, daily_eligible=True,
             breach_ret=0.02, breach_eligible=True),
    ]
    t = ca.run_causal_arm(recs)
    assert t["both_clock"]["n_confirmed_days"] == 1
    assert t["both_clock"]["value"] == "NOT-CONFIRMED"     # equal/unknown family signs block
    assert t["both_clock"]["confirmed"] is False


def test_both_clock_cell_not_identifiable_when_clocks_not_eligible():
    recs = [
        _day("SPY", "20260605", 1.0, 0.02, 0.1,
             daily_ret=None, daily_eligible=False,
             breach_ret=None, breach_eligible=False),
    ]
    t = ca.run_causal_arm(recs)
    assert t["both_clock"]["n_eligible_days"] == 0
    assert t["both_clock"]["value"] == "NOT-IDENTIFIABLE"
    assert t["both_clock"]["confirmed"] is False


# ---------------------------------------------------------------------------
# 8. Solver + diagnostics surfaced in the decision table
# ---------------------------------------------------------------------------
def test_solver_and_diagnostics_surfaced():
    recs = _genuine_vanna_fixture(40)
    t = ca.run_causal_arm(recs)
    assert t["solver"] == "numpy.lstsq(SVD)"
    assert t["rank"] >= t["p"]
    assert t["cond"] > 0
    assert t["max_vif"] > 0
    assert "beta_status" in t


# ---------------------------------------------------------------------------
# Surprise (L5) semantics
# ---------------------------------------------------------------------------
def test_surprise_descriptive_habitat_when_missing():
    recs = [_day("SPY", "20260605", 1.0, 0.02, 0.1)]
    ss = ca.surprise_status(recs[0])
    assert ss["surprise"] is None
    assert ss["surprise_status"] == "DESCRIPTIVE-HABITAT"
    assert ss["causal_surprise_eligible"] is False


def test_surprise_operational_when_supplied():
    recs = [_day("SPY", "20260605", 1.0, 0.02, 0.1)]
    recs[0]["l2"]["surprise"] = 0.5
    recs[0]["l2"]["event_habitat"] = "FOMC"
    ss = ca.surprise_status(recs[0])
    assert ss["surprise"] == 0.5
    assert ss["causal_surprise_eligible"] is True


# ---------------------------------------------------------------------------
# Deterministic smoke (single unique-day decision table)
# ---------------------------------------------------------------------------
def test_deterministic_smoke_emits_single_table():
    recs = _genuine_vanna_fixture(29)
    t = ca.run_causal_arm(recs)
    assert t["effective_n_unique_days"] == 29
    assert len(t["rows"]) == 29
    # every row keyed by one unique day
    assert len({r["unique_day"] for r in t["rows"]}) == 29
    md = ca.render_table_md(t)
    assert "Single Decision Table" in md
    assert "BOTH-CLOCK" in md
    assert "β" in md
    assert f"effective unique-day n = **29**" in md


def test_deterministic_smoke_deterministic():
    recs = _genuine_vanna_fixture(29)
    a = ca.run_causal_arm(recs)
    b = ca.run_causal_arm(recs)
    assert a["beta"] == b["beta"]
    assert a["rows"] == b["rows"]
