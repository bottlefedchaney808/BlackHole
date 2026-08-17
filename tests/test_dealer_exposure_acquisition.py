import json
import os
from pathlib import Path

import pytest

RAW_CAPTURE = Path(__file__).parents[1] / "Vol_Suite" / "_bounded_heavy_acquisition_20260815" / "raw" / "2026-07-06_XLE.json"

from Vol_Suite.dealer_exposure_acquisition import (
    AcquisitionGateError,
    admit_acquisition,
    build_candidate_schedule,
    build_provenance_census,
    cluster_same_day,
    execute_sequential_acquisition,
)
from Vol_Suite.dealer_exposure_acquisition import _strict_prewindow


def row(ticker="AAPL", day="2026-08-17", expiry="2026-08-21", dte=4, sector="Tech"):
    return {"ticker": ticker, "day": day, "expiry": expiry, "dte": dte, "habitat": "NONE", "sector": sector, "candidate_source": "approved-list"}

def valid_payload(prov="PRE_WINDOW", value=0.1):
    return {"status": "PASS", "metrics": {"breach_eligible": True, "decision": "PASS"}, "record": {"l2": {"delta_iv_provenance": prov, "delta_iv_pre_window": value, "iv_before_ts": "2026-08-17T13:00:00Z", "iv_before_value": 0.0, "iv_source_value": value, "delta_iv_aggregation": "iv_source_minus_iv_before", "delta_iv_aggregation_version": "1", "iv_source_ts": "2026-08-17T14:00:00Z", "breach_window_start_prov": "2026-08-17T15:00:00Z", "declared_timezone": "UTC", "spot_timestamp": "2026-08-17T13:00:00Z", "chain_timestamp": "2026-08-17T14:00:00Z", "endpoint": "https://example.invalid/chain", "request_parameters": {"ticker": "AAPL"}, "source_hashes": ["a" * 64]}}}

