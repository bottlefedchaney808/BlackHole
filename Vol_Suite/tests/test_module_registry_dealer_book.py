"""Tests for Vol_Suite/module_registry.py's dealer-book cluster (Task 3 of
the modularization overhaul, `.superpowers/sdd/task-3-brief.md`).

Covers: the four ModuleSpec registrations (slug/category/requires/
default_selected), aggregation via shared.module_registry.all_modules(),
the repo-root flat-import fragile surface, and fail-loud control flow for
each run() -- mocked ThetaData/local-file calls only, no live network.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import Vol_Suite.module_registry as vs_registry
from shared.module_registry import ModuleResult, all_modules

pytestmark = pytest.mark.unit


def _by_slug(slug: str):
    matches = [m for m in vs_registry.MODULES if m.slug == slug]
    assert len(matches) == 1, f"expected exactly one {slug!r} module, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# ModuleSpec registration shape
# ---------------------------------------------------------------------------


class TestModuleSpecRegistration:
    def test_all_four_slugs_present(self):
        slugs = {m.slug for m in vs_registry.MODULES}
        assert slugs == {"dealer_exposure", "dealer_flow", "position_book", "dual_book"}

    def test_dealer_exposure_spec_fields(self):
        m = _by_slug("dealer_exposure")
        assert m.suite == "vol_suite"
        assert m.category == "exposure"
        assert m.requires == []
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_expiry"
        assert m.cli_entry == "Vol_Suite/dealer_exposure_module.py"

    def test_dealer_flow_spec_fields(self):
        m = _by_slug("dealer_flow")
        assert m.suite == "vol_suite"
        assert m.category == "flow"
        assert m.requires == ["dealer_exposure"]
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_expiry"
        assert m.cli_entry == "Vol_Suite/dealer_flow_module.py"

    def test_position_book_spec_fields(self):
        m = _by_slug("position_book")
        assert m.suite == "vol_suite"
        assert m.category == "exposure"
        assert m.requires == []
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_only"
        assert m.cli_entry == "Vol_Suite/dealer_position_book.py"

    def test_dual_book_spec_fields(self):
        m = _by_slug("dual_book")
        assert m.suite == "vol_suite"
        assert m.category == "exposure"
        assert m.requires == []
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_expiry"
        assert m.cli_entry == "Vol_Suite/dual_book.py"

    def test_no_module_defaults_to_selected(self):
        # Explicit plan guidance: none of these four auto-include in an
        # unselected unified run.
        assert all(not m.default_selected for m in vs_registry.MODULES)


class TestAllModulesAggregation:
    def test_all_modules_includes_all_four_without_raising(self):
        modules = all_modules()
        slugs = {m.slug for m in modules}
        assert {"dealer_exposure", "dealer_flow", "position_book", "dual_book"} <= slugs

    def test_all_modules_has_no_duplicate_slugs(self):
        modules = all_modules()
        slugs = [m.slug for m in modules]
        assert len(slugs) == len(set(slugs))


# ---------------------------------------------------------------------------
# Flat cwd-relative imports fragile surface: Vol_Suite/module_registry.py
# must import cleanly from a repo-root-relative context, not just from
# inside Vol_Suite/ (where tests/conftest.py already puts Vol_Suite/ on
# sys.path for us). Spawn a fresh subprocess with ONLY the repo root
# inserted, matching how shared/module_registry.py::_suite_modules()
# actually imports "Vol_Suite.module_registry" in production.
# ---------------------------------------------------------------------------


class TestRepoRootImport:
    def test_imports_cleanly_from_repo_root_only(self):
        code = (
            "import sys; "
            "sys.path.insert(0, r'%s'); "
            "import Vol_Suite.module_registry as m; "
            "assert len(m.MODULES) == 4, m.MODULES; "
            "print('OK')"
        ) % str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, (
            f"repo-root-relative import of Vol_Suite.module_registry failed:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# Fakes for the fetch-layer seams -- no live ThetaData/network in any of
# these tests.
# ---------------------------------------------------------------------------


class _FakeTD:
    """Stand-in ThetaDataController -- closes cleanly, never makes a real
    call since the functions under test are monkeypatched before they'd
    reach td.<method>()."""

    def close(self):
        pass


def _exposure_result(**overrides):
    defaults = dict(
        ticker="SPY",
        expiry="20261016",
        spot=650.0,
        gex_reference=1.0,
        book_gamma=2.0,
        charm_1d=3.0,
        residual_vanna_inventory=4.0,
        band_n=100.0,
        band_z=0.5,
        band_regime="QUIET/ABSORBED",
        structural=SimpleNamespace(status="available"),
        vanna_flow_live=250_000.0,
        vanna_flow_provenance="SURFACE_CHANGE",
        d_iv_used=0.02,
        flow_volume_rows=0,
        flow_provenance="live_snapshot+snapshot_only",
        flow_layer="snapshot_only",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# dealer_exposure: fail-loud control flow
# ---------------------------------------------------------------------------


class TestDealerExposureRun:
    def test_happy_path_returns_ok_with_context_patch(self, monkeypatch):
        result = _exposure_result()

        def fake_fetch(ticker, expiry, output_dir, *, td=None):
            return (["chart1.png", "chart2.png"], "interp text", result)

        monkeypatch.setattr(vs_registry, "fetch_dealer_exposure", fake_fetch)

        out = vs_registry._run_dealer_exposure(
            {"ticker": "SPY", "expiry": "20261016", "output_dir": "."}, td=_FakeTD()
        )

        assert isinstance(out, ModuleResult)
        assert out.status == "ok"
        assert out.context_patch == {"dealer_exposure_result": result}
        assert out.metrics["band_z"] == 0.5
        assert len(out.artifacts) == 2
        assert all(a.kind == "png" for a in out.artifacts)

    def test_fetch_failure_does_not_fake_success(self, monkeypatch):
        """The most important test in this task (per the brief): a
        simulated fetch failure must never come back as status='ok'."""

        def boom(ticker, expiry, output_dir, *, td=None):
            raise RuntimeError("ThetaData snapshot unavailable: simulated failure")

        monkeypatch.setattr(vs_registry, "fetch_dealer_exposure", boom)

        out = vs_registry._run_dealer_exposure(
            {"ticker": "SPY", "expiry": "20261016"}, td=_FakeTD()
        )

        assert out.status == "failed"
        assert out.status != "ok"
        assert "simulated failure" in out.metrics["error"]
        assert out.context_patch is None
        assert out.artifacts == []

    def test_missing_ticker_fails_loud_not_silently(self):
        out = vs_registry._run_dealer_exposure({})
        assert out.status == "failed"
        assert "ticker" in out.metrics["error"]


# ---------------------------------------------------------------------------
# dealer_flow: reuse-context, fail-loud, judgment-call metrics
# ---------------------------------------------------------------------------


class TestDealerFlowRun:
    def test_missing_upstream_context_patch_is_skipped_not_faked_ok(self):
        out = vs_registry._run_dealer_flow({"ticker": "SPY"})
        assert out.status == "skipped"
        assert out.status != "ok"
        assert "dealer_exposure_result" in out.metrics["reason"]

    def test_reuses_exposure_result_without_refetching(self, monkeypatch):
        called = {"fetch": False}

        def fail_if_called(*a, **k):
            called["fetch"] = True
            raise AssertionError("dealer_flow must not re-fetch")

        monkeypatch.setattr(vs_registry, "fetch_dealer_exposure", fail_if_called)

        result = _exposure_result()
        out = vs_registry._run_dealer_flow({"dealer_exposure_result": result})

        assert called["fetch"] is False
        assert out.status == "ok"
        assert out.metrics["vanna_flow_live"] == 250_000.0
        assert out.metrics["d_iv_used"] == 0.02
        assert out.metrics["flow_layer"] == "snapshot_only"

    def test_unavailable_vanna_flow_is_skipped_with_reason_not_faked_ok(self):
        result = _exposure_result(
            vanna_flow_live=None, vanna_flow_provenance="delta_iv_missing"
        )
        out = vs_registry._run_dealer_flow({"dealer_exposure_result": result})

        assert out.status == "skipped"
        assert out.status != "ok"
        assert "delta_iv_missing" in out.metrics["reason"]


# ---------------------------------------------------------------------------
# position_book: units contract + fail-loud
# ---------------------------------------------------------------------------


class TestPositionBookRun:
    def _fake_result(self):
        return SimpleNamespace(
            ticker="SPY",
            total_net=-24_790_000.0,
            arm="div_signed",
            lookback=150,
            dates_used=["20260101", "20260901"],
        )

    def test_metrics_carry_units_disambiguation_key(self, monkeypatch):
        monkeypatch.setattr(
            vs_registry.dealer_position_book,
            "load_history_days",
            lambda lookback=150: [{"date": "d1"}, {"date": "d2"}],
        )
        monkeypatch.setattr(
            vs_registry.dealer_position_book,
            "accumulate_position_book",
            lambda days, lookback, arm, ticker: self._fake_result(),
        )
        monkeypatch.setattr(vs_registry.dual_book, "load_dual_fit", lambda: object())
        monkeypatch.setattr(
            vs_registry.delta_band,
            "band_position",
            lambda n, fit: SimpleNamespace(
                n=n, dev=0.0, z=-0.3, regime="QUIET/ABSORBED"
            ),
        )

        out = vs_registry._run_position_book({"ticker": "SPY"})

        assert out.status == "ok"
        assert out.metrics["units"] == "vanna_weighted_oi_delta_contracts"
        assert out.metrics["band_z"] == -0.3
        assert out.context_patch["position_book_result"].total_net == -24_790_000.0

    def test_insufficient_cache_days_fails_loud_not_faked_ok(self, monkeypatch):
        monkeypatch.setattr(
            vs_registry.dealer_position_book,
            "load_history_days",
            lambda lookback=150: [{"date": "only_one_day"}],
        )

        out = vs_registry._run_position_book({"ticker": "SPY"})

        assert out.status == "failed"
        assert out.status != "ok"
        assert ">=2 cache days" in out.metrics["error"]


# ---------------------------------------------------------------------------
# dual_book: fail-loud + units passthrough
# ---------------------------------------------------------------------------


class TestDualBookRun:
    def test_happy_path_passes_through_units_dict(self, monkeypatch):
        expo = _exposure_result()
        dual_result = SimpleNamespace(
            exposure=expo,
            position=SimpleNamespace(total_net=-1.0e6),
            position_z=-0.2,
            position_regime="QUIET/ABSORBED",
            spread=2.0e6,
            units={
                "exposure": "shares",
                "position": "vanna_weighted_oi_delta_contracts",
            },
        )
        monkeypatch.setattr(
            vs_registry.dual_book,
            "fetch_dual_book",
            lambda td, ticker, expiry, lookback, arm: dual_result,
        )

        out = vs_registry._run_dual_book(
            {"ticker": "SPY", "expiry": "20261016"}, td=_FakeTD()
        )

        assert out.status == "ok"
        assert out.metrics["units"] == {
            "exposure": "shares",
            "position": "vanna_weighted_oi_delta_contracts",
        }
        assert out.metrics["spread"] == 2.0e6
        assert out.context_patch == {"dual_book_result": dual_result}

    def test_fetch_failure_does_not_fake_success(self, monkeypatch):
        def boom(td, ticker, expiry, lookback, arm):
            raise RuntimeError("position book needs >=2 cache days, got 1")

        monkeypatch.setattr(vs_registry.dual_book, "fetch_dual_book", boom)

        out = vs_registry._run_dual_book({"ticker": "SPY"}, td=_FakeTD())

        assert out.status == "failed"
        assert out.status != "ok"
        assert "cache days" in out.metrics["error"]
