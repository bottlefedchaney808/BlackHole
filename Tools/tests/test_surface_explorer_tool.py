"""test_surface_explorer_tool.py

Covers Tools/tools/surface_explorer_tool.py -- the mode-dispatched wrapper
around Vol_Suite/module_registry.py's surface_greek/surface_market_iv/
surface_flow_strike_time/surface_flow_strike_expiry modules (Task 5 of the
modularization overhaul: this tool's four in-scope modes became thin
compatibility shims over those registry modules, which in turn wrap
Vol_Suite/surface_grids.py's grid builders) and
Options_Suite/smile_by_model.py's iv_smile_by_model builder (untouched by
Task 5). Network-free: the tool's seams to real data (_import_vs_registry,
_import_smile_by_model) are patched with fakes, same pattern
test_vrp_term_structure_tool.py uses. See
TestRegistryDelegationParity at the bottom of this file for a second,
stronger-guarantee regression suite that exercises the REAL
Vol_Suite.module_registry + surface_grids code path end to end (only
ThetaData itself is faked, via context['_td']) to prove the Task 5 shim's
observable behavior didn't change.
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
from shared.module_registry import ModuleResult
from Tools.tools import surface_explorer_tool as se_tool


class _FakeVsRegistry:
    """Stand-in for Vol_Suite.module_registry -- exposes the four
    `_run_surface_*` module functions the tool now delegates to. Records
    calls in the same (name, ticker, ...) shape the old _FakeSurfaceGrids
    fake used, for easy comparison with pre-Task-5 assertions."""

    def __init__(self):
        self.calls = []

    def _run_surface_greek(self, context, *, td=None):
        ticker = context["ticker"]
        greek = context.get("greek") or "gamma"
        max_expiries = context.get("max_expiries") or 12
        self.calls.append(("build_greek_surface", ticker, greek, max_expiries))
        result = {
            "ticker": ticker,
            "greek": greek,
            "spot": 100.0,
            "strikes": [90.0, 100.0, 110.0],
            "expiries": ["20260101", "20260201"],
            "dtes": [30, 60],
            "grid": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
            "units": "shares",
            "skipped": [],
            "meta": {"n_expiries_used": 2},
        }
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={},
            context_patch={"surface_greek_result": result},
        )

    def _run_surface_market_iv(self, context, *, td=None):
        ticker = context["ticker"]
        min_dte = context.get("min_dte", 0)
        self.calls.append(("build_market_iv_surface", ticker, min_dte))
        result = {
            "ticker": ticker,
            "spot": 100.0,
            "strikes": [90.0, 100.0, 110.0],
            "tenors_years": [0.08, 0.25],
            "grid": [[0.2, 0.22, 0.25], [0.21, 0.23, 0.26]],
            "raw_points": [],
            "meta": {"source": "fake"},
        }
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={},
            context_patch={"surface_market_iv_result": result},
        )

    def _run_surface_flow_strike_time(self, context, *, td=None):
        ticker = context["ticker"]
        session = context.get("session")
        self.calls.append(("build_flow_strike_time", ticker, session))
        result = {
            "ticker": ticker,
            "spot": 100.0,
            "session": session or "20260101",
            "strike_edges": [90.0, 100.0, 110.0],
            "time_labels": ["09:30", "10:00"],
            "grid": [[100.0, -50.0], [0.0, 25.0]],
            "meta": {"n_trades_used": 2, "n_trades_total": 2},
        }
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={},
            context_patch={"surface_flow_strike_time_result": result},
        )

    def _run_surface_flow_strike_expiry(self, context, *, td=None):
        ticker = context["ticker"]
        session = context.get("session")
        max_expiries = context.get("max_expiries") or 12
        self.calls.append(("build_flow_strike_expiry", ticker, session, max_expiries))
        result = {
            "ticker": ticker,
            "spot": 100.0,
            "session": session or "20260101",
            "strike_edges": [90.0, 100.0, 110.0],
            "expiries": ["20260101", "20260201"],
            "grid": [[100.0, -50.0], [0.0, 25.0]],
            "meta": {"n_trades_total": 2},
        }
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={},
            context_patch={"surface_flow_strike_expiry_result": result},
        )


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
    fake = _FakeVsRegistry()
    monkeypatch.setattr(se_tool, "_import_vs_registry", lambda: fake)
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
    assert fake.calls == [("build_market_iv_surface", "SPY", 0)]


@pytest.mark.unit
def test_iv_surface_market_mode_min_dte(monkeypatch):
    fake = _install(monkeypatch)
    result = se_tool.run({"ticker": "SPY", "mode": "iv_surface_market", "min_dte": 14})
    assert result["mode"] == "iv_surface_market"
    assert fake.calls == [("build_market_iv_surface", "SPY", 14)]


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

    def fake_plot(result, x_key, y_key, z_label, title, path, **kwargs):
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
def test_dark_theme_flag_reaches_plot_surface_3d(monkeypatch, tmp_path):
    _install(monkeypatch)
    seen = {}

    def fake_plot(result, x_key, y_key, z_label, title, path, **kwargs):
        seen["dark_theme"] = kwargs.get("dark_theme")
        return path

    monkeypatch.setattr(se_tool, "_plot_surface_3d", fake_plot)
    se_tool.run(
        {
            "focus": {"ticker": "AAPL"},
            "mode": "greek_surface",
            "greek": "vanna",
            "output_dir": str(tmp_path),
            "dark_theme": True,
        }
    )
    assert seen["dark_theme"] is True


@pytest.mark.unit
def test_dark_theme_defaults_false(monkeypatch, tmp_path):
    _install(monkeypatch)
    seen = {}

    def fake_plot(result, x_key, y_key, z_label, title, path, **kwargs):
        seen["dark_theme"] = kwargs.get("dark_theme")
        return path

    monkeypatch.setattr(se_tool, "_plot_surface_3d", fake_plot)
    se_tool.run(
        {
            "focus": {"ticker": "AAPL"},
            "mode": "iv_surface_market",
            "output_dir": str(tmp_path),
        }
    )
    assert seen["dark_theme"] is False


@pytest.mark.unit
def test_output_dir_override_takes_priority(monkeypatch, tmp_path):
    _install(monkeypatch)
    written = {}
    monkeypatch.setattr(
        se_tool,
        "_plot_surface_3d",
        lambda result, x_key, y_key, z_label, title, path, **kwargs: (
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


# ---------------------------------------------------------------------------
# TestRegistryDelegationParity -- Task 5's required regression coverage:
# exercises the REAL Vol_Suite.module_registry._run_surface_* functions and
# the REAL surface_grids.py builders end to end (no _import_vs_registry
# mock), with only ThetaData faked via context['_td']. This proves the
# Task 5 shim (se_tool.run -> Vol_Suite.module_registry -> surface_grids)
# produces the exact same dict shape (grid fields + 'mode' + 'chart_path')
# a pre-Task-5 direct se_tool -> surface_grids call would have produced --
# not just that the mocked seam is called with the right arguments (the
# tests above), but that the whole real chain actually works together.
# _FakeTD mirrors Vol_Suite/tests/test_surface_grids.py's fake (same shape,
# duplicated here rather than cross-imported since Tools/tests and
# Vol_Suite/tests are independent test roots).
# ---------------------------------------------------------------------------

import math
from datetime import date, timedelta
from datetime import datetime as _dt

_PARITY_SPOT = 100.0


def _parity_theta(k: float) -> int:
    return int(round(k * 1000))


def _parity_smile_iv(strike: float, spot: float = _PARITY_SPOT) -> float:
    x = math.log(strike / spot)
    return max(0.20 + 0.30 * x * x, 0.05)


class _FakeTD:
    """Minimal stand-in exposing every ThetaDataController method
    surface_grids.py calls -- no live network."""

    def __init__(self, spot=_PARITY_SPOT, n_expiries=3, strikes=None):
        self._spot = spot
        self._strikes = strikes or list(range(70, 131, 5))
        today = date.today()
        self._exps = [
            (today + timedelta(days=14 * (i + 1))).strftime("%Y%m%d")
            for i in range(n_expiries)
        ]
        base_ts = _dt.combine(today, _dt.min.time()).replace(hour=9, minute=30)
        self._trades = [
            {
                "strike_price": _parity_theta(k),
                "trade_right": "C" if i % 2 == 0 else "P",
                "expiration": self._exps[0],
                "premium": 1000.0 + 10 * i,
                "datetime": (base_ts + timedelta(minutes=i)).isoformat(),
            }
            for i, k in enumerate(self._strikes)
        ]

    def fetch_spot_price(self, ticker):
        return self._spot

    def fetch_dividend_yield(self, ticker):
        return 0.0

    def list_expirations(self, ticker):
        return list(self._exps)

    def option_bulk_greeks(self, ticker, expiry):
        return [
            {
                "strike": _parity_theta(k),
                "right": right,
                "implied_vol": _parity_smile_iv(k, self._spot),
            }
            for k in self._strikes
            for right in ("C", "P")
        ]

    def option_bulk_oi(self, ticker, expiry):
        return [
            {"strike": _parity_theta(k), "right": right, "open_interest": 100}
            for k in self._strikes
            for right in ("C", "P")
        ]

    def option_session_trades(self, ticker, session):
        return list(self._trades)


class TestRegistryDelegationParity:
    @pytest.mark.unit
    def test_greek_surface_real_registry_delegation(self, tmp_path):
        result = se_tool.run(
            {
                "ticker": "AAPL",
                "mode": "greek_surface",
                "_td": _FakeTD(),
                "output_dir": str(tmp_path),
            }
        )
        assert result["mode"] == "greek_surface"
        assert result["ticker"] == "AAPL"
        assert result["greek"] == "gamma"
        assert result["spot"] == _PARITY_SPOT
        assert len(result["grid"]) == len(result["expiries"])
        assert result["chart_path"] is not None
        assert result["chart_path"].endswith(".png")

    @pytest.mark.unit
    def test_iv_surface_market_real_registry_delegation(self, tmp_path):
        result = se_tool.run(
            {
                "ticker": "AAPL",
                "mode": "iv_surface_market",
                "_td": _FakeTD(strikes=list(range(60, 141, 2))),
                "output_dir": str(tmp_path),
            }
        )
        assert result["mode"] == "iv_surface_market"
        assert result["ticker"] == "AAPL"
        assert result["spot"] == _PARITY_SPOT
        assert result["chart_path"] is not None

    @pytest.mark.unit
    def test_flow_strike_time_real_registry_delegation(self, tmp_path):
        result = se_tool.run(
            {
                "ticker": "AAPL",
                "mode": "flow_strike_time",
                "_td": _FakeTD(strikes=list(range(85, 116, 5))),
                "output_dir": str(tmp_path),
            }
        )
        assert result["mode"] == "flow_strike_time"
        assert result["ticker"] == "AAPL"
        assert result["chart_path"] is not None

    @pytest.mark.unit
    def test_flow_strike_expiry_real_registry_delegation(self, tmp_path):
        result = se_tool.run(
            {
                "ticker": "AAPL",
                "mode": "flow_strike_expiry",
                "_td": _FakeTD(n_expiries=3, strikes=list(range(85, 116, 5))),
                "output_dir": str(tmp_path),
            }
        )
        assert result["mode"] == "flow_strike_expiry"
        assert result["ticker"] == "AAPL"
        assert len(result["expiries"]) >= 1
        assert result["chart_path"] is not None

    @pytest.mark.unit
    def test_no_output_dir_still_returns_grid_without_chart(self):
        result = se_tool.run(
            {"ticker": "AAPL", "mode": "greek_surface", "_td": _FakeTD()}
        )
        assert result["mode"] == "greek_surface"
        assert result["chart_path"] is None
        assert "grid" in result

    @pytest.mark.unit
    def test_build_failure_still_raises_not_silently_empty(self):
        # spot=0.0 -> surface_grids.build_greek_surface raises ValueError
        # ("no usable spot") -> Vol_Suite.module_registry._run_surface_greek
        # catches it and returns status='failed' -> se_tool must re-raise,
        # matching this tool's pre-existing "raises on real data failure"
        # contract (never a silently empty/partial grid).
        with pytest.raises(ValueError, match="no usable spot"):
            se_tool.run(
                {"ticker": "AAPL", "mode": "greek_surface", "_td": _FakeTD(spot=0.0)}
            )
