import json
import math
from types import SimpleNamespace

import pytest
from provenance_contract import canonical_json_bytes, canonical_sha256
from run_live_vs_expiry_book_common_input import (
    CanonicalPayload,
    ComparisonInvalid,
    compare_common_input,
    make_canonical_input,
    validate_causal_eligibility,
    write_deterministic_artifact,
)

EXPIRY = "20260918"


def _input():
    return make_canonical_input(
        "IWM", "20260814", EXPIRY, 35, 220.0, "2026-08-14T13:00:00Z",
        [{"strike": 210, "right": "P", "iv": 0.24, "oi": 1000},
         {"strike": 220, "right": "C", "iv": 0.20, "oi": 1200}],
        ["a" * 64],
    )


def _expected_vanna(strike, iv=0.2):
    T = 35 / 365
    d1 = (math.log(220.0 / strike) + (0.05 + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    return -math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) * d2 / iv


def _row(strike, right, value, *, expiry=EXPIRY, iv=0.2):
    return SimpleNamespace(strike=strike, right=right, expiry=expiry, iv=iv, T=35 / 365,
                           greeks={"vanna": _expected_vanna(strike, iv)},
                           exposure_of=lambda _g, value=value: value)


def _engines(live_accumulate=True, *, live_rows=None, new_rows=None):
    def live(payload):
        consumed = payload.read()
        return SimpleNamespace(
            attestation=__import__("hashlib").sha256(consumed).hexdigest(),
            sign_model="vol_surface_replication", accumulate=live_accumulate,
            gamma_records=live_rows or [
                SimpleNamespace(strike=210, right="P", expiry=EXPIRY, vanna=-0.02, oi=1000, applied_sign=1),
                SimpleNamespace(strike=220, right="C", expiry=EXPIRY, vanna=0.01, oi=1200, applied_sign=1),
            ],
        )

    def new(payload):
        consumed = payload.read()
        return SimpleNamespace(attestation=__import__("hashlib").sha256(consumed).hexdigest(),
                               expiry=EXPIRY, T=35 / 365,
                               rows=new_rows or [_row(210, "P", -20.0), _row(220, "C", 12.0)])
    return live, new


def test_adapters_consume_one_immutable_payload_and_compare_levels_not_flow():
    live, new = _engines()
    result = compare_common_input(_input(), live, new)
    assert result["input_hash"] == result["config"]["canonical_sha256"]
    assert result["coverage"] == {"live": 2, "new": 2, "common": 2, "total": 2}
    assert result["config"]["flow_compared"] is False
    assert {p["live_units"] for p in result["pairs"]} == {"shares/vol-pt"}
    assert {p["new_sign_source"] for p in result["pairs"]} == {"-1xBS"}
    assert {p["class"] for p in result["pairs"]} == {"same"}


def test_adapter_that_transforms_or_ignores_payload_invalidates_comparison():
    _live, new = _engines()

    def dishonest_live(payload):
        # It ignores the opaque payload and cannot forge a harness attestation.
        return SimpleNamespace(attestation="forged",
                               sign_model="vol_surface_replication", accumulate=True,
                               gamma_records=[])

    with pytest.raises(ComparisonInvalid, match="did not consume canonical") as exc:
        compare_common_input(_input(), dishonest_live, new)
    assert exc.value.invalid_result["status"] == "INVALID"
    assert exc.value.invalid_result["exclusions"][0]["engine"] == "live"


def test_live_fallback_is_comparison_invalid():
    live, new = _engines(live_accumulate=False)
    with pytest.raises(ComparisonInvalid, match="identity/actual accumulation"):
        compare_common_input(_input(), live, new)


@pytest.mark.parametrize("case", ["shared_subset", "duplicate", "extra", "wrong_expiry"])
def test_coverage_must_equal_canonical_keys_and_expiry(case):
    live, new = _engines()
    if case == "shared_subset":
        live, new = _engines(
            live_rows=[SimpleNamespace(strike=210, right="P", expiry=EXPIRY, vanna=-0.02, oi=1000, applied_sign=1)],
            new_rows=[_row(210, "P", -20.0)],
        )
    elif case == "duplicate":
        live, new = _engines(new_rows=[_row(210, "P", -20.0), _row(210, "P", -21.0), _row(220, "C", 12.0)])
    elif case == "extra":
        live, new = _engines(new_rows=[_row(210, "P", -20.0), _row(220, "C", 12.0), _row(230, "P", 3.0)])
    else:
        live, new = _engines(new_rows=[_row(210, "P", -20.0, expiry="20261016"), _row(220, "C", 12.0)])

    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new)
    assert exc.value.invalid_result["status"] == "INVALID"
    reasons = {item["reason"] for item in exc.value.invalid_result["exclusions"]}
    expected = {"missing strike/right key", "duplicate strike/right row", "extra strike/right key", "wrong expiry"}
    assert reasons & expected


