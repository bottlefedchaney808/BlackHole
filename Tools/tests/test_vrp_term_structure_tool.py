"""test_vrp_term_structure_tool.py

Covers Tools/tools/vrp_term_structure_tool.py -- the standalone wrapper
around Vol_Suite/vrp_term_structure.py::compute_vrp_term_structure.

The two things worth pinning down here are the ones that break silently:

1. The tool feeds compute_vrp_term_structure the SAME argument order the
   real function declares (ticker, td, spot, r, q) -- getting r and q the
   wrong way round produces a plausible-looking term structure that is
   quietly wrong.
2. The returned dict is JSON-serializable. Every tenor that fails inside
   compute_vrp_term_structure comes back as a VrpTermPoint full of NaN
   (that is its documented gap-marker), and json.dumps emits a bare `NaN`
   token for those, which no strict parser can read -- the same regression
   Vol_Suite's own vol_result.json guards against.
"""
import json
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tools.registry FIRST, deliberately. Every tool module does
# `from Tools.registry import ToolSpec` while registry.py's own
# TOOLS = _load_tools() imports every tool module back -- so importing a tool
# module before the registry re-enters a half-initialized registry and dies on
# a missing TOOL_SPEC. This ordering is the same one the real caller
# (registry -> tool) always takes; it is not specific to this tool.
import Tools.registry  # noqa: E402,F401
from Tools.tools import vrp_term_structure_tool as vrp_tool  # noqa: E402


class _FakePoint:
    def __init__(self, label="1mo", vrp=2.0):
        self.expiry_label = label
        self.expiry_date = "20260717"
        self.T_years = 0.08
        self.fair_vol_pct = 31.0
        self.atm_iv_pct = 29.0
        self.convexity_pct = vrp
        self.rv_30d_pct = 27.5


class _FakeResult:
    def __init__(self, points=None, shape="upward"):
        self.ticker = "AAPL"
        self.timestamp = "2026-08-12T00:00:00Z"
        self.points = points if points is not None else [_FakePoint()]
        self.shape = shape
        self.chart_path = None


class _FakeTD:
    def __init__(self):
        self.closed = False

    def fetch_spot_price(self, ticker):
        return 150.0

    def fetch_dividend_yield(self, ticker):
        return 0.01

    def fetch_risk_free_rate(self, t):
        return 0.05

    def close(self):
        self.closed = True


def _install(monkeypatch, compute, td=None, plot=None):
    """Patch the tool's two seams: its ThetaData factory and its lazy
    import of the Vol_Suite module."""
    client = td if td is not None else _FakeTD()

    class _FakeModule:
        pass

    module = _FakeModule()
    module.compute_vrp_term_structure = compute
    module.plot_vrp_term_structure = plot or (lambda result, path: path)

    monkeypatch.setattr(vrp_tool, "_theta_client", lambda: client)
    monkeypatch.setattr(vrp_tool, "_import_vrp_module", lambda: module)
    return client


@pytest.mark.unit
def test_run_delegates_to_compute_vrp_term_structure(monkeypatch):
    calls = {}

    def fake_compute(ticker, td, spot, r, q):
        calls["args"] = (ticker, spot, r, q)
        calls["td"] = td
        return _FakeResult(shape="contango")

    client = _install(monkeypatch, fake_compute)

    result = vrp_tool.run({"focus": {"ticker": "AAPL"}})

    assert result["available"] is True
    assert result["shape"] == "contango"
    # ticker, spot, r, q -- in that order, with r=risk-free and q=dividend.
    assert calls["args"] == ("AAPL", 150.0, 0.05, 0.01)
    # The live client is what gets handed to the computation, and it is
    # closed afterwards rather than leaked.
    assert calls["td"] is client
    assert client.closed is True


@pytest.mark.unit
def test_run_returns_json_serializable_points_with_nan_scrubbed(monkeypatch):
    nan = float("nan")

    class _NanPoint(_FakePoint):
        def __init__(self):
            super().__init__(label="12mo")
            self.T_years = nan
            self.fair_vol_pct = nan
            self.convexity_pct = nan

    _install(monkeypatch,
             lambda ticker, td, spot, r, q: _FakeResult(
                 points=[_FakePoint(), _NanPoint()]))

    result = vrp_tool.run({"focus": {"ticker": "AAPL"}})

    assert len(result["points"]) == 2
    assert result["points"][0]["expiry_label"] == "1mo"
    assert result["points"][0]["convexity_pct"] == pytest.approx(2.0)
    assert result["points"][1]["convexity_pct"] is None
    assert result["points"][1]["fair_vol_pct"] is None

    raw = json.dumps(result)
    assert "NaN" not in raw and "Infinity" not in raw


@pytest.mark.unit
def test_run_accepts_a_top_level_ticker_too(monkeypatch):
    calls = {}

    def fake_compute(ticker, td, spot, r, q):
        calls["ticker"] = ticker
        return _FakeResult()

    _install(monkeypatch, fake_compute)
    assert vrp_tool.run({"ticker": "NVDA"})["available"] is True
    assert calls["ticker"] == "NVDA"


@pytest.mark.unit
def test_run_without_a_ticker_reports_unavailable_rather_than_raising(monkeypatch):
    _install(monkeypatch, lambda *a, **kw: _FakeResult())
    result = vrp_tool.run({"focus": {}})
    assert result["available"] is False
    assert "ticker" in result["error"]


@pytest.mark.unit
def test_run_reports_a_failed_computation_as_unavailable_with_the_error(monkeypatch):
    def boom(ticker, td, spot, r, q):
        raise RuntimeError("ThetaData unavailable")

    client = _install(monkeypatch, boom)
    result = vrp_tool.run({"focus": {"ticker": "AAPL"}})

    assert result["available"] is False
    assert "ThetaData unavailable" in result["error"]
    # Still closed on the failure path.
    assert client.closed is True


@pytest.mark.unit
def test_run_writes_a_chart_when_the_context_carries_an_output_dir(monkeypatch, tmp_path):
    plotted = {}

    def fake_plot(result, path):
        plotted["path"] = path
        return path

    _install(monkeypatch,
             lambda ticker, td, spot, r, q: _FakeResult(),
             plot=fake_plot)

    result = vrp_tool.run({"focus": {"ticker": "AAPL"},
                           "output_dir": str(tmp_path)})

    assert result["chart_path"] == plotted["path"]
    assert str(tmp_path) in result["chart_path"]
    assert result["chart_path"].endswith(".png")


@pytest.mark.unit
def test_a_failing_chart_does_not_fail_the_whole_run(monkeypatch, tmp_path):
    def boom_plot(result, path):
        raise RuntimeError("matplotlib blew up")

    _install(monkeypatch,
             lambda ticker, td, spot, r, q: _FakeResult(),
             plot=boom_plot)

    result = vrp_tool.run({"focus": {"ticker": "AAPL"},
                           "output_dir": str(tmp_path)})

    assert result["available"] is True
    assert result["chart_path"] is None


@pytest.mark.unit
def test_tool_spec_is_well_formed():
    spec = vrp_tool.TOOL_SPEC
    assert spec.slug == "vrp-term-structure"
    assert spec.name == "VRP Term Structure"
    assert spec.description.strip()
    assert spec.run is vrp_tool.run