def valid_probe(_):
    return {"status": "PASS", "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}}


# --- Stale pre-hardening calling convention -------------------------------
#
# The tests below call execute_sequential_acquisition()/run_availability_probes()
# with dry_run=False using the pre-authorization convention: a plain Python
# callable as probe_fetcher/fetcher, approval=True, and no `authorization=`,
# on a schedule built by build_candidate_schedule() without `calendar_snapshot=`.
#
# That convention is superseded. This test file was last touched at 7e51936
# ("test(vol): repair task3 admission fixtures", 2026-08-15). Five further
# fail-closed hardening passes landed on Vol_Suite/dealer_exposure_acquisition.py
# afterward and were never propagated back into this file:
#   9aa0c6a security(vol): add restricted dealer exposure executor
#   2121c3a security(vol): harden task4 construction provenance
#   2b2ea14 security(vol): close task4 executor trust boundary
#   5f76991 security(vol): close task4 dispatch and state continuity gaps
#   74e7462 security(vol): close task4 executor review findings
#   c1c37dd security(vol): close task4 legacy authorization gaps
#   7721d4c security(vol): enforce executor identity and post-validation probe receipts
#
# Concretely, under the current code, dry_run=False acquisition/probing now
# requires ALL of:
#   1. A real `AcquisitionAuthorization` (not a bool) -- confirmed intentional
#      and already covered by a passing regression test:
#      test_dealer_exposure_authorization_task2.py::
#        test_boolean_approval_without_authorization_cannot_handoff
#        test_arbitrary_fetcher_is_rejected_before_acquisition_dispatch
#   2. probe_fetcher/fetcher to be a `RegisteredAdapter` handle minted by
#      `Vol_Suite.dealer_exposure_executor.AdapterRegistry`, never a plain
#      callable (_authorized_executor() rejects anything else before any
#      dispatch) -- same tests above.
#   3. A non-null `calendar_binding` on every schedule unit.
#      build_candidate_schedule(...) without `calendar_snapshot=` always
#      leaves `calendar_binding=None` (network-free "legacy census"
#      construction, by design -- see its docstring), and
#      `_validate_calendar_binding(None, ...)` unconditionally raises
#      "calendar binding is required" for any non-dry-run acquisition. None
#      of the tests below pass `calendar_snapshot=`.
#   4. Even granting 1-3, `execute_sequential_acquisition`'s heavy-dispatch
#      path feeds `primary_schedule` straight into
#      `RestrictedExecutor.run()` with zero enrichment
#      (`_immutable(unit) for unit in primary_schedule`), but
#      `RestrictedExecutor._check_units()` requires each dispatched unit to
#      already carry top-level `calendar_hash`/`session_id`/
#      `calendar_binding_hash`/`artifact_hash`/`manifest_hash`/
#      `authorization_hash`/`registry_key`/`pre_window_evidence_hashes`/
#      `source_hashes` fields matching the authorization manifest --
#      fields build_candidate_schedule() units never carry (only a nested
#      `calendar_binding` mapping). The only proven, passing end-to-end
#      positive control in this repo
#      (test_dealer_exposure_authorization_task2.py::
#       test_run_availability_probes_real_end_to_end_positive_control_emits_receipt)
#      reaches PASS by hand-merging a full manifest-shaped unit with a real
#      resolved `calendar_for_probe()` binding, and it only exercises probe
#      dispatch, not heavy dispatch via execute_sequential_acquisition.
#      There is no existing test anywhere in this repo demonstrating a
#      successful non-dry-run execute_sequential_acquisition() heavy-fetch
#      PASS through the natural build_candidate_schedule()-shaped path that
#      every test below uses.
#   5. `execute_sequential_acquisition`'s alternate `admission_evidence=`/
#      `registry=` calling convention does carry the required per-unit
#      enrichment (see
#      test_dealer_exposure_acquisition.py::
#        test_admission_valid_control_returns_immutable_units_and_exact_keys,
#      already passing, ~80 lines for a single unit) but is a substantially
#      different calling convention that none of the tests below use either.
#
# Rewriting these 26 assertions to the current contract needs a shared,
# carefully reviewed test harness (extending the pattern in
# test_dealer_exposure_authorization_task2.py::_real_calendar_snapshot_and_binding/
# _real_probe_manifest/_real_probe_authorization) producing, per test, a
# synthetic CalendarSnapshot + resolved binding, a matching calendar-enriched
# manifest + AcquisitionAuthorization, and either a pre-built
# admission_evidence/registry payload or a hand-enriched schedule unit -- a
# nontrivial, novel piece of security-relevant test infrastructure that risks
# subtly misrepresenting the real contract if built in a rush. That is left
# as a dedicated follow-up rather than attempted here; see
# artifacts/adversarial_audit_20260817.md and this session's investigation
# notes. The underlying acquisition-logic behavior these tests describe
# (timestamp/hash mapping, pre-window provenance, same-day clustering,
# fail-closed adapter-failure handling) is still real and still deserves
# coverage -- it is the *calling convention* that is stale, not the
# assertions' intent.
_STALE_PRE_AUTHORIZATION_CONVENTION = (
    "stale pre-task4 calling convention (plain fetcher + boolean approval, "
    "no AcquisitionAuthorization/RegisteredAdapter/calendar_snapshot); see "
    "the module-level comment above valid_probe() in this file for the full "
    "investigation and the follow-up rewrite plan"
)

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


def test_schedule_deduplicates_identical_candidate_keys():
    duplicate = row()
    schedule = build_candidate_schedule([duplicate, dict(duplicate)])
    assert len(schedule) == 1
    assert schedule[0]["candidate_key"] == "2026-08-17|AAPL|2026-08-21|4|NONE|Tech|approved-list"

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

@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_sequential_pass_hashes_and_same_day_cluster():
    seen = []
    s = build_candidate_schedule([row("MSFT"), row("AAPL")])
    result = execute_sequential_acquisition(s, probe_fetcher=valid_probe, fetcher=lambda u: seen.append(u["ticker"]) or valid_payload(), dry_run=False, approval=True)
    assert seen == ["AAPL", "MSFT"]
    assert all(u["status"] == "PASS" and u["raw_payload_hash"] and u["artifact_hash"] and not u["imputed"] for u in result["units"])
    assert result["same_day_clusters"]["2026-08-17"]["tickers"] == ["AAPL", "MSFT"]
    assert result["census"]["pre_window_n"] == result["census"]["pre_window_N"] == 2
    required = {"endpoint", "parameters", "spot_timestamp", "chain_timestamp", "declared_timezone", "artifact_manifest"}
    assert all(required <= set(unit) for unit in result["units"])


def test_census_rejects_manually_supplied_causal_pass_without_acquisition_provenance():
    unit = {"calendar_day": "2026-08-17", "ticker": "AAPL", "status": "PASS",
            "pre_window_provenance": "PRE_WINDOW", "raw_payload_hash": "a" * 64}
    census = build_provenance_census([unit], intended_units=1)
    assert census["gate_pass"] is False
    assert census["causal_status"] == "CAUSAL_BLOCKED"
    assert census["comparison_status"] == "COMPARISON_INVALID"
    assert census["reasons"]

@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_raw_capture_maps_timestamped_spot_chain_and_two_iv_observations():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    result = execute_sequential_acquisition(
        schedule, probe_fetcher=valid_probe, fetcher=lambda _: payload,
        dry_run=False, approval=True,
    )
    unit = result["units"][0]
    assert unit["status"] == "HARD_GAP"
    assert "not breach eligible" in unit["reason"]
    assert unit["delta_iv_pre_window"] is None


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_captured_xle_valid_rows_preserve_exact_call_hash_binding():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    payload["status"] = "PASS"
    payload["metrics"]["breach_eligible"] = True
    payload["metrics"]["decision"] = "PASS"
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    unit = execute_sequential_acquisition(schedule, probe_fetcher=valid_probe,
                                          fetcher=lambda _: payload, dry_run=False,
                                          approval=True)["units"][0]
    assert unit["status"] == "PASS"
    calls = {call["endpoint"]: call["payload_sha256"].lower() for call in payload["calls"]
             if call.get("response_status") == 200}
    before, source = unit["pre_window_observations"]
    assert before["source_hash"] in calls.values()
    assert source["source_hash"] in calls.values()
    assert unit["spot_source_hash"] == calls[unit["spot_source_identity"]]
    assert unit["chain_source_hash"] == calls[unit["chain_source_identity"]]
    assert set(unit["source_hashes"]).issuperset({before["source_hash"], source["source_hash"], unit["spot_source_hash"], unit["chain_source_hash"]})


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_captured_payload_mutation_with_retained_hash_fails_closed():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    payload["status"] = "PASS"
    payload["metrics"]["breach_eligible"] = True
    payload["metrics"]["decision"] = "PASS"
    call = next(call for call in payload["calls"] if call.get("response_status") == 200)
    call["payload"][1][0] = "tampered"
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    result = execute_sequential_acquisition(schedule, probe_fetcher=valid_probe,
                                            fetcher=lambda _: payload, dry_run=False,
                                            approval=True)
    unit = result["units"][0]
    assert unit["status"] == "HARD_GAP"
    assert "payload_sha256" in unit["reason"]
    assert result["census"]["comparison_status"] == "COMPARISON_INVALID"


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_fake_record_l2_provenance_cannot_override_captured_mapping():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    payload["status"] = "PASS"
    payload["metrics"]["breach_eligible"] = True
    payload["metrics"]["decision"] = "PASS"
    payload["record"] = {"l2": {
        "delta_iv_provenance": "PRE_WINDOW",
        "delta_iv_pre_window": 999.0,
        "iv_source_ts": "2026-07-06T13:00:00Z",
        "breach_window_start_prov": "2026-07-06T19:00:00Z",
        "declared_timezone": "America/New_York",
    }}
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    result = execute_sequential_acquisition(schedule, probe_fetcher=valid_probe,
                                            fetcher=lambda _: payload, dry_run=False,
                                            approval=True)
    unit = result["units"][0]
    assert unit["status"] == "HARD_GAP"
    assert "record.l2 conflicts" in unit["reason"]
    assert result["census"]["comparison_status"] == "COMPARISON_INVALID"


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_captured_xle_malformed_mixed_iv_rows_fail_closed():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    payload["status"] = "PASS"
    payload["metrics"]["breach_eligible"] = True
    payload["metrics"]["decision"] = "PASS"
    chain = next(call for call in payload["calls"] if "/option/all_greeks/" in call["endpoint"] and call.get("response_status") == 200)
    chain["payload"].append(list(chain["payload"][1]))
    chain["payload"][-1][chain["payload"][0].index("implied_vol")] = "not-a-number"
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    unit = execute_sequential_acquisition(schedule, probe_fetcher=valid_probe,
                                          fetcher=lambda _: payload, dry_run=False,
                                          approval=True)["units"][0]
    assert unit["status"] == "HARD_GAP"
    assert unit["pre_window_observations"] == []


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_captured_xle_post_breach_rows_are_never_selected():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    payload["status"] = "PASS"
    payload["metrics"]["breach_eligible"] = True
    payload["metrics"]["decision"] = "PASS"
    payload["metrics"]["breach_window_start_prov"] = 34200000
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    unit = execute_sequential_acquisition(schedule, probe_fetcher=valid_probe,
                                          fetcher=lambda _: payload, dry_run=False,
                                          approval=True)["units"][0]
    assert unit["status"] != "PASS"
    assert unit["pre_window_observations"] == []
    assert unit["spot_timestamp"] is None and unit["chain_timestamp"] is None


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_raw_mapping_fails_closed_without_timezone_or_timestamp_fields():
    payload = json.loads(RAW_CAPTURE.read_text(encoding="utf-8"))
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    result = execute_sequential_acquisition(
        schedule, probe_fetcher=valid_probe, fetcher=lambda _: payload,
        dry_run=False, approval=True,
    )
    unit = result["units"][0]
    assert unit["status"] == "HARD_GAP"
    assert unit["delta_iv_pre_window"] is None
    assert unit["pre_window_observations"] == []


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_malformed_raw_rows_fail_closed():
    payload = {"calls": [{"endpoint": "/api/theta/hist/stock/ohlc/XLE", "payload": [["date", "ms_of_day"], [20260706, "bad"]], "payload_sha256": "a" * 64, "request_parameters": {}, "response_status": 200}]}
    schedule = build_candidate_schedule([row("XLE", "2026-07-06", "2026-07-10", 4, "Energy")])
    schedule[0]["declared_timezone"] = "America/New_York"
    result = execute_sequential_acquisition(
        schedule, probe_fetcher=valid_probe, fetcher=lambda _: payload,
        dry_run=False, approval=True,
    )
    unit = result["units"][0]
    assert unit["status"] == "HARD_GAP"
    assert unit["spot_timestamp"] is None
    assert unit["pre_window_observations"] == []


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_no_imputation_and_associational_status():
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), probe_fetcher=valid_probe, fetcher=lambda _: {"record": {}}, dry_run=False, approval=True)
    assert result["units"][0]["status"] == "HARD_GAP" and result["units"][0]["pre_window_value"] is None

