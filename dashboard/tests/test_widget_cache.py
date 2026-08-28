import json
import math

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


def test_set_sanitizes_nan_and_infinity_to_none(tmp_path):
    # Real financial calcs can produce NaN/Infinity (e.g. an undefined
    # ratio). json.dumps() writes these as non-standard literals without
    # error, but Starlette's JSONResponse (allow_nan=False) 500s trying to
    # serialize them back out over GET /api/widgets/{id} -- sanitize at
    # write time so the cache is always valid, standard JSON.
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set(
        "position_analysis",
        {
            "score": float("nan"),
            "ratio": float("inf"),
            "neg_ratio": float("-inf"),
            "nested": {"vals": [1.0, float("nan"), 3.0]},
            "fine": 2.5,
        },
    )
    row = cache.get("position_analysis")
    assert row["payload"] == {
        "score": None,
        "ratio": None,
        "neg_ratio": None,
        "nested": {"vals": [1.0, None, 3.0]},
        "fine": 2.5,
    }
    # And the stored bytes are standards-compliant JSON, not the NaN/
    # Infinity literals Python's json module would otherwise happily write.
    with cache._connect() as conn:
        raw = conn.execute(
            "SELECT payload FROM widget_cache WHERE widget_id = ?",
            ("position_analysis",),
        ).fetchone()[0]
    assert "NaN" not in raw and "Infinity" not in raw
    json.loads(raw)  # must not raise


def test_get_returns_none_for_true_none_untouched(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("signals", {"a": None, "b": float("nan")})
    row = cache.get("signals")
    assert row["payload"] == {"a": None, "b": None}


def test_sanitize_leaves_normal_floats_alone():
    from dashboard.widget_cache import _sanitize_for_json

    assert _sanitize_for_json(3.14) == 3.14
    assert _sanitize_for_json(0.0) == 0.0
    assert math.isnan(float("nan"))  # sanity on the test's own assumption
    assert _sanitize_for_json(float("nan")) is None