def test_deadband_classifies_zero_without_dividing_by_zero():
    live, _new = _engines()
    zero_new = _engines(new_rows=[_row(210, "P", 0.0), _row(220, "C", 0.0)])[1]
    result = compare_common_input(_input(), live, zero_new, deadband=1e-9)
    assert all(p["class"] == "zero-vs-nonzero" for p in result["pairs"])
    assert result["aggregate"]["D_conv"] == 1.0


def test_adapter_contract_is_dependency_independent():
    """The injected payload/digest contract is the smoke test; no matplotlib import is needed."""
    live, new = _engines()
    assert compare_common_input(_input(), live, new)["status"] == "VALID"


def test_malformed_input_right_is_rejected_without_normalization():
    with pytest.raises(ValueError, match="invalid right"):
        make_canonical_input(
            "IWM", "20260814", EXPIRY, 35, 220.0, "ts",
            [{"strike": 210, "right": "PUT", "iv": 0.24, "oi": 1000}], ["h"],
        )


def test_malformed_output_right_is_invalid():
    live, _new = _engines()
    new = _engines(new_rows=[_row(210, "PUT", -20.0), _row(220, "C", 12.0)])[1]
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new)
    assert any("malformed right" in item["reason"] for item in exc.value.invalid_result["exclusions"])


def test_vanna_invariant_is_runtime_verified():
    live, _new = _engines()
    bad = _row(210, "P", -20.0)
    bad.greeks["vanna"] += 0.5
    new = _engines(new_rows=[bad, _row(220, "C", 12.0)])[1]
    with pytest.raises(ComparisonInvalid, match="coverage") as exc:
        compare_common_input(_input(), live, new)
    assert any("rec.vanna is not -1xBS" in item["reason"]
               for item in exc.value.invalid_result["exclusions"])


def test_payload_is_opaque_and_only_read_records_bytes():
    payload = CanonicalPayload(b"canonical")
    assert not hasattr(payload, "canonical_input")
    assert not hasattr(payload, "consume")
    assert not hasattr(payload, "read_sha256")
    assert not hasattr(payload, "read_bytes")
    assert not any(name in {"data", "digest", "canonical_bytes", "_accessed"} for name in dir(payload))
    with pytest.raises((AttributeError, TypeError)):
        vars(payload)
    assert payload.read() == b"canonical"


def test_malformed_strike_row_is_structured_invalid():
    live, _new = _engines()
    malformed = SimpleNamespace(right="P", expiry=EXPIRY)
    new = _engines(new_rows=[malformed, _row(220, "C", 12.0)])[1]
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new)
    assert any("malformed strike/right row" in item["reason"]
               for item in exc.value.invalid_result["exclusions"])


# Fields required by run_live_vs_expiry_book_common_input.py's
# _PROVENANCE_FIELDS[24:] -- calendar/session/event identity that must be
# present on the unit, echoed in the registry's artifact manifest, and
# duplicated on the top-level registry entry (see
# _validate_registered_provenance's "registry calendar field is detached"
# check). These are independent of dealer_exposure_universe.py's calendar
# binding contract; this module only requires the fields to be present,
# non-empty, and consistent between unit/manifest/entry -- it does not
# recompute them from a live OpEx calendar snapshot.
_CALENDAR_PROVENANCE = {
    "calendar_hash": "e" * 64,
    "calendar_policy_version": "us-options-v1",
    "resolver_code_hash": "f" * 64,
    "snapshot_hash": "1" * 64,
    "as_of": "2026-08-13T00:00:00Z",
    "event_ids": ["opex-1"],
    "event_types": ["OPEX"],
    "event_overlap": False,
    "event_window_id": "opex-1:OPEX_DAY",
    "window_start": "2026-08-14T09:30:00-04:00",
    "window_end": "2026-08-14T16:00:00-04:00",
    "window_policy": "OPEX_DAY",
    "nominal_date": "2026-08-14",
    "observed_expiry_date": "2026-08-14",
    "session_id": "S-2026-08-14",
    "session_status": "OPEN",
    "regular_open": "2026-08-14T09:30:00-04:00",
    "regular_close": "2026-08-14T16:00:00-04:00",
    "early_close": False,
    "settlement_style": "PM_CLOSE",
    "settlement_timestamp": "2026-08-14T16:00:00-04:00",
    "calendar_binding_hash": "2" * 64,
}


def _causal_unit(status="PASS", provenance="PRE_WINDOW", value=0.1, source="2026-08-14T12:00:00Z", breach="2026-08-14T13:00:00Z", source_hash="a" * 64, *, raw_hash="b" * 64, artifact_hash="c" * 64, timezone="America/New_York"):
    candidate_key = "IWM|2026-08-14"
    unit = {"ticker": "IWM", "calendar_day": "2026-08-14", "candidate_key": candidate_key, "status": status,
            "pre_window_provenance": provenance, "pre_window_value": value,
            "delta_iv_pre_window": value, "iv_before_ts": "2026-08-14T11:00:00Z",
            "iv_before_value": 0.0, "iv_source_ts": source, "iv_source_value": value,
            "delta_iv_aggregation": "iv_source_minus_iv_before",
            "delta_iv_aggregation_version": "1",
            "breach_window_start_prov": breach, "source_hashes": [source_hash],
            "raw_payload_hash": raw_hash, "artifact_hash": artifact_hash,
            "declared_timezone": timezone, "timezone": timezone, "endpoint": "https://example.invalid/chain",
            "request_parameters": {"ticker": "IWM", "day": "2026-08-14"},
            "spot_timestamp": "2026-08-14T10:00:00Z", "chain_timestamp": source,
            "expiry": EXPIRY, "dte": 35, "canonical_input_hash": _input().input_hash,
            "imputed": False, "no_imputation": True,
            "same_day_cluster": {"cluster_id": "2026-08-14", "calendar_day": "2026-08-14",
                                  "tickers": ["IWM"], "n_tickers": 1,
                                  "aggregation_rule": "preserve_ticker_values_v1"}}
    unit.update(_CALENDAR_PROVENANCE)
    return unit


def _registry(unit, payload=b"verified canonical payload"):
    import hashlib
    unit["raw_payload_hash"] = hashlib.sha256(payload).hexdigest()
    manifest = {key: unit[key] for key in (
        "candidate_key", "ticker", "calendar_day", "canonical_input_hash", "status", "raw_payload_hash", "endpoint", "request_parameters",
        "declared_timezone", "timezone", "spot_timestamp", "chain_timestamp", "iv_source_ts",
        "breach_window_start_prov", "source_hashes", "same_day_cluster",
        "iv_before_ts", "iv_before_value", "iv_source_value", "delta_iv_aggregation",
        "delta_iv_aggregation_version", "expiry", "dte", "canonical_input_hash",
        "imputed", "no_imputation", *_CALENDAR_PROVENANCE)}
    unit["artifact_hash"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    entry = {"artifact_hash": unit["artifact_hash"],
            "raw_payload_hash": unit["raw_payload_hash"], "candidate_key": unit["candidate_key"],
            "status": unit["status"], "artifact_manifest": manifest,
            "source_hashes": unit["source_hashes"], "payload_bytes": payload,
            "ticker": unit["ticker"], "calendar_day": unit["calendar_day"],
            "canonical_input_hash": unit["canonical_input_hash"],
            "expiry": unit["expiry"], "dte": unit["dte"]}
    for field in (*_CALENDAR_PROVENANCE, "timezone"):
        entry[field] = unit[field]
    return {unit["artifact_hash"]: entry}


def test_causal_gate_blocks_missing_equal_later_and_mixed_provenance():
    for units in (
        [_causal_unit(value=None)],
        [_causal_unit(source="2026-08-14T13:00:00Z")],
        [_causal_unit(source="2026-08-14T14:00:00Z")],
        [_causal_unit(), _causal_unit(provenance="ASSOCIATIONAL", value=None)],
    ):
        result = validate_causal_eligibility(units)
        assert result["causal_status"] == "CAUSAL_BLOCKED"


def test_compare_returns_causal_blocked_invalid_result():
    live, new = _engines()
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new, provenance_units=[_causal_unit(value=None)])
    assert exc.value.invalid_result["status"] == "COMPARISON_INVALID"
    assert exc.value.invalid_result["causal_status"] == "CAUSAL_BLOCKED"


