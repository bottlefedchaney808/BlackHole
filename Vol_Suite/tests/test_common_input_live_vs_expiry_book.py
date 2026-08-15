from types import SimpleNamespace

import pytest
from run_live_vs_expiry_book_common_input import (
    ComparisonInvalid,
    compare_common_input,
    make_canonical_input,
)

EXPIRY = "20260918"


def _input():
    return make_canonical_input(
        "IWM", "20260814", EXPIRY, 35, 220.0, "2026-08-14T13:00:00Z",
        [{"strike": 210, "right": "P", "iv": 0.24, "oi": 1000},
         {"strike": 220, "right": "C", "iv": 0.20, "oi": 1200}],
        ["raw-sha256"],
    )


def _row(strike, right, value, *, expiry=EXPIRY):
    return SimpleNamespace(strike=strike, right=right, expiry=expiry,
                           exposure_of=lambda _g, value=value: value)


def _engines(live_accumulate=True, *, live_rows=None, new_rows=None):
    def live(payload):
        return SimpleNamespace(
            consumed_input_sha256=payload.digest,
            sign_model="vol_surface_replication", accumulate=live_accumulate,
            gamma_records=live_rows or [
                SimpleNamespace(strike=210, right="P", expiry=EXPIRY, vanna=-0.02, oi=1000, applied_sign=1),
                SimpleNamespace(strike=220, right="C", expiry=EXPIRY, vanna=0.01, oi=1200, applied_sign=1),
            ],
        )

    def new(payload):
        return SimpleNamespace(consumed_input_sha256=payload.digest,
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
    live, new = _engines()

    def dishonest_live(payload):
        result = live(payload)
        result.consumed_input_sha256 = "not-the-bytes-i-consumed"
        return result

    with pytest.raises(ComparisonInvalid, match="consumed-input digest") as exc:
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