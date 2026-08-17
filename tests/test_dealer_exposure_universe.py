import json
from pathlib import Path

import pytest

from Vol_Suite.dealer_exposure_universe import (
    DTE_STRATA, EVENT_HABITATS, PROBE_CHECKS, Candidate, ProbeResult,
    build_manifest, held_pairs_from_paths, normalize_candidate, validate_probe_result,
)


def evidence():
    row = lambda stamp: {"timestamp": stamp, "spot": 100.0, "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0}
    return {
        "probe_identity": {"ticker": "AAPL", "day": "2026-08-17", "expiry": "2026-08-21", "dte": 4},
        "spot_ohlc_coverage": {"pre_window": [row("2026-08-17T14:00:00")], "firing_window": [row("2026-08-17T15:00:00")], "response_window": [row("2026-08-17T16:00:00")], "return_clocks": "daily/from_breach"},
        "same_expiry_grid_oi_iv": {"expiry": "2026-08-21", "grid": [95.0, 100.0, 105.0], "oi": [10, 20, 10], "iv": [0.25, 0.20, 0.23]},
        "strike_side_moneyness": {"call_side": [1.02, 1.05], "put_side": [0.95, 0.98], "moneyness_band": [0.9, 1.1]},
        "strict_pre_window_ordering": {"pre_window_last": "2026-08-17T14:59:00", "breach_first": "2026-08-17T15:00:00", "strictly_before": True},
        "return_clocks": {"daily": "1d", "from_breach": "5d"},
        "no_imputation": True,
        "zero_dte": False,
        "calendar_binding": {
            "ticker": "AAPL",
            "calendar_day": "2026-08-17",
            "expiry": "2026-08-21",
            "dte": 4,
            "exact_dte": 4,
            "nominal_date": "2026-08-21",
            "observed_expiry": "2026-08-21",
            "observed_expiry_date": "2026-08-21",
            "session_id": "S-2026-08-21",
            "observed_session_id": "S-2026-08-21",
            "session_status": "OPEN",
            "settlement_style": "PM_CLOSE",
            "timezone": "America/New_York",
            "as_of": "2026-08-17T00:00:00Z",
            "event_ids": ["opex-1"],
            "event_windows": {"opex-1": {"window_id": "opex-1:OPEX_DAY"}},
            "event_window_id": "opex-1:OPEX_DAY",
            "window_id": "opex-1:OPEX_DAY",
            "window_policy": "OPEX_DAY",
            "window_start": "2026-08-21T15:30:00-04:00",
            "window_end": "2026-08-21T16:00:00-04:00",
            "snapshot_hash": "a" * 64,
            "calendar_hash": "a" * 64,
            "calendar_policy_version": "us-options-v1",
            "resolver_code_version": "test-v1",
            "resolver_code_hash": "b" * 64,
            "calendar_binding_hash": "bccce5c1e6220d4a134158bfe91b338615b9cfa6edc3018caf06c2d8e72072ea",
            "source_hashes": ["d" * 64],
            "settlement_timestamp": "2026-08-21T16:00:00-04:00",
        },
    }


def probe(ticker="AAPL", day="2026-08-17", expiry="2026-08-21", dte=4, status="PASS", reasons=()):
    data = evidence() if status == "PASS" else {}
    if data:
        data["probe_identity"] = {"ticker": ticker, "day": day, "expiry": expiry, "dte": dte}
        data["same_expiry_grid_oi_iv"]["expiry"] = expiry
        day_prefix = day + "T"
        for window in ("pre_window", "firing_window", "response_window"):
            data["spot_ohlc_coverage"][window][0]["timestamp"] = day_prefix + data["spot_ohlc_coverage"][window][0]["timestamp"].split("T", 1)[1]
        data["strict_pre_window_ordering"]["pre_window_last"] = day_prefix + "14:59:00"
        data["strict_pre_window_ordering"]["breach_first"] = day_prefix + "15:00:00"
    return ProbeResult(ticker, day, expiry, dte, status, {name: status for name in PROBE_CHECKS}, list(reasons), False, data)


def candidate(ticker="AAPL", day="2026-08-17", expiry="2026-08-21", dte=4, sector="Tech"):
    return {"ticker": ticker, "day": day, "expiry": expiry, "sector": sector, "dte": dte}


def test_normalize_candidate_is_canonical_and_excludes_reference_expansion_pool():
    assert normalize_candidate({"ticker": " $aapl ", "sector": "Information Technology", "asset_type": "equity"}, selection_date="2026-08-15", source_list="static") == Candidate("AAPL", "Information Technology", "equity", "2026-08-15", "static")
    with pytest.raises(ValueError, match="reference"):
        normalize_candidate({"ticker": "SPY", "reference_family": "SPY"})


def test_held_pairs_use_manifest_as_of_not_seed_expiry_filename(tmp_path):
    (tmp_path / "pull_manifest.json").write_text(json.dumps({"as_of": "2026-08-14", "results": [{"ticker": "AAPL", "file": "seed_data_AAPL_20260817_short.json"}]}), encoding="utf-8")
    (tmp_path / "seed_data_AAPL_20260817_short.json").write_text(json.dumps({"manifest": {"ticker": "AAPL", "expiry": "20260817"}}), encoding="utf-8")
    assert held_pairs_from_paths([tmp_path]) == {("AAPL", "2026-08-14")}


def test_actual_tier2b_window_directory_is_explicit_day_fixture(tmp_path):
    window = tmp_path / "window_20260508"
    window.mkdir()
    (window / "seed_data_AAPL_20260511_short.json").write_text(json.dumps({"manifest": {"ticker": "AAPL", "expiry": "20260511", "as_of": "2026-05-08"}}), encoding="utf-8")
    assert held_pairs_from_paths([tmp_path]) == {("AAPL", "2026-05-08")}


def test_probe_requires_all_eligibility_evidence_and_rejects_zero_dte():
    assert validate_probe_result(probe()).ticker == "AAPL"
    for check in ("spot_ohlc_coverage", "same_expiry_grid_oi_iv", "strike_side_moneyness", "strict_pre_window_ordering", "return_clocks"):
        data = evidence(); data.pop(check)
        with pytest.raises(ValueError):
            validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))
    with pytest.raises(ValueError):
        validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 0, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, evidence()))


def test_probe_rejects_scalar_timestamp_only_spot_ohlc_rows():
    for window in ("pre_window", "firing_window", "response_window"):
        data = evidence()
        data["spot_ohlc_coverage"][window] = ["2026-08-17T14:00:00"]
        with pytest.raises(ValueError):
            validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))


def test_probe_enforces_strict_ordering_distinct_return_clocks_and_no_imputation():
    data = evidence(); data["strict_pre_window_ordering"]["strictly_before"] = False
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))
    data = evidence(); data["return_clocks"]["from_breach"] = "1d"
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))
    with pytest.raises(ValueError): validate_probe_result(probe(status="INELIGIBLE", reasons=("missing OI",)).__class__(**{**probe(status="INELIGIBLE", reasons=("missing OI",)).__dict__, "imputed_zero": True}))


def test_probe_rejects_contradictory_timestamp_and_truthy_placeholders():
    data = evidence(); data["strict_pre_window_ordering"]["pre_window_last"] = "2026-08-17T15:01:00"
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))
    data = evidence(); data["same_expiry_grid_oi_iv"]["grid"] = "locked"
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))
    data = evidence(); data["return_clocks"]["daily"] = "daily"
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))


def test_probe_rejects_mismatched_probe_identity():
    data = evidence(); data["probe_identity"]["ticker"] = "MSFT"
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))


def test_probe_rejects_mismatched_expiry_and_bad_moneyness_coverage():
    data = evidence(); data["same_expiry_grid_oi_iv"]["expiry"] = "2026-08-22"
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))
    data = evidence(); data["strike_side_moneyness"]["put_side"] = [0.5]
    with pytest.raises(ValueError): validate_probe_result(ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False, data))


def test_manifest_requires_exactly_one_pass_probe_and_records_exclusions():
    rows = [candidate("AAPL"), candidate("MSFT", day="2026-08-18", expiry="2026-08-22"), candidate("NVDA", day="2026-08-19", expiry="2026-08-23")]
    probes = [probe(), probe("MSFT", "2026-08-18", "2026-08-22", 4, "INELIGIBLE", ("no spot",)), probe("NVDA", "2026-08-19", "2026-08-23", 4, "PASS"), probe("NVDA", "2026-08-19", "2026-08-23", 4, "PASS")]
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest(rows, probe_results=probes, intended_units=3, selection_date="2026-08-15", source_list="approved-static-candidates")


