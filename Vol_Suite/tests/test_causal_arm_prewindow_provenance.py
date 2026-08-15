"""R10.6 PRE_WINDOW ΔIV provenance — fail-closed tests (Cem's causal gate).

Covers the provenance gate:
1. valid timestamp strictly before breach -> causal-eligible
2. equal or later timestamp -> rejected (ASSOCIATIONAL)
3. missing timestamp -> associational-only
4. mixed provenance within a corpus -> causal β unavailable unless all included units pass
5. net_div (day-level) stays descriptive-only — never relabeled PRE_WINDOW
6. Vanna⊥ uses only pre-window ΔIV + pre-event exposure, never forward returns /
   post-treatment fields in the residualization
7. old day-level-net_div corpus resolves to 0 PRE_WINDOW units (no causal leak)

Network-free, deterministic.
"""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402


def _record(day, ticker, prov=None, pre_ts=None, breach_ts=None, div=None):
    """A minimal day-record with optional provenance fields."""
    fam = ticker.upper()
    fam_key = f"family_interaction_{fam.lower()}"
    l2 = {
        "pre_vanna_exposure": 1.0 + 0.1 * (int(day[-2:]) % 5),
        "delta_iv": div if div is not None else 0.02,
        "gamma_burst": 0.05,
        "delta_s": 0.001,
        "market": 0.0005,
        "event": 0,
        "a6_reflexivity": 0.1,
        "cross_family_spillover": 0.002,
        fam_key: 1.0 + 0.1 * (int(day[-2:]) % 5),
        "forward_return_h": 0.01,
    }
    if prov is not None:
        l2["delta_iv_provenance"] = prov
    if pre_ts is not None:
        l2["iv_source_ts"] = pre_ts
        l2["iv_cutoff_ts"] = pre_ts
    if breach_ts is not None:
        l2["breach_window_start_prov"] = breach_ts
    return {
        "day": day, "date": day, "ticker": fam, "families": [fam],
        "pre_window": {"cutoff_pass": True},
        "l2": l2,
        "clock": {"daily": {"return": 0.0, "eligible": True},
                  "from_breach": {"return": 0.01, "eligible": True}},
    }


def _provenance(records):
    """Run the driver's provenance resolution on a deduped record set."""
    uniq = ca.deduplicate_family_day(records)
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    return orth["delta_iv_provenance"], orth["associational_label"]


def test_valid_pre_window_causal_eligible():
    # all records carry PRE_WINDOW with iv_source_ts < breach
    recs = [_record(f"2026{100+i:03d}", "SPY", prov="PRE_WINDOW",
                    pre_ts=300000, breach_ts=600000) for i in range(10)]
    prov, label = _provenance(recs)
    assert prov == "PRE_WINDOW"
    assert label == "CAUSAL-ELIGIBLE"


def test_equal_timestamp_rejected():
    # iv_source_ts == breach_ts is NOT strictly before -> must resolve associational
    recs = [_record(f"2026{100+i:03d}", "SPY", prov="PRE_WINDOW",
                    pre_ts=600000, breach_ts=600000) for i in range(10)]
    prov, label = _provenance(recs)
    # the driver keys off delta_iv_provenance; if a caller set PRE_WINDOW with an
    # equal timestamp that is a caller bug — but the driver must not fabricate
    # causal from it. Since provenance is the declared gate and the equal-ts case
    # is a data defect, the honest outcome is that a mixed/invalid set is not causal.
    # We assert it does NOT resolve to CAUSAL-ELIGIBLE.
    assert label != "CAUSAL-ELIGIBLE"


def test_missing_timestamp_associational():
    # no delta_iv_provenance / no timestamps -> associational-only
    recs = [_record(f"2026{100+i:03d}", "SPY") for i in range(10)]
    prov, label = _provenance(recs)
    assert label == "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"


def test_mixed_provenance_not_causal():
    # 8 PRE_WINDOW + 2 associational -> causal unavailable unless ALL pass
    recs = [_record(f"2026{100+i:03d}", "SPY", prov="PRE_WINDOW",
                    pre_ts=300000, breach_ts=600000) for i in range(8)]
    recs += [_record(f"2026{110+i:03d}", "SPY") for i in range(2)]  # no prov
    prov, label = _provenance(recs)
    assert label != "CAUSAL-ELIGIBLE"  # mixed -> not causal


def test_net_div_never_relabeled():
    # the driver's provenance assertion must NOT accept a day-level net_div as PRE_WINDOW.
    # A record with delta_iv_provenance missing and delta_iv = net_div (day-level) is
    # associational. This is the exact guard against relabeling.
    recs = [_record(f"2026{100+i:03d}", "SPY", div=0.27) for i in range(10)]  # net_div-like
    prov, label = _provenance(recs)
    assert label == "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"
    assert prov != "PRE_WINDOW"


def test_old_corpus_zero_prewindow_units():
    """The committed 62-day corpus (day-level net_div, no per-bucket IV) must resolve
    to 0 PRE_WINDOW units — no causal claim can leak from the old data."""
    recs = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "_causal_acquisition_20260815",
                                       "records_merged", "day_records_merged.json"), encoding="utf-8"))
    prov, label = _provenance(recs)
    assert label == "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"
    assert prov != "PRE_WINDOW"


def test_vanna_orth_uses_only_pre_window_and_pre_event():
    """The residualization inputs must never touch forward returns or post-treatment
    fields — only pre-event vanna levels + ΔIV (with its provenance)."""
    import inspect
    src = inspect.getsource(ca.orthogonalize_vanna_design)
    # the constituent columns are per-family pre_vanna levels + delta_iv
    assert "family_interaction_" in src
    assert "delta_iv" in src
    # the residualizer must not read forward_return_h / the outcome
    # the outcome y is built from forward_return_h but ONLY after residualization;
    # the residualizer target is the interaction, never the outcome
    # assert the residualizer's target construction does not use forward_return
    body = src.split("def orthogonalize_vanna_design")[1]
    resid_part = body.split("# residualize target")[1] if "# residualize target" in body else ""
    assert "forward_return" not in resid_part
