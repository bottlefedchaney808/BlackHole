"""The state cache, its invalidation, and the conditional poll.

Why these exist
---------------
`GET /api/state` rebuilds entropy, ELMo and the conviction series over every
cached bar. Measured live 2026-09-20 on BTC-PERP 15m (35,168 bars) that is
3.9s of recompute for a 32MB payload -- and `static/js/app.js` polled it every
5 seconds unconditionally. Six seconds of work arriving every five is an
unbounded queue, and the endpoint measured 30s end-to-end against 5.6s in
isolation: the gap WAS the backlog.

The fix is a cache keyed on (symbol, epoch, bar fingerprint) plus an ETag. That
makes correctness entirely a question of invalidation, so every way the frame
can change without a bar arriving gets a test here. A missed bump is not a slow
chart -- it is a chart that silently keeps drawing stale parameters, which is
strictly worse than the problem being fixed.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from chart_app.bar_cache import BarCache
from chart_app.server import create_app
from shared.chart_data import CandleRecord


def _bars(n: int, start: datetime | None = None) -> list[CandleRecord]:
    base = start or datetime(2026, 9, 1, 9, 30)
    return [
        CandleRecord(
            timestamp=base + timedelta(minutes=15 * i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0 + i,
        )
        for i in range(n)
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Never touch the real profile store. The live one WAS overwritten once by
    # a test that did (2026-09-20) -- that is what this fixture is for.
    monkeypatch.setenv("CHART_APP_PROFILES", str(tmp_path / "profiles.json"))
    cache = BarCache(tmp_path / "b.db")
    cache.upsert("SPY", "15m", _bars(80))
    return TestClient(create_app(cache)), cache


# --------------------------------------------------------------- fingerprint


def test_fingerprint_is_stable_and_moves_with_the_bars(tmp_path):
    cache = BarCache(tmp_path / "b.db")
    cache.upsert("SPY", "15m", _bars(10))
    first = cache.fingerprint("SPY", "15m")
    assert first == cache.fingerprint("SPY", "15m")  # pure read, no drift
    assert first[0] == 10

    # A NEW bar moves it.
    cache.upsert("SPY", "15m", _bars(1, datetime(2026, 9, 2, 9, 30)))
    assert cache.fingerprint("SPY", "15m") != first

    # So does a backfill landing BEHIND the newest bar, which cannot move the
    # max timestamp -- this is why the count is in the key too.
    before = cache.fingerprint("SPY", "15m")
    cache.upsert("SPY", "15m", _bars(1, datetime(2026, 8, 1, 9, 30)))
    assert cache.fingerprint("SPY", "15m") != before

    # Per (ticker, interval), not global.
    assert cache.fingerprint("QQQ", "15m") == (0, None)


def test_fingerprint_of_an_empty_cache(tmp_path):
    assert BarCache(tmp_path / "b.db").fingerprint("SPY", "15m") == (0, None)


# ------------------------------------------------------------------ the cache


def test_repeat_call_serves_the_same_bytes(client):
    c, _ = client
    a = c.get("/api/state")
    b = c.get("/api/state")
    assert a.status_code == b.status_code == 200
    assert a.headers["ETag"] == b.headers["ETag"]
    assert a.content == b.content


def test_if_none_match_answers_304_with_no_body(client):
    c, _ = client
    first = c.get("/api/state")
    etag = first.headers["ETag"]
    again = c.get("/api/state", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""
    # The 32MB payload is the thing being avoided; assert the saving is real.
    assert len(first.content) > len(again.content)


def test_a_stale_etag_gets_the_full_frame(client):
    c, _ = client
    r = c.get("/api/state", headers={"If-None-Match": '"not-the-current-one"'})
    assert r.status_code == 200
    assert r.json()["ticker"] == "SPY"


# ------------------------------------------------------------- invalidation


def test_a_new_bar_invalidates(client):
    c, cache = client
    before = c.get("/api/state").headers["ETag"]
    cache.upsert("SPY", "15m", _bars(1, datetime(2026, 9, 3, 9, 30)))
    after = c.get("/api/state")
    assert after.headers["ETag"] != before
    assert after.status_code == 200
    assert len(after.json()["bars"]) == 81


def test_a_symbol_switch_invalidates(client):
    c, cache = client
    cache.upsert("QQQ", "1d", _bars(80))
    before = c.get("/api/state").headers["ETag"]
    c.post("/api/symbol", json={"ticker": "QQQ", "interval": "1d"})
    after = c.get("/api/state")
    assert after.headers["ETag"] != before
    assert after.json()["ticker"] == "QQQ"


def test_an_rh_position_invalidates(client):
    c, _ = client
    before = c.get("/api/state").headers["ETag"]
    c.post("/api/rh", json={"position": {"qty": 10, "avg_price": 400.0}, "fills": []})
    after = c.get("/api/state")
    assert after.headers["ETag"] != before
    assert after.json()["rh"]["position"]["qty"] == 10


def test_saving_a_profile_invalidates(client):
    """The one that matters most.

    A saved profile feeds the LIVE chart through `snapshot.build_state`, so
    without this bump "takes effect at the next poll" would quietly have become
    "takes effect at the next NEW BAR" -- on a 1d chart, tomorrow.
    """
    c, _ = client
    before = c.get("/api/state")
    assert before.json()["profile"]["source"] == "defaults"

    saved = c.post("/api/profiles", json={"elmo": {"alma_window": 21}, "config": {}})
    assert saved.json()["ok"] is True

    after = c.get("/api/state")
    assert after.headers["ETag"] != before.headers["ETag"]
    assert after.json()["profile"]["source"] == "SPY|15m"
    assert after.json()["profile"]["elmo"]["alma_window"] == 21


def test_clearing_a_profile_invalidates(client):
    c, _ = client
    c.post("/api/profiles", json={"elmo": {"alma_window": 21}, "config": {}})
    before = c.get("/api/state").headers["ETag"]
    assert c.request("DELETE", "/api/profiles").json()["removed"] is True
    after = c.get("/api/state")
    assert after.headers["ETag"] != before
    assert after.json()["profile"]["source"] == "defaults"


# ------------------------------------------------------------------- version


def test_version_endpoint_reports_without_building(client):
    c, cache = client
    v = c.get("/api/state/version").json()
    assert v["ok"] is True
    assert v["ticker"] == "SPY"
    assert v["bars"] == 80
    assert v["last_ts"] is not None

    # Its etag matches the built frame's once one exists...
    etag = c.get("/api/state").headers["ETag"]
    assert c.get("/api/state/version").json()["etag"] == etag
    # ...and goes empty the moment the frame is stale, rather than reporting a
    # digest for bars that have moved on.
    cache.upsert("SPY", "15m", _bars(1, datetime(2026, 9, 4, 9, 30)))
    assert c.get("/api/state/version").json()["etag"] == ""


# ------------------------------------------------------- payload equivalence


def test_the_cached_frame_is_the_same_payload_as_before(client):
    """Caching must not change WHAT is served, only how often it is built."""
    c, _ = client
    fresh = c.get("/api/state").json()
    cached = c.get("/api/state").json()
    assert fresh == cached
    for key in (
        "ticker",
        "interval",
        "bars",
        "scores",
        "elmo",
        "algo",
        "live",
        "overlays",
        "oscillators",
        "whale",
        "profile",
        "ok",
    ):
        assert key in fresh, key
    assert fresh["ok"] is True


# ---------------------------------------------------------- asset stamping


def test_root_stamps_assets_with_their_mtime(client):
    """A JS edit should need a RELOAD, not a server restart."""
    c, _ = client
    body = c.get("/").text
    assert "__V__" not in body  # every placeholder resolved
    assert "/static/js/app.js?v=" in body
    assert "/static/css/app.css?v=" in body
    stamp = body.split("/static/js/app.js?v=")[1].split('"')[0]
    assert stamp.isdigit() and int(stamp) > 0


# ------------------------------------------------- no duplicate ELMo work


def test_passing_elmo_in_is_equivalent_to_recomputing_it():
    """`run_backtest(elmo=...)` must be a pure saving, not a behaviour change.

    `/api/backtest` computed ELMo to drive the chart's conviction line and then
    called `run_backtest`, which recomputed the identical series from the
    identical records -- ~2.3s of the ~5.7s a slider drag cost on a 35k-bar
    chart. Handing the object over is only safe if it scores the same, so pin
    that rather than trusting the argument.
    """
    from chart_app.backtest import run_backtest
    from chart_app.elmo import compute_elmo

    records = _bars(400)
    overrides = {"alma_window": 11}

    recomputed = run_backtest(records, interval="15m", elmo_overrides=overrides)
    handed_over = run_backtest(
        records,
        interval="15m",
        elmo_overrides=overrides,
        elmo=compute_elmo(records, **overrides),
    )
    assert recomputed.metrics == handed_over.metrics
    assert recomputed.trades == handed_over.trades
    assert recomputed.equity == handed_over.equity


def test_elmo_object_wins_over_overrides_when_both_are_given():
    """The object is the thing actually scored, so it must be the one used."""
    from chart_app.backtest import run_backtest
    from chart_app.elmo import compute_elmo

    records = _bars(400)
    # Deliberately contradictory: overrides say 5, the object says 30.
    result = run_backtest(
        records,
        interval="15m",
        elmo_overrides={"alma_window": 5},
        elmo=compute_elmo(records, alma_window=30),
    )
    expected = run_backtest(records, interval="15m", elmo_overrides={"alma_window": 30})
    assert result.metrics == expected.metrics
