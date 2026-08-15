import json
from types import SimpleNamespace

import pytest

import Vol_Suite.dealer_exposure_expansion as expansion
from Vol_Suite.dealer_exposure_expansion import (
    ExpansionApprovalError,
    build_expansion_manifest,
    run_expansion_plan,
)
from Vol_Suite.run_live_vs_expiry_book_common_input import (
    ComparisonInvalid,
    make_canonical_input,
)


def candidate(ticker, day, dte, *, event="NONE", sector="Tech"):
    expiry = __import__("datetime").date.fromisoformat(day) + __import__("datetime").timedelta(days=dte)
    return {
        "ticker": ticker,
        "calendar_day": day,
        "expiry": expiry.isoformat(),
        "dte": dte,
        "habitat": event,
        "sector": sector,
        "candidate_source": "selection-2026-08-15",
    }


def test_dry_run_is_deterministic_and_network_free(tmp_path):
    rows = [candidate("MSFT", "2026-08-18", 4), candidate("AAPL", "2026-08-17", 2, event="EARNINGS")]
    first = run_expansion_plan(rows, held_pairs=(), output_root=tmp_path, dry_run=True)
    second = run_expansion_plan(list(reversed(rows)), held_pairs=(), output_root=tmp_path, dry_run=True)
    assert first == second
    assert first["network_fetch_allowed"] is False
    assert first["planned_candidates"] == ["2026-08-17|AAPL|2026-08-19|2|EARNINGS|Tech|selection-2026-08-15", "2026-08-18|MSFT|2026-08-22|4|NONE|Tech|selection-2026-08-15"]
    assert not list(tmp_path.glob("**/*.json"))


def test_held_exclusions_are_explicit_and_not_in_denominator():
    result = build_expansion_manifest(
        [candidate("AAPL", "2026-08-17", 2), candidate("MSFT", "2026-08-17", 4)],
        held_pairs={("AAPL", "2026-08-17")},
    )
    assert result["exclusions"][0]["reason"] == "held_ticker_day"
    assert result["expected_ticker_day_units"] == 1
    assert result["intended_unique_day_denominator"] == 1


def test_event_control_and_dte_strata_balance_is_reported():
    rows = [
        candidate("AAPL", "2026-08-17", 2, event="EARNINGS"),
        candidate("MSFT", "2026-08-18", 4),
        candidate("NVDA", "2026-08-19", 8),
    ]
    result = build_expansion_manifest(rows)
    assert result["quota"]["event_target_fraction"] == pytest.approx(1 / 3)
    assert result["counts"]["event"] == 1
    assert result["counts"]["control"] == 2
    assert result["counts"]["dte_strata"] == {"1-3": 1, "4-7": 1, "8-10": 1}
    assert result["balance_gate"] is True


def test_paths_registry_and_artifacts_are_deterministic():
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)], output_root="Vol_Suite/_causal_acquisition_20260815")
    assert result["registry_path"] == "Vol_Suite/_causal_acquisition_20260815/registry.json"
    assert result["units"][0]["raw_artifact_path"].endswith("raw/AAPL/2026-08-17.json")
    assert result["units"][0]["record_artifact_path"].endswith("records/AAPL/2026-08-17.json")


def test_network_requires_explicit_approval_flag_and_executor():
    rows = [candidate("AAPL", "2026-08-17", 2)]
    with pytest.raises(ExpansionApprovalError, match="--approve-network"):
        run_expansion_plan(rows, dry_run=False)
    with pytest.raises(ExpansionApprovalError, match="executor"):
        run_expansion_plan(rows, dry_run=False, approve_network=True)


def test_gate_contracts_and_no_imputation_are_published():
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)])
    assert all(result["gates"][key] == value for key, value in {
        "THETADATA_HIST_CONCURRENCY": 1,
        "pre_window_observations_required": 2,
        "no_imputation": True,
        "balanced_panel": True,
        "approval_required": True,
    }.items())
    assert result["stop_conditions"]
    assert result["no_imputation"] is True
    json.dumps(result, sort_keys=True)


