from types import SimpleNamespace

import pytest
from run_live_vs_expiry_book_common_input import (
    ComparisonInvalid,
    compare_common_input,
    make_canonical_input,
)


def _input():
    return make_canonical_input(
        "IWM", "20260814", "20260918", 35, 220.0, "2026-08-14T13:00:00Z",
        [{"strike": 210, "right": "P", "iv": 0.24, "oi": 1000},
         {"strike": 220, "right": "C", "iv": 0.20, "oi": 1200}],
        ["raw-sha256"],
    )


def _engines(live_accumulate=True):
    seen = []
    def live(inp):
        seen.append(("live", inp.canonical_bytes()))
        return SimpleNamespace(
            sign_model="vol_surface_replication", accumulate=live_accumulate,
            gamma_records=[
                SimpleNamespace(strike=210, right="P", vanna=-0.02, oi=1000, applied_sign=1),
                SimpleNamespace(strike=220, right="C", vanna=0.01, oi=1200, applied_sign=1),
            ],
        )
    def new(inp):
        seen.append(("new", inp.canonical_bytes()))
        rows = [
            SimpleNamespace(strike=210, right="P", exposure_of=lambda g: -20.0),
            SimpleNamespace(strike=220, right="C", exposure_of=lambda g: 12.0),
        ]
        return SimpleNamespace(rows=rows)
    return live, new, seen


def test_canonical_input_is_identical_and_compares_levels_not_flow():
    live, new, seen = _engines()
    result = compare_common_input(_input(), live, new)
    assert seen[0][1] == seen[1][1]
    assert result["coverage"] == {"live": 2, "new": 2, "common": 2, "total": 2}
    assert result["config"]["flow_compared"] is False
    assert {p["live_units"] for p in result["pairs"]} == {"shares/vol-pt"}
    assert {p["new_sign_source"] for p in result["pairs"]} == {"-1xBS"}
    assert {p["class"] for p in result["pairs"]} == {"same"}


def test_live_fallback_is_comparison_invalid():
    live, new, _ = _engines(live_accumulate=False)
    with pytest.raises(ComparisonInvalid, match="identity/actual accumulation"):
        compare_common_input(_input(), live, new)


def test_missing_strike_coverage_is_fail_closed():
    live, _new, _ = _engines()
    def partial_new(inp):
        return SimpleNamespace(rows=[SimpleNamespace(strike=210, right="P", exposure_of=lambda g: -20.0)])
    with pytest.raises(ComparisonInvalid, match="100% strike/right coverage"):
        compare_common_input(_input(), live, partial_new)


def test_deadband_classifies_zero_without_dividing_by_zero():
    live, _new, _ = _engines()
    def zero_new(inp):
        return SimpleNamespace(rows=[
            SimpleNamespace(strike=210, right="P", exposure_of=lambda g: 0.0),
            SimpleNamespace(strike=220, right="C", exposure_of=lambda g: 0.0),
        ])
    result = compare_common_input(_input(), live, zero_new, deadband=1e-9)
    assert all(p["class"] == "zero-vs-nonzero" for p in result["pairs"])
    assert result["aggregate"]["D_conv"] == 1.0
