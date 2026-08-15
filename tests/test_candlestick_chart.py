from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import matplotlib
import pytest

from shared.chart_data import CandlePayload
from shared.chart_data import CandleRecord, ChartDataError
from shared.candlestick_chart import render_candlestick


def _payload(*, with_volume: bool = True) -> CandlePayload:
    records = (
        CandleRecord(datetime(2026, 8, 1), 100, 104, 98, 103, 1000 if with_volume else None),
        CandleRecord(datetime(2026, 8, 2), 103, 105, 101, 102, 1200 if with_volume else None),
        CandleRecord(datetime(2026, 8, 3), 102, 108, 101, 107, 1400 if with_volume else None),
    )
    return CandlePayload(
        ticker="SPY",
        interval="1d",
        lookback="1m",
        source="fixture",
        observations=records,
        as_of=datetime(2026, 8, 4),
    )


def test_render_creates_nonempty_png_and_parent_directory(tmp_path: Path):
    output = tmp_path / "nested" / "charts" / "spy.png"

    result = render_candlestick(_payload(), output)

    assert result == output
    assert output.is_file()
    assert output.stat().st_size > 0
    assert output.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_does_not_mutate_payload(tmp_path: Path):
    payload = _payload()
    before = payload

    render_candlestick(payload, tmp_path / "spy.png")

    assert payload == before
    assert payload.observations == before.observations


def test_render_includes_volume_subplot_and_metadata(tmp_path: Path, monkeypatch):
    from PIL import Image

    titles = []
    original_set_title = matplotlib.axes.Axes.set_title

    def capture_title(axis, label, *args, **kwargs):
        titles.append(label)
        return original_set_title(axis, label, *args, **kwargs)

    monkeypatch.setattr(matplotlib.axes.Axes, "set_title", capture_title)
    output = render_candlestick(_payload(), tmp_path / "spy.png")
    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.width >= 800
        assert image.height >= 500
    assert titles == [
        "SPY · 1d · lookback 1m · source fixture · as of 2026-08-04 "
        "· observations 2026-08-01 to 2026-08-03"
    ]


def test_render_without_volume_still_writes_chart(tmp_path: Path):
    output = render_candlestick(_payload(with_volume=False), tmp_path / "spy.png")

    assert output.is_file()
    assert output.stat().st_size > 0


def test_render_rejects_non_daily_interval_before_plotting(tmp_path: Path, monkeypatch):
    payload = replace(_payload(), interval="5m")
    monkeypatch.setattr(
        "shared.candlestick_chart.plt.subplots",
        lambda *args, **kwargs: pytest.fail("plotting must not start for a non-daily payload"),
    )

    with pytest.raises(ChartDataError, match="interval"):
        render_candlestick(payload, tmp_path / "intraday.png")


@pytest.mark.parametrize("payload", [None, CandlePayload("SPY", "1d", "1m", "fixture", ())])
def test_render_rejects_missing_or_empty_payload(payload, tmp_path: Path):
    with pytest.raises(ChartDataError, match="payload|observations"):
        render_candlestick(payload, tmp_path / "empty.png")


def test_render_rejects_invalid_payload_type(tmp_path: Path):
    with pytest.raises(ChartDataError, match="payload"):
        render_candlestick(object(), tmp_path / "invalid.png")


def test_render_rejects_directory_output_path(tmp_path: Path):
    with pytest.raises(ChartDataError, match="output_path"):
        render_candlestick(_payload(), tmp_path)


def test_render_rejects_malformed_timestamp_as_chart_data_error(tmp_path: Path):
    payload = CandlePayload(
        "SPY",
        "1d",
        "1m",
        "fixture",
        (CandleRecord("2026-08-01", 100, 104, 98, 103, 1000),),
    )

    with pytest.raises(ChartDataError, match="timestamp"):
        render_candlestick(payload, tmp_path / "invalid-timestamp.png")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("open", "not-a-number", "OHLC"),
        ("high", float("nan"), "OHLC"),
        ("close", 110, "OHLC"),
    ],
)
def test_render_rejects_malformed_numeric_ohlc_as_chart_data_error(
    field: str, value: object, match: str, tmp_path: Path
):
    values = {"open": 100, "high": 104, "low": 98, "close": 103}
    values[field] = value
    payload = CandlePayload(
        "SPY",
        "1d",
        "1m",
        "fixture",
        (CandleRecord(datetime(2026, 8, 1), **values),),
    )

    with pytest.raises(ChartDataError, match=match):
        render_candlestick(payload, tmp_path / f"invalid-{field}.png")


# Keep accidental imports from silently masking the intended contract.
assert CandlePayload is not None
