import json

import pytest

import Vol_Suite.dealer_exposure_expansion as expansion
from Vol_Suite.dealer_exposure_expansion import (
    ExpansionApprovalError,
    build_expansion_manifest,
    run_expansion_plan,
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
    result = run_expansion_plan(rows, dry_run=False, approve_network=True, executor=lambda unit: calls.append(unit["candidate_key"]), acquisition_evidence=evidence)
    assert calls == [u["candidate_key"] for u in plan["units"]]
    assert result["execution_audit"]["invoked"] == calls
    assert result["network_fetch_allowed"] is True
