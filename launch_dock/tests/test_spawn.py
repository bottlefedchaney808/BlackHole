from launch_dock.launch import DETACH
from launch_dock.spawn import finish_from_journal, poll, spawn


def test_spawn_detaches_and_does_not_invent_books():
    seen = {}

    class Proc:
        pid = 77

    def popen(argv, **kw):
        seen["argv"] = argv
        seen["flags"] = kw["creationflags"]
        seen["cwd"] = kw["cwd"]
        assert kw["stdin"] is not None
        return Proc()

    card = {"id": "perp-btc", "instrument": "BTC-PERP", "state": "stopped", "pnl_since": "", "base_capital": None}
    out = spawn(card, ["python", "-m", "chart_app.run_live_perp"], popen=popen, cwd=r"E:/BlackHole_Investments/BlackHole")
    assert out["pid"] == 77
    assert out["state"] == "launching"
    assert seen["flags"] == DETACH
    assert out["pnl_since"] == ""


def test_journal_session_is_stored_not_invented():
    text = '{"kind":"session","pnl_since":"2026-09-21T12:00:00+00:00","equity":80.0}\n'
    out = finish_from_journal(
        {"id": "perp-btc", "state": "launching", "pnl_since": "", "base_capital": None},
        text,
        saved_at="2026-09-21T11:00:00+00:00",
    )
    assert out["pnl_since"] == "2026-09-21T12:00:00+00:00"
    assert out["base_capital"] == 80.0
    assert out["state"] == "running"


def test_missing_session_stays_launching():
    out = finish_from_journal({"state": "launching", "pnl_since": ""}, "", saved_at=None)
    assert out["state"] == "launching"


def test_poll_reaps_and_stales_without_cancel():
    cards = [
        {"id": "scan", "seed": "stocks", "state": "running", "pid": 3, "running_saved_at": "T1"},
        {"id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "15m",
         "state": "running", "pid": 9, "running_saved_at": "T1", "argv": ["x"]},
    ]
    out = poll(cards, alive=lambda pid: pid == 9, saved_at_for=lambda card: "T2")
    assert out[0]["state"] == "stopped" and out[0]["pid"] is None
    assert out[1]["state"] == "stale" and out[1]["argv"] == ["x"]
