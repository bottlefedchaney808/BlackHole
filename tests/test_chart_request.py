from __future__ import annotations

from datetime import datetime

import pytest

from shared.chart_data import ChartDataError, normalize_candles
from shared.chart_request import (
    ChartArtifact,
    ChartRequest,
    build_spot_chart_request,
    render_spot_chart,
)


_ROWS = [
    {
        "timestamp": "2026-08-02",
        "open": 101,
        "high": 105,
        "low": 99,
        "close": 104,
        "volume": 20,
    },
    {
        "timestamp": "2026-08-01",
        "open": 98,
        "high": 102,
        "low": 97,
        "close": 101,
    },
]


def test_build_spot_chart_request_normalizes_ticker_and_defaults():
    request = build_spot_chart_request(" spy ")

    assert isinstance(request, ChartRequest)
    assert request.ticker == "SPY"
    assert request.lookback == "6m"
    assert request.interval == "1d"


@pytest.mark.parametrize("interval", ["3m", "5m", "10m", "15m", "30m", "1h", "4h"])
def test_build_spot_chart_request_intraday_defaults_to_one_day(interval):
    request = build_spot_chart_request("SPY", interval=interval)
    assert request.interval == interval
    assert request.lookback == "1d"


def test_build_spot_chart_request_rejects_invalid_ticker():
    with pytest.raises(ChartDataError, match="ticker"):
        build_spot_chart_request("   ")


@pytest.mark.parametrize("lookback", ["0d", -1, 1.5])
def test_build_spot_chart_request_rejects_invalid_lookback(lookback):
    with pytest.raises(ChartDataError, match="lookback"):
        build_spot_chart_request("SPY", lookback=lookback)


def test_unsupported_interval_is_rejected_before_provider_invocation(tmp_path):
    called = False

    def provider(ticker, lookback):
        nonlocal called
        called = True
        return _ROWS

    with pytest.raises(ChartDataError, match="interval"):
        render_spot_chart(
            "SPY",
            interval="2h",
            output_path=tmp_path / "chart.png",
            provider=provider,
        )

    assert called is False


def test_render_spot_chart_composes_provider_and_real_renderer_with_metadata(tmp_path):
    provider_calls = []

    def provider(ticker, lookback):
        provider_calls.append((ticker, lookback))
        return _ROWS

    output_path = tmp_path / "nested" / "chart.png"
    artifact = render_spot_chart(
        " spy ",
        lookback="2m",
        output_path=output_path,
        provider=provider,
    )

    assert isinstance(artifact, ChartArtifact)
    assert artifact.path == output_path.resolve()
    assert artifact.path.is_absolute()
    assert artifact.ticker == "SPY"
    assert artifact.interval == "1d"
    assert artifact.lookback == "2m"
    assert artifact.source == "injected"
    assert artifact.observation_range == (
        datetime(2026, 8, 1),
        datetime(2026, 8, 2),
    )
    assert artifact.row_count == 2
    assert artifact.warnings == ()
    assert artifact.as_of == datetime(2026, 8, 2)
    assert provider_calls == [("SPY", "2m")]
    rendered = artifact.path.read_bytes()
    assert rendered.startswith(b"\x89PNG\r\n\x1a\n")
    assert rendered


def test_render_spot_chart_copies_payload_as_of_metadata(monkeypatch, tmp_path):
    as_of = datetime(2026, 8, 3, 15)
    payload = normalize_candles(
        _ROWS,
        ticker="SPY",
        lookback="2m",
        source="fixture",
        as_of=as_of,
    )

    def fake_fetch(ticker, *, lookback, provider=None):
        assert ticker == "SPY"
        assert lookback == "2m"
        assert provider is None
        return payload

    monkeypatch.setattr("shared.chart_request.fetch_daily_candles", fake_fetch)
    output_path = tmp_path / "as-of.png"
    artifact = render_spot_chart("SPY", lookback="2m", output_path=output_path)

    assert artifact.as_of == as_of
    assert artifact.source == "fixture"
    assert artifact.observation_range == (datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert artifact.lookback == "2m"
    assert artifact.path.is_absolute()
    assert artifact.path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_render_spot_chart_rejects_invalid_ticker_before_provider(tmp_path):
    called = False

    def provider(ticker, lookback):
        nonlocal called
        called = True
        return _ROWS

    with pytest.raises(ChartDataError, match="ticker"):
        render_spot_chart("", output_path=tmp_path / "chart.png", provider=provider)

    assert called is False


def test_render_spot_chart_rejects_unsafe_ticker_before_provider(tmp_path):
    called = False

    def provider(ticker, lookback):
        nonlocal called
        called = True
        return _ROWS

    with pytest.raises(ChartDataError, match="ticker"):
        render_spot_chart("BRK.B?x=1", output_path=tmp_path / "chart.png", provider=provider)

    assert called is False


def test_render_spot_chart_accepts_direction_overlay(tmp_path, monkeypatch):
    from shared.chart_data import CandlePayload, CandleRecord
    from shared import chart_request as cr
    from datetime import datetime

    records = (CandleRecord(timestamp=datetime(2026, 8, 12, 9, 30), open=100.0,
                            high=102.0, low=99.0, close=101.0, volume=1000),)
    payload = CandlePayload(ticker="SPY", interval="1d", lookback="6m",
                            source="thetadata", observations=records)

    def fake_provider(ticker, lookback=None, provider=None):
        return payload

    monkeypatch.setattr(cr, "fetch_daily_candles", fake_provider)
    out = tmp_path / "chart.png"
    art = cr.render_spot_chart("SPY", interval="1d", output_path=out,
                               direction_overlay=[
                                   {"date": "2026-08-12", "conviction": "HIGH",
                                    "score": 4, "signals": {}},
                               ],
                               live_note="LIVE: HIGH (4/5)")
    assert art.path.exists()
