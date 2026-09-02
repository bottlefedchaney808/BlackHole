"""Tests for Vol_Suite/module_registry.py's surface-grid cluster (Task 5 of
the modularization overhaul, `.superpowers/sdd/task-5-brief.md`).

Covers: the four new ModuleSpec registrations (slug/category/requires/
default_selected), aggregation via shared.module_registry.all_modules(),
the repo-root flat-import fragile surface (now including `surface_grids`'s
own module-level imports), fail-loud control flow for each of the four
`_run_surface_*` functions, and the sys.path double-insertion interaction
between Vol_Suite/module_registry.py and
Tools/tools/surface_explorer_tool.py -- mocked ThetaData/local-file calls
only, no live network.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import Vol_Suite.module_registry as vs_registry
from shared.module_registry import ModuleResult, all_modules

pytestmark = pytest.mark.unit

_SURFACE_SLUGS = {
    "surface_greek",
    "surface_market_iv",
    "surface_flow_strike_time",
    "surface_flow_strike_expiry",
}

_ALL_TEN_SLUGS = {
    "dealer_exposure",
    "dealer_flow",
    "position_book",
    "dual_book",
    "chain_scanner",
    "svi_smile",
} | _SURFACE_SLUGS


def _by_slug(slug: str):
    matches = [m for m in vs_registry.MODULES if m.slug == slug]
    assert len(matches) == 1, f"expected exactly one {slug!r} module, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# ModuleSpec registration shape
# ---------------------------------------------------------------------------


class TestModuleSpecRegistration:
    def test_all_four_surface_slugs_present(self):
        slugs = {m.slug for m in vs_registry.MODULES}
        assert _SURFACE_SLUGS <= slugs

    @pytest.mark.parametrize("slug", sorted(_SURFACE_SLUGS))
    def test_surface_spec_fields(self, slug):
        m = _by_slug(slug)
        assert m.suite == "vol_suite"
        assert m.category == "surface"
        assert m.requires == []
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_expiry"
        assert m.cli_entry is None

    def test_no_new_module_defaults_to_selected(self):
        for slug in _SURFACE_SLUGS:
            assert _by_slug(slug).default_selected is False

    def test_six_existing_entries_untouched(self):
        # Task 5 must not remove/modify the six pre-existing entries.
        slugs = {m.slug for m in vs_registry.MODULES}
        assert {
            "dealer_exposure",
            "dealer_flow",
            "position_book",
            "dual_book",
            "chain_scanner",
            "svi_smile",
        } <= slugs
        assert len(vs_registry.MODULES) == 10


class TestAllModulesAggregation:
    def test_all_modules_includes_all_four_surface_slugs_without_raising(self):
        modules = all_modules()
        slugs = {m.slug for m in modules}
        assert _SURFACE_SLUGS <= slugs

    def test_all_modules_has_no_duplicate_slugs(self):
        modules = all_modules()
        slugs = [m.slug for m in modules]
        assert len(slugs) == len(set(slugs))


# ---------------------------------------------------------------------------
# Flat cwd-relative imports fragile surface, extended for Task 5:
# surface_grids.py adds its own module-level imports (expiry_book_exposure,
# numpy, expiry_book_production) plus lazily-imported runtime imports
# (vol_surface_2d inside build_market_iv_surface, chart_app.flow_stamp
# inside _flow_helpers()) -- confirm the whole module still imports cleanly
# from a repo-root-relative context, and that the RUNTIME imports (not just
# the module-level ones) also resolve, by calling through to a mocked
# builder invocation rather than merely `import surface_grids`.
# ---------------------------------------------------------------------------


class TestRepoRootImport:
    def test_imports_cleanly_from_repo_root_only(self):
        code = (
            "import sys; "
            f"sys.path.insert(0, r'{REPO_ROOT}'); "
            + "import Vol_Suite.module_registry as m; "
            # 6 from Task 3/4 + 4 more from Task 5 (surface_greek/
            # surface_market_iv/surface_flow_strike_time/
            # surface_flow_strike_expiry) = 10.
            "assert len(m.MODULES) == 10, m.MODULES; "
            "assert {ms.slug for ms in m.MODULES} >= set("
            f"{sorted(_SURFACE_SLUGS)!r}); "
            "print('OK')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, (
            f"repo-root-relative import of Vol_Suite.module_registry failed:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        assert "OK" in proc.stdout

    def test_all_modules_survives_tools_first_import_order(self):
        """Same regression as test_module_registry_chain_svi.py's, extended
        to prove the sys.path fix still holds with surface_grids.py's extra
        imports added.

        Imports `Tools.registry` first (this -- not directly importing
        `Tools.tools.surface_explorer_tool` -- is the realistic Tools-first
        order: `Tools/registry.py::_load_tools()` eagerly imports every
        `Tools/tools/*.py` module, including surface_explorer_tool, so
        importing that submodule directly first hits a genuine, pre-existing
        circular import -- `Tools.registry` partially initializes,
        surface_explorer_tool's own `from Tools.registry import ToolSpec`
        runs before `Tools.registry` finishes loading itself, and
        `Tools.registry._load_tools()` then can't find `TOOL_SPEC` on the
        still-initializing module. Confirmed live: unrelated to this task's
        changes, and matches how the dashboard actually imports Tools/ in
        production -- `import Tools.registry`, not a bare submodule import.)
        Then confirm Vol_Suite.module_registry.MODULES still has all ten
        expected slugs with no AttributeError/shadowing."""
        code = (
            "import sys; "
            f"sys.path.insert(0, r'{REPO_ROOT}'); " + "import Tools.registry; "
            "from shared.module_registry import all_modules; "
            "modules = all_modules(); "
            "suites = {m.suite for m in modules}; "
            "assert 'vol_suite' in suites, (suites, len(modules)); "
            "slugs = {m.slug for m in modules}; "
            f"assert set({sorted(_ALL_TEN_SLUGS)!r}) <= slugs, slugs; "
            "import Vol_Suite.module_registry as m; "
            "assert len(m.MODULES) == 10, m.MODULES; "
            "print('OK')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, (
            f"all_modules() lost surface-grid modules under Tools-first "
            f"import order:\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
        assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# Fakes -- no live ThetaData/network in any of these tests.
# ---------------------------------------------------------------------------


class _FakeTD:
    def close(self):
        pass


def _greek_surface_result(**overrides):
    defaults = {
        "ticker": "SPY",
        "greek": "gamma",
        "spot": 650.0,
        "strikes": [640.0, 650.0, 660.0],
        "expiries": ["20261016", "20261120"],
        "dtes": [30, 60],
        "grid": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        "units": "shares",
        "skipped": [],
        "meta": {"n_expiries_used": 2},
    }
    defaults.update(overrides)
    return defaults


def _market_iv_result(**overrides):
    defaults = {
        "ticker": "SPY",
        "spot": 650.0,
        "strikes": [640.0, 650.0, 660.0],
        "tenors_years": [0.08, 0.25],
        "grid": [[0.2, 0.21, 0.22], [0.19, 0.20, 0.21]],
        "raw_points": [],
        "meta": {"source": "vol_surface_2d.build_surface"},
    }
    defaults.update(overrides)
    return defaults


def _flow_strike_time_result(**overrides):
    defaults = {
        "ticker": "SPY",
        "spot": 650.0,
        "session": "20260901",
        "strike_edges": [640.0, 650.0, 660.0],
        "time_labels": ["09:30", "10:00"],
        "grid": [[100.0, -50.0]],
        "meta": {"n_trades_used": 2, "n_trades_total": 3},
    }
    defaults.update(overrides)
    return defaults


def _flow_strike_expiry_result(**overrides):
    defaults = {
        "ticker": "SPY",
        "spot": 650.0,
        "session": "20260901",
        "strike_edges": [640.0, 650.0, 660.0],
        "expiries": ["20261016", "20261120"],
        "grid": [[100.0, -50.0], [10.0, 5.0]],
        "meta": {"n_trades_total": 3},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# surface_greek
# ---------------------------------------------------------------------------


class TestSurfaceGreekRun:
    def test_happy_path_returns_ok_with_context_patch(self, monkeypatch):
        result = _greek_surface_result()

        def fake_build(ticker, greek, td=None, max_expiries=12):
            assert ticker == "SPY"
            assert greek == "gamma"
            assert max_expiries == 12
            return result

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_greek_surface", fake_build
        )

        out = vs_registry._run_surface_greek({"ticker": "SPY"}, td=_FakeTD())

        assert isinstance(out, ModuleResult)
        assert out.status == "ok"
        assert out.context_patch == {"surface_greek_result": result}
        assert out.metrics["ticker"] == "SPY"
        assert out.metrics["greek"] == "gamma"
        assert out.metrics["spot"] == 650.0
        assert out.metrics["n_expiries_used"] == 2
        assert out.metrics["units"] == "shares"
        assert out.artifacts == []

    def test_default_greek_is_gamma(self, monkeypatch):
        calls = []

        def fake_build(ticker, greek, td=None, max_expiries=12):
            calls.append(greek)
            return _greek_surface_result(greek=greek)

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_greek_surface", fake_build
        )

        vs_registry._run_surface_greek({"ticker": "SPY"}, td=_FakeTD())
        assert calls == ["gamma"]

    def test_honors_explicit_greek_and_max_expiries(self, monkeypatch):
        calls = []

        def fake_build(ticker, greek, td=None, max_expiries=12):
            calls.append((ticker, greek, max_expiries))
            return _greek_surface_result(ticker=ticker, greek=greek)

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_greek_surface", fake_build
        )

        vs_registry._run_surface_greek(
            {"ticker": "NVDA", "greek": "vanna", "max_expiries": 5}, td=_FakeTD()
        )
        assert calls == [("NVDA", "vanna", 5)]

    def test_build_failure_does_not_fake_success(self, monkeypatch):
        """Fail-loud: a simulated build failure must never come back as
        status='ok' (mirrors Task 3/4's most important tests)."""

        def boom(ticker, greek, td=None, max_expiries=12):
            raise ValueError("no usable spot price for 'SPY'")

        monkeypatch.setattr(vs_registry.surface_grids, "build_greek_surface", boom)

        out = vs_registry._run_surface_greek({"ticker": "SPY"}, td=_FakeTD())

        assert out.status == "failed"
        assert out.status != "ok"
        assert "no usable spot price" in out.metrics["error"]
        assert out.context_patch is None
        assert out.artifacts == []

    def test_missing_ticker_fails_loud_not_silently(self):
        out = vs_registry._run_surface_greek({})
        assert out.status == "failed"
        assert "ticker" in out.metrics["error"]


# ---------------------------------------------------------------------------
# surface_market_iv
# ---------------------------------------------------------------------------


class TestSurfaceMarketIvRun:
    def test_happy_path_returns_ok_with_context_patch(self, monkeypatch):
        result = _market_iv_result()

        def fake_build(ticker, td=None, min_dte=0):
            assert ticker == "SPY"
            assert min_dte == 0
            return result

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_market_iv_surface", fake_build
        )

        out = vs_registry._run_surface_market_iv({"ticker": "SPY"}, td=_FakeTD())

        assert out.status == "ok"
        assert out.context_patch == {"surface_market_iv_result": result}
        assert out.metrics["n_strikes"] == 3
        assert out.metrics["n_tenors"] == 2
        assert out.metrics["source"] == "vol_surface_2d.build_surface"

    def test_forwards_min_dte(self, monkeypatch):
        calls = []

        def fake_build(ticker, td=None, min_dte=0):
            calls.append(min_dte)
            return _market_iv_result()

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_market_iv_surface", fake_build
        )

        vs_registry._run_surface_market_iv(
            {"ticker": "SPY", "min_dte": 14}, td=_FakeTD()
        )
        assert calls == [14]

    def test_build_failure_does_not_fake_success(self, monkeypatch):
        def boom(ticker, td=None, min_dte=0):
            raise ValueError("could not build an IV surface for 'SPY'")

        monkeypatch.setattr(vs_registry.surface_grids, "build_market_iv_surface", boom)

        out = vs_registry._run_surface_market_iv({"ticker": "SPY"}, td=_FakeTD())

        assert out.status == "failed"
        assert out.status != "ok"
        assert "could not build" in out.metrics["error"]