def test_invalid_dte_is_excluded_without_imputation():
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 0)])
    assert result["expected_ticker_day_units"] == 0
    assert result["exclusions"][0]["reason"] == "invalid_dte"
    assert result["no_imputation"] is True


def test_probe_only_never_writes_or_calls_executor(tmp_path):
    called = []
    result = run_expansion_plan([candidate("AAPL", "2026-08-17", 2)], output_root=tmp_path, dry_run=True, executor=lambda _: called.append(1))
    assert called == []
    assert result["mode"] == "dry-run"
    assert not list(tmp_path.glob("**/*"))


def test_manifest_can_be_written_only_by_explicit_plan_export(tmp_path):
    result = run_expansion_plan([candidate("AAPL", "2026-08-17", 2)], output_root=tmp_path, dry_run=True, write_manifest=True)
    path = tmp_path / "expansion_manifest.json"
    assert path.exists()
    assert json.loads(path.read_text()) == result
    assert result["network_fetch_allowed"] is False


def test_selection_provenance_is_preserved():
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)])
    assert result["selection_provenance"] == {"candidate_source": "selection-2026-08-15", "candidate_count": 1}
    assert result["units"][0]["candidate_source"] == "selection-2026-08-15"


def test_strict_sequential_gate_is_fail_closed(monkeypatch):
    monkeypatch.setenv("THETADATA_HIST_CONCURRENCY", "2")
    with pytest.raises(ExpansionApprovalError, match="THETADATA_HIST_CONCURRENCY=1"):
        build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)])
    monkeypatch.setenv("THETADATA_HIST_CONCURRENCY", "1")
    build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)])


def test_event_control_imbalance_stops_plan():
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 2, event="EARNINGS")])
    assert result["balance_gate"] is False
    assert any("event/control" in stop for stop in result["stop_conditions"])
    assert result["network_fetch_allowed"] is False


def test_two_pre_window_observations_and_no_imputation_are_required():
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)])
    assert result["gates"]["pre_window_observations_required"] == 2
    assert result["gates"]["imputation_policy"] == "reject_missing"


def test_held_reference_paths_compose_task_one(tmp_path):
    held = tmp_path / "held.json"
    held.write_text('{"ticker":"AAPL","as_of":"2026-08-17"}')
    result = build_expansion_manifest([candidate("AAPL", "2026-08-17", 2)], held_paths=[held])
    assert result["exclusions"][0]["held_reference"] == "held.json"
    assert result["expected_ticker_day_units"] == 0


def _gated_evidence(result):
    keys = [u["candidate_key"] for u in result["units"]]
    return {"probes": [{"candidate_key": key, "status": "PASS", "validated": True, "invoked": True} for key in keys],
            "units": [{"candidate_key": key, "pre_window_observations": [1, 2]} for key in keys],
            "artifact_registry": {}}


@pytest.mark.parametrize("evidence", [None, {"probes": []}, {"probes": [], "units": [], "artifact_registry": {}}])
def test_network_executor_is_fail_closed_without_complete_admission(evidence):
    calls = []
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda unit: calls.append(unit), acquisition_evidence=evidence)
    assert calls == []
    assert result["mode"] == "blocked"
    assert result["execution_audit"]["blocked"]


def test_network_executor_rejects_missing_pre_window_and_incomplete_coverage():
    calls = []
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    evidence["units"] = evidence["units"][:1]
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda unit: calls.append(unit), acquisition_evidence=evidence)
    assert calls == []
    assert any("coverage" in str(item) or "PRE_WINDOW" in str(item) for item in result["execution_audit"]["blocked"])


def test_network_executor_only_receives_validated_primary_units(monkeypatch):
    calls = []
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda unit: (calls.append(unit["candidate_key"]) or {"status": "SUCCESS", "validated": True, "success": True}), acquisition_evidence=evidence)
    assert calls == [u["candidate_key"] for u in plan["units"]]
    assert result["execution_audit"]["invoked"] == calls
    assert result["network_fetch_allowed"] is True


def _canonical():
    return make_canonical_input(
        "AAPL", "2026-08-17", "2026-08-19", 2, 100.0,
        "2026-08-17T12:00:00+00:00",
        [{"strike": 100.0, "right": "C", "iv": 0.2, "oi": 10}],
        ["a" * 64],
    )