def test_causal_gate_requires_registered_payload_binding_and_rejects_forged_hash():
    unit = _causal_unit()
    assert validate_causal_eligibility([unit])["causal_status"] == "CAUSAL_BLOCKED"
    registry = _registry(unit)
    assert validate_causal_eligibility([unit], intended_units=1, artifact_registry=registry)["causal_status"] == "CAUSAL_ELIGIBLE"
    unit["raw_payload_hash"] = "d" * 64
    assert validate_causal_eligibility([unit], artifact_registry=registry)["causal_status"] == "CAUSAL_BLOCKED"


def test_causal_gate_rejects_wrong_calendar_day_in_declared_timezone():
    unit = _causal_unit(source="2026-08-15T05:30:00Z", breach="2026-08-15T06:00:00Z")
    result = validate_causal_eligibility([unit], artifact_registry=_registry(unit))
    assert result["causal_status"] == "CAUSAL_BLOCKED"
    assert any("calendar_day" in reason["reason"] for reason in result["reasons"])


def test_valid_registered_provenance_is_causally_eligible():
    unit = _causal_unit(source="2026-08-14T10:00:00-04:00", breach="2026-08-14T19:00:00Z")
    result = validate_causal_eligibility([unit], intended_units=1, artifact_registry=_registry(unit))
    assert result["causal_status"] == "CAUSAL_ELIGIBLE"


def test_direct_causal_validator_requires_explicit_denominator():
    unit = _causal_unit()
    result = validate_causal_eligibility([unit], artifact_registry=_registry(unit))
    assert result["status"] == "COMPARISON_INVALID"
    assert result["causal_status"] == "CAUSAL_BLOCKED"
    assert result["N"] == 0
    assert any("explicit intended_units or intended_corpus_manifest" in item["reason"] for item in result["reasons"])


@pytest.mark.parametrize("field", ["endpoint", "request_parameters", "spot_timestamp", "chain_timestamp", "iv_source_ts", "breach_window_start_prov"])
def test_causal_gate_requires_complete_acquisition_provenance(field):
    unit = _causal_unit()
    registry = _registry(unit)
    unit.pop(field)
    assert validate_causal_eligibility([unit], artifact_registry=registry)["causal_status"] == "CAUSAL_BLOCKED"


def test_causal_gate_rejects_forged_manifest_cross_fields():
    unit = _causal_unit()
    registry = _registry(unit)
    entry = next(iter(registry.values()))
    entry["artifact_manifest"]["candidate_key"] = "FORGED"
    assert validate_causal_eligibility([unit], artifact_registry=registry)["causal_status"] == "CAUSAL_BLOCKED"


def test_valid_registry_identity_round_trip_is_causally_eligible():
    unit = _causal_unit()
    registry = _registry(unit)
    assert validate_causal_eligibility([unit], intended_units=1, artifact_registry=registry)["causal_status"] == "CAUSAL_ELIGIBLE"


