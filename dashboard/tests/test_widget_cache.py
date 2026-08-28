from dashboard.widget_cache import WidgetCache


def test_get_returns_none_when_missing(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    assert cache.get("positions") is None


def test_set_then_get_round_trips(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("positions", {"positions": [{"ticker": "NVDA"}]}, status="ok")
    row = cache.get("positions")
    assert row is not None
    assert row["payload"] == {"positions": [{"ticker": "NVDA"}]}
    assert row["status"] == "ok"
    assert isinstance(row["computed_at"], str) and row["computed_at"]


def test_set_overwrites_existing(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("signals", {"n": 1}, status="ok")
    first_computed_at = cache.get("signals")["computed_at"]
    cache.set("signals", {"n": 2}, status="error")
    row = cache.get("signals")
    assert row["payload"] == {"n": 2}
    assert row["status"] == "error"
    assert row["computed_at"] >= first_computed_at


def test_widget_ids_are_independent(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("positions", {"a": 1})
    cache.set("signals", {"b": 2})
    assert cache.get("positions")["payload"] == {"a": 1}
    assert cache.get("signals")["payload"] == {"b": 2}


def test_creates_parent_directory(tmp_path):
    nested = tmp_path / "nested" / "dir" / "widgets.db"
    cache = WidgetCache(nested)
    cache.set("positions", {"a": 1})
    assert nested.exists()
    assert cache.get("positions")["payload"] == {"a": 1}
