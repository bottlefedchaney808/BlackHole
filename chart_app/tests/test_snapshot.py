from datetime import datetime

from chart_app.bar_cache import BarCache
from chart_app.snapshot import build_state
from shared.chart_data import CandleRecord


def test_build_state_empty(tmp_path):
    st = build_state(BarCache(tmp_path / "b.db"), "SPY", "1d")
    assert st["bars"] == []
    assert st["live"]["conviction"] == "NONE"
    assert st["live"]["score"] == 0
    assert st["live"]["signals"] == {
        "whale": False, "wave3": False, "squeeze": False, "trend": False, "liquidity": False,
    }
    assert st["signals"] == []
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
    assert len(st["signals"]) == n
    assert all(len(st["overlays"][k]) == n for k in st["overlays"])

def test_build_state_stamps_whale_from_one_flow_fn(tmp_path):
    cache = BarCache(tmp_path / "b.db")
    recs = [
        CandleRecord(datetime(2026, 8, 18, 9, 30), 1, 1, 1, 1, 1),
        CandleRecord(datetime(2026, 8, 18, 9, 45), 1, 1, 1, 1, 1),
    ]
    cache.upsert("SPY", "15m", recs)
    calls = []

    def flow_fn(root, start, end, min_premium):
        calls.append((root, start, end, min_premium))
        return [{"premium": 30_000, "timestamp": "2026-08-18T09:40:00"}]

    cache_box = {}
    st = build_state(cache, "SPY", "15m", flow_fn=flow_fn, flow_cache=cache_box)
    assert len(calls) == 1
    assert st["signals"][0]["whale"] is False
    assert st["signals"][1]["whale"] is True
    build_state(cache, "SPY", "15m", flow_fn=flow_fn, flow_cache=cache_box)
    assert len(calls) == 1

