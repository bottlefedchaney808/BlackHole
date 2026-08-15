import json
import os

import pytest

from Vol_Suite.dealer_exposure_acquisition import (
    AcquisitionGateError,
    build_candidate_schedule,
    build_provenance_census,
    cluster_same_day,
    execute_sequential_acquisition,
)


def row(ticker="AAPL", day="2026-08-17", expiry="2026-08-21", dte=4, sector="Tech"):
    return {"ticker": ticker, "day": day, "expiry": expiry, "dte": dte, "habitat": "NONE", "sector": sector, "candidate_source": "approved-list"}

def valid_payload(prov="PRE_WINDOW", value=0.1):
    return {"record": {"l2": {"delta_iv_provenance": prov, "delta_iv_pre_window": value, "iv_source_ts": "2026-08-17T14:00:00", "breach_window_start_prov": "2026-08-17T15:00:00"}}}

def test_schedule_sort_and_held_exclusion(tmp_path):
    p = tmp_path / "held.json"; p.write_text(json.dumps({"as_of":"2026-08-17", "ticker":"AAPL"}), encoding="utf-8")
    s = build_candidate_schedule([row("MSFT"), row("AAPL")], held_paths=[p])
    assert [x["ticker"] for x in s] == ["AAPL", "MSFT"]
    assert s[0]["held_pair_exclusion"] is True and s[0]["held_day_reference"] == "held.json:2026-08-17"
    assert s[1]["held_pair_exclusion"] is False

def test_schedule_deterministic_and_contract_key():
    s = build_candidate_schedule([row("MSFT"), row("AAPL")])
    assert s == build_candidate_schedule([row("AAPL"), row("MSFT")])
    assert s[0]["candidate_key"] == "2026-08-17|AAPL|2026-08-21|4|NONE|Tech|approved-list"

def test_dry_run_does_not_fetch_and_is_probe_only(monkeypatch):
    monkeypatch.setenv("THETADATA_HIST_CONCURRENCY", "1")
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), fetcher=lambda _: pytest.fail("network"), dry_run=True)
    assert result["mode"] == "probe-only" and result["network_heavy_acquisition_executed"] is False
    assert result["units"][0]["status"] == "INELIGIBLE" and result["units"][0]["imputed"] is False

def test_approval_and_strict_concurrency_gates(monkeypatch):
    monkeypatch.setenv("THETADATA_HIST_CONCURRENCY", "2")
    with pytest.raises(AcquisitionGateError, match="THETADATA_HIST_CONCURRENCY=1"):
        execute_sequential_acquisition([], dry_run=True)
    monkeypatch.setenv("THETADATA_HIST_CONCURRENCY", "1")
    with pytest.raises(AcquisitionGateError, match="approval"):
        execute_sequential_acquisition([], dry_run=False)

def test_sequential_pass_hashes_and_same_day_cluster():
    seen = []
    s = build_candidate_schedule([row("MSFT"), row("AAPL")])
    result = execute_sequential_acquisition(s, fetcher=lambda u: seen.append(u["ticker"]) or valid_payload(), dry_run=False, approval=True)
    assert seen == ["AAPL", "MSFT"]
    assert all(u["status"] == "PASS" and u["raw_payload_hash"] and u["artifact_hash"] and not u["imputed"] for u in result["units"])
    assert result["same_day_clusters"]["2026-08-17"]["tickers"] == ["AAPL", "MSFT"]
    assert result["census"]["pre_window_n"] == result["census"]["pre_window_N"] == 2

def test_no_imputation_and_associational_status():
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), fetcher=lambda _: {"record": {}}, dry_run=False, approval=True)
    assert result["units"][0]["status"] == "ASSOCIATIONAL" and result["units"][0]["pre_window_value"] is None

def test_hard_gap_retained_without_imputation():
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("down")), dry_run=False, approval=True)
    assert result["units"][0]["status"] == "HARD_GAP" and result["units"][0]["imputed"] is False

def test_fail_loud_census_below_100_percent():
    units = [{"calendar_day":"2026-08-17", "ticker":"AAPL", "status":"HARD_GAP", "pre_window_provenance":"NONE", "raw_payload_hash":"x"}]
    with pytest.raises(AcquisitionGateError, match="100% PRE_WINDOW"):
        build_provenance_census(units, intended_units=1, fail_loud=True)
    c = build_provenance_census(units, intended_units=1)
    assert c["gate_pass"] is False and c["pre_window_coverage"] == 0.0

def test_census_requires_hash_and_counts_statuses():
    with pytest.raises(AcquisitionGateError, match="provenance"):
        build_provenance_census([{ "calendar_day":"d", "ticker":"A", "status":"PASS", "pre_window_provenance":"PRE_WINDOW"}], intended_units=1)
    c = build_provenance_census([], intended_units=0)
    assert c["gate_pass"] and c["pre_window_coverage"] == 1.0 and c["raw_payload_hash_census"]

