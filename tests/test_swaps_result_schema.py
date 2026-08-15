"""Tests for `shared.schemas.validate_swaps_result`.

`swaps_result.json` is the artifact orchestrator.py::_build_swaps_result()
writes from context['swap_activity'] during a `--unified` run -- a
same-process DB read surfaced next to the subprocess suites' results (see
dashboard/output_runs.py's SUITE_MARKER_FILES['swaps']).

Mirrors the style of tests/test_schemas.py: a minimal-but-valid payload
factory, then targeted mutations per failure case.
"""

import pytest

from shared.schemas import (
    SWAPS_RESULT_SCHEMA_VERSION,
    SWAPS_RESULT_STATUSES,
    validate_swaps_result,
)

TS = "2026-08-01T12:00:00Z"


def swaps_result_payload(**over):
    """A minimal but schema-valid swaps_result.json payload."""
    payload = {
        "schema_version": SWAPS_RESULT_SCHEMA_VERSION,
        "suite": "swaps",
        "status": "ok",
        "ticker": "NVDA",
        "timestamp": TS,
        "row_count": 1,
        "top_notional": [{"product": "Equity Swap", "total_notional": 1_000_000}],
        "effective_date": "2026-08-01",
    }
    payload.update(over)
    return payload


@pytest.mark.unit
def test_valid_payload_passes():
    validate_swaps_result(swaps_result_payload())  # raises on violation


@pytest.mark.unit
def test_valid_no_data_payload_passes():
    validate_swaps_result(swaps_result_payload(
        status="no_data", row_count=0, top_notional=[], effective_date=None))


@pytest.mark.unit
def test_valid_error_payload_passes():
    validate_swaps_result(swaps_result_payload(
        status="error", row_count=0, top_notional=[], error="DB locked"))


@pytest.mark.unit
def test_not_a_dict_fails():
    with pytest.raises(Exception):
        validate_swaps_result(["not", "a", "dict"])


@pytest.mark.unit
def test_wrong_schema_version_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(schema_version=999))


@pytest.mark.unit
def test_wrong_suite_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(suite="vol"))


@pytest.mark.unit
def test_status_outside_enum_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(status="pending"))


@pytest.mark.unit
def test_status_enum_matches_documented_three_values():
    assert set(SWAPS_RESULT_STATUSES) == {"ok", "no_data", "error"}


@pytest.mark.unit
def test_missing_ticker_fails():
    payload = swaps_result_payload()
    del payload["ticker"]
    with pytest.raises(Exception):
        validate_swaps_result(payload)


@pytest.mark.unit
def test_empty_ticker_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(ticker=""))


@pytest.mark.unit
def test_missing_timestamp_fails():
    payload = swaps_result_payload()
    del payload["timestamp"]
    with pytest.raises(Exception):
        validate_swaps_result(payload)


@pytest.mark.unit
def test_negative_row_count_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(row_count=-1))


@pytest.mark.unit
def test_bool_row_count_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(row_count=True))


@pytest.mark.unit
def test_top_notional_must_be_a_list():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(top_notional={"product": "x"}))


@pytest.mark.unit
def test_top_notional_entry_must_be_an_object():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(top_notional=["not-an-object"]))


@pytest.mark.unit
def test_top_notional_entry_missing_product_fails():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(top_notional=[{"total_notional": 5}]))


@pytest.mark.unit
def test_error_status_requires_error_message():
    payload = swaps_result_payload(status="error", row_count=0, top_notional=[])
    with pytest.raises(Exception):
        validate_swaps_result(payload)


@pytest.mark.unit
def test_error_status_rejects_empty_error_message():
    with pytest.raises(Exception):
        validate_swaps_result(swaps_result_payload(
            status="error", row_count=0, top_notional=[], error="   "))