def test_wrong_deadband_is_rejected_exactly():
    with pytest.raises(ComparisonInvalid, match="exactly 0.01"):
        expansion.compare_expansion_common_input(_canonical(), live_runner=lambda *_args, **_kwargs: None, new_runner=lambda *_args: None, deadband=0.0100001)


@pytest.mark.parametrize("missing", ["spot", "expiry", "dte", "T", "rows"])
def test_result_identity_omissions_are_structured_invalid(missing):
    inp = _canonical()
    row = SimpleNamespace(strike=100.0, right="C")
    result = SimpleNamespace(spot=100.0, expiry=inp.expiry, dte=inp.dte, T=inp.dte / 365.0, rows=[row], gamma_records=[row])
    setattr(result, missing, None)
    with pytest.raises(ComparisonInvalid, match="identity|record count"):
        expansion._require_result_identity(result, inp, "new")


def test_missing_live_config_attestation_is_structured_invalid():
    with pytest.raises(ComparisonInvalid, match="attestation"):
        expansion._require_live_attestation(SimpleNamespace())


@pytest.mark.parametrize("mutation", [{"route": "legacy"}, {"deadband": 0.02}, {"dealer_vanna_flow": -1},
                                       {"sign_model": "wrong"}, {"accumulate": False}, {"attested": False}])
def test_mutated_live_config_attestation_is_structured_invalid(mutation):
    attestation = {**expansion.LIVE_CONFIG, "attested": True}
    attestation.update(mutation)
    with pytest.raises(ComparisonInvalid, match="attestation"):
        expansion._require_live_attestation(SimpleNamespace(config_attestation=attestation))


def test_config_attestation_requires_explicit_validation_flag():
    attestation = dict(expansion.LIVE_CONFIG)
    with pytest.raises(ComparisonInvalid, match="validated"):
        expansion._require_live_attestation(SimpleNamespace(config_attestation=attestation))


def test_executor_requires_explicit_validated_success(monkeypatch):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True,
                                executor=lambda _unit: {"status": "SUCCESS", "success": True},
                                acquisition_evidence=evidence)
    assert result["network_fetch_allowed"] is False


def test_missing_per_strike_provenance_is_structured_invalid():
    with pytest.raises(ComparisonInvalid, match="provenance"):
        expansion._require_strike_provenance(SimpleNamespace(), "live")


def test_structured_executor_failure_is_hard_gap(monkeypatch):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda _unit: {"status": "FAILED", "reason": "adapter unavailable"}, acquisition_evidence=evidence)
    assert result["mode"] == "failed-execution"
    assert result["network_fetch_allowed"] is False
    assert result["execution_audit"]["blocked"][0]["classification"] == "HARD_GAP"


def _identity_row(**overrides):
    values = {"strike": 100.0, "right": "C", "expiry": "2026-08-19", "spot": 100.0,
              "dte": 2, "T": 2 / 365.0, "iv": 0.2, "oi": 10,
              "source_hash": "a" * 64, "config_hash": "config-1", "sign_provenance": "attested"}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("field,value", [("expiry", "2026-08-20"), ("spot", 101.0), ("dte", 3),
                                          ("T", 3 / 365.0), ("iv", 0.21), ("oi", 11),
                                          ("source_hash", "b" * 64)])
def test_every_per_strike_identity_field_is_checked(field, value):
    inp = _canonical()
    row = _identity_row(**{field: value})
    result = SimpleNamespace(spot=100.0, expiry=inp.expiry, dte=inp.dte, T=inp.dte / 365.0, rows=[row])
    with pytest.raises(ComparisonInvalid, match="per-strike|source/config"):
        expansion._require_result_identity(result, inp, "new")


def test_valid_per_strike_identity_is_returned_for_pair_evidence():
    inp = _canonical()
    row = _identity_row()
    result = SimpleNamespace(spot=100.0, expiry=inp.expiry, dte=inp.dte, T=inp.dte / 365.0, rows=[row])
    assert expansion._require_result_identity(result, inp, "new") == [row]


