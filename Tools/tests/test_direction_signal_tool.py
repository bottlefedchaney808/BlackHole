"""test_direction_signal_tool.py -- covers the Directional Engine tool
(Tools/tools/direction_signal_tool.py): unified + per-module modes, slug,
and whale-mode numeric overrides."""
import sys
import types
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import Tools.registry  # noqa: E402,F401
from Tools.tools import direction_signal_tool  # noqa: E402


@pytest.mark.unit
def test_run_unified_delegates_to_signal_generator(monkeypatch):
    class FakeGen:
        def generate(self, tk):
            return {"conviction": "HIGH", "ticker": tk}
    monkeypatch.setattr(direction_signal_tool, "_signal_generator", lambda: FakeGen())
    ctx = {"focus": {"ticker": "SPY"}, "mode": "unified"}
    assert direction_signal_tool.run(ctx) == {"conviction": "HIGH", "ticker": "SPY"}


@pytest.mark.unit
def test_run_individual_trend_mode(monkeypatch):
    fake_trend = types.ModuleType("Direction.trend_engine")
    fake_trend.analyze_trend = lambda tk: {"signal": True, "ticker": tk}
    monkeypatch.setitem(sys.modules, "Direction.trend_engine", fake_trend)
    ctx = {"focus": {"ticker": "SPY"}, "mode": "trend"}
    assert direction_signal_tool.run(ctx)["signal"] is True


@pytest.mark.unit
def test_run_whale_mode_passes_min_premium_and_threshold_bps(monkeypatch):
    fake_whale = types.ModuleType("Direction.whale_scanner")
    captured = {}

    def fake_scan(tk, **kw):
        captured.update(kw)
        return {"bias": "bullish", "ticker": tk}
    fake_whale.scan = fake_scan
    monkeypatch.setitem(sys.modules, "Direction.whale_scanner", fake_whale)

    ctx = {"focus": {"ticker": "SPY"}, "mode": "whale",
           "min_premium": "30000", "threshold_bps": "5"}
    assert direction_signal_tool.run(ctx)["bias"] == "bullish"
    assert captured == {"min_premium": 30000.0, "threshold_bps": 5.0}


@pytest.mark.unit
def test_run_whale_mode_without_overrides_calls_scan_with_defaults(monkeypatch):
    fake_whale = types.ModuleType("Direction.whale_scanner")
    captured = {}

    def fake_scan(tk, **kw):
        captured.update(kw)
        return {"bias": "neutral", "ticker": tk}
    fake_whale.scan = fake_scan
    monkeypatch.setitem(sys.modules, "Direction.whale_scanner", fake_whale)

    ctx = {"focus": {"ticker": "SPY"}, "mode": "whale"}
    direction_signal_tool.run(ctx)
    assert captured == {}


@pytest.mark.unit
def test_run_raises_without_ticker():
    with pytest.raises(ValueError):
        direction_signal_tool.run({"focus": {}})


@pytest.mark.unit
def test_run_rejects_unknown_mode():
    with pytest.raises(ValueError):
        direction_signal_tool.run({"focus": {"ticker": "SPY"}, "mode": "bogus"})


@pytest.mark.unit
def test_tool_spec_is_well_formed():
    spec = direction_signal_tool.TOOL_SPEC
    assert spec.slug == "directional-engine"
    assert spec.name == "Directional Engine"
    assert spec.run is direction_signal_tool.run
