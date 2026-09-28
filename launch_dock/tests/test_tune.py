import json

import pytest

from launch_dock import tune
from launch_dock.server import create_server

CARD = {"id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "5m",
        "pid": None, "state": "stopped", "argv": []}


def _report(verdict="ADOPT"):
    wf = lambda r: {"oos_total_return_pct": r, "oos_worst_fold_pct": -0.4,  # noqa: E731
                    "oos_positive_folds": 3, "oos_folds_scored": 4, "oos_trades": 31}
    return {"tune": [{"ticker": "BTC-PERP", "interval": "5m", "cost_bps": 5.0,
                      "verdict": verdict, "oos_delta_pct": 5.65,
                      "incumbent_oos": wf(5.53), "tuned_oos": wf(11.19),
                      "candidate_config": {"entry_long": 64.0, "macd_fast": 14},
                      "candidate_elmo": {"entropy_window": 9},
                      "seed_source": "BTC-USD|5m", "allow_short": True}]}


def test_argv_is_always_the_sleeve_rate():
    argv = tune.tune_argv(CARD, shorts="on", seed_from="BTC-USD|5m", report=tune.Path("r.json"))
    assert argv[argv.index("--cost-bps") + 1] == "5.0"
    assert argv[argv.index("--allow-short") + 1] == "on"
    assert argv[argv.index("--seed-from") + 1] == "BTC-USD|5m"
    assert "--seed-from" not in tune.tune_argv(CARD, shorts="off", seed_from=" ", report=tune.Path("r"))
    stock = tune.tune_argv({**CARD, "seed": "stocks", "instrument": "FCEL"}, shorts="keep",
                           seed_from="", report=tune.Path("r"))
    assert stock[stock.index("--tickers") + 1] == "FCEL"
    assert stock[stock.index("--cost-bps") + 1] == "5.0"
    with pytest.raises(ValueError):
        tune.tune_argv({**CARD, "seed": "event_desk"}, shorts="on", seed_from="", report=tune.Path("r"))


def test_only_an_adopt_can_be_written():
    s = tune.summarize(_report("KEEP INCUMBENT"))
    with pytest.raises(ValueError):
        tune.adoption(s, existing=None)
    rec = tune.adoption(tune.summarize(_report()), existing={"capital": 125.0})
    assert rec["validation"] == "walk_forward"
    assert rec["elmo"] == {"entropy_window": 9} and rec["capital"] == 125.0
    assert "cost_bps=5.0" in rec["note"] and "BTC-USD|5m" in rec["note"]


def test_start_status_and_adopt_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_APP_PROFILES", str(tmp_path / "chart_app_profiles.json"))

    class Proc:
        pid = 9

    seen = {}

    def popen(argv, **kw):
        seen.update(kw, argv=argv)
        return Proc()

    dock = create_server(tmp_path, popen=popen, alive=lambda pid: False,
                         chart_probe=lambda: False)
    dock.registry["cards"].append(dict(CARD))
    status, body = dock.handle("POST", "/api/tune", {"id": "perp-btc", "shorts": "on"})
    assert status == 200 and body["pid"] == 9
    assert seen["log_name"] == "perp-btc.tune"

    (tmp_path / "tune_runs" / "perp-btc_short_on.json").write_text(json.dumps(_report()))
    status, body = dock.handle("GET", "/api/tune?id=perp-btc")
    assert body["running"] is False
    assert body["results"]["on"]["verdict"] == "ADOPT" and body["results"]["off"] is None

    status, body = dock.handle("POST", "/api/adopt", {"id": "perp-btc", "shorts": "on"})
    assert status == 200, body
    assert body["saved"]["validation"] == "walk_forward"
    from chart_app import profiles
    saved = profiles.resolve("BTC-PERP", "5m", path=tmp_path / "chart_app_profiles.json")
    assert saved["config"]["macd_fast"] == 14 and saved["elmo"]["entropy_window"] == 9

    status, _ = dock.handle("POST", "/api/adopt", {"id": "perp-btc", "shorts": "off"})
    assert status == 400


def test_a_second_tune_is_refused_while_one_runs(tmp_path):
    class Proc:
        pid = 9

    dock = create_server(tmp_path, popen=lambda argv, **kw: Proc(), alive=lambda pid: True)
    dock.registry["cards"].append(dict(CARD))
    assert dock.handle("POST", "/api/tune", {"id": "perp-btc", "shorts": "on"})[0] == 200
    status, body = dock.handle("POST", "/api/tune", {"id": "perp-btc", "shorts": "off"})
    assert status == 400 and "already running" in body["error"]
