"""test_surface_explorer_tool.py

Covers Tools/tools/surface_explorer_tool.py -- the mode-dispatched wrapper
around Vol_Suite/surface_grids.py's grid builders and
Options_Suite/smile_by_model.py's iv_smile_by_model builder. Network-free:
the tool's only seams to real data (_import_surface_grids,
_import_smile_by_model) are patched with fakes, same pattern
test_vrp_term_structure_tool.py uses.
"""

import json
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tools.registry FIRST -- see test_vrp_term_structure_tool.py's note on why.
import Tools.registry  # noqa: F401
from Tools.tools import surface_explorer_tool as se_tool


class _FakeSurfaceGrids:
    def __init__(self):
        self.calls = []

    def build_greek_surface(self, ticker, greek, max_expiries=12):
        self.calls.append(("build_greek_surface", ticker, greek, max_expiries))
        return {
            "ticker": ticker,
            "greek": greek,
            "spot": 100.0,
            "strikes": [90.0, 100.0, 110.0],
            "expiries": ["20260101", "20260201"],
            "dtes": [30, 60],
            "grid": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
            "units": "shares",
            "skipped": [],
            "meta": {},
        }

    def build_market_iv_surface(self, ticker):
        self.calls.append(("build_market_iv_surface", ticker))
        return {
            "ticker": ticker,
            "spot": 100.0,
            "strikes": [90.0, 100.0, 110.0],
            "tenors_years": [0.08, 0.25],
            "grid": [[0.2, 0.22, 0.25], [0.21, 0.23, 0.26]],
            "raw_points": [],
            "meta": {},
        }

    def build_flow_strike_time(self, ticker, session=None):
        self.calls.append(("build_flow_strike_time", ticker, session))
        return {
            "ticker": ticker,
            "spot": 100.0,
            "session": session or "20260101",
            "strike_edges": [90.0, 100.0, 110.0],
            "time_labels": ["09:30", "10:00"],
            "grid": [[100.0, -50.0], [0.0, 25.0]],
            "meta": {},
        }

    def build_flow_strike_expiry(self, ticker, session=None, max_expiries=12):
        self.calls.append(("build_flow_strike_expiry", ticker, session, max_expiries))
        return {
            "ticker": ticker,
            "spot": 100.0,
            "session": session or "20260101",
            "strike_edges": [90.0, 100.0, 110.0],
            "expiries": ["20260101", "20260201"],
            "grid": [[100.0, -50.0], [0.0, 25.0]],
            "meta": {},
        }


class _FakeSmileByModel:
    def __init__(self):
        self.calls = []

    def build_iv_smile_by_model(
        self,
        ticker,
        expiry=None,
        strike=None,
        option_type="call",
        include_mc=True,
        include_heston=True,
    ):
        self.calls.append(
            (
                "build_iv_smile_by_model",
                ticker,
                expiry,
                strike,
                option_type,
                include_mc,
                include_heston,
            )
        )
        return {
            "ticker": ticker,
            "spot": 100.0,
            "expiry": expiry or "20260101",
            "strike": strike or 100.0,
            "option_type": option_type,
            "curves": {
                "CRR": {"strikes": [90.0, 100.0, 110.0], "ivs": [0.21, 0.20, 0.22]},
                "MC": {"strikes": [90.0, 100.0, 110.0], "ivs": [0.19, 0.20, 0.21]},
            },
            "market": {"strikes": [90.0, 100.0, 110.0], "ivs": [0.22, 0.21, 0.23]},
            "meta": {"include_mc": include_mc, "include_heston": include_heston},
        }


def _install(monkeypatch):
    fake = _FakeSurfaceGrids()
    monkeypatch.setattr(se_tool, "_import_surface_grids", lambda: fake)
    return fake


def _install_smile(monkeypatch):
    fake = _FakeSmileByModel()
    monkeypatch.setattr(se_tool, "_import_smile_by_model", lambda: fake)
    return fake


