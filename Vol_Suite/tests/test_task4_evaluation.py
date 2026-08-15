import hashlib
import json

import pytest
from run_task4_evaluation import (
    EvaluationInvalid,
    decision_ladder,
    evaluate_task4,
    power_diagnostics,
    unique_calendar_days,
)


def row(day, ticker, y=0.02, v=2.0, div=0.1, *, daily=-0.01, breach=0.02):
    input_hash = hashlib.sha256(f"input:{day}:{ticker}".encode()).hexdigest()
    source_hash = hashlib.sha256(f"source:{day}:{ticker}".encode()).hexdigest()
    payload = f"payload:{day}:{ticker}".encode()
    raw_hash = hashlib.sha256(payload).hexdigest()
    candidate = f"{ticker}|{day}"
    manifest = {"candidate_key": candidate, "ticker": ticker, "calendar_day": day,
                "canonical_input_hash": input_hash, "source_hashes": [source_hash]}
    artifact_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    registry = {artifact_hash: {"artifact_hash": artifact_hash, "raw_payload_hash": raw_hash,
                                "candidate_key": candidate, "ticker": ticker, "calendar_day": day,
                                "canonical_input_hash": input_hash, "source_hashes": [source_hash],
                                "artifact_manifest": manifest, "payload_bytes": payload}}
    return {
        "day": day, "ticker": ticker, "family": ticker, "candidate_key": candidate,
        "canonical_input_hash": input_hash,
        "artifact_registry": registry,
        "comparison": {"status": "VALID", "input_hash": input_hash,
                       "coverage": {"live": 1, "new": 1, "common": 1, "total": 1},
                       "artifact_hash": artifact_hash,
                       "source_hashes": [source_hash]},
        "provenance": {"causal_status": "CAUSAL_ELIGIBLE", "no_imputation": True,
                       "artifact_hash": artifact_hash, "input_hash": input_hash,
                       "raw_payload_hash": raw_hash, "source_hashes": [source_hash],
                       "record_artifact_hash": artifact_hash},
        "pre_vanna": v, "delta_iv_pre_window": div, "gamma_burst": 1.0,
        "delta_s": 0.01, "market": 0.005, "a6_reflexivity": 0.2,
        "cross_family_spillover": 0.1, "event": int(day == "2026-01-02"),
        "daily_return": daily, "from_breach_return": breach,
        "forward_return_h": y, "placebo_return": 0.0, "reverse_return": 0.0,
        "opposite_convention_vanna": -v,
    }


def test_unique_calendar_day_counts_same_day_tickers_once_and_never_uses_zero_outcome():
    rows = [row("2026-01-01", "SPY"), row("2026-01-01", "QQQ"), row("2026-01-02", "SPY")]
    assert unique_calendar_days(rows) == 2
    result = evaluate_task4(rows, min_days=1)
    assert result["n_unique_days"] == 2
    assert result["causal"]["n"] == 2


def test_missing_outcome_is_invalid_not_imputed_zero():
    bad = row("2026-01-01", "SPY")
    del bad["forward_return_h"]
    with pytest.raises(EvaluationInvalid, match="outcome"):
        evaluate_task4([bad], min_days=1)


def test_incomplete_provenance_registry_fails_closed():
    bad = row("2026-01-01", "SPY")
    bad["provenance"] = {"causal_status": "CAUSAL_BLOCKED", "no_imputation": True}
    with pytest.raises(EvaluationInvalid, match="provenance|registry|hash"):
        evaluate_task4([bad], min_days=1)


def test_placebo_and_reverse_are_reported_without_best_lag_selection():
    result = evaluate_task4([row(f"2026-01-{i:02d}", "SPY") for i in range(1, 8)], min_days=1)
    assert set(result["falsifiers"]) >= {"placebo", "reverse_lead_lag"}
    assert result["config"]["best_lag_selection"] is False


def test_power_interpretation_never_confuses_correlational_target_with_beta_power():
    diag = power_diagnostics(beta=1.0, se=0.1, n=29, p=10, target_days=257)
    assert diag["correlational_target_days"] == 29
    assert diag["beta_target_days"] == 257
    assert diag["reach_80"] is False
    assert diag["n_for_80"] is not None


def test_decision_ladder_is_explicit_and_never_promotes():
    assert decision_ladder(descriptive_ok=True, causal_ok=False, falsifiers_ok=True, powered=True) == "INDETERMINATE"
    assert decision_ladder(descriptive_ok=False, causal_ok=True, falsifiers_ok=True, powered=True) == "WORSE"
    assert decision_ladder(descriptive_ok=True, causal_ok=True, falsifiers_ok=True, powered=True) == "BETTER"
    assert decision_ladder(descriptive_ok=True, causal_ok=True, falsifiers_ok=False, powered=True) == "WORSE"


def test_invalid_comparison_artifact_fails_closed():
    bad = row("2026-01-01", "SPY")
    bad["comparison"] = {"status": "COMPARISON_INVALID"}
    with pytest.raises(EvaluationInvalid, match="comparison"):
        evaluate_task4([bad], min_days=1)


