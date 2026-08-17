"""test_backtesting_tool.py -- covers Tools/tools/backtesting_tool.py, focused
on the strategy-source resolver (chain_strategies.json fallback)."""
import json
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import Tools.registry  # noqa: E402,F401
from Tools.tools import backtesting_tool  # noqa: E402


@pytest.mark.unit
def test_resolve_strategies_prefers_context_inline():
    ctx = {"strategies": [{"strategy_type": "straddle", "legs": []}]}
    assert backtesting_tool._resolve_strategies(ctx)[0]["strategy_type"] == "straddle"


@pytest.mark.unit
def test_resolve_strategies_reads_chain_strategies_artifact(tmp_path):
    artifact = tmp_path / "chain_strategies.json"
    artifact.write_text(json.dumps({"strategies": [
        {"strategy_type": "call_spread", "legs": []}]}), encoding="utf-8")
    ctx = {"focus": {"ticker": "SPY"}, "_output_dir_override": str(tmp_path)}
    resolved = backtesting_tool._resolve_strategies(ctx)
    assert resolved[0]["strategy_type"] == "call_spread"


@pytest.mark.unit
def test_resolve_strategies_empty_when_no_source(tmp_path):
    ctx = {"focus": {"ticker": "SPY"}, "_output_dir_override": str(tmp_path)}
    assert backtesting_tool._resolve_strategies(ctx) == []


@pytest.mark.unit
def test_dealer_gamma_study_always_runs_all_models(monkeypatch):
    """The study always runs ONE combined backtest (all three live models:
    v1, v2_live, dealer_exposure) -- there is no per-model selector."""
    import dataclasses
    import types

    @dataclasses.dataclass
    class _Result:
        v1_diff: float = 0.1

    calls = {}
    fake_bs3 = types.ModuleType("backtest_stage3")
    fake_bs3.DEFAULT_LOOKBACK_DAYS = 90
    fake_bs3.DEFAULT_FORWARD_WINDOW_DAYS = 5

    def _run(ticker, **kw):
        calls.update(kw)
        return _Result()
    fake_bs3.run_backtest = _run
    fake_bs3.format_backtest_report = lambda r: "REPORT"
    monkeypatch.setitem(sys.modules, "backtest_stage3", fake_bs3)

    ctx = {"focus": {"ticker": "SPY"}}
    out = backtesting_tool.run_dealer_gamma_study(ctx)
    assert out["sign_model"] == "all"
    assert out["report"] == "REPORT"
    assert calls["accumulate"] is True
    assert calls["sign_model"] == "all"
