"""Tests for Vol_Suite/module_registry.py's chain_scanner + svi_smile
entries (Task 4 of the modularization overhaul,
`.superpowers/sdd/task-4-brief.md`).

Covers: the two new ModuleSpec registrations (slug/category/requires/
default_selected), aggregation via shared.module_registry.all_modules(),
the repo-root flat-import fragile surface (chain_scanner/svi_smile's
extra imports -- options_chain_scanner, expiry_book_exposure,
expiry_book_production, smile_by_model), fail-loud control flow for
chain_scanner, and svi_smile's mode-dispatch across its three call sites
-- mocked ThetaData/local-file calls only, no live network.
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
    def test_chain_scanner_and_svi_smile_slugs_present(self):
        slugs = {m.slug for m in vs_registry.MODULES}
        assert {"chain_scanner", "svi_smile"} <= slugs

    def test_chain_scanner_spec_fields(self):
        m = _by_slug("chain_scanner")
        assert m.suite == "vol_suite"
        assert m.category == "scanner"
        assert m.requires == []
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_expiry"
        assert m.cli_entry is None

    def test_svi_smile_spec_fields(self):
        m = _by_slug("svi_smile")
        assert m.suite == "vol_suite"
        assert m.category == "smile"
        assert m.requires == []
        assert m.default_selected is False
        assert m.archive.key_shape == "ticker_expiry"
        assert m.cli_entry is None

    def test_neither_new_module_defaults_to_selected(self):
        for slug in ("chain_scanner", "svi_smile"):
            assert _by_slug(slug).default_selected is False


class TestAllModulesAggregation:
    def test_all_modules_includes_both_new_slugs_without_raising(self):
        modules = all_modules()
        slugs = {m.slug for m in modules}
        assert {"chain_scanner", "svi_smile"} <= slugs

    def test_all_modules_has_no_duplicate_slugs(self):
        modules = all_modules()
        slugs = [m.slug for m in modules]
        assert len(slugs) == len(set(slugs))


# ---------------------------------------------------------------------------
# Flat cwd-relative imports fragile surface, extended for Task 4: this file
# now also imports options_chain_scanner/expiry_book_exposure/
# expiry_book_production (flat, Vol_Suite-relative) and smile_by_model
# (flat, Options_Suite-relative) -- confirm the whole module still imports
# cleanly from a repo-root-relative context with both new suite dirs
# resolved on sys.path.
# ---------------------------------------------------------------------------


class TestRepoRootImport:
    def test_imports_cleanly_from_repo_root_only(self):
        code = (
            "import sys; "
            f"sys.path.insert(0, r'{REPO_ROOT}'); "
            + "import Vol_Suite.module_registry as m; "
            "assert len(m.MODULES) == 6, m.MODULES; "
            "assert {ms.slug for ms in m.MODULES} >= {'chain_scanner', 'svi_smile'}; "
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
        """Regression for the silent-vanish failure mode: in a process that
        imports Tools/registry.py BEFORE Vol_Suite.module_registry,
        surface_explorer_tool's module-scope sys.path front-insert of
        Options_Suite/ used to leave Vol_Suite/ behind Options_Suite/; this
        file's membership-guarded insert then skipped (dir already present)
        and the flat `import expiry_selector` resolved to Options_Suite's
        shadowing copy -> AttributeError -> swallowed by
        shared.module_registry._suite_modules() -> all 6 vol_suite modules
        silently missing from all_modules() (the dashboard does exactly this
        Tools-first import order). The fix (move-to-front) must keep
        all_modules() complete regardless of import order."""
        code = (
            "import sys; "
            f"sys.path.insert(0, r'{REPO_ROOT}'); "
            + "import Tools.registry; "
            "from shared.module_registry import all_modules; "
            "modules = all_modules(); "
            "suites = {m.suite for m in modules}; "
            "assert 'vol_suite' in suites, (suites, len(modules)); "
            "slugs = {m.slug for m in modules}; "
            "assert {'dealer_exposure', 'dealer_flow', 'position_book', "
            "'dual_book', 'chain_scanner', 'svi_smile'} <= slugs, slugs; "
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
            f"all_modules() lost suite modules under Tools-first import order:\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
        assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# Fakes -- no live ThetaData/network in any of these tests.
# ---------------------------------------------------------------------------


class _FakeTD:
    def close(self):
        pass


# ---------------------------------------------------------------------------
# chain_scanner: fail-loud control flow
# ---------------------------------------------------------------------------


class TestChainScannerRun:
    def _fake_scan_result(self, **overrides):
        defaults = {
            "ticker": "SPY",
            "expiry": "20261016",
            "spot": 650.0,
            "atm_iv_pct": 18.5,
            "rv_match_pct": 16.0,
            "regime": "RICH",
            "verdict": "EDGE DETECTED",
            "edge_candidates": [{"strike": 660.0, "right": "C"}],
            "net_vanna_shares": 1234.0,
            "svi_params": {"sigma_atm": 0.18},
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_happy_path_returns_ok_with_artifacts_and_metrics(
        self, monkeypatch, tmp_path
    ):
        result = self._fake_scan_result()
        strategies_path = tmp_path / "chain_strategies.json"
        strategies_path.write_text("{}", encoding="utf-8")

        def fake_run_chain_scanner(
            ticker, target_years, expiration, output_dir, jump_risk_signal=None
        ):
            return (
                [str(tmp_path / "scan.csv"), str(tmp_path / "scan.png")],
                "interp text",
                result,
            )

        monkeypatch.setattr(
            vs_registry.ocs, "run_chain_scanner", fake_run_chain_scanner
        )

        out = vs_registry._run_chain_scanner(
            {"ticker": "SPY", "expiry": "20261016", "output_dir": str(tmp_path)}
        )

        assert isinstance(out, ModuleResult)
        assert out.status == "ok"
        assert out.metrics["regime"] == "RICH"
        assert out.metrics["verdict"] == "EDGE DETECTED"
        assert out.metrics["edge_candidates_count"] == 1
        assert out.context_patch == {"chain_scanner_result": result}
        kinds = {a.kind for a in out.artifacts}
        assert kinds == {"csv", "png", "json"}
        assert len(out.artifacts) == 3

    def test_scan_failure_does_not_fake_success(self, monkeypatch):
        """Fail-loud: a simulated scan failure must never come back as
        status='ok' (mirrors Task 3's most important dealer_exposure
        test)."""

        def boom(ticker, target_years, expiration, output_dir, jump_risk_signal=None):
            raise RuntimeError("ThetaData chain fetch unavailable: simulated failure")

        monkeypatch.setattr(vs_registry.ocs, "run_chain_scanner", boom)

        out = vs_registry._run_chain_scanner({"ticker": "SPY", "expiry": "20261016"})

        assert out.status == "failed"
        assert out.status != "ok"
        assert "simulated failure" in out.metrics["error"]
        assert out.context_patch is None
        assert out.artifacts == []

    def test_missing_ticker_fails_loud_not_silently(self):
        out = vs_registry._run_chain_scanner({})
        assert out.status == "failed"
        assert "ticker" in out.metrics["error"]

    def test_no_strategies_artifact_written_is_not_an_error(
        self, monkeypatch, tmp_path
    ):
        """chain_strategies.json missing on disk (e.g. a test double that
        skips writing it) is tolerated -- only the CSV/PNG files
        run_chain_scanner actually returned become artifacts."""
        result = self._fake_scan_result(edge_candidates=[])

        def fake_run_chain_scanner(
            ticker, target_years, expiration, output_dir, jump_risk_signal=None
        ):
            return ([str(tmp_path / "scan.csv")], "interp text", result)

        monkeypatch.setattr(
            vs_registry.ocs, "run_chain_scanner", fake_run_chain_scanner
        )

        out = vs_registry._run_chain_scanner(
            {"ticker": "SPY", "expiry": "20261016", "output_dir": str(tmp_path)}
        )

        assert out.status == "ok"
        assert len(out.artifacts) == 1
        assert out.artifacts[0].kind == "csv"


# ---------------------------------------------------------------------------
# svi_smile: mode dispatch across the three call sites
# ---------------------------------------------------------------------------


class TestSviSmileModeDispatch:
    def test_unknown_mode_fails_loud(self):
        out = vs_registry._run_svi_smile(
            {"ticker": "SPY", "expiry": "20261016", "mode": "not_a_real_mode"},
            td=_FakeTD(),
        )
        assert out.status == "failed"
        assert "not_a_real_mode" in out.metrics["error"]

    def test_missing_ticker_fails_loud(self):
        out = vs_registry._run_svi_smile({"mode": "chain_scanner"}, td=_FakeTD())
        assert out.status == "failed"
        assert "ticker" in out.metrics["error"]

    def test_chain_scanner_mode_calls_fit_svi_smile(self, monkeypatch):
        gather_calls = []
        fit_calls = []

        def fake_gather_inputs(td, ticker, expiration):
            gather_calls.append((ticker, expiration))
            return ("FAKE_DF", 651.2, 0.1, 650.0)

        def fake_fit_svi_smile(df, forward, T_years, spot=None):
            fit_calls.append((df, forward, T_years, spot))
            return ("FAKE_DF_OUT", 0.01, -0.02, {"sigma_atm": 0.2})

        monkeypatch.setattr(
            vs_registry, "_svi_chain_scanner_inputs", fake_gather_inputs
        )
        monkeypatch.setattr(vs_registry.ocs, "fit_svi_smile", fake_fit_svi_smile)

        out = vs_registry._run_svi_smile(
            {"ticker": "SPY", "expiry": "20261016", "mode": "chain_scanner"},
            td=_FakeTD(),
        )

        assert out.status == "ok"
        assert out.metrics["mode"] == "chain_scanner"
        assert len(gather_calls) == 1
        assert gather_calls[0] == ("SPY", "20261016")
        assert len(fit_calls) == 1
        assert fit_calls[0] == ("FAKE_DF", 651.2, 0.1, 650.0)
        assert out.metrics["svi_params"] == {"sigma_atm": 0.2}

    def test_exposure_overlay_mode_calls_svi_rp_overlay(self, monkeypatch):
        gather_calls = []
        overlay_calls = []
        fake_overlay = SimpleNamespace(
            ticker="SPY",
            sigma_atm=0.19,
            cheap_strikes=[640.0],
            rich_strikes=[660.0],
            marks=[],
            net_cheap_oi=100.0,
            net_rich_oi=50.0,
            term_structure_flag="contango",
            butterfly_clamped=False,
        )

        def fake_gather_inputs(td, ticker, expiration):
            gather_calls.append((ticker, expiration))
            return ({(650.0, "C"): 0.2}, {(650.0, "C"): 10}, 650.0, 0.1, 0.01)

        def fake_svi_rp_overlay(
            chain_iv, spot, T, oi_by=None, ticker=None, r=None, q=None
        ):
            overlay_calls.append((chain_iv, spot, T, oi_by, ticker, r, q))
            return fake_overlay

        monkeypatch.setattr(
            vs_registry, "_svi_exposure_overlay_inputs", fake_gather_inputs
        )
        monkeypatch.setattr(vs_registry.ebe, "svi_rp_overlay", fake_svi_rp_overlay)

        out = vs_registry._run_svi_smile(
            {"ticker": "SPY", "expiry": "20261016", "mode": "exposure_overlay"},
            td=_FakeTD(),
        )

        assert out.status == "ok"
        assert out.metrics["mode"] == "exposure_overlay"
        assert len(gather_calls) == 1
        assert gather_calls[0] == ("SPY", "20261016")
        assert len(overlay_calls) == 1
        assert overlay_calls[0][1] == 650.0  # spot
        assert overlay_calls[0][2] == 0.1  # T
        assert out.metrics["sigma_atm"] == 0.19
        assert out.metrics["term_structure_flag"] == "contango"

    def test_model_comparison_mode_calls_build_iv_smile_by_model(self, monkeypatch):
        calls = []

        def fake_build(
            ticker,
            td=None,
            expiry=None,
            strike=None,
            option_type="call",
            include_mc=True,
            include_heston=True,
        ):
            calls.append(
                (ticker, expiry, strike, option_type, include_mc, include_heston)
            )
            return {"ticker": ticker, "curves": {}, "meta": {}}

        monkeypatch.setattr(
            vs_registry.smile_by_model, "build_iv_smile_by_model", fake_build
        )

        out = vs_registry._run_svi_smile(
            {
                "ticker": "SPY",
                "expiry": "20261016",
                "mode": "model_comparison",
                "option_type": "put",
                "include_heston": False,
            },
            td=_FakeTD(),
        )

        assert out.status == "ok"
        assert out.metrics["mode"] == "model_comparison"
        assert len(calls) == 1
        ticker, expiry, _strike, option_type, include_mc, include_heston = calls[0]
        assert ticker == "SPY"
        assert expiry == "20261016"
        assert option_type == "put"
        assert include_mc is True
        assert include_heston is False

    def test_default_mode_is_chain_scanner(self, monkeypatch):
        calls = []

        def fake_gather_inputs(td, ticker, expiration):
            calls.append((ticker, expiration))
            return ("FAKE_DF", 651.2, 0.1, 650.0)

        monkeypatch.setattr(
            vs_registry, "_svi_chain_scanner_inputs", fake_gather_inputs
        )
        monkeypatch.setattr(
            vs_registry.ocs,
            "fit_svi_smile",
            lambda df, forward, T_years, spot=None: (df, 0.0, 0.0, None),
        )

        out = vs_registry._run_svi_smile(
            {"ticker": "SPY", "expiry": "20261016"}, td=_FakeTD()
        )

        assert out.status == "ok"
        assert out.metrics["mode"] == "chain_scanner"
        assert len(calls) == 1

    def test_underlying_failure_fails_loud_not_faked_ok(self, monkeypatch):
        def boom(td, ticker, expiration):
            raise RuntimeError("ThetaData snapshot unavailable: simulated failure")

        monkeypatch.setattr(vs_registry, "_svi_chain_scanner_inputs", boom)

        out = vs_registry._run_svi_smile(
            {"ticker": "SPY", "expiry": "20261016", "mode": "chain_scanner"},
            td=_FakeTD(),
        )

        assert out.status == "failed"
        assert out.status != "ok"
        assert "simulated failure" in out.metrics["error"]
        assert out.context_patch is None
