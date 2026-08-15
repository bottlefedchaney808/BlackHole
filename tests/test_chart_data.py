from datetime import datetime, timezone

import pytest

from shared import chart_data
from shared.chart_data import (
    CandlePayload,
    CandleRecord,
    ChartDataError,
    normalize_candles,
)


def test_normalize_candles_sorts_and_preserves_optional_volume():
    payload = normalize_candles(
        [
            {"timestamp": "2026-08-02", "open": 101, "high": 105, "low": 99, "close": 104, "volume": 20},
            {"timestamp": "2026-08-01", "open": 98, "high": 102, "low": 97, "close": 101},
        ],
        ticker="SPY",
        lookback="1m",
        source="fixture",
    )

    assert isinstance(payload, CandlePayload)
    assert isinstance(payload.observations[0], CandleRecord)
    assert [r.timestamp.isoformat() for r in payload.observations] == [
        "2026-08-01T00:00:00",
        "2026-08-02T00:00:00",
    ]
    assert payload.observations[0].volume is None
    assert payload.observations[1].volume == 20.0
    assert payload.quality.row_count == 2
    assert payload.quality.missing_fields == ()
    assert payload.quality.warnings == ()


def test_normalize_candles_populates_payload_metadata_and_as_of():
    as_of = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
    payload = normalize_candles(
        [{"timestamp": "2026-08-01T12:00:00Z", "open": "1", "high": "2", "low": "0.5", "close": 1.5}],
        ticker=" spy ",
        interval="1d",
        lookback=10,
        source="fixture",
        as_of=as_of,
    )

    assert payload.ticker == "SPY"
    assert payload.interval == "1d"
    assert payload.lookback == 10
    assert payload.source == "fixture"
    assert payload.as_of == datetime(2026, 8, 3, 15)
    assert payload.observations[0].timestamp == datetime(2026, 8, 1, 12)
    assert payload.observations[0].open == 1.0


@pytest.mark.parametrize(
    "row, message",
    [
        ({"timestamp": "2026-08-01", "open": 1, "high": 2, "close": 1}, "missing required field: low"),
        ({"timestamp": "not-a-date", "open": 1, "high": 2, "low": 0, "close": 1}, "invalid timestamp"),
        ({"timestamp": "2026-08-01", "open": float("nan"), "high": 2, "low": 0, "close": 1}, "finite"),
        ({"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": float("inf")}, "finite"),
        ({"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": 1, "volume": float("nan")}, "finite"),
        ({"timestamp": "2026-08-01", "open": 1, "high": 3, "low": 0, "close": 3.5}, "OHLC invariant"),
    ],
)
def test_normalize_candles_rejects_invalid_rows(row, message):
    with pytest.raises(ChartDataError, match=message):
        normalize_candles([row], ticker="SPY")


def test_normalize_candles_rejects_duplicate_timestamps():
    row = {"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": 1}
    with pytest.raises(ChartDataError, match="duplicate timestamp"):
        normalize_candles([row, dict(row)], ticker="SPY")


def test_normalize_candles_rejects_empty_input():
    with pytest.raises(ChartDataError, match="no candle rows"):
        normalize_candles([], ticker="SPY")


def test_normalize_candles_rejects_non_mapping_rows():
    with pytest.raises(ChartDataError, match="row must be a mapping"):
        normalize_candles([("2026-08-01", 1, 2, 0, 1)], ticker="SPY")


def test_normalize_candles_rejects_invalid_as_of():
    row = {"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": 1}
    with pytest.raises(ChartDataError, match="invalid as_of"):
        normalize_candles([row], ticker="SPY", as_of="not-a-date")


def test_normalize_candles_rejects_bad_ohlc_order():
    row = {"timestamp": "2026-08-01", "open": 3, "high": 2, "low": 0, "close": 1}
    with pytest.raises(ChartDataError, match="OHLC invariant"):
        normalize_candles([row], ticker="SPY")


def test_normalize_candles_rejects_missing_ticker():
    row = {"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": 1}
    with pytest.raises(ChartDataError, match="ticker"):
        normalize_candles([row], ticker=" ")


def test_normalize_candles_accepts_date_like_timestamp_and_none_volume():
    row = {"timestamp": datetime(2026, 8, 1), "open": 1, "high": 2, "low": 0, "close": 1, "volume": None}
    payload = normalize_candles([row], ticker="SPY")
    assert payload.observations[0].volume is None
    assert payload.observations[0].timestamp == datetime(2026, 8, 1)


def test_normalize_candles_rejects_invalid_interval():
    row = {"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": 1}
    with pytest.raises(ChartDataError, match="interval"):
        normalize_candles([row], ticker="SPY", interval="5m")


def test_quality_metadata_is_immutable_enough_for_payload_consumers():
    row = {"timestamp": "2026-08-01", "open": 1, "high": 2, "low": 0, "close": 1}
    payload = normalize_candles([row], ticker="SPY")
    assert payload.quality.missing_fields == ()
    assert payload.quality.warnings == ()
    assert payload.as_of is None
    assert payload.observations[0].volume is None


# Keep the import-level contract explicit for downstream tasks.
def test_public_contract_exports_expected_types():
    expected_names = {"CandleRecord", "CandlePayload", "ChartDataError", "normalize_candles"}

    assert expected_names.issubset(set(chart_data.__all__))
    assert all(hasattr(chart_data, name) for name in expected_names)