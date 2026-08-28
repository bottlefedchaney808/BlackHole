from datetime import datetime

from chart_app.bar_cache import BarCache
from shared.chart_data import CandleRecord


def _bar(ts, c):
    return CandleRecord(
        timestamp=datetime.fromisoformat(ts), open=c, high=c, low=c, close=c, volume=1
    )


def test_upsert_and_load_sorted(tmp_path):
    cache = BarCache(tmp_path / "bars.db")
    n = cache.upsert(
        "SPY", "1d", [_bar("2026-08-18T00:00:00", 2), _bar("2026-08-17T00:00:00", 1)]
    )
    assert n == 2
    loaded = cache.load("SPY", "1d")
    assert [b.close for b in loaded] == [1, 2]
    assert cache.last_ts("SPY", "1d") == datetime(2026, 8, 18)


def test_upsert_is_idempotent(tmp_path):
    cache = BarCache(tmp_path / "bars.db")
    cache.upsert("SPY", "1d", [_bar("2026-08-18T00:00:00", 1)])
    cache.upsert("SPY", "1d", [_bar("2026-08-18T00:00:00", 9)])
    loaded = cache.load("SPY", "1d")
    assert len(loaded) == 1
    assert loaded[0].close == 9


def test_session_defaults_to_none(tmp_path):
    cache = BarCache(tmp_path / "bars.db")
    assert cache.get_session() is None


def test_session_round_trips_and_overwrites(tmp_path):
    cache = BarCache(tmp_path / "bars.db")
    cache.set_session("SPY", "15m")
    assert cache.get_session() == ("SPY", "15m")
    cache.set_session("QQQ", "1d")
    assert cache.get_session() == ("QQQ", "1d")