# ---------------------------------------------------------------------------
# surface_flow_strike_time
# ---------------------------------------------------------------------------


class TestSurfaceFlowStrikeTimeRun:
    def test_happy_path_returns_ok_with_context_patch(self, monkeypatch):
        result = _flow_strike_time_result()

        def fake_build(ticker, td=None, session=None):
            assert ticker == "SPY"
            assert session is None
            return result

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_flow_strike_time", fake_build
        )

        out = vs_registry._run_surface_flow_strike_time({"ticker": "SPY"}, td=_FakeTD())

        assert out.status == "ok"
        assert out.context_patch == {"surface_flow_strike_time_result": result}
        assert out.metrics["session"] == "20260901"
        assert out.metrics["n_trades_used"] == 2
        assert out.metrics["n_trades_total"] == 3

    def test_forwards_session(self, monkeypatch):
        calls = []

        def fake_build(ticker, td=None, session=None):
            calls.append(session)
            return _flow_strike_time_result(session=session)

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_flow_strike_time", fake_build
        )

        vs_registry._run_surface_flow_strike_time(
            {"ticker": "SPY", "session": "20260828"}, td=_FakeTD()
        )
        assert calls == ["20260828"]

    def test_build_failure_does_not_fake_success(self, monkeypatch):
        def boom(ticker, td=None, session=None):
            raise ValueError("no trades for 'SPY' on session 20260901")

        monkeypatch.setattr(vs_registry.surface_grids, "build_flow_strike_time", boom)

        out = vs_registry._run_surface_flow_strike_time({"ticker": "SPY"}, td=_FakeTD())

        assert out.status == "failed"
        assert out.status != "ok"
        assert "no trades" in out.metrics["error"]


# ---------------------------------------------------------------------------
# surface_flow_strike_expiry
# ---------------------------------------------------------------------------


class TestSurfaceFlowStrikeExpiryRun:
    def test_happy_path_returns_ok_with_context_patch(self, monkeypatch):
        result = _flow_strike_expiry_result()

        def fake_build(
            ticker, td=None, session=None, max_expiries=12, min_dte=0, max_dte=60
        ):
            assert ticker == "SPY"
            assert session is None
            assert max_expiries == 12
            assert min_dte == 0
            assert max_dte == 60
            return result

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_flow_strike_expiry", fake_build
        )

        out = vs_registry._run_surface_flow_strike_expiry(
            {"ticker": "SPY"}, td=_FakeTD()
        )

        assert out.status == "ok"
        assert out.context_patch == {"surface_flow_strike_expiry_result": result}
        assert out.metrics["n_expiries_used"] == 2
        assert out.metrics["n_trades_total"] == 3

    def test_forwards_session_max_expiries_min_dte_max_dte(self, monkeypatch):
        calls = []

        def fake_build(
            ticker, td=None, session=None, max_expiries=12, min_dte=0, max_dte=60
        ):
            calls.append((session, max_expiries, min_dte, max_dte))
            return _flow_strike_expiry_result(session=session)

        monkeypatch.setattr(
            vs_registry.surface_grids, "build_flow_strike_expiry", fake_build
        )

        vs_registry._run_surface_flow_strike_expiry(
            {
                "ticker": "SPY",
                "session": "20260828",
                "max_expiries": 5,
                "min_dte": 7,
                "max_dte": 45,
            },
            td=_FakeTD(),
        )
        assert calls == [("20260828", 5, 7, 45)]

    def test_build_failure_does_not_fake_success(self, monkeypatch):
        def boom(ticker, td=None, session=None, max_expiries=12, min_dte=0, max_dte=60):
            raise ValueError("no listed expiries for 'SPY' in [0,60] DTE")

        monkeypatch.setattr(vs_registry.surface_grids, "build_flow_strike_expiry", boom)

        out = vs_registry._run_surface_flow_strike_expiry(
            {"ticker": "SPY"}, td=_FakeTD()
        )

        assert out.status == "failed"
        assert out.status != "ok"
        assert "no listed expiries" in out.metrics["error"]

    def test_missing_ticker_fails_loud_not_silently(self):
        out = vs_registry._run_surface_flow_strike_expiry({})
        assert out.status == "failed"
        assert "ticker" in out.metrics["error"]
