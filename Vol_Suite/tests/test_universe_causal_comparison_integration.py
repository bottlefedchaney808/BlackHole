import json
from pathlib import Path

import Vol_Suite.run_universe_causal_comparison as runner
from Vol_Suite.run_universe_causal_comparison import (
    main,
    run_universe_causal_comparison,
)


def _unit(ticker: str, day: str = "2026-01-02") -> dict:
    return {"candidate_key": f"{ticker}|{day}", "ticker": ticker, "calendar_day": day}


def _eligible(units, **_kwargs):
    return {"causal_status": "CAUSAL_ELIGIBLE", "n": len(list(units)), "N": len(list(units)), "reasons": []}


def test_missing_runner_output_is_comparison_invalid(tmp_path: Path):
    out = tmp_path / "verdict.json"
    result = run_universe_causal_comparison(
        {"intended_unique_day_denominator": 1},
        {"units": [], "artifact_registry": {}},
        output_path=out,
    )
    assert result["status"] == "COMPARISON_INVALID"
    assert json.loads(out.read_text())["status"] == "COMPARISON_INVALID"


def test_two_tickers_one_day_are_clustered_and_not_compared_independently(monkeypatch):
    monkeypatch.setattr(runner, "validate_causal_eligibility", _eligible)
    calls = []
    monkeypatch.setattr(runner, "compare_expansion_common_input", lambda *args, **kwargs: calls.append(1))
    result = run_universe_causal_comparison(
        {"intended_unique_day_denominator": 1},
        {"units": [_unit("SPY"), _unit("QQQ")], "artifact_registry": {"ok": {}}},
        live_runner=lambda *_args, **_kwargs: None,
        new_runner=lambda *_args, **_kwargs: None,
    )
    assert result["status"] == "CAUSAL_BLOCKED"
    assert result["blocked_stage"] == "TASK3_CLUSTER_ADAPTER"
    assert result["same_day_units"]["2026-01-02"]["n_tickers"] == 2
    assert calls == []


def test_default_task4_composition_calls_evaluator(monkeypatch):
    monkeypatch.setattr(runner, "validate_causal_eligibility", _eligible)
    monkeypatch.setattr(runner, "_unit_input", lambda _unit: object())
    monkeypatch.setattr(runner, "compare_expansion_common_input", lambda *args, **kwargs: {"status": "VALID"})
    seen = []

    def evaluate(records):
        seen.extend(records)
        return {"status": "VALID", "decision": "WORSE"}

    monkeypatch.setattr(runner.run_task4_evaluation, "evaluate_task4", evaluate)
    result = run_universe_causal_comparison(
        {"intended_unique_day_denominator": 1},
        {"units": [_unit("SPY")], "artifact_registry": {"ok": {}}},
        live_runner=lambda *_args, **_kwargs: None,
        new_runner=lambda *_args, **_kwargs: None,
    )
    assert result["status"] == "WORSE"
    assert seen[0]["day"] == "2026-01-02"
    assert result["task4_evaluation"]["decision"] == "WORSE"


def test_cli_exit_is_nonzero_for_blocked_statuses(monkeypatch):
    for status in ("COMPARISON_INVALID", "CAUSAL_BLOCKED", "INDETERMINATE", "HARD_GAP", "FAILED_EXECUTION"):
        monkeypatch.setattr(runner, "run_universe_causal_comparison", lambda *args, _status=status, **kwargs: {"status": _status})
        assert main(["manifest.json", "evidence.json", "--output", "out.json"]) != 0


def test_cli_exit_is_zero_only_for_completed_decisions(monkeypatch):
    for status in ("BETTER", "WORSE"):
        monkeypatch.setattr(runner, "run_universe_causal_comparison", lambda *args, _status=status, **kwargs: {"status": _status})
        assert main(["manifest.json", "evidence.json", "--output", "out.json"]) == 0


def test_live_file_is_unchanged_after_runner_import():
    import subprocess
    live = Path(__file__).parents[1] / "dealer_positioning.py"
    base = subprocess.check_output([
        "git", "show", "1bc6fe926e05fff73f3e769f541d356ac8e361b:Vol_Suite/dealer_positioning.py",
    ], cwd=live.parents[1]).decode().replace("\r\n", "\n")
    assert live.read_text(encoding="utf-8").replace("\r\n", "\n") == base