@pytest.mark.unit
def test_greek_surface_default_mode_and_greek(monkeypatch):
    fake = _install(monkeypatch)
    result = se_tool.run({"focus": {"ticker": "aapl"}})
    assert result["mode"] == "greek_surface"
    assert result["greek"] == "gamma"
    assert fake.calls == [("build_greek_surface", "AAPL", "gamma", 12)]
    assert result["chart_path"] is None  # no output_dir supplied


@pytest.mark.unit
def test_greek_surface_honors_explicit_greek_and_max_expiries(monkeypatch):
    fake = _install(monkeypatch)
    se_tool.run(
        {"ticker": "NVDA", "mode": "greek_surface", "greek": "vanna", "max_expiries": 5}
    )
    assert fake.calls == [("build_greek_surface", "NVDA", "vanna", 5)]


@pytest.mark.unit
def test_iv_surface_market_mode(monkeypatch):
    fake = _install(monkeypatch)
    result = se_tool.run({"ticker": "SPY", "mode": "iv_surface_market"})
    assert result["mode"] == "iv_surface_market"
    assert fake.calls == [("build_market_iv_surface", "SPY")]


@pytest.mark.unit
def test_flow_strike_time_mode_passes_session(monkeypatch):
    fake = _install(monkeypatch)
    result = se_tool.run(
        {"ticker": "QQQ", "mode": "flow_strike_time", "session": "20260115"}
    )
    assert result["mode"] == "flow_strike_time"
    assert fake.calls == [("build_flow_strike_time", "QQQ", "20260115")]


@pytest.mark.unit
def test_flow_strike_expiry_mode(monkeypatch):
    fake = _install(monkeypatch)
    result = se_tool.run({"ticker": "QQQ", "mode": "flow_strike_expiry"})
    assert result["mode"] == "flow_strike_expiry"
    assert fake.calls == [("build_flow_strike_expiry", "QQQ", None, 12)]


@pytest.mark.unit
def test_unknown_mode_raises(monkeypatch):
    _install(monkeypatch)
    with pytest.raises(ValueError, match="unknown surface-explorer mode"):
        se_tool.run({"ticker": "AAPL", "mode": "bogus"})


@pytest.mark.unit
def test_missing_ticker_raises(monkeypatch):
    _install(monkeypatch)
    with pytest.raises(ValueError, match="requires a ticker"):
        se_tool.run({"mode": "greek_surface"})


@pytest.mark.unit
def test_result_is_json_serializable(monkeypatch):
    _install(monkeypatch)
    result = se_tool.run({"ticker": "AAPL", "mode": "iv_surface_market"})
    raw = json.dumps(result)
    assert "NaN" not in raw and "Infinity" not in raw


@pytest.mark.unit
def test_a_failing_chart_render_does_not_fail_the_whole_run(monkeypatch, tmp_path):
    _install(monkeypatch)
    monkeypatch.setattr(
        se_tool,
        "_plot_surface_3d",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("matplotlib blew up")),
    )
    result = se_tool.run(
        {
            "focus": {"ticker": "AAPL"},
            "mode": "iv_surface_market",
            "output_dir": str(tmp_path),
        }
    )
    assert result["chart_path"] is None


@pytest.mark.unit
def test_chart_written_when_output_dir_present(monkeypatch, tmp_path):
    _install(monkeypatch)
    written = {}

    def fake_plot(result, x_key, y_key, z_label, title, path):
        written["path"] = path
        return path

    monkeypatch.setattr(se_tool, "_plot_surface_3d", fake_plot)
    result = se_tool.run(
        {
            "focus": {"ticker": "AAPL"},
            "mode": "greek_surface",
            "output_dir": str(tmp_path),
        }
    )
    assert result["chart_path"] == written["path"]
    assert str(tmp_path) in result["chart_path"]
    assert result["chart_path"].endswith(".png")


