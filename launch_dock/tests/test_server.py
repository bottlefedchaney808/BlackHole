import json
from launch_dock.server import create_server


def test_copy_and_seed_round_trip(tmp_path):
    httpd = create_server(tmp_path)
    httpd.registry["cards"].append({
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "15m",
        "leverage": 6.0, "live": True, "subaccount": 0, "pnl_since": "T",
        "base_capital": 10.0, "pid": None, "argv": [], "running_saved_at": None,
        "state": "stopped",
    })
    status, body = httpd.handle("POST", "/api/copy", {"source_id": "perp-btc", "new_id": "perp-xrp", "instrument": "XRP-PERP"})
    assert status == 200
    assert body["card"]["leverage"] == 2.0
    assert body["card"]["live"] is False
    status, body = httpd.handle("POST", "/api/seed", {"hours": 4.0, "cap_dollars": 80.0})
    assert status == 200
    assert httpd.registry["seeds"]["perp"]["cap_dollars"] == 80.0
    status, body = httpd.handle("POST", "/api/launch", {"id": "perp-xrp"})
    assert status == 200
    assert "--leverage" in body["argv"] and "2.0" in body["argv"]
    assert "--live" not in body["argv"]
    assert body["spawned"] is False


def test_injected_popen_detaches(tmp_path):
    class Proc:
        pid = 5

    seen = {}

    def popen(argv, **kw):
        seen["cwd"] = kw["cwd"]
        seen["flags"] = kw["creationflags"]
        assert kw["stdin"] is not None
        return Proc()

    httpd = create_server(tmp_path, popen=popen)
    httpd.registry["seeds"]["perp"]["cap_dollars"] = 80.0
    httpd.registry["cards"].append({
        "id": "perp-xrp", "seed": "perp", "instrument": "XRP-PERP", "interval": "15m",
        "leverage": 2.0, "live": False, "subaccount": 0, "pnl_since": "",
        "base_capital": None, "pid": None, "argv": [], "running_saved_at": None,
        "state": "stopped",
    })
    status, body = httpd.handle("POST", "/api/launch", {"id": "perp-xrp"})
    assert status == 200
    assert body["spawned"] is True
    assert body["card"]["pid"] == 5
    assert body["card"]["state"] == "launching"
    assert seen["flags"] == 0x00000200 | 0x00000008 | 0x08000000
    assert seen["cwd"] == r"E:/BlackHole_Investments/BlackHole"
