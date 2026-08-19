"""Tests for the scripts/render_direction_chart.py CLI (v2 per-bar overlay).

The CLI's only interesting logic is its wiring: feed EVERY bar timestamp
(v2) to ``replay_direction`` instead of deduped dates (v1), pass the replay
out + a live note derived from the LAST bar's v2 evaluation into
``render_spot_chart``, and print the per-bar score series.  Everything else
is delegated to already-tested modules, so these tests monkeypatch the three
delegates and assert on the call shapes + stdout -- no network.
"""

import importlib.util
from datetime import datetime
from pathlib import Path

from shared.chart_data import CandlePayload, CandleRecord
from shared.chart_request import ChartArtifact

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_direction_chart.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("render_direction_chart_cli", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(*timestamps):
    records = tuple(
        CandleRecord(timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.5,
                     volume=1_000_000)
        for ts in timestamps
    )
    return CandlePayload(ticker="SPY", interval="15m", lookback="5d",
                         source="thetadata", observations=records)


def _artifact(row_count):
    return ChartArtifact(path=Path("artifacts/SPY_5d_15m_direction.png"),
                         ticker="SPY", interval="15m", lookback="5d",
                         source="thetadata",
                         observation_range=(datetime(2026, 8, 17, 9, 30),
                                            datetime(2026, 8, 17, 10, 0)),
                         as_of=None, row_count=row_count)


def test_intraday_feeds_every_bar_timestamp_and_prints_score_series(monkeypatch, capsys):
    """v2 wiring: three bars on ONE day -> three replay evaluations (v1
    would have deduped to a single date) and the score series varies."""
    from shared import spot_history
    from Direction import replay as replay_mod

    bar_times = (datetime(2026, 8, 17, 9, 30), datetime(2026, 8, 17, 9, 45),
                 datetime(2026, 8, 17, 10, 0))
    captured = {}

    monkeypatch.setattr(spot_history, "fetch_intraday_candles",
                        lambda ticker, *, interval, lookback: _payload(*bar_times))

    def fake_replay(ticker, bar_ts, generate_fn=None):
        captured["bar_ts"] = list(bar_ts)
        return [
            {"ts": bar_ts[0], "conviction": "NONE", "score": 2, "signals": {}},
            {"ts": bar_ts[1], "conviction": "MEDIUM", "score": 3, "signals": {}},
            {"ts": bar_ts[2], "conviction": "HIGH", "score": 5,
             "signals": {"whale": True, "wave3": True, "squeeze": True,
                         "trend": True, "liquidity": True}},
        ]

    monkeypatch.setattr(replay_mod, "replay_direction", fake_replay)

    cli = _load_cli()
    rendered = {}

    def fake_render(ticker, *, interval, lookback, output_path,
                    direction_overlay=None, live_note=None):
        rendered["overlay"] = direction_overlay
        rendered["live_note"] = live_note
        return _artifact(row_count=3)

    monkeypatch.setattr(cli, "render_spot_chart", fake_render)

    assert cli.main(["SPY", "--interval", "15m", "--lookback", "5d"]) == 0

    # Every bar timestamp, ascending -- not one deduped date.
    assert captured["bar_ts"] == [ts.isoformat() for ts in bar_times]
    assert len(captured["bar_ts"]) == 3
    # Overlay passes straight through; live note comes from the LAST bar's
    # v2 evaluation (matches the newest marker).
    assert rendered["overlay"] is not None and len(rendered["overlay"]) == 3
    assert rendered["live_note"] == "LIVE: HIGH (5/5)"

    out = capsys.readouterr().out
    assert "scores: [2, 3, 5]" in out
    assert "SPY_5d_15m_direction.png | 3 bars" in out
    assert "2026-08-17T09:30:00 -> 2026-08-17T10:00:00" in out


def test_custom_out_path_is_forwarded(monkeypatch, capsys):
    from shared import spot_history
    from Direction import replay as replay_mod

    bar_time = datetime(2026, 8, 17, 10, 0)

    monkeypatch.setattr(spot_history, "fetch_intraday_candles",
                        lambda ticker, *, interval, lookback: _payload(bar_time))
    monkeypatch.setattr(replay_mod, "replay_direction",
                        lambda ticker, bar_ts, generate_fn=None: [
                            {"ts": bar_ts[0], "conviction": "NONE", "score": 0,
                             "signals": {}}])

    cli = _load_cli()
    captured = {}

    def fake_render(ticker, *, interval, lookback, output_path,
                    direction_overlay=None, live_note=None):
        captured["out"] = str(output_path)
        return _artifact(row_count=1)

    monkeypatch.setattr(cli, "render_spot_chart", fake_render)

    assert cli.main(["SPY", "--interval", "15m", "--lookback", "5d",
                     "--out", "artifacts/custom.png"]) == 0
    assert captured["out"].endswith("artifacts/custom.png")
    assert "scores: [0]" in capsys.readouterr().out
