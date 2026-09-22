import json

import pytest

from chart_app import profiles
from launch_dock.sync import ChartSaveError, save_preset


def test_chart_down_writes_the_file(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    rec = save_preset(
        "NVDA",
        "15m",
        config={"entry_long": 30},
        elmo={},
        capital=None,
        path=path,
        chart_post=None,
        existing=None,
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["NVDA|15m"]["config"]["entry_long"] == 30
    assert rec["validation"] == "in_sample"


def test_chart_up_posts_and_does_not_write(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    seen = {}

    def post(url, body):
        seen["url"] = url
        seen["body"] = body
        return {"ok": True, "saved": {"ticker": "NVDA", "validation": "in_sample"}}

    save_preset(
        "NVDA",
        "15m",
        config={"entry_long": 30},
        elmo={"liq_window": 7},
        capital=1000.0,
        path=path,
        chart_post=post,
        existing=None,
    )
    assert seen["url"] == "http://127.0.0.1:8791/api/profiles"
    assert seen["body"]["ticker"] == "NVDA"
    assert seen["body"]["interval"] == "15m"
    assert not path.exists()


def test_post_failure_does_not_direct_write(tmp_path):
    path = tmp_path / "chart_app_profiles.json"

    def post(url, body):
        return {"ok": False, "error": "bad"}

    with pytest.raises(ChartSaveError, match="bad"):
        save_preset(
            "NVDA",
            "15m",
            config={},
            elmo={},
            capital=None,
            path=path,
            chart_post=post,
            existing=None,
        )
    assert not path.exists()


def test_unchanged_save_keeps_walk_forward(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    profiles.save(
        "NVDA",
        "15m",
        elmo={"liq_window": 7},
        validation="walk_forward",
        path=path,
    )
    existing = profiles.resolve("NVDA", "15m", path=path)
    rec = save_preset(
        "NVDA",
        "15m",
        config={},
        elmo={"liq_window": 7},
        capital=None,
        path=path,
        chart_post=None,
        existing=existing,
    )
    assert rec["validation"] == "walk_forward"


def test_changed_knob_downgrades_to_in_sample(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    profiles.save(
        "NVDA",
        "15m",
        elmo={"liq_window": 7},
        validation="walk_forward",
        path=path,
    )
    existing = profiles.resolve("NVDA", "15m", path=path)
    rec = save_preset(
        "NVDA",
        "15m",
        config={},
        elmo={"liq_window": 8},
        capital=None,
        path=path,
        chart_post=None,
        existing=existing,
    )
    assert rec["validation"] == "in_sample"
