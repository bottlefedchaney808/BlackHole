from pathlib import Path
import json
from chart_app import profiles
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
    assert body["card"]["leverage"] == 1.0
    assert body["card"]["live"] is False
    status, body = httpd.handle("POST", "/api/seed", {"hours": 4.0, "cap_dollars": 80.0})
    assert status == 200
    assert httpd.registry["seeds"]["perp"]["cap_dollars"] == 80.0
    status, body = httpd.handle("POST", "/api/launch", {"id": "perp-xrp"})
    assert status == 400 and "no saved profile XRP-PERP|15m" in body["error"]
    profiles.save("XRP-PERP", "15m", config={}, path=httpd.profile_path)
    status, body = httpd.handle("POST", "/api/launch", {"id": "perp-xrp"})
    assert status == 200
    assert "--leverage" in body["argv"] and "1.0" in body["argv"]
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
    profiles.save("XRP-PERP", "15m", config={}, path=httpd.profile_path)
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


def test_tail_reads_only_that_cards_log(tmp_path):
    httpd = create_server(tmp_path, alive=lambda pid: pid == 42)
    httpd.registry["cards"].append({"id": "perp-btc", "seed": "perp", "pid": 42, "state": "running"})
    httpd.registry["cards"].append({"id": "perp-xrp", "seed": "perp", "pid": None, "state": "stopped"})
    logs = tmp_path / "launch_dock_logs"
    logs.mkdir()
    (logs / "perp-btc.log").write_text("".join(f"line {i}\n" for i in range(10)))
    (logs / "launch.log").write_text("shared noise\n")

    status, body = httpd.handle("GET", "/api/tail?id=perp-btc&lines=3")
    assert status == 200
    assert body["lines"] == ["line 7", "line 8", "line 9"]
    assert body["alive"] is True

    status, body = httpd.handle("GET", "/api/tail?id=perp-xrp")
    assert status == 200
    assert body["exists"] is False and body["lines"] == []

    status, _ = httpd.handle("GET", "/api/tail?id=nope")
    assert status == 400


def test_tail_drops_the_line_cut_by_the_seek(tmp_path):
    from launch_dock import server

    log = tmp_path / "big.log"
    log.write_text("x" * (server._TAIL_BYTES + 10) + "\nwhole\n")
    assert server.tail_lines(log, 5) == ["whole"]


def test_spawn_names_the_log_after_the_card(tmp_path):
    from launch_dock.spawn import spawn

    seen = {}

    class Proc:
        pid = 1

    def popen(argv, **kw):
        seen.update(kw)
        return Proc()

    spawn({"id": "perp-btc"}, ["x"], popen=popen, cwd=str(tmp_path))
    assert seen["log_name"] == "perp-btc"


def test_profile_inventory_and_make_card(tmp_path):
    httpd = create_server(tmp_path)
    profiles.save("SOL-PERP", "5m", config={"allow_short": False}, validation="walk_forward",
                  metrics={"walk_forward": {"held": {"oos_total_return_pct": 74.05}}},
                  path=httpd.profile_path)
    profiles.save("SPY", "15m", config={}, path=httpd.profile_path)
    status, body = httpd.handle("GET", "/api/profiles")
    rows = {r["key"]: r for r in body["profiles"]}
    assert rows["SOL-PERP|5m"]["oos_pct"] == 74.05 and rows["SOL-PERP|5m"]["saved_at"]
    assert rows["SOL-PERP|5m"]["carded"] and rows["SPY|15m"]["carded"]
    status, body = httpd.handle("POST", "/api/card-from-profile", {"key": "SOL-PERP|5m"})
    assert status == 200
    card_id = body["card"]["id"]
    status, body = httpd.handle("GET", "/api/profiles")
    assert {r["key"]: r for r in body["profiles"]}["SOL-PERP|5m"]["used_by"] == [card_id]
    status, body = httpd.handle("POST", "/api/card", {"id": card_id, "fields": {"cap_pct": 0.3, "live": True}})
    assert status == 200 and body["card"]["cap_pct"] == 0.3
    status, body = httpd.handle("POST", "/api/launch", {"id": card_id})
    assert status == 200 and "--cap-fraction" in body["argv"] and "KXSOLPERP" not in body["argv"]
    status, body = httpd.handle("POST", "/api/delete", {"id": card_id})
    assert status == 400  # it is "launching" now


