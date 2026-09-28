from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_plays_intent as di


def test_watch_excluded_and_size_null():
    cards = [
        {"ticker": "AAPL", "lean": "long", "score": 72, "thesis": "story",
         "journal_ref": "trading_journal/daily-plays/2026-09-27/AAPL.md"},
        {"ticker": "QQQ", "lean": "watch", "score": None, "thesis": ""},
    ]
    out = di.build_intent("20260927T120000Z", "2026-09-27T12:00:00Z", cards)
    assert [i["ticker"] for i in out["intents"]] == ["AAPL"]
    assert out["intents"][0]["status"] == "draft"
    assert out["intents"][0]["size"]["notional_usd"] is None
    assert out["intents"][0]["size"]["pct_equity"] is None
    assert out["capital"]["equity"] is None
    assert out["capital"]["cash"] is None
    assert out["capital"]["asof_utc"] is None
    assert out["capital"]["source"] is None
    assert out["intents"][0]["instruction"] == "story | capital snapshot not wired"
    assert "side" not in out["intents"][0]
    assert "qty" not in out["intents"][0]
    assert "limit" not in out["intents"][0]
    assert "venue" not in out["intents"][0]


def test_schema_rejects_missing_ticker():
    try:
        di.validate_cards([{"lean": "long", "score": 1}])
    except di.SchemaError:
        return
    raise AssertionError("expected SchemaError")


def test_schema_rejects_bad_lean():
    try:
        di.validate_cards([{"ticker": "AAPL", "lean": "yolo", "score": 1}])
    except di.SchemaError:
        return
    raise AssertionError("expected SchemaError")


def test_schema_allows_null_score_and_measured_zero_iv():
    di.validate_cards([
        {"ticker": "AAPL", "lean": "watch", "score": None,
         "stats": {"atm_iv": 0}},
    ])


def test_write_intent_writes_draft_json(tmp_path):
    cards = [
        {"ticker": "AAPL", "lean": "long", "score": 72, "thesis": "story",
         "journal_ref": "trading_journal/daily-plays/2026-09-27/AAPL.md"},
    ]
    payload = di.build_intent("20260927T120000Z", "2026-09-27T12:00:00Z", cards)
    path = di.write_intent(tmp_path, payload)
    assert path == tmp_path / "desk-intent" / "20260927T120000Z.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "20260927T120000Z"
    assert data["intents"][0]["status"] == "draft"
    assert all(i["status"] != "ready" for i in data["intents"])
    assert "side" not in data["intents"][0]
