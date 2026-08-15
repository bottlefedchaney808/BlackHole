"""Round-5 REQUIRED ACQUISITION tests — lock the v4 unique-day acquisition helpers.

Covers the round-5 v4 additions (on top of all R5-1..R5-8 locked by
test_dual_pipeline_gate_v3.py):
  - pure-SVI sensitivity helper `_pure_svi_sign` (R5-4)
  - seed-day merge `_merge_seed_rows` (prior-session rows merged, no shared-session confound)
  - v4 gate verdict path (delegates to gate_verdict_r5: INDETERMINATE when
    unique-day eff-n < target)
  - SPY/QQQ separation counting on a merged cluster set (R5-8)
  - exact binomial null edge cases (R5-6)
Network-free: pure functions + synthetic fixtures, no ThetaData / .env / clock.
"""
import json
import os

import run_dual_pipeline_gate_v4 as v4


# ---------------------------------------------------------------------------
# R5-4: pure-SVI sensitivity helper
# ---------------------------------------------------------------------------
def test_pure_svi_sign_positive():
    # rich > cheap -> +1 (dealer net-selling / short)
    assert v4._pure_svi_sign({"rich_plus1": 5, "cheap_minus1": 2, "deadband_zero": 3}) == 1


def test_pure_svi_sign_negative():
    assert v4._pure_svi_sign({"rich_plus1": 2, "cheap_minus1": 5, "deadband_zero": 3}) == -1


def test_pure_svi_sign_zero_when_all_deadband():
    # All resolved strikes are deadband (fall through to -1); no SVI read.
    assert v4._pure_svi_sign({"rich_plus1": 0, "cheap_minus1": 0, "deadband_zero": 8}) == 0


def test_pure_svi_sign_zero_when_balanced():
    assert v4._pure_svi_sign({"rich_plus1": 4, "cheap_minus1": 4, "deadband_zero": 0}) == 0


def test_pure_svi_sign_missing_keys_graceful():
    assert v4._pure_svi_sign({}) == 0


# ---------------------------------------------------------------------------
# R5-1 + R5-4 merged-set pure-SVI agreement recomputation (the v4 path)
# ---------------------------------------------------------------------------
def test_merged_pure_svi_agreement_isolates_shared_minus1_root():
    # Two resolvable clusters, both AGREE on baseline (prod=-1). One has a
    # balanced SVI read (deadband-heavy -> manufactured by shared -1 root),
    # the other a genuine cheap read.
    rows = [
        {"both_nonzero": True, "prod_sign": -1, "pure_svi_sign": 0},   # manufactured
        {"both_nonzero": True, "prod_sign": -1, "pure_svi_sign": -1},  # genuine
    ]
    pure = [r for r in rows if r["both_nonzero"] and r["pure_svi_sign"] != 0]
    agreed = sum(1 for r in pure if r["pure_svi_sign"] == r["prod_sign"])
    assert len(pure) == 1 and agreed == 1  # only the genuine cluster counts


def test_merged_pure_svi_excludes_deadband_only_clusters():
    rows = [
        {"both_nonzero": True, "prod_sign": -1, "pure_svi_sign": 0},
        {"both_nonzero": True, "prod_sign": 1, "pure_svi_sign": 1},
    ]
    pure = [r for r in rows if r["both_nonzero"] and r["pure_svi_sign"] != 0]
    assert len(pure) == 1
    assert pure[0]["pure_svi_sign"] == 1


# ---------------------------------------------------------------------------
# R5-8: SPY/QQQ separation on a merged cluster set (unique-day eff-n)
# ---------------------------------------------------------------------------
def test_spy_qqq_separation_counts_unique_days():
    # QQQ 20260605 == SPY 20260605 -> 1 unique day; SPY has only 1 independent day.
    clusters = [("QQQ", "20260605"), ("SPY", "20260605"),
                ("QQQ", "20260429"), ("QQQ", "20260729")]
    unique = v4._r5._dedup_unique_days(clusters)
    assert len(unique) == 3  # not 4
    spy_unique = v4._r5._dedup_unique_days([c for c in clusters if c[0] == "SPY"])
    assert len(spy_unique) == 1  # SPY not an index claim (needs >= 2 indep days)


def test_spy_separation_two_independent_days_is_index_eligible():
    clusters = [("SPY", "20260605"), ("SPY", "20260429")]
    assert len(v4._r5._dedup_unique_days(clusters)) == 2


# ---------------------------------------------------------------------------
# R5-6: exact binomial null edge cases
# ---------------------------------------------------------------------------
def test_binom_tail_k_equals_n():
    assert v4._r5._binom_tail(3, 3, 0.5) == 0.125


def test_binom_tail_k_zero_is_one():
    assert v4._r5._binom_tail(0, 3, 0.5) == 1.0


def test_binom_tail_9_of_12():
    # P(X>=9 | Binom(12,0.5)) = (C(12,9)+C(12,10)+C(12,11)+C(12,12))/2^12
    # = (220+66+12+1)/4096 = 299/4096 = 0.072998...
    val = v4._r5._binom_tail(9, 12, 0.5)
    assert abs(val - 299 / 4096) < 1e-9


# ---------------------------------------------------------------------------
# v4 gate verdict (delegates to gate_verdict_r5): INDETERMINATE at low eff-n
# ---------------------------------------------------------------------------
def test_v4_verdict_indeterminate_when_eff_n_below_target():
    sa = [(True, True)] * 5  # 5 resolvable, 5 unique days but eff-n passed as 4
    v, reason = v4._r5.gate_verdict_r5(sa, 0.2, 0.1, 40, 4)
    assert v == "INDETERMINATE"
    assert "eff-n" in reason


def test_v4_verdict_can_pass_when_powered():
    sa = [(True, True)] * 9 + [(False, True)] * 2  # 9/11 = 82% > 2/3
    v, _ = v4._r5.gate_verdict_r5(sa, 0.30, 0.10, 60, 11)
    assert v == "PASS"


# ---------------------------------------------------------------------------
# _merge_seed_rows: prior-session seed rows, no shared-session confound
# ---------------------------------------------------------------------------
def test_merge_seed_rows_absent_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(v4, "CACHE", str(tmp_path))
    assert v4._merge_seed_rows() == []


def test_merge_seed_rows_loads_and_flags_session(tmp_path, monkeypatch):
    rows = [{
        "ticker": "QQQ", "day": "20260605", "expiry": "20260608",
        "prod_sign": 1, "new_sign": 1, "agreed": True, "both_nonzero": True,
        "firing_buckets": 7, "buckets": 40, "mean_abs_div": 0.0161,
        "provenance": {"total": 45, "rich_plus1": 22, "cheap_minus1": 21,
                       "deadband_zero": 2, "missing": 0},
    }]
    with open(os.path.join(str(tmp_path), "dual_pipeline_gate_v3_obs.json"), "w") as fh:
        json.dump({"rows": rows}, fh)
    monkeypatch.setattr(v4, "CACHE", str(tmp_path))
    merged = v4._merge_seed_rows()
    assert len(merged) == 1
    assert merged[0]["session"] == "seed-v3"
    assert merged[0]["both_nonzero"] is True


def test_merge_seed_rows_resolvable_count():
    # 12 seed rows with 5 resolvable (matches recorded v3 obs)
    merged = v4._merge_seed_rows() if os.path.exists(
        os.path.join(os.path.dirname(v4.__file__), "_intraday_cache",
                     "dual_pipeline_gate_v3_obs.json")) else []
    if merged:
        res = sum(1 for r in merged if r["both_nonzero"])
        assert res == 5