def test_opposite_convention_is_sensitivity_not_primary():
    result = evaluate_task4([row(f"2026-01-{i:02d}", "SPY") for i in range(1, 8)], min_days=1)
    assert result["sensitivity"]["opposite_convention"]["role"] == "SENSITIVITY"
    assert result["config"]["auto_promote"] is False


def test_task3_minimal_valid_status_is_rejected():
    bad = row("2026-01-01", "SPY")
    bad["comparison"] = {"status": "VALID"}
    with pytest.raises(EvaluationInvalid, match="schema|coverage|hash"):
        evaluate_task4([bad], min_days=1)


@pytest.mark.parametrize("mutation", [
    lambda r: r.pop("artifact_registry"),
    lambda r: next(iter(r["artifact_registry"].values())).update(candidate_key="FORGED"),
    lambda r: r["provenance"].update(raw_payload_hash="f" * 64),
])
def test_task3_registry_is_required_and_candidate_raw_binding_is_verified(mutation):
    bad = row("2026-01-01", "SPY")
    mutation(bad)
    with pytest.raises(EvaluationInvalid, match="registry|candidate|raw|identity"):
        evaluate_task4([bad], min_days=1)


@pytest.mark.parametrize("mutation", [
    lambda r: r["comparison"]["coverage"].update(common=0),
    lambda r: r["comparison"].update(source_hashes=["0" * 64]),
    lambda r: r["provenance"].update(artifact_hash="f" * 64),
    lambda r: r["provenance"].update(record_artifact_hash="e" * 64),
])
def test_task3_provenance_identity_mismatches_are_rejected(mutation):
    bad = row("2026-01-01", "SPY")
    mutation(bad)
    with pytest.raises(EvaluationInvalid):
        evaluate_task4([bad], min_days=1)


def test_balanced_sensitivity_is_deterministic_and_separate_from_primary():
    records = [row("2026-01-01", "SPY"), row("2026-01-01", "QQQ"),
               row("2026-01-02", "SPY"), row("2026-01-03", "QQQ")]
    first = evaluate_task4(records, min_days=1)
    second = evaluate_task4(list(reversed(records)), min_days=1)
    assert first["sensitivity"]["balanced_panel"]["role"] == "SENSITIVITY"
    assert first["sensitivity"]["balanced_panel"] == second["sensitivity"]["balanced_panel"]
    assert first["causal"] == first["primary_all_eligible"]


def test_all_strata_run_same_diagnostics_and_pooled_keeps_no_firing_days():
    records = [row("2026-01-01", "SPY", daily=-0.01, breach=0.02),
               row("2026-01-02", "QQQ", daily=0.01, breach=-0.02),
               row("2026-01-03", "IWM", daily=-0.01, breach=0.02)]
    result = evaluate_task4(records, min_days=1)
    assert result["n_unique_days"] == 3
    assert result["clocks"]["daily_close_to_close"]["negative_days"] == 2
    assert result["clocks"]["daily_close_to_close"]["positive_days"] == 1
    assert result["clocks"]["from_breach"]["negative_days"] == 1
    assert result["clocks"]["from_breach"]["positive_days"] == 2
    assert result["clocks"]["daily_close_to_close"]["mean_return"] == pytest.approx(-1 / 300)
    assert result["strata"]["pooled"]["n"] == 3
    assert result["strata"]["pooled"]["coverage"] == 1.0
    assert result["strata"]["pooled"]["no_firing_days"] == 2
    for name in ("event_only", "control_only", "pooled"):
        stratum = result["strata"][name]
        assert set(stratum) >= {"descriptive", "daily", "from_breach", "causal", "power", "status", "n", "coverage"}
        assert set(stratum["daily"]) >= {"negative_days", "positive_days", "mean_return"}
        assert set(stratum["from_breach"]) >= {"negative_days", "positive_days", "mean_return"}


def test_missing_falsifiers_are_indeterminate_and_cannot_make_worse():
    records = [row(f"2026-01-{i:02d}", "SPY") for i in range(1, 8)]
    for record in records:
        record.pop("placebo_return")
        record.pop("reverse_return")
    result = evaluate_task4(records, min_days=1)
    assert result["falsifiers"]["placebo"]["status"] == "NOT_AVAILABLE"
    assert result["falsifiers"]["reverse_lead_lag"]["interpretation"] == "NOT_AVAILABLE"
    assert result["falsifiers"]["placebo"]["drives_decision"] is False
    assert result["decision"] != "WORSE"


def test_non_identifiable_primary_and_placebo_are_neutral_not_worse():
    records = [row(f"2026-01-{i:02d}", "SPY", y=0.01) for i in range(1, 15)]
    for record in records:
        record["placebo_return"] = 0.02
    result = evaluate_task4(records, min_days=1)
    assert result["causal"]["status"] == "NOT-IDENTIFIABLE"
    assert result["falsifiers"]["placebo"]["fit"]["status"] == "NOT-IDENTIFIABLE"
    assert result["falsifiers"]["placebo"]["drives_decision"] is False
    assert result["decision"] != "WORSE"


def test_underpowered_is_indeterminate_even_with_positive_estimate():
    result = evaluate_task4([row("2026-01-01", "SPY"), row("2026-01-02", "SPY")], min_days=1)
    assert result["power"]["underpowered"] is True
    assert result["decision"] == "INDETERMINATE"