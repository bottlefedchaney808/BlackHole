from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_plays_collect as orch


def test_success_writes_collect_and_ok_status(tmp_path):
    art = tmp_path / "daily-plays"
    art.mkdir()
    producers = {
        "rumors": lambda: ["AAPL"],
        "x": lambda: ["NVDA"],
        "sentiment": lambda tickers: {
            "AAPL": {"narrative": {"cns": 55, "war": 0.4, "bullish_pct": 60,
                                   "bearish_pct": 20, "volume": 2, "sources": ["stocktwits"]},
                     "stats": {"atm_iv": None, "iv_rank": None, "skew": None,
                               "oi_surge": False, "max_pain": None, "gex": None, "spot": None},
                     "errors": []},
            "NVDA": {"narrative": {"cns": 10, "war": 0.1, "bullish_pct": 50,
                                   "bearish_pct": 50, "volume": 1, "sources": ["stocktwits"]},
                     "stats": {"atm_iv": None, "iv_rank": None, "skew": None,
                               "oi_surge": False, "max_pain": None, "gex": None, "spot": None},
                     "errors": []},
        },
    }
    rc = orch.run_collect(artifact_dir=art, producers=producers, enabled=("rumors", "x", "sentiment"))
    assert rc == 0
    status = json.loads((art / "collect_status.json").read_text(encoding="utf-8"))
    assert status["ok"] is True
    data = json.loads((art / "collect.json").read_text(encoding="utf-8"))
    assert set(data["tickers"]) == {"AAPL", "NVDA"}


def test_producer_failure_does_not_replace_collect_json(tmp_path):
    art = tmp_path / "daily-plays"
    art.mkdir()
    (art / "collect.json").write_text('{"tickers": {"KEEP": {}}}', encoding="utf-8")

    def boom():
        raise RuntimeError("x_buzz missing")

    rc = orch.run_collect(
        artifact_dir=art,
        producers={"x": boom, "sentiment": lambda t: {}},
        enabled=("x", "sentiment"),
    )
    assert rc == 1
    status = json.loads((art / "collect_status.json").read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert "x_buzz missing" in status["error"]
    kept = json.loads((art / "collect.json").read_text(encoding="utf-8"))
    assert "KEEP" in kept["tickers"]


def test_reddit_configured_is_fatal(tmp_path):
    art = tmp_path / "daily-plays"
    art.mkdir()
    rc = orch.run_collect(
        artifact_dir=art,
        producers={},
        enabled=("reddit",),
    )
    assert rc == 1
    status = json.loads((art / "collect_status.json").read_text(encoding="utf-8"))
    assert status["ok"] is False


def test_empty_universe_is_fatal(tmp_path):
    art = tmp_path / "daily-plays"
    art.mkdir()
    (art / "collect.json").write_text('{"tickers": {"KEEP": {}}}', encoding="utf-8")
    rc = orch.run_collect(
        artifact_dir=art,
        producers={"rumors": lambda: [], "sentiment": lambda t: {}},
        enabled=("rumors", "sentiment"),
    )
    assert rc == 1
    status = json.loads((art / "collect_status.json").read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert "empty universe" in status["error"]
    kept = json.loads((art / "collect.json").read_text(encoding="utf-8"))
    assert "KEEP" in kept["tickers"]


def test_default_enabled_excludes_reddit():
    assert orch.DEFAULT_ENABLED == ("rumors", "x", "sentiment")
    assert "reddit" not in orch.DEFAULT_ENABLED


def test_map_sentiment_row_unusual_oi_missing_or_error_oi_surge_is_none():
    missing = orch.map_sentiment_row({"oi": {"spot": 10.0, "atm_iv": 0.2, "skew_vol_pts": 1.0}})
    assert missing["stats"]["oi_surge"] is None
    errored = orch.map_sentiment_row(
        {"scanners": {"unusual_oi": {"status": "error", "surge_detected": True}}}
    )
    assert errored["stats"]["oi_surge"] is None


def test_map_sentiment_row_unusual_oi_ok_surge_false_is_measured_false():
    out = orch.map_sentiment_row(
        {"scanners": {"unusual_oi": {"status": "ok", "surge_detected": False}}}
    )
    assert out["stats"]["oi_surge"] is False


def test_map_sentiment_row_skew_ok_uses_skew_signal():
    out = orch.map_sentiment_row(
        {
            "oi": {"skew_vol_pts": 1.1},
            "scanners": {"skew": {"status": "ok", "skew_signal": 2.5}},
        }
    )
    assert out["stats"]["skew"] == 2.5


def test_map_sentiment_row_max_pain_ok_uses_max_pain_strike():
    out = orch.map_sentiment_row(
        {"scanners": {"max_pain": {"status": "ok", "max_pain_strike": 150.0}}}
    )
    assert out["stats"]["max_pain"] == 150.0