@pytest.mark.parametrize("field", [
    "ticker", "calendar_day", "expiry", "dte", "canonical_input_hash",
    "candidate_key", "status", "raw_payload_hash", "source_hashes", "artifact_hash",
])
def test_causal_gate_rejects_mutated_top_level_registry_identity(field):
    unit = _causal_unit()
    registry = _registry(unit)
    entry = next(iter(registry.values()))
    if field == "artifact_hash" or field == "raw_payload_hash":
        entry[field] = "f" * 64
    elif field == "source_hashes":
        entry[field] = ["f" * 64]
    elif field == "dte":
        entry[field] = 36
    elif field == "status":
        entry[field] = "FAIL"
    else:
        entry[field] = "FORGED"
    result = validate_causal_eligibility([unit], intended_units=1, artifact_registry=registry)
    assert result["causal_status"] == "CAUSAL_BLOCKED"
    assert result["status"] == "COMPARISON_INVALID"


@pytest.mark.parametrize("mutator", [
    lambda u: u.pop("iv_before_ts"),
    lambda u: u.pop("iv_source_value"),
    lambda u: u.update(delta_iv_pre_window=0.7),
    lambda u: u.update(delta_iv_aggregation="day_level_delta_iv"),
    lambda u: u.update(iv_before_ts="2026-08-14T14:00:00Z"),
    lambda u: u.update(iv_before_ts="2026-08-13T23:00:00Z"),
])
def test_causal_gate_rejects_scalar_or_unverifiable_delta_iv(mutator):
    unit = _causal_unit()
    registry = _registry(unit)
    mutator(unit)
    assert validate_causal_eligibility([unit], artifact_registry=registry)["causal_status"] == "CAUSAL_BLOCKED"


def test_causal_gate_requires_same_day_clustering_metadata():
    unit = _causal_unit()
    registry = _registry(unit)
    unit.pop("same_day_cluster")
    assert validate_causal_eligibility([unit], artifact_registry=registry)["causal_status"] == "CAUSAL_BLOCKED"


def test_declared_timezone_handles_utc_boundary_calendar_day():
    unit = _causal_unit(source="2026-08-15T03:30:00Z", breach="2026-08-15T03:45:00Z")
    result = validate_causal_eligibility([unit], intended_units=1, artifact_registry=_registry(unit))
    assert result["causal_status"] == "CAUSAL_ELIGIBLE"


def test_compare_blocks_forged_registered_provenance():
    live, new = _engines()
    unit = _causal_unit()
    registry = _registry(unit)
    unit["raw_payload_hash"] = "d" * 64
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new, provenance_units=[unit], artifact_registry=registry)
    assert exc.value.invalid_result["status"] == "COMPARISON_INVALID"
    assert exc.value.invalid_result["causal_status"] == "CAUSAL_BLOCKED"


@pytest.mark.parametrize("field", ["spot", "strike", "iv", "oi"])
def test_canonical_input_rejects_non_finite_numbers(field):
    rows = [{"strike": 210, "right": "P", "iv": 0.24, "oi": 1000}]
    spot = 220.0
    if field == "spot":
        spot = float("nan")
    else:
        rows[0][field] = float("inf")
    with pytest.raises(ValueError):
        make_canonical_input("IWM", "20260814", EXPIRY, 35, spot, "2026-08-14T13:00:00Z", rows, ["a" * 64])


def test_canonical_json_refuses_nan_and_inf():
    inp = _input()
    object.__setattr__(inp, "spot", float("inf"))
    with pytest.raises(ValueError, match="non-finite"):
        inp.canonical_bytes()


@pytest.mark.parametrize("field", ["delta_iv_pre_window", "iv_before_ts", "same_day_cluster"])
def test_causal_validator_missing_nested_fields_is_structured_fail_closed(field):
    unit = _causal_unit()
    registry = _registry(unit)
    unit.pop(field)
    result = validate_causal_eligibility([unit], artifact_registry=registry)
    assert result["status"] == "COMPARISON_INVALID"
    assert result["causal_status"] == "CAUSAL_BLOCKED"
    assert result["reasons"]