@pytest.mark.parametrize("failure", [
    {"status": "HARD_GAP"}, {"status": "FAILED_EXECUTION"}, {"status": "BLOCKED"},
    {"status": "ERROR"}, {"status": "FAIL"}, {"success": False}, {"ok": False}, None,
])
def test_all_structured_executor_failures_block_network(monkeypatch, failure):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda _unit: failure, acquisition_evidence=evidence)
    assert result["network_fetch_allowed"] is False
    assert result["mode"] == "failed-execution"
    assert result["execution_audit"]["blocked"][0]["status"] == "FAILED_EXECUTION"


@pytest.mark.parametrize("failure", [
    {"status": "SUCCESS", "validated": True, "success": True, "ok": False},
    {"status": "OK", "validated": True, "success": False, "ok": True},
    {"status": "SUCCESS", "validated": True, "success": False, "ok": False},
    {"status": "SUCCESS", "validated": True, "success": True, "ok": True, "error": "adapter failure"},
])
def test_contradictory_executor_success_evidence_blocks_network(monkeypatch, failure):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda _unit: failure, acquisition_evidence=evidence)
    assert result["network_fetch_allowed"] is False
    assert result["mode"] == "failed-execution"
    assert result["execution_audit"]["blocked"][0]["status"] == "FAILED_EXECUTION"


def test_execution_gate_rejects_duplicate_evidence_units_without_overwrite(monkeypatch):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    evidence["units"].append(dict(evidence["units"][0]))
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda _unit: {"status": "SUCCESS", "validated": True, "success": True}, acquisition_evidence=evidence)
    assert result["network_fetch_allowed"] is False
    assert result["mode"] == "blocked"
    assert any(item.get("reason") == "duplicate evidence candidate identity" for item in result["execution_audit"]["blocked"])


@pytest.mark.parametrize("result", [
    {"status": "SUCCESS", "validated": True, "success": "false"},
    {"status": "SUCCESS", "validated": True, "success": 0},
    {"status": "SUCCESS", "validated": True, "success": 1},
    {"status": "SUCCESS", "validated": True, "success": None},
    {"status": "SUCCESS", "validated": True},
    {"status": "SUCCESS", "success": True},
    {"status": "SUCCESS", "validated": True, "success": True, "ok": "false"},
    {"status": "SUCCESS", "validated": True, "success": True, "ok": 1},
    {"status": "SUCCESS", "validated": True, "success": True, "ok": None},
])
def test_executor_requires_strict_boolean_success_fields(monkeypatch, result):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    output = run_expansion_plan(rows, dry_run=False, approve_network=True,
                                executor=lambda _unit: result, acquisition_evidence=evidence)
    assert output["network_fetch_allowed"] is False
    assert output["mode"] == "failed-execution"


def test_execution_gate_blocks_unhashable_candidate_identity(monkeypatch):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    evidence["units"][0]["candidate_key"] = ["malformed", {"unhashable": True}]
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    output = run_expansion_plan(rows, dry_run=False, approve_network=True,
                                executor=lambda _unit: {"status": "SUCCESS", "validated": True, "success": True},
                                acquisition_evidence=evidence)
    assert output["network_fetch_allowed"] is False
    assert output["mode"] == "blocked"
    assert any(item["reason"] == "malformed evidence candidate identity" for item in output["execution_audit"]["blocked"])


def test_execution_gate_admits_valid_unique_evidence_units(monkeypatch):
    rows = [candidate("AAPL", "2026-08-17", 2, event="EARNINGS"), candidate("MSFT", "2026-08-18", 4)]
    plan = build_expansion_manifest(rows)
    evidence = _gated_evidence(plan)
    monkeypatch.setattr(expansion, "validate_causal_eligibility", lambda *args, **kwargs: {"causal_status": "CAUSAL_ELIGIBLE", "reasons": []})
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda _unit: {"status": "SUCCESS", "validated": True, "success": True}, acquisition_evidence=evidence)
    assert result["network_fetch_allowed"] is True
    assert result["execution_audit"]["blocked"] == []
    assert result["execution_audit"]["invoked"] == [u["candidate_key"] for u in plan["units"]]
