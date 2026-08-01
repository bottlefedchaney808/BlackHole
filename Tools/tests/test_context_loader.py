"""test_context_loader.py

Exercises context_loader.list_available_contexts() / load_context() against
a synthetic suite_context.json fixture, without touching any real suite
output directory (SUITE_OUTPUT_ROOTS is monkeypatched to a tmp_path layout
built by this test).
"""
import json
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools import context_loader  # noqa: E402


def _minimal_context(**overrides) -> dict:
    """Build a minimal, schema-valid suite_context dict (same shape
    build_suite_context produces), so fixtures don't drift from the real
    schema in suite_context.py.
    """
    base = {
        "schema_version": 1,
        "run_id": "test_run_001",
        "created_at_utc": "2026-07-30T12:00:00Z",
        "output_dir": str(Path("/tmp/does_not_matter").as_posix()),
        "focus": {
            "ticker": "SPY",
            "option_type": "call",
            "strike": None,
            "target_years": 0.25,
            "expiration_date": "2026-12-18",
        },
        "basket": {
            "index_ticker": "SPY",
            "tickers": ["SPY"],
            "weights": [1.0],
        },
        "sentiment": {
            "manifest_path": "/tmp/manifest.json",
            "pack_json_path": None,
            "group_id": None,
            "ranked_tickers": [],
        },
        "var": {
            "horizon_days": 1,
            "confidence": 0.99,
            "positions": None,
        },
        "controls": {
            "run_options_suite": False,
            "run_var_suite": False,
            "compile_pdf": False,
        },
        "paths": {
            "options_suite_root": "/tmp/Options_Suite",
            "var_suite_root": "/tmp/VaR_Tools_Simulations",
            "sentiment_suite_root": "/tmp/sentiment-scanner",
        },
        "data_sources": [],
        "strategies": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def fixture_roots(tmp_path, monkeypatch):
    """Build outputs/<run_id>/suite_context.json under two fake suite
    roots, one valid and one deliberately corrupted, and point
    context_loader at them instead of the real repo layout.
    """
    vol_root = tmp_path / "Vol_Suite" / "outputs"
    orch_root = tmp_path / "orchestrator_output"

    run_a = vol_root / "20260730_120000"
    run_a.mkdir(parents=True)
    ctx_a = _minimal_context(
        run_id="run_a", created_at_utc="2026-07-30T12:00:00Z",
        output_dir=str(run_a.as_posix()),
    )
    (run_a / "suite_context.json").write_text(json.dumps(ctx_a), encoding="utf-8")

    run_b = vol_root / "20260731_090000"
    run_b.mkdir(parents=True)
    ctx_b = _minimal_context(
        run_id="run_b", created_at_utc="2026-07-31T09:00:00Z",
        output_dir=str(run_b.as_posix()),
    )
    (run_b / "suite_context.json").write_text(json.dumps(ctx_b), encoding="utf-8")

    run_bad = orch_root / "20260729_000000"
    run_bad.mkdir(parents=True)
    bad_ctx = _minimal_context()
    del bad_ctx["focus"]  # missing required field -> should fail validation
    (run_bad / "suite_context.json").write_text(json.dumps(bad_ctx), encoding="utf-8")

    fake_roots = {
        "Vol_Suite": [vol_root],
        "Options_Suite": [tmp_path / "Options_Suite" / "outputs"],
        "VaR_Tools_Simulations": [tmp_path / "VaR_Tools_Simulations" / "outputs"],
        "sentiment-scanner": [tmp_path / "sentiment-scanner" / "outputs"],
        "orchestrator": [orch_root],
    }
    monkeypatch.setattr(context_loader, "SUITE_OUTPUT_ROOTS", fake_roots)
    return {"run_a": run_a, "run_b": run_b, "run_bad": run_bad}


def test_list_available_contexts_sorted_newest_first(fixture_roots):
    summaries = context_loader.list_available_contexts()
    valid = [s for s in summaries if s["valid"]]

    assert len(valid) == 2
    # run_b (2026-07-31) is newer than run_a (2026-07-30)
    assert valid[0]["run_id"] == "run_b"
    assert valid[1]["run_id"] == "run_a"
    assert valid[0]["ticker"] == "SPY"
    assert valid[0]["created_at_utc"] == "2026-07-31T09:00:00Z"
    assert valid[0]["path"].endswith("suite_context.json")


def test_list_available_contexts_reports_invalid_without_dropping(fixture_roots):
    summaries = context_loader.list_available_contexts()
    invalid = [s for s in summaries if not s["valid"]]

    assert len(invalid) == 1
    assert invalid[0]["suite"] == "orchestrator"
    assert "error" in invalid[0]
    assert invalid[0]["run_id"] is None


def test_load_context_returns_full_validated_dict(fixture_roots):
    summaries = context_loader.list_available_contexts()
    run_b_summary = next(s for s in summaries if s.get("run_id") == "run_b")

    full = context_loader.load_context(run_b_summary["path"])

    assert full["run_id"] == "run_b"
    assert full["focus"]["ticker"] == "SPY"
    assert full["schema_version"] == 1


def test_load_context_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        context_loader.load_context(str(tmp_path / "nope" / "suite_context.json"))


def test_no_output_roots_present_yields_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(context_loader, "SUITE_OUTPUT_ROOTS", {
        "Vol_Suite": [tmp_path / "nowhere"],
    })
    assert context_loader.list_available_contexts() == []