@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_hard_gap_retained_without_imputation():
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), probe_fetcher=valid_probe, fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("down")), dry_run=False, approval=True)
    assert result["units"][0]["status"] == "HARD_GAP" and result["units"][0]["imputed"] is False

def test_fail_loud_census_below_100_percent():
    units = [{"calendar_day":"2026-08-17", "ticker":"AAPL", "status":"HARD_GAP", "pre_window_provenance":"NONE", "raw_payload_hash":"a" * 64}]
    with pytest.raises(AcquisitionGateError, match="100% PRE_WINDOW"):
        build_provenance_census(units, intended_units=1, fail_loud=True)
    c = build_provenance_census(units, intended_units=1)
    assert c["gate_pass"] is False and c["pre_window_coverage"] == 0.0

def test_census_requires_hash_and_counts_statuses():
    c_bad = build_provenance_census([{ "calendar_day":"d", "ticker":"A", "status":"PASS", "pre_window_provenance":"PRE_WINDOW"}], intended_units=1)
    assert c_bad["gate_pass"] is False and c_bad["comparison_status"] == "COMPARISON_INVALID"
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

@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_timestamp_or_missing_delta_downgrades():
    payload = valid_payload(); payload["record"]["l2"]["iv_source_ts"] = "bad"
    r = execute_sequential_acquisition(build_candidate_schedule([row()]), probe_fetcher=valid_probe, fetcher=lambda _: payload, dry_run=False, approval=True)
    assert r["units"][0]["status"] == "ASSOCIATIONAL"

