import pytest
from launch_dock.stop import stop_card


def _card(**over):
    card = {
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP",
        "live": True, "pid": 42, "state": "running",
    }
    card.update(over)
    return card


def test_stop_cancels_that_ticker_then_kills():
    calls = []

    def cancel(ticker):
        calls.append(("cancel", ticker))
        return 1

    def kill(pid):
        calls.append(("kill", pid))

    out = stop_card(
        _card(),
        cmdline="python -m chart_app.run_live_perp --ticker BTC-PERP --live",
        cancel=cancel, kill=kill,
    )
    assert calls == [("cancel", "KXBTCPERP"), ("kill", 42)]
    assert out["state"] == "stopped"
    assert out["pid"] is None


def test_cancel_failure_does_not_kill():
    killed = []

    def cancel(ticker):
        raise RuntimeError("exchange down")

    out = stop_card(
        _card(),
        cmdline="python -m chart_app.run_live_perp --ticker BTC-PERP",
        cancel=cancel, kill=lambda pid: killed.append(pid),
    )
    assert killed == []
    assert out["state"] == "running"
    assert "exchange down" in out["error"]


def test_dead_live_pid_still_cancels_and_does_not_kill():
    calls = []
    out = stop_card(
        _card(),
        cmdline=None,
        cancel=lambda ticker: calls.append(ticker) or 0,
        kill=lambda pid: calls.append("kill"),
    )
    assert calls == ["KXBTCPERP"]
    assert out["state"] == "stopped"


def test_wrong_command_line_is_unknown():
    calls = []
    out = stop_card(
        _card(),
        cmdline="python something_else.py",
        cancel=lambda ticker: calls.append(ticker),
        kill=lambda pid: calls.append("kill"),
    )
    assert calls == []
    assert out["state"] == "unknown"


def test_event_desk_stop_does_not_cancel():
    calls = []
    out = stop_card(
        {"id": "desk", "seed": "event_desk", "instrument": "", "live": False, "pid": 7, "state": "running"},
        cmdline="py -3.11 event_desk/churn.py watch",
        cancel=lambda ticker: calls.append(ticker),
        kill=lambda pid: calls.append(pid),
    )
    assert calls == [7]
    assert out["state"] == "stopped"
