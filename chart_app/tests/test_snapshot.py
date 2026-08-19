from datetime import datetime
from shared.chart_data import CandleRecord
from chart_app.bar_cache import BarCache
from chart_app.snapshot import build_state

def test_build_state_empty(tmp_path):
    st = build_state(BarCache(tmp_path / "b.db"), "SPY", "1d")
    assert st["bars"] == []
    assert st["live"] == {"conviction": "NONE", "score": 0}
    assert st["rh"]["position"] is None

def test_build_state_lengths(tmp_path):
    cache = BarCache(tmp_path / "b.db")
    recs = [
        CandleRecord(datetime(2026, 1, 2), 100, 101, 99, 100, 1)
        for _ in range(3)
    ]
    # distinct timestamps
    recs = [CandleRecord(datetime(2026, 1, 2 + i), 100, 101, 99, 100.0 + i, 1) for i in range(3)]
    cache.upsert("SPY", "1d", recs)
    st = build_state(cache, "SPY", "1d")
    n = len(st["bars"])
    assert n == 3
    assert len(st["scores"]) == n and len(st["markers"]) == n
    assert all(len(st["overlays"][k]) == n for k in st["overlays"])