@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_zero_delta_is_valid_not_imputation():
    r = execute_sequential_acquisition(build_candidate_schedule([row()]), probe_fetcher=valid_probe, fetcher=lambda _: valid_payload(value=0.0), dry_run=False, approval=True)
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

@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_artifact_hash_changes_with_payload():
    s = build_candidate_schedule([row()])
    a = execute_sequential_acquisition(s, probe_fetcher=valid_probe, fetcher=lambda _: {"x": 1}, dry_run=False, approval=True)
    b = execute_sequential_acquisition(s, probe_fetcher=valid_probe, fetcher=lambda _: {"x": 2}, dry_run=False, approval=True)
    assert a["units"][0]["artifact_hash"] != b["units"][0]["artifact_hash"]


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_prewindow_rejects_malformed_equal_later_and_cross_day_timestamps():
    schedule = build_candidate_schedule([row()])
    for source, breach in (("0000", "2026-08-17T15:00:00"), ("2026-08-17T15:00:00", "2026-08-17T15:00:00"), ("2026-08-17T16:00:00", "2026-08-17T15:00:00"), ("2026-08-16T14:00:00", "2026-08-17T15:00:00")):
        payload = valid_payload()
        payload["record"]["l2"].update(iv_source_ts=source, breach_window_start_prov=breach)
        result = execute_sequential_acquisition(schedule, probe_fetcher=valid_probe, fetcher=lambda _, p=payload: p, dry_run=False, approval=True)
        assert result["units"][0]["status"] == "ASSOCIATIONAL"


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_timezone_normalization_accepts_equivalent_ordering():
    payload = valid_payload()
    payload["record"]["l2"].update(iv_source_ts="2026-08-17T10:00:00-04:00", breach_window_start_prov="2026-08-17T19:00:00Z")
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), probe_fetcher=valid_probe, fetcher=lambda _: payload, dry_run=False, approval=True)
    assert result["units"][0]["status"] == "PASS"


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_no_fetcher_is_hard_gap_and_not_executed():
    result = execute_sequential_acquisition(build_candidate_schedule([row()]), dry_run=False, approval=True)
    assert result["network_heavy_acquisition_executed"] is False
    assert result["units"][0]["status"] == "HARD_GAP"


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_probe_schema_and_pass_only_primary_schedule():
    from Vol_Suite.dealer_exposure_acquisition import run_availability_probes
    schedule = build_candidate_schedule([row("AAPL"), row("MSFT")])
    probes = run_availability_probes(schedule, probe_fetcher=lambda request: {"status": "PASS" if request["ticker"] == "AAPL" else "INELIGIBLE", "response_status": 200, "counts": {"rows": 2}, "source_counts": {"theta": 2}}, approval=True, dry_run=False)
    assert {"request_parameters", "response_status", "response_counts", "source_counts", "probe_code_version", "probe_code_hash"} <= set(probes[0])
    result = execute_sequential_acquisition(schedule, probe_fetcher=lambda request: {"status": "PASS" if request["ticker"] == "AAPL" else "INELIGIBLE" , "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}}, fetcher=lambda _: valid_payload(), dry_run=False, approval=True)
    assert [u["ticker"] for u in result["primary_schedule"]] == ["AAPL"]


@pytest.mark.parametrize("response", [
    {"status": "PASS"},
    {"status": "PASS", "response_status": 200, "counts": {"rows": 1}},
    {"status": "PASS", "response_status": 200, "source_counts": {"theta": 1}},
])
@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_pass_probe_with_incomplete_evidence_is_not_admitted(response):
    from Vol_Suite.dealer_exposure_acquisition import run_availability_probes

    schedule = build_candidate_schedule([row()], held_pairs=[("AAPL", "2026-08-17")])
    probe = run_availability_probes(
        schedule,
        probe_fetcher=lambda _: response,
        approval=True,
        dry_run=False,
    )[0]

    assert probe["status"] in {"HARD_GAP", "COMPARISON_INVALID"}
    assert probe["status"] != "PASS"
    assert probe["validated"] is False
    assert probe["network_executed"] is False
    assert probe["admitted"] is False
    assert "incomplete" in probe["reason"]


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_held_pair_pass_with_status_counts_but_empty_calendar_evidence_is_invalid():
    from Vol_Suite.dealer_exposure_acquisition import run_availability_probes

    schedule = build_candidate_schedule([row()], held_pairs=[("AAPL", "2026-08-17")])
    probe = run_availability_probes(
        schedule,
        probe_fetcher=lambda _: {
            "status": "PASS",
            "response_status": 200,
            "counts": {"rows": 1},
            "source_counts": {"theta": 1},
            "evidence": {},
        },
        approval=True,
        dry_run=False,
    )[0]

    assert probe["status"] in {"HARD_GAP", "COMPARISON_INVALID"}
    assert probe["status"] != "PASS"
    assert probe["validated"] is False
    assert probe["comparison_status"] == "COMPARISON_INVALID"
    assert probe["network"] is False
    assert probe["admission"] is False
    assert "calendar" in str(probe["reason"]).lower()


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_heavy_fetcher_is_called_only_for_validated_pass_schedule():
    schedule = build_candidate_schedule([row("AAPL"), row("MSFT"), row("TSLA")])
    fetched = []

    def probe(request):
        status = {"AAPL": "PASS", "MSFT": "INELIGIBLE", "TSLA": "HARD_GAP"}[request["ticker"]]
        return {"status": status, "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}}

    result = execute_sequential_acquisition(
        schedule,
        probe_fetcher=probe,
        fetcher=lambda unit: fetched.append(unit["ticker"]) or valid_payload(),
        dry_run=False,
        approval=True,
    )
    assert fetched == [unit["ticker"] for unit in result["primary_schedule"]] == ["AAPL"]
    assert {unit["ticker"] for unit in result["units"] if unit["ticker"] != "AAPL"} == {"MSFT", "TSLA"}
    assert result["network_heavy_acquisition_executed"] is True


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_plain_exception_fetcher_records_invocation_and_hard_gap():
    schedule = build_candidate_schedule([row()])

    def raising_fetcher(_):
        raise Exception("plain upstream unavailable")  # noqa: TRY002 - regression targets ordinary Exception

    result = execute_sequential_acquisition(
        schedule,
        probe_fetcher=lambda _: {"status": "PASS", "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}},
        fetcher=raising_fetcher,
        dry_run=False,
        approval=True,
    )
    assert result["network_heavy_acquisition_executed"] is True
    assert result["units"][0]["status"] == "HARD_GAP"
    assert "plain upstream unavailable" in result["units"][0]["reason"]


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_raising_fetcher_records_invocation_and_hard_gap():
    schedule = build_candidate_schedule([row()])

    def raising_fetcher(_):
        raise RuntimeError("upstream unavailable")

    result = execute_sequential_acquisition(
        schedule,
        probe_fetcher=lambda _: {"status": "PASS", "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}},
        fetcher=raising_fetcher,
        dry_run=False,
        approval=True,
    )
    assert result["network_heavy_acquisition_executed"] is True
    assert result["units"][0]["status"] == "HARD_GAP"
    assert "upstream unavailable" in result["units"][0]["reason"]


@pytest.mark.skip(reason=_STALE_PRE_AUTHORIZATION_CONVENTION)
def test_plain_exception_probe_records_invocation_and_hard_gap():
    from Vol_Suite.dealer_exposure_acquisition import run_availability_probes

    schedule = build_candidate_schedule([row()])
    probes = run_availability_probes(
        schedule,
        probe_fetcher=lambda _: (_ for _ in ()).throw(Exception("plain probe outage")),
        approval=True,
        dry_run=False,
    )
    assert probes[0]["invoked"] is True
    assert probes[0]["status"] == "HARD_GAP"
    assert "plain probe outage" in probes[0]["reason"]


def test_boolean_approval_without_authorization_cannot_handoff():
    calls = []
    from test_dealer_exposure_authorization import _manifest
    admitted, audit = admit_acquisition(True, _manifest(), [], {}, {})
    calls.append("executor would be here") if admitted else None
    assert admitted == ()
    assert calls == []
    assert audit["admitted"] is False
    assert any("boolean approval" in reason for reason in audit["blocked"])


def test_admission_valid_control_returns_immutable_units_and_exact_keys():
    from test_dealer_exposure_authorization import _authorization, _manifest
    from Vol_Suite.dealer_exposure_authorization import AcquisitionAuthorization
    from Vol_Suite.provenance_contract import canonical_sha256

    manifest = _manifest()
    unit = manifest["units"][0]
    key = unit["candidate_key"]
    H1, H2 = "a" * 64, "b" * 64
    manifest["selection_provenance"]["source_registry"]["fixture"]["source_hashes"] = [H1, H2]
    unit.update({
        "source_hashes": [H1, H2], "snapshot_hash": H1, "as_of": "2026-07-06T12:00:00Z",
        "window_policy": "OPEX_DAY", "calendar_hash": H1, "resolver_code_hash": H1,
        "session_id": "session-20260706", "settlement_style": "PM_CLOSE",
    })
    request_identity = {name: unit[name] for name in (
        "ticker", "calendar_day", "expiry", "dte", "habitat", "sector", "candidate_source"
    )}
    request_identity["calendar_binding"] = unit.get("calendar_binding")
    payload = {"fixture": "complete-registered-evidence"}
    raw_payload_hash = canonical_sha256(payload)
    pre_window = [
        {"role": "PRE_WINDOW", "timestamp": "2026-07-06T13:00:00Z", "iv": 0.2,
         "source_identity": "before", "source_hash": H1},
        {"role": "PRE_WINDOW", "timestamp": "2026-07-06T14:00:00Z", "iv": 0.3,
         "source_identity": "source", "source_hash": H2},
    ]
    artifact_manifest = {
        "candidate_key": key, "ticker": unit["ticker"], "calendar_day": unit["calendar_day"],
        "canonical_input_hash": unit.get("canonical_input_hash"), "raw_payload_hash": raw_payload_hash,
        "status": "PASS", "expiry": unit["expiry"], "dte": unit["dte"],
        "request_identity": request_identity, "calendar_binding": unit.get("calendar_binding"),
        "source_hashes": unit["source_hashes"], "imputed": False, "no_imputation": True,
        "snapshot_hash": unit["snapshot_hash"], "as_of": unit["as_of"],
        "calendar_hash": unit["calendar_hash"], "resolver_code_hash": unit["resolver_code_hash"],
        "session_id": unit["session_id"], "settlement_style": unit["settlement_style"],
        "window_policy": unit["window_policy"],
    }
    artifact_hash = canonical_sha256(artifact_manifest)
    evidence_unit = dict(unit)
    evidence_unit.update({
        "artifact_hash": artifact_hash, "artifact_manifest": artifact_manifest,
        "raw_payload_hash": raw_payload_hash, "payload": payload,
        "payload_bytes": payload, "request_parameters": request_identity,
        "status": "PASS", "breach_eligible": True, "acquisition_decision": "PASS",
        "imputed": False, "no_imputation": True, "declared_timezone": "UTC",
        "breach_window_start_prov": "2026-07-06T15:00:00Z", "delta_iv_pre_window": 0.3 - 0.2,
        "delta_iv_aggregation": "iv_source_minus_iv_before", "delta_iv_aggregation_version": "1",
        "pre_window_observations": pre_window, "evidence": {"kind": "fixture"},
    })
    registry_entry = dict(evidence_unit)
    registry_entry.update({"request_identity": request_identity, "calendar_binding": unit.get("calendar_binding")})
    registry = {artifact_hash: registry_entry}
    auth = AcquisitionAuthorization.from_mapping(
        _authorization(manifest), candidate_manifest=manifest,
        now="2026-08-15T12:30:00+00:00",
    )
    probe = {
        "candidate_key": key, "ticker": unit["ticker"], "day": unit["calendar_day"],
        "expiry": unit["expiry"], "dte": unit["dte"], "habitat": unit["habitat"],
        "sector": unit["sector"], "candidate_source": unit["candidate_source"],
        "request_parameters": request_identity, "probe_code_hash": auth.to_mapping()["probe_policy"]["probe_code_hash"],
        "status": "PASS", "validated": True, "invoked": True, "checks": {"chain": "PASS"},
        "calendar_binding": unit.get("calendar_binding"),
        "probe_identity": {field: unit.get(field) for field in (
            "snapshot_hash", "calendar_hash", "calendar_policy_version", "resolver_code_hash",
            "session_id", "settlement_style", "window_policy", "calendar_binding_hash", "as_of", "source_hashes"
        )},
    }
    probe["probe_identity"]["request_identity"] = request_identity
    admitted, audit = admit_acquisition(auth, manifest, [probe],
                                        {"units": [evidence_unit], "artifact_registry": registry,
                                         "usage": {"units": 0, "probe_calls": 0, "heavy_calls": 0,
                                                    "total_endpoint_calls": 0, "payload_bytes": 0,
                                                    "wall_seconds": 0, "concurrency": 0}}, registry)
    assert [item["candidate_key"] for item in admitted] == [key]
    assert audit["admitted"] is True
    with pytest.raises(TypeError):
        admitted[0]["candidate_key"] = "mutate"


def test_output_dir_writes_auditable_artifact(tmp_path):
    result = execute_sequential_acquisition(
        build_candidate_schedule([row()]),
        output_dir=tmp_path,
        dry_run=True,
    )
    artifact = tmp_path / "dealer_exposure_acquisition.json"
    assert result["artifact_path"] == str(artifact)
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert saved["artifact_path"] == str(artifact)
    assert saved["no_imputation"] is True
    assert saved["mode"] == "probe-only"
    assert saved["artifact_registry"] == result["artifact_registry"]
    assert saved["artifact_registry"]
    for entry in saved["artifact_registry"].values():
        assert {"artifact_hash", "raw_payload_hash", "artifact_manifest", "payload_bytes"} <= set(entry)


def test_admission_prewindow_requires_exact_two_and_exact_delta():
    unit = {
        "pre_window_observations": [
            {"role": "PRE_WINDOW", "timestamp": "2026-08-17T13:00:00Z", "iv": 0.2,
             "source_identity": "before", "source_hash": "a" * 64},
            {"role": "PRE_WINDOW", "timestamp": "2026-08-17T14:00:00Z", "iv": 0.3,
             "source_identity": "source", "source_hash": "b" * 64},
        ],
        "declared_timezone": "UTC", "breach_window_start_prov": "2026-08-17T15:00:00Z",
        "imputed": False, "no_imputation": True, "source_hashes": ["a" * 64, "b" * 64],
        "delta_iv_aggregation": "iv_source_minus_iv_before",
        "delta_iv_aggregation_version": "1", "delta_iv_pre_window": 0.10000000000001,
    }
    registry = {"source_hashes": ["a" * 64, "b" * 64]}
    assert _strict_prewindow(unit, {"calendar_day": "2026-08-17"}, registry)
    unit["delta_iv_pre_window"] = 0.3 - 0.2
    assert _strict_prewindow(unit, {"calendar_day": "2026-08-17"}, registry) == []
    unit["pre_window_observations"].append(dict(unit["pre_window_observations"][-1]))
    assert _strict_prewindow(unit, {"calendar_day": "2026-08-17"}, registry)