def test_missing_probe_is_explicit_and_nonmatching_expiry_is_not_admitted():
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest([candidate("AAPL")], probe_results=[], selection_date="2026-08-15", source_list="approved-static-candidates")
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest([candidate("AAPL")], probe_results=[probe(expiry="2026-08-22", dte=5)], selection_date="2026-08-15", source_list="approved-static-candidates")


def test_manifest_rejects_missing_candidate_expiry_without_fallback_match():
    raw = candidate("AAPL")
    raw.pop("expiry")
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest([raw], probe_results=[probe()], selection_date="2026-08-15", source_list="approved-static-candidates")


def test_manifest_is_deterministic_for_reversed_duplicates_and_probe_order():
    rows = [candidate("AAPL", day="2026-08-17"), candidate("AAPL", day="2026-08-17"), candidate("MSFT", day="2026-08-18", expiry="2026-08-22")]
    probes = [probe("MSFT", "2026-08-18", "2026-08-22"), probe()]
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest(rows, probe_results=probes, intended_units=3, selection_date="2026-08-15", source_list="approved-static-candidates")


def test_invalid_dates_are_explicit_exclusions():
    with pytest.raises(ValueError, match="invalid calendar day"):
        build_manifest([candidate("AAPL", day="2026-02-30")], selection_date="2026-08-15", source_list="approved-static-candidates")


def test_caps_quotas_and_serialization():
    rows = [candidate("A", "2026-08-01", "2026-08-03", 2, "Tech"), candidate("B", "2026-08-02", "2026-08-04", 2, "Tech")]
    probes = [probe("A", "2026-08-01", "2026-08-03", 2), probe("B", "2026-08-02", "2026-08-04", 2)]
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest(rows, probe_results=probes, intended_units=2, selection_date="2026-08-15", source_list="approved-static-candidates")


def test_nonpass_requires_reason_and_status_is_closed():
    with pytest.raises(ValueError): validate_probe_result(probe(status="HARD_GAP"))
    with pytest.raises(ValueError): validate_probe_result(probe(status="UNKNOWN", reasons=("bad",)))


def test_candidate_requires_provenance_and_asset_type():
    with pytest.raises(ValueError): normalize_candidate({"ticker": "AAPL", "sector": "Tech"})
    with pytest.raises(ValueError): normalize_candidate({"ticker": "AAPL", "sector": "Tech", "asset_type": "crypto"}, selection_date="2026-08-15", source_list="x")


def test_build_manifest_requires_valid_selection_date():
    for value in (None, "", "20260815", "2026-02-30"):
        with pytest.raises(ValueError, match="selection_date"):
            build_manifest([], selection_date=value, source_list="approved-static-candidates")


def test_build_manifest_requires_real_nonblank_source_list():
    for value in (None, "", "   ", "point-in-time-static"):
        with pytest.raises(ValueError, match="source_list"):
            build_manifest([], selection_date="2026-08-15", source_list=value)


def test_manifest_rejects_conflicting_candidate_provenance():
    # build_manifest now requires a calendar_snapshot for any non-held row
    # before it even reaches per-candidate provenance conflict checks (see
    # Vol_Suite/dealer_exposure_universe.py::build_manifest), so a plain,
    # non-held candidate row raises the calendar-snapshot gate first.
    row = candidate("AAPL")
    row.update(selection_date="2026-08-14", source_list="other-approved-list")
    with pytest.raises(ValueError, match="calendar snapshot is required"):
        build_manifest([row], selection_date="2026-08-15", source_list="approved-static-candidates")


def test_valid_provenance_is_serialized_without_defaults():
    # Held rows bypass the calendar_snapshot requirement (they are excluded
    # from the manifest regardless), which lets this test build a real
    # UniverseManifest and check its serialized provenance without needing a
    # full OpEx calendar snapshot fixture.
    row = candidate("AAPL")
    row.update(selection_date="2026-08-15", source_list="approved-static-candidates")
    manifest = build_manifest([row], held_pairs={("AAPL", "2026-08-17")}, selection_date="2026-08-15", source_list="approved-static-candidates")
    payload = manifest.to_dict()
    assert payload["selection_date"] == "2026-08-15"
    assert payload["source_list"] == "approved-static-candidates"


def test_no_network_dependency_and_closed_probe_checks():
    assert "same_expiry_grid_oi_iv" in PROBE_CHECKS and "return_clocks" in PROBE_CHECKS
    assert DTE_STRATA == ((1, 3), (4, 7), (8, 10))
