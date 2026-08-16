from __future__ import annotations

from copy import deepcopy

import pytest

from Vol_Suite.dealer_exposure_acquisition import _strict_prewindow, admit_acquisition
from Vol_Suite.dealer_exposure_authorization import AcquisitionAuthorization

from test_dealer_exposure_authorization import _authorization, _manifest


H1 = "a" * 64
H2 = "b" * 64


def _auth_and_manifest():
    manifest = _manifest()
    auth = AcquisitionAuthorization.from_mapping(
        _authorization(manifest), candidate_manifest=manifest,
        now="2026-08-15T12:30:00+00:00",
    )
    return auth, manifest


def _prewindow():
    return {
        "candidate_key": "XLE|2026-07-06|2026-07-06|1",
        "calendar_day": "2026-07-06",
        "declared_timezone": "UTC",
        "breach_window_start_prov": "2026-07-06T15:00:00Z",
        "imputed": False,
        "no_imputation": True,
        "delta_iv_pre_window": 0.1,
        "delta_iv_aggregation": "iv_source_minus_iv_before",
        "delta_iv_aggregation_version": "1",
        "pre_window_observations": [
            {"role": "PRE_WINDOW", "timestamp": "2026-07-06T13:00:00Z", "iv": 0.2, "source_identity": "before", "source_hash": H1},
            {"role": "PRE_WINDOW", "timestamp": "2026-07-06T14:00:00Z", "iv": 0.3, "source_identity": "source", "source_hash": H2},
        ],
    }


def test_strict_prewindow_rejects_role_only_and_accepts_fully_evidenced_control():
    item = _prewindow()
    unit = {"calendar_day": item["calendar_day"], "source_hashes": [H1, H2]}
    registry = {"source_hashes": [H1, H2]}
    assert _strict_prewindow(item, unit, registry) == []
    incomplete = deepcopy(item)
    incomplete["pre_window_observations"] = [{"role": "PRE_WINDOW"}, {"role": "PRE_WINDOW"}]
    assert _strict_prewindow(incomplete, unit, registry)


@pytest.mark.parametrize("field", ["units", "probe_calls", "heavy_calls", "total_endpoint_calls", "payload_bytes", "wall_seconds", "concurrency"])
def test_missing_each_cost_counter_is_not_zero(field):
    auth, manifest = _auth_and_manifest()
    evidence = {"usage": {name: 0 for name in ("units", "probe_calls", "heavy_calls", "total_endpoint_calls", "payload_bytes", "wall_seconds", "concurrency")}}
    evidence["usage"].pop(field)
    _, audit = admit_acquisition(auth, manifest, [], evidence, {})
    assert not audit["admitted"]
    assert any("cost usage counters" in reason for reason in audit["blocked"])


def test_forged_probe_identity_and_shallow_registry_are_blocked():
    auth, manifest = _auth_and_manifest()
    unit = manifest["units"][0]
    probe = {"candidate_key": unit["candidate_key"], "request_parameters": {}, "probe_identity": {"calendar_hash": "forged"}}
    evidence = {"usage": {"units": 0, "probe_calls": 0, "heavy_calls": 0, "total_endpoint_calls": 0, "payload_bytes": 0, "wall_seconds": 0, "concurrency": 1}}
    _, audit = admit_acquisition(auth, manifest, [probe], evidence, {"x": {}})
    assert not audit["admitted"]
    assert any("identity" in reason or "registry" in reason for reason in audit["blocked"])


def test_manifest_projection_mismatch_is_rejected_before_admission():
    auth, manifest = _auth_and_manifest()
    altered = deepcopy(manifest)
    altered["units"][0]["ticker"] = "MSFT"
    _, audit = admit_acquisition(auth, altered, [], {}, {})
    assert not audit["admitted"]
    assert audit["blocked"]
