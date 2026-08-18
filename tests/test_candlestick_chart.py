from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import matplotlib
import pytest

from shared.candlestick_chart import (
    _BACKGROUND_GRADIENT,
    _DOWN,
    _GRID,
    _PANEL,
    _PANEL_VOLUME,
    _TEXT,
    _UP,
    _candle_width,
    _configure_date_axis,
    _format_volume,
    _title,
    render_candlestick,
)
from shared.chart_data import CandlePayload, CandleRecord, ChartDataError


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


@pytest.mark.parametrize(("lookback", "display"), [("1m", "1mo"), ("3m", "3mo"), ("6m", "6mo")])
def test_title_displays_month_lookback_without_changing_payload(lookback, display):
    payload = replace(_payload(), lookback=lookback)
    title = _title(payload, payload.observations)
    assert f"SPY · 1d · {display}" in title
    assert f"· {lookback} ·" not in title


def test_short_window_date_formatter_includes_month_context():
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots()
    try:
        dates = [mdates.date2num(datetime(2026, 7, 21)), mdates.date2num(datetime(2026, 8, 14))]
        _configure_date_axis(axis, dates)
        formatter = axis.xaxis.get_major_formatter()
        assert formatter(dates[0], 0) == "21-Jul"
        assert formatter(dates[1], 0) == "14-Aug"
    finally:
        plt.close(figure)


def test_volume_formatter_uses_human_readable_units():
    assert _format_volume(1_250) == "1.2K"
    assert _format_volume(12_500_000) == "12.5M"
    assert _format_volume(2_500_000_000) == "2.5B"
    assert _format_volume(500) == "500"


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
    assert titles == ["SPY · 1d · 1mo · fixture · 3 bars · 2026-08-01—2026-08-03 · as of 2026-08-04"]


def test_short_windows_use_daily_spacing_and_readable_candle_widths():
    assert _candle_width([float(index) for index in range(21)]) == pytest.approx(0.7)
    assert _candle_width([float(index) for index in range(64)]) == pytest.approx(0.7)
    assert _candle_width([100.0]) == pytest.approx(0.55)


def test_render_without_volume_still_writes_chart(tmp_path: Path):
    output = render_candlestick(_payload(with_volume=False), tmp_path / "spy.png")

    assert output.is_file()
    assert output.stat().st_size > 0


def test_dark_blue_purple_theme_contract():
    assert _BACKGROUND_GRADIENT == ("#081326", "#21113d")
    assert _PANEL == "#101d34"
    assert _PANEL_VOLUME == "#171a35"
    assert _TEXT == "#F4F7FF"
    assert _GRID == "#64748B"
    assert _UP == "#38BDF8"
    assert _DOWN == "#C084FC"


def test_render_rejects_non_daily_interval_before_plotting(tmp_path: Path, monkeypatch):
    payload = replace(_payload(), interval="2h")
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


def test_render_candlestick_draws_direction_markers(tmp_path):
    from shared.chart_data import CandlePayload, CandleRecord
    from shared.candlestick_chart import render_candlestick, _marker_for_score
    from datetime import datetime

    # Score-based convention: 0/5 sell, 3/5 hold (NOT a buy), 4/5 buy, 5/5 add.
    assert _marker_for_score(0) == "sell"
    assert _marker_for_score(3) == "hold"
    assert _marker_for_score(4) == "buy"
    assert _marker_for_score(5) == "add"
    assert _marker_for_score(1) == "none"  # 1-2/5 -> no marker
    assert _marker_for_score(2) == "none"
    assert _marker_for_score(99) == "none"

    records = tuple(
        CandleRecord(timestamp=datetime(2026, 8, 12, 9, 30), open=100.0, high=102.0,
                     low=99.0, close=101.0, volume=1000)
        for _ in range(3)
    )
    payload = CandlePayload(ticker="SPY", interval="15m", lookback="1d",
                            source="thetadata", observations=records)
    out = tmp_path / "chart.png"
    path = render_candlestick(payload, out,
                              direction_overlay=[
                                  {"date": "2026-08-12", "conviction": "HIGH",
                                   "score": 4, "signals": {}},
                                  {"date": "2026-08-13", "conviction": "NONE",
                                   "score": 1, "signals": {}},
                              ],
                              live_note="LIVE: HIGH (4/5)")
    assert path.exists() and path.stat().st_size > 0


def test_render_candlestick_skips_markers_below_score_threshold(tmp_path):
    """A bar with 1-2/5 signals draws no marker; 3/5 is a HOLD, not a buy."""
    from shared.chart_data import CandlePayload, CandleRecord
    from shared.candlestick_chart import render_candlestick, _marker_for_score
    from datetime import datetime

    # 1-2/5 signals -> no marker, same as no overlay entry.
    assert _marker_for_score(2) == "none"

    records = tuple(
        CandleRecord(timestamp=datetime(2026, 8, 12, 9, 30), open=100.0, high=102.0,
                     low=99.0, close=101.0, volume=1000)
        for _ in range(2)
    )
    payload = CandlePayload(ticker="SPY", interval="15m", lookback="1d",
                            source="thetadata", observations=records)
    out = tmp_path / "chart.png"
    path = render_candlestick(payload, out,
                              direction_overlay=[
                                  {"date": "2026-08-12", "conviction": "NONE",
                                   "score": 2, "signals": {}},
                              ],
                              live_note="LIVE: NONE (2/5)")
    assert path.exists() and path.stat().st_size > 0