def test_causal_validator_rejects_cluster_id_and_membership_mismatch():
    unit = _causal_unit()
    unit["same_day_cluster"]["cluster_id"] = "2026-08-15"
    assert validate_causal_eligibility([unit], artifact_registry=_registry(unit))["causal_status"] == "CAUSAL_BLOCKED"
    unit = _causal_unit()
    unit["same_day_cluster"]["n_tickers"] = 2
    assert validate_causal_eligibility([unit], artifact_registry=_registry(unit))["causal_status"] == "CAUSAL_BLOCKED"


@pytest.mark.parametrize("field", ["vanna", "oi", "applied_sign"])
def test_non_finite_live_output_is_structured_invalid(field):
    rows = [
        SimpleNamespace(strike=210, right="P", expiry=EXPIRY, vanna=-0.02, oi=1000, applied_sign=1),
        SimpleNamespace(strike=220, right="C", expiry=EXPIRY, vanna=0.01, oi=1200, applied_sign=1),
    ]
    setattr(rows[0], field, float("nan"))
    live, new = _engines(live_rows=rows)
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new)
    assert any("finite" in item["reason"] for item in exc.value.invalid_result["exclusions"])


def test_non_finite_new_output_is_structured_invalid():
    live, _new = _engines()
    bad = _row(210, "P", -20.0)
    bad.greeks["vanna"] = float("inf")
    new = _engines(new_rows=[bad, _row(220, "C", 12.0)])[1]
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new)
    assert any("finite" in item["reason"] for item in exc.value.invalid_result["exclusions"])


def test_artifact_writer_is_strict_and_refuses_invalid_payload(tmp_path):
    target = tmp_path / "comparison.json"
    with pytest.raises(ComparisonInvalid):
        write_deterministic_artifact(str(target), {"status": "COMPARISON_INVALID", "value": float("nan")})
    assert not target.exists()
    with pytest.raises(ComparisonInvalid):
        write_deterministic_artifact(str(target), {"status": "VALID", "value": float("nan")})
    assert not target.exists()


def test_causal_gate_rejects_contradictory_same_day_clusters_across_units():
    first = _causal_unit()
    second = _causal_unit()
    second["ticker"] = "AAPL"
    second["candidate_key"] = "AAPL|2026-08-14"
    second["same_day_cluster"] = {"cluster_id": "2026-08-14", "calendar_day": "2026-08-14",
                                   "tickers": ["AAPL"], "n_tickers": 1,
                                   "aggregation_rule": "preserve_ticker_values_v1"}
    registry = {}
    registry.update(_registry(first))
    registry.update(_registry(second))
    result = validate_causal_eligibility([first, second], intended_units=2, artifact_registry=registry)
    assert result["causal_status"] == "CAUSAL_BLOCKED"
    assert any("inconsistent across unit set" in reason["reason"] for reason in result["reasons"])


def test_missing_gamma_or_rows_container_is_structured_invalid():
    live, new = _engines()

    def missing_gamma(payload):
        payload.read()
        return SimpleNamespace(sign_model="vol_surface_replication", accumulate=True, gamma_records=None)

    def missing_rows(payload):
        payload.read()
        return SimpleNamespace(rows=None)

    with pytest.raises(ComparisonInvalid, match="live result container"):
        compare_common_input(_input(), missing_gamma, new)
    with pytest.raises(ComparisonInvalid, match="new result container"):
        compare_common_input(_input(), live, missing_rows)


def test_runner_exception_is_structured_comparison_invalid():
    _live, new = _engines()

    def raising_runner(payload):
        payload.read()
        raise RuntimeError("runner unavailable")

    with pytest.raises(ComparisonInvalid, match="live runner failed") as exc:
        compare_common_input(_input(), raising_runner, new)
    assert exc.value.invalid_result["status"] == "COMPARISON_INVALID"


def test_compare_accepts_fully_bound_provenance_unit():
    live, new = _engines()
    unit = _causal_unit()
    assert compare_common_input(_input(), live, new, provenance_units=[unit], artifact_registry=_registry(unit), intended_units=1)["status"] == "VALID"


