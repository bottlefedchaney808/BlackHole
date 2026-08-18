from datetime import datetime

from Direction import replay

SAME_DAY_TS = [
    "2026-08-12T09:30:00",
    "2026-08-12T09:45:00",
    "2026-08-12T10:00:00",
    "2026-08-12T10:15:00",
    "2026-08-12T10:30:00",
]


def test_replay_calls_generate_once_per_bar_timestamp():
    calls = []

    def fake_generate(ticker, ts=None):
        calls.append(ts)
        return {}

    out = replay.replay_direction("SPY", SAME_DAY_TS, generate_fn=fake_generate)
    assert calls == SAME_DAY_TS  # 5 same-day timestamps -> 5 calls, not 1
    assert len(out) == 5
    assert [entry["ts"] for entry in out] == SAME_DAY_TS


def test_replay_entries_carry_ts_not_date():
    def fake_generate(ticker, ts=None):
        return {"whale": True, "wave3": True, "squeeze": True}

    out = replay.replay_direction(
        "SPY",
        [datetime(2026, 8, 12, 9, 30), "2026-08-12T09:45:00"],
        generate_fn=fake_generate,
    )
    assert "date" not in out[0]
    assert set(out[0]) == {"ts", "conviction", "score", "signals"}
    assert out[0]["ts"] == "2026-08-12T09:30:00"
    assert out[0]["score"] == 3
    assert out[0]["conviction"] == "HIGH"
    assert out[0]["signals"] == {
        "whale": True,
        "wave3": True,
        "squeeze": True,
        "trend": False,
        "liquidity": False,
    }
    assert out[1]["ts"] == "2026-08-12T09:45:00"


def test_replay_isolates_per_bar_failure():
    def fake_generate(ticker, ts=None):
        if ts == "2026-08-12T10:00:00":
            raise RuntimeError("network down")
        return {"whale": True, "wave3": True, "squeeze": True}

    out = replay.replay_direction("SPY", SAME_DAY_TS, generate_fn=fake_generate)
    assert out[0] == {
        "ts": "2026-08-12T09:30:00",
        "conviction": "HIGH",
        "score": 3,
        "signals": {
            "whale": True,
            "wave3": True,
            "squeeze": True,
            "trend": False,
            "liquidity": False,
        },
    }
    assert out[2] == {
        "ts": "2026-08-12T10:00:00",
        "conviction": "NONE",
        "score": 0,
        "signals": {},
    }
    assert out[4]["conviction"] == "HIGH" and out[4]["score"] == 3


def test_replay_default_generate_is_v2_indicator_path(monkeypatch):
    from Direction import indicator

    calls = []

    def fake_compose(ticker, bar_ts, *, state):
        calls.append(bar_ts)
        return {"whale": True, "wave3": True, "squeeze": True}

    monkeypatch.setattr(indicator, "_compose_signals", fake_compose)
    out = replay.replay_direction("SPY", SAME_DAY_TS[:2])
    assert calls == SAME_DAY_TS[:2]  # default path runs per bar, wired to v2 compose
    assert out[0]["conviction"] == "HIGH"
    assert out[0]["score"] == 3
