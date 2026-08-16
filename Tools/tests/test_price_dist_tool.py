"""test_price_dist_tool.py

Covers Tools/tools/price_dist_tool.py -- the standalone wrapper around
VaR_Tools_Simulations/main.py::_build_price_dist_from_context.

The wrapper's only real job is picking the focus ticker out of the context and
delegating, so that is what is pinned here, plus the lazy-import guard: the tool
must load VaR's main.py under the unique `var_tools_main` module name rather
than a bare `import main` (Options_Suite and sentiment-scanner also ship a
main.py, and whichever landed in sys.modules first would silently win).
"""
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tools.registry FIRST -- see the note in test_vrp_term_structure_tool.py.
import Tools.registry  # noqa: E402,F401
from Tools.tools import price_dist_tool  # noqa: E402


@pytest.mark.unit
def test_run_delegates_to_build_price_dist(monkeypatch):
    var_main = price_dist_tool._import_var_main()
    monkeypatch.setattr(
        var_main, "_build_price_dist_from_context",
        lambda ctx, tk: {"status": "ok", "ticker": tk})
    result = price_dist_tool.run({"focus": {"ticker": "AAPL"}})
    assert result == {"status": "ok", "ticker": "AAPL"}


@pytest.mark.unit
def test_run_raises_without_ticker():
    """The wrapper needs a ticker to select which VaR builder runs."""
    with pytest.raises(ValueError):
        price_dist_tool.run({"focus": {}})


@pytest.mark.unit
def test_run_dispatches_by_mode(monkeypatch):
    var_main = price_dist_tool._import_var_main()
    seen = {}
    def fake_mc(payload, ticker=None, **kw):
        seen["ticker"], seen["kw"] = ticker, kw
        return {"module": "mc_sim_1yr", "ticker": ticker}
    monkeypatch.setattr(var_main, "_build_mc_sim_from_context", fake_mc)
    ctx = {"focus": {"ticker": "SPY"}, "mode": "mc_sim", "horizon_days": 63, "n_sims": 5000}
    out = price_dist_tool.run(ctx)
    assert out["module"] == "mc_sim_1yr"
    assert seen["ticker"] == "SPY"
    assert seen["kw"]["horizon_days"] == 63
    assert seen["kw"]["n_sims"] == 5000


@pytest.mark.unit
def test_run_rejects_unknown_mode():
    with pytest.raises(ValueError):
        price_dist_tool.run({"focus": {"ticker": "SPY"}, "mode": "bogus"})


@pytest.mark.unit
def test_import_var_main_loads_under_unique_module_name():
    var_main = price_dist_tool._import_var_main()
    assert sys.modules.get("var_tools_main") is var_main
    assert hasattr(var_main, "_build_price_dist_from_context")


@pytest.mark.unit
def test_tool_spec_is_well_formed():
    spec = price_dist_tool.TOOL_SPEC
    assert spec.slug == "simulations"
    assert spec.name == "Simulations"
    assert spec.description.strip()
    assert spec.run is price_dist_tool.run
