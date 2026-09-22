from launch_dock.status import mark_stale, reap


def test_perp_stale_when_own_saved_at_moves():
    card = {"seed": "perp", "state": "running", "running_saved_at": "T1", "argv": ["x"]}
    out = mark_stale(card, "T2")
    assert out["state"] == "stale"
    assert out["argv"] == ["x"]


def test_stocks_never_stale():
    card = {"seed": "stocks", "state": "running", "running_saved_at": "T1"}
    assert mark_stale(card, "T2")["state"] == "running"


def test_reap_marks_stopped_without_touching_orders():
    out = reap({"seed": "stocks", "state": "running", "pid": 3}, alive=False)
    assert out["state"] == "stopped"
    assert out["pid"] is None