@pytest.mark.parametrize("field,value", [
    ("ticker", "AAPL"), ("calendar_day", "2026-08-15"),
    ("expiry", "20261016"), ("dte", 63),
    ("canonical_input_hash", "d" * 64),
])
def test_compare_blocks_provenance_detached_from_canonical_identity(field, value):
    live, new = _engines()
    unit = _causal_unit()
    registry = _registry(unit)
    unit[field] = value
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, new, provenance_units=[unit], artifact_registry=registry)
    assert exc.value.invalid_result["status"] == "COMPARISON_INVALID"
    assert exc.value.invalid_result["causal_status"] == "CAUSAL_BLOCKED"


@pytest.mark.parametrize("field,value", [("imputed", True), ("no_imputation", False), ("no_imputation", None)])
def test_causal_gate_rejects_imputed_or_missing_no_imputation_flags(field, value):
    unit = _causal_unit()
    registry = _registry(unit)
    unit[field] = value
    assert validate_causal_eligibility([unit], artifact_registry=registry)["causal_status"] == "CAUSAL_BLOCKED"


def test_causal_gate_rejects_duplicate_candidate_and_provenance_units():
    first = _causal_unit()
    second = _causal_unit()
    registry = _registry(first)
    result = validate_causal_eligibility([first, second], intended_units=2, artifact_registry=registry)
    assert result["causal_status"] == "CAUSAL_BLOCKED"
    assert any("duplicate" in reason["reason"] for reason in result["reasons"])


def test_registry_rejects_post_registration_expiry_or_dte_mutation():
    for field, value in (("expiry", "20261016"), ("dte", 63)):
        unit = _causal_unit()
        registry = _registry(unit)
        unit[field] = value
        result = validate_causal_eligibility([unit], artifact_registry=registry)
        assert result["causal_status"] == "CAUSAL_BLOCKED"


def test_compare_requires_explicit_intended_units_for_causal_mode():
    unit = _causal_unit()
    with pytest.raises(ComparisonInvalid, match="intended_units or intended_corpus_manifest") as exc:
        compare_common_input(_input(), *_engines(), provenance_units=[unit], artifact_registry=_registry(unit))
    assert exc.value.invalid_result["causal_status"] == "CAUSAL_BLOCKED"


def test_compare_blocks_partial_supplied_corpus_against_intended_count():
    unit = _causal_unit()
    with pytest.raises(ComparisonInvalid, match="incomplete"):
        compare_common_input(_input(), *_engines(), provenance_units=[unit], artifact_registry=_registry(unit), intended_units=2)


def test_compare_requires_exact_intended_corpus_identities():
    unit = _causal_unit()
    with pytest.raises(ComparisonInvalid, match="incomplete") as exc:
        compare_common_input(_input(), *_engines(), provenance_units=[unit], artifact_registry=_registry(unit),
                             intended_corpus_manifest=["OTHER|2026-08-14"])
    assert any("exactly match intended" in item["reason"] for item in exc.value.invalid_result["exclusions"])


@pytest.mark.parametrize("mutation,needle", [
    (lambda result: setattr(result, "T", 34 / 365), "result T"),
    (lambda result: setattr(result, "expiry", "20261016"), "result expiry"),
    (lambda result: setattr(result.rows[0], "T", 34 / 365), "row T"),
    (lambda result: setattr(result.rows[0], "expiry", "20261016"), "row expiry"),
])
def test_new_engine_expiry_and_t_are_bound_to_canonical_input(mutation, needle):
    live, base_new = _engines()
    def bad_new(payload):
        result = base_new(payload)
        mutation(result)
        return result
    with pytest.raises(ComparisonInvalid) as exc:
        compare_common_input(_input(), live, bad_new)
    assert any(needle in item["reason"] for item in exc.value.invalid_result["exclusions"])


def test_shared_canonical_hash_is_strict_and_representation_sensitive():
    assert canonical_sha256({"value": 1}) != canonical_sha256({"value": "1"})
    with pytest.raises(TypeError):
        canonical_json_bytes({"value": object()})
    changed = make_canonical_input("IWM", "20260814", EXPIRY, 35, 220.0, "2026-08-14T13:00:00Z",
                                   [{"strike": 210, "right": "P", "iv": 0.24, "oi": 1001}], ["a" * 64])
    assert changed.input_hash != _input().input_hash