def test_copy_rejects_bad_ticker_with_a_message(tmp_path):
    httpd = create_server(tmp_path)
    httpd.registry["cards"].append({"id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP",
                                    "interval": "5m", "state": "stopped"})
    status, body = httpd.handle("POST", "/api/copy", {"source_id": "perp-btc", "instrument": "PERP"})
    assert status == 400 and "not a Kalshi perp" in body["error"]


def test_instruments_lists_stock_tickers_from_profiles(tmp_path):
    httpd = create_server(tmp_path)
    for key in ("SPY", "QQQ", "*", "BTC-PERP", "GOLD"):
        profiles.save(key, "5m", config={}, path=httpd.profile_path)
    status, body = httpd.handle("GET", "/api/instruments")
    assert status == 200
    assert body["stocks"] == ["QQQ", "SPY"]  # no wildcard, no Kalshi names
    assert "BTC-PERP" in body["perp"]


def test_copy_stock_card_onto_another_stock(tmp_path):
    httpd = create_server(tmp_path)
    httpd.registry["cards"].append({"id": "stocks-spy", "seed": "stocks", "instrument": "SPY",
                                    "interval": "5m", "live": True, "state": "running"})
    status, body = httpd.handle("POST", "/api/copy", {"source_id": "stocks-spy", "instrument": "qqq"})
    assert status == 200
    card = body["card"]
    assert card["id"] == "stocks-qqq" and card["instrument"] == "QQQ" and card["seed"] == "stocks"
    assert card["state"] == "stopped" and card["live"] is False
    assert card["interval"] == "5m" and card["leverage"] is None
    status, body = httpd.handle("GET", "/api/cards")
    assert [c["id"] for c in body["cards"]] == ["stocks-spy", "stocks-qqq"]


def test_stock_card_from_profile_gets_walk_forward(tmp_path):
    httpd = create_server(tmp_path)
    profiles.save("FCEL", "5m", config={"allow_short": False}, path=httpd.profile_path)
    profiles.save("*", "5m", config={}, path=httpd.profile_path)
    profiles.save("BTC-USD", "5m", config={}, path=httpd.profile_path)
    rows = {r["key"]: r for r in httpd.handle("GET", "/api/profiles")[1]["profiles"]}
    assert rows["FCEL|5m"]["carded"] and not rows["*|5m"]["carded"]
    assert not rows["BTC-USD|5m"]["carded"]
    assert httpd.handle("POST", "/api/card-from-profile", {"key": "BTC-USD|5m"})[0] == 400
    status, body = httpd.handle("POST", "/api/card-from-profile", {"key": "FCEL|5m"})
    assert status == 200 and body["card"]["seed"] == "stocks"
    card = next(c for c in httpd.handle("GET", "/api/cards")[1]["cards"] if c["id"] == "stocks-fcel")
    assert card["preset_source"] == "FCEL|5m"  # runs its own tune, not *|5m
    status, body = httpd.handle("POST", "/api/tune", {"id": "stocks-fcel", "shorts": "keep"})
    assert status == 200 and body["spawned"] is False
    argv = body["argv"]
    assert argv[argv.index("--tickers") + 1] == "FCEL" and argv[argv.index("--interval") + 1] == "5m"


def test_stock_card_renders_walk_forward_not_copy():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert "if (card.seed === 'perp' || card.seed === 'stocks') row.appendChild(walkForward(card));" in html
    assert "copyControl(instruments.stocks" not in html
    assert "copyControl(instruments.perp), delB" in html
