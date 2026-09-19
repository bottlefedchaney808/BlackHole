"""Per-symbol / per-timeframe parameter sets.

The point of these is not convenience. Four call sites build an ElmoResult and
the LIVE one took no overrides, so a tuned knob never reached the chart. These
tests pin that a saved profile does reach it, and that a profile always says
how it was validated -- a slider drag is an in-sample fit and must read as one.
"""

from __future__ import annotations

import json

import pytest

from chart_app import profiles


@pytest.fixture()
def store(tmp_path):
    return tmp_path / "profiles.json"


def test_resolution_goes_specific_to_general(store):
    profiles.save(None, "15m", elmo={"liq_window": 7}, path=store)
    profiles.save("SPY", "15m", elmo={"liq_window": 3}, path=store)

    # The symbol's own profile wins where it exists...
    spy = profiles.resolve("SPY", "15m", path=store)
    assert spy["elmo"]["liq_window"] == 3
    assert spy["source"] == "SPY|15m"

    # ...and a symbol with no profile of its own still gets the timeframe's,
    # which is the structurally-justified one: windows are counted in bars, so
    # a horizon is timeframe-specific whatever the symbol.
    qqq = profiles.resolve("QQQ", "15m", path=store)
    assert qqq["elmo"]["liq_window"] == 7
    assert qqq["source"] == "*|15m"


def test_a_miss_returns_defaults_not_none(store):
    """Callers must not need a branch -- the chart draws either way."""
    got = profiles.resolve("NVDA", "4h", path=store)
    assert got["source"] == "defaults"
    assert got["config"] == {} and got["elmo"] == {}
    assert got["validation"] == "none"


def test_every_profile_carries_how_it_was_validated(store):
    """Per-symbol tuning measured +11.5pp in sample and +0.55pp out of it. A
    profile that cannot say which kind of number it is is a trap."""
    rec = profiles.save("SPY", "1d", elmo={"liq_window": 5}, path=store)
    assert rec["validation"] == "in_sample"   # the honest default
    assert rec["saved_at"]

    with pytest.raises(ValueError):
        profiles.save("SPY", "1d", validation="backtested_trust_me", path=store)

    strong = profiles.save("SPY", "1d", validation="walk_forward", path=store)
    assert strong["validation"] == "walk_forward"


def test_a_corrupt_store_reads_as_empty_rather_than_breaking_the_chart(store):
    store.write_text("{not json", encoding="utf-8")
    assert profiles.load_all(store) == {}
    assert profiles.resolve("SPY", "15m", path=store)["source"] == "defaults"


def test_delete_round_trip(store):
    profiles.save("SPY", "15m", elmo={"liq_window": 3}, path=store)
    assert profiles.delete("SPY", "15m", path=store) is True
    assert profiles.delete("SPY", "15m", path=store) is False
    assert profiles.resolve("SPY", "15m", path=store)["source"] == "defaults"


def test_ticker_case_does_not_create_a_second_profile(store):
    profiles.save("spy", "15m", elmo={"liq_window": 3}, path=store)
    profiles.save("SPY", "15m", elmo={"liq_window": 4}, path=store)
    assert len(json.loads(store.read_text(encoding="utf-8"))) == 1
    assert profiles.resolve("SpY", "15m", path=store)["elmo"]["liq_window"] == 4