@pytest.mark.unit
def test_output_dir_override_takes_priority(monkeypatch, tmp_path):
    _install(monkeypatch)
    written = {}
    monkeypatch.setattr(
        se_tool,
        "_plot_surface_3d",
        lambda result, x_key, y_key, z_label, title, path: (
            written.setdefault("path", path) or path
        ),
    )
    override_dir = tmp_path / "override"
    override_dir.mkdir()
    result = se_tool.run(
        {
            "ticker": "AAPL",
            "mode": "greek_surface",
            "output_dir": str(tmp_path / "not_used"),
            "_output_dir_override": str(override_dir),
        }
    )
    assert str(override_dir) in result["chart_path"]


@pytest.mark.unit
def test_iv_smile_by_model_default_args(monkeypatch):
    fake = _install_smile(monkeypatch)
    result = se_tool.run({"ticker": "SPY", "mode": "iv_smile_by_model"})
    assert result["mode"] == "iv_smile_by_model"
    assert fake.calls == [
        ("build_iv_smile_by_model", "SPY", None, None, "call", True, True)
    ]
    assert result["chart_path"] is None  # no output_dir supplied


@pytest.mark.unit
def test_iv_smile_by_model_honors_explicit_fields(monkeypatch):
    fake = _install_smile(monkeypatch)
    se_tool.run(
        {
            "ticker": "AAPL",
            "mode": "iv_smile_by_model",
            "expiry": "20261016",
            "strike": "150.5",
            "option_type": "put",
            "include_mc": False,
            "include_heston": False,
        }
    )
    assert fake.calls == [
        (
            "build_iv_smile_by_model",
            "AAPL",
            "20261016",
            150.5,
            "put",
            False,
            False,
        )
    ]


@pytest.mark.unit
def test_iv_smile_by_model_mode_aliases(monkeypatch):
    for alias in ("iv-smile-by-model", "smile_by_model", "IV_SMILE_BY_MODEL"):
        fake = _install_smile(monkeypatch)
        result = se_tool.run({"ticker": "QQQ", "mode": alias})
        assert result["mode"] == "iv_smile_by_model"


@pytest.mark.unit
def test_iv_smile_by_model_chart_written_when_output_dir_present(monkeypatch, tmp_path):
    _install_smile(monkeypatch)
    written = {}

    def fake_plot(result, path):
        written["path"] = path
        return path

    monkeypatch.setattr(se_tool, "_plot_smile_by_model", fake_plot)
    result = se_tool.run(
        {
            "ticker": "SPY",
            "mode": "iv_smile_by_model",
            "output_dir": str(tmp_path),
        }
    )
    assert result["chart_path"] == written["path"]
    assert str(tmp_path) in result["chart_path"]
    assert result["chart_path"].endswith(".png")


@pytest.mark.unit
def test_iv_smile_by_model_failing_chart_render_does_not_fail_the_whole_run(
    monkeypatch, tmp_path
):
    _install_smile(monkeypatch)
    monkeypatch.setattr(
        se_tool,
        "_plot_smile_by_model",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("matplotlib blew up")),
    )
    result = se_tool.run(
        {
            "ticker": "SPY",
            "mode": "iv_smile_by_model",
            "output_dir": str(tmp_path),
        }
    )
    assert result["chart_path"] is None


@pytest.mark.unit
def test_iv_smile_by_model_result_is_json_serializable(monkeypatch):
    _install_smile(monkeypatch)
    result = se_tool.run({"ticker": "SPY", "mode": "iv_smile_by_model"})
    raw = json.dumps(result)
    assert "NaN" not in raw and "Infinity" not in raw


@pytest.mark.unit
def test_tool_spec_is_well_formed():
    spec = se_tool.TOOL_SPEC
    assert spec.slug == "surface-explorer"
    assert spec.name == "Surface Explorer"
    assert spec.description.strip()
    assert spec.run is se_tool.run