def test_invalid_schedule_contracts():
    bad = row(); bad.pop("candidate_source")
    with pytest.raises(ValueError, match="candidate_source"):
        build_candidate_schedule([bad])
    with pytest.raises(ValueError, match="reference"):
        build_candidate_schedule([row("SPY")])

def test_same_day_cluster_status_precedence():
    c = cluster_same_day([{ "calendar_day":"d", "ticker":"A", "status":"INELIGIBLE"}, {"calendar_day":"d", "ticker":"B", "status":"HARD_GAP"}])
    assert c["d"]["status"] == "HARD_GAP" and c["d"]["n_tickers"] == 2

def test_timestamp_or_missing_delta_downgrades():
    payload = valid_payload(); payload["record"]["l2"]["iv_source_ts"] = "bad"
    r = execute_sequential_acquisition(build_candidate_schedule([row()]), fetcher=lambda _: payload, dry_run=False, approval=True)
    assert r["units"][0]["status"] == "ASSOCIATIONAL"

def test_zero_delta_is_valid_not_imputation():
    r = execute_sequential_acquisition(build_candidate_schedule([row()]), fetcher=lambda _: valid_payload(value=0.0), dry_run=False, approval=True)
    assert r["units"][0]["status"] == "PASS" and r["units"][0]["imputed"] is False

def test_output_is_json_serializable_and_no_import_execution():
    import Vol_Suite.dealer_exposure_acquisition as module
    assert module.NETWORK_ACQUISITION_EXECUTED is False
    json.dumps(execute_sequential_acquisition([], dry_run=True))

def test_census_fail_loud_zero_empty_passes():
    assert build_provenance_census([], intended_units=0, fail_loud=True)["gate_pass"] is True

def test_concurrency_env_is_one_after_valid_entrypoint(monkeypatch):
    monkeypatch.setenv("THETADATA_HIST_CONCURRENCY", "1")
    execute_sequential_acquisition([], dry_run=True)
    assert os.environ["THETADATA_HIST_CONCURRENCY"] == "1"

def test_cluster_empty():
    assert cluster_same_day([]) == {}

def test_schedule_dte_stratum():
    assert build_candidate_schedule([row()])[0]["dte_stratum"] == [4, 7]

def test_artifact_hash_changes_with_payload():
    s = build_candidate_schedule([row()])
    a = execute_sequential_acquisition(s, fetcher=lambda _: {"x": 1}, dry_run=False, approval=True)
    b = execute_sequential_acquisition(s, fetcher=lambda _: {"x": 2}, dry_run=False, approval=True)
    assert a["units"][0]["artifact_hash"] != b["units"][0]["artifact_hash"]


def test_prewindow_rejects_malformed_equal_later_and_cross_day_timestamps():
    schedule = build_candidate_schedule([row()])
    for source, breach in (("0000", "2026-08-17T15:00:00"), ("2026-08-17T15:00:00", "2026-08-17T15:00:00"), ("2026-08-17T16:00:00", "2026-08-17T15:00:00"), ("2026-08-16T14:00:00", "2026-08-17T15:00:00")):
        payload = valid_payload()
        payload["record"]["l2"].update(iv_source_ts=source, breach_window_start_prov=breach)
        result = execute_sequential_acquisition(schedule, fetcher=lambda _, p=payload: p, dry_run=False, approval=True)
        assert result["units"][0]["status"] == "ASSOCIATIONAL"


def test_timezone_normalization_accepts_equivalent_ordering():
    payload = valid_payload()
    payload["record"]["l2"].update(iv_source_ts="2026-08-17T10:00:00-04:00", breach_window_start_prov="2026-08-17T19:00:00Z")
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), fetcher=lambda _: payload, dry_run=False, approval=True)
    assert result["units"][0]["status"] == "PASS"


def test_no_fetcher_is_hard_gap_and_not_executed():
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), dry_run=False, approval=True)
    assert result["network_heavy_acquisition_executed"] is False
    assert result["units"][0]["status"] == "HARD_GAP"


def test_probe_schema_and_pass_only_primary_schedule():
    from Vol_Suite.dealer_exposure_acquisition import run_availability_probes
    schedule = build_candidate_schedule([row("AAPL"), row("MSFT")])
    probes = run_availability_probes(schedule, probe_fetcher=lambda request: {"status": "PASS" if request["ticker"] == "AAPL" else "INELIGIBLE", "response_status": 200, "counts": {"rows": 2}, "source_counts": {"theta": 2}}, approval=True, dry_run=False)
    assert {"request_parameters", "response_status", "response_counts", "source_counts", "probe_code_version", "probe_code_hash"} <= set(probes[0])
    result = execute_sequential_acquisition(schedule, probe_fetcher=lambda request: {"status": "PASS" if request["ticker"] == "AAPL" else "INELIGIBLE", "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}}, fetcher=lambda _: valid_payload(), dry_run=False, approval=True)
    assert [u["ticker"] for u in result["primary_schedule"]] == ["AAPL"]
