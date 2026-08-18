import pytest

from Direction import replay


def test_replay_returns_one_entry_per_date():
    def fake_generate(ticker, as_of=None):
        return {"conviction": "HIGH", "score": 4, "signals": {"whale": True}}

    out = replay.replay_direction("SPY", ["2026-08-12", "2026-08-13"], generate_fn=fake_generate)
    assert out == [
        {"date": "2026-08-12", "conviction": "HIGH", "score": 4, "signals": {"whale": True}},
        {"date": "2026-08-13", "conviction": "HIGH", "score": 4, "signals": {"whale": True}},
    ]


def test_replay_degrades_on_per_date_failure():
    def fake_generate(ticker, as_of=None):
        raise RuntimeError("network down")

    out = replay.replay_direction("SPY", ["2026-08-14"], generate_fn=fake_generate)
    assert out == [{"date": "2026-08-14", "conviction": "NONE", "score": 0, "signals": {}}]


def test_replay_defaults_to_real_generate(monkeypatch):
    from Direction import signal_generator
    calls = []
    monkeypatch.setattr(signal_generator, "generate",
                        lambda ticker, as_of=None: calls.append(as_of) or
                        {"conviction": "NONE", "score": 0, "signals": {}})
    replay.replay_direction("SPY", ["2026-08-12"])
    assert calls == ["2026-08-12"]
