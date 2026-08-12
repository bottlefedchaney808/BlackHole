"""Tests for `shared.summary` — per-suite extractors and `build_run_summary()`.

Quant Console plan, Phase 1 / Task 3
(docs/superpowers/plans/2026-08-01-quant-console.md). Extractors take an
already-parsed `*_result.json` dict and reduce it to the `modules[]` entry
shape validated by `shared.schemas.validate_quant_summary`; `build_run_summary`
scans a run directory for those marker files, calls the matching extractor,
and assembles (but does not write) a `quant_summary.json`-shaped dict.

All fixtures live in shared/tests/fixtures/ — canned `*_result.json` payloads,
including malformed-JSON and missing-field variants, so these tests need no
network and no live suite run.
"""

import json
import shutil
from pathlib import Path

import pytest

from shared.schemas import validate_quant_summary
from shared.summary import (
    _extract_options,
    _extract_sentiment,
    _extract_var,
    _extract_vol,
    build_run_summary,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


# ── _extract_vol ─────────────────────────────────────────────────────────


class TestExtractVol:
    def test_ok_result_yields_ok_status_with_headline_and_metrics(self):
        entry = _extract_vol(_load("vol_result_ok.json"))
        assert entry["status"] == "ok"
        assert entry["headline"]
        assert isinstance(entry["headline"], str)
        assert entry["metrics"]["vol_spread_pts"] == 24.2
        assert entry["metrics"]["NVDA_fair_vol_pct"] == 42.5
        assert entry["metrics"]["QQQ_fair_vol_pct"] == 18.3
        assert entry["warnings"] == []

    def test_ok_result_picks_top_gamma_record_by_abs_dollar_gamma(self):
        # Fixture's second record has dollar_gamma=-2,000,000 -- larger in
        # magnitude than the first record's +1,260,000 -- so it must win.
        entry = _extract_vol(_load("vol_result_ok.json"))
        assert entry["metrics"]["top_gamma_strike"] == 125
        assert entry["metrics"]["top_gamma_dollar"] == -2000000

    def test_error_result_yields_error_status_with_error_as_headline(self):
        entry = _extract_vol(_load("vol_result_error.json"))
        assert entry["status"] == "error"
        assert entry["headline"] == "ThetaData connection timed out"

    def test_missing_vol_surface_key_yields_degraded_not_raise(self):
        entry = _extract_vol(_load("vol_result_missing_field.json"))
        assert entry["status"] == "degraded"
        assert entry["headline"]

    def test_malformed_input_never_raises(self):
        entry = _extract_vol({"status": "ok"})  # no vol_surface at all
        assert entry["status"] == "degraded"

    def test_non_dict_input_never_raises(self):
        entry = _extract_vol("not a dict")
        assert entry["status"] == "degraded"

    def test_result_always_carries_module_id(self):
        entry = _extract_vol(_load("vol_result_ok.json"))
        assert entry["module"] == "vol"


# ── _extract_options ────────────────────────────────────────────────────


class TestExtractOptions:
    def test_ok_result_yields_ok_status_with_price_and_iv(self):
        entry = _extract_options(_load("options_result_ok.json"))
        assert entry["status"] == "ok"
        assert entry["metrics"]["price"] == 12.34
        assert entry["metrics"]["method"] == "CRR"
        assert entry["metrics"]["delta"] == 0.55
        assert "NVDA" in entry["headline"]

    def test_error_result_yields_error_status(self):
        entry = _extract_options(_load("options_result_error.json"))
        assert entry["status"] == "error"
        assert entry["headline"] == "context-mode not wired up for this method"

    def test_missing_price_field_yields_degraded_not_raise(self):
        entry = _extract_options(_load("options_result_missing_field.json"))
        assert entry["status"] == "degraded"

    def test_result_always_carries_module_id(self):
        entry = _extract_options(_load("options_result_ok.json"))
        assert entry["module"] == "options"


# ── _extract_var ─────────────────────────────────────────────────────────


class TestExtractVar:
    def test_ok_result_yields_ok_status_with_var_and_cvar(self):
        entry = _extract_var(_load("var_result_ok.json"))
        assert entry["status"] == "ok"
        assert entry["metrics"]["var"] == 152340.12
        assert entry["metrics"]["cvar"] == 210500.55
        assert entry["metrics"]["confidence"] == 0.99
        assert entry["metrics"]["horizon_days"] == 10

    def test_error_result_yields_error_status(self):
        entry = _extract_var(_load("var_result_error.json"))
        assert entry["status"] == "error"
        assert entry["headline"] == "module hist_sim not implemented in context mode"

    def test_missing_cvar_field_yields_degraded_not_raise(self):
        entry = _extract_var(_load("var_result_missing_field.json"))
        assert entry["status"] == "degraded"

    def test_result_always_carries_module_id(self):
        entry = _extract_var(_load("var_result_ok.json"))
        assert entry["module"] == "var"


# ── _extract_sentiment ───────────────────────────────────────────────────


class TestExtractSentiment:
    def test_ok_result_yields_ok_status_with_ranked_ticker_count(self):
        entry = _extract_sentiment(_load("sentiment_result_ok.json"))
        assert entry["status"] == "ok"
        assert entry["metrics"]["ranked_ticker_count"] == 3
        assert "NVDA" in entry["headline"]

    def test_missing_sentiment_block_yields_degraded_not_raise(self):
        entry = _extract_sentiment(_load("sentiment_result_missing_field.json"))
        assert entry["status"] == "degraded"

    def test_result_always_carries_module_id(self):
        entry = _extract_sentiment(_load("sentiment_result_ok.json"))
        assert entry["module"] == "sentiment"


class TestExtractSentimentMarketSignalsBundle:
    """`sentiment_result.json` has two producers.

    `orchestrator.py::run_market_signals_stage` writes a
    scanners/simulations/direction bundle under the same filename the real
    sentiment-scanner's `--export-context` uses. Before this branch existed
    every unified run's market-signals block fell through to `degraded`.
    """

    def test_bundle_yields_ok_status_and_module_id(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        assert entry["module"] == "sentiment"
        # Fixture is `status: partial` (skew + corr_sim failed) -- partial data
        # is still real data, so it reports `ok` with warnings, not `degraded`.
        assert entry["status"] == "ok"
        assert entry["headline"]

    def test_scanner_metrics_are_flattened_without_double_prefixing(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        metrics = entry["metrics"]
        # `MaxPainScan.max_pain_strike` already carries the scanner name.
        assert metrics["max_pain_strike"] == 180.0
        assert metrics["max_pain_value"] == 41200000.0
        assert metrics["iv_rank_regime"] == "RICH"
        assert metrics["iv_rank_vrp_pct"] == 4.3
        # `UnusualOiScan.oi_change_pct` does *not* start with the scanner key,
        # so it keeps the prefix.
        assert metrics["unusual_oi_oi_change_pct"] == 5.78

    def test_successful_scanner_with_null_error_field_is_not_treated_as_failed(self):
        # Every scanner dataclass carries `error: Optional[str] = None`, so a
        # successful scan still serializes `"error": null` -- a bare
        # `"error" in scan` membership test would mark all four as failed.
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        assert not any("iv_rank" in w for w in entry["warnings"])
        assert "iv_rank_regime" in entry["metrics"]
        assert "iv_rank_error" not in entry["metrics"]

    def test_failed_scanner_becomes_a_warning_and_contributes_no_metrics(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        assert any("skew" in w for w in entry["warnings"])
        assert not any(k.startswith("skew") for k in entry["metrics"])

    def test_simulation_metrics_are_flattened(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        metrics = entry["metrics"]
        assert metrics["mc_sim_terminal_price_mean"] == 209.8
        assert metrics["mc_sim_terminal_price_p5"] == 108.7
        assert metrics["copula_terminal_price_p95"] == 389.4

    def test_failed_simulation_becomes_a_warning(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        assert any("corr_sim" in w for w in entry["warnings"])
        assert not any(k.startswith("corr_sim") for k in entry["metrics"])

    def test_array_and_dict_fields_never_land_in_metrics(self):
        # Task 8's `terminal_price_histogram` (20 bin dicts), corr_sim's
        # matrices, `data_quality`, and the scanners' `pain_profile` /
        # `top_strikes` lists must not be flattened into scalar metrics.
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        for key, value in entry["metrics"].items():
            assert isinstance(value, (int, float, str, bool)), key
        assert "mc_sim_terminal_price_histogram" not in entry["metrics"]
        assert "mc_sim_data_quality" not in entry["metrics"]
        assert "max_pain_pain_profile" not in entry["metrics"]

    def test_bookkeeping_fields_are_not_reported_as_metrics(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        for noise in (
            "mc_sim_suite",
            "mc_sim_status",
            "mc_sim_timestamp",
            "mc_sim_module",
            "iv_rank_ticker",
            "iv_rank_timestamp",
        ):
            assert noise not in entry["metrics"]

    def test_direction_conviction_and_signals_are_surfaced(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        metrics = entry["metrics"]
        assert metrics["direction_conviction"] == "MODERATE"
        assert metrics["direction_score"] == 3
        assert metrics["direction_signal_whale"] is True
        assert metrics["direction_signal_wave3"] is False

    def test_partial_bundle_fixture_produces_warnings(self):
        # The shipped fixture's failures are per-scanner/per-sim, so this only
        # covers `_flatten_bundle_section`'s warnings -- see
        # `test_bundle_level_errors_only_still_warn` for the top-level list.
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        assert entry["warnings"]

    def test_bundle_level_errors_only_still_warn(self):
        # A scanner/var_engine import failure leaves `scanners`/`simulations`
        # empty, so the stage's `partial` status has no per-item error to
        # flatten -- the reason lives only in the top-level `errors` list.
        bundle = {
            "suite": "sentiment",
            "status": "partial",
            "ticker": "AAPL",
            "scanners": {},
            "simulations": {},
            "direction": None,
            "errors": ["scanner import failed: boom"],
        }
        entry = _extract_sentiment(bundle)
        assert entry["status"] == "ok"
        assert any("boom" in w for w in entry["warnings"])

    def test_bundle_level_errors_are_not_double_reported(self):
        # The stage writes per-item failures into BOTH the per-key payload and
        # the top-level `errors` list, using identical wording.
        bundle = {
            "status": "partial",
            "ticker": "AAPL",
            "scanners": {"iv_rank": {"error": "timeout"}},
            "simulations": {"mc_sim": {"var_1yr": 1.5}},
            "direction": {"conviction": "WEAK", "score": 1, "signals": {}},
            "errors": ["iv_rank scanner failed: timeout"],
        }
        entry = _extract_sentiment(bundle)
        assert len([w for w in entry["warnings"] if "timeout" in w]) == 1

    def test_malformed_direction_warns_and_stays_out_of_metrics(self):
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {},
                "direction": ["not", "a", "dict"],
            }
        )
        assert any("direction" in w for w in entry["warnings"])
        assert "direction_conviction" not in entry["metrics"]

    def test_non_scalar_direction_conviction_never_lands_in_metrics(self):
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {},
                "direction": {"conviction": {"nested": "junk"}, "score": 3},
            }
        )
        assert "direction_conviction" not in entry["metrics"]
        for value in entry["metrics"].values():
            assert isinstance(value, (bool, int, float, str))

    def test_all_ok_bundle_reports_full_scanner_and_sim_counts(self):
        bundle = {
            "suite": "sentiment",
            "status": "ok",
            "ticker": "AAPL",
            "scanners": {"iv_rank": {"regime": "FAIR", "error": None}},
            "simulations": {"mc_sim": {"terminal_price_mean": 210.0}},
            "direction": {"conviction": "STRONG", "score": 5, "signals": {}},
        }
        entry = _extract_sentiment(bundle)
        assert entry["status"] == "ok"
        assert entry["warnings"] == []
        assert "1/1" in entry["headline"]

    def test_bundle_status_error_passes_through_as_error(self):
        bundle = {
            "suite": "sentiment",
            "status": "error",
            "ticker": "AAPL",
            "scanners": {"iv_rank": {"error": "boom"}},
            "simulations": {},
            "direction": None,
            "errors": ["scanner import failed: boom"],
        }
        entry = _extract_sentiment(bundle)
        assert entry["status"] == "error"
        assert "boom" in entry["headline"]

    def test_bundle_detected_by_simulations_key_alone(self):
        entry = _extract_sentiment(
            {"status": "ok", "simulations": {"mc_sim": {"var_1yr": 1.5}}}
        )
        assert entry["status"] == "ok"
        assert entry["metrics"]["mc_sim_var_1yr"] == 1.5

    def test_garbage_bundle_still_degrades_instead_of_raising(self):
        entry = _extract_sentiment({"scanners": "not a dict", "simulations": 7})
        assert entry["status"] == "degraded"

    def test_unknown_bundle_status_degrades(self):
        entry = _extract_sentiment({"status": "weird", "scanners": {}})
        assert entry["status"] == "degraded"


class TestExtractSentimentBundleDistributions:
    """Task 11: the sim builders' `terminal_price_histogram` (Task 8) is
    array-shaped, so it never reaches `metrics`; it is surfaced instead as a
    `distributions[]` list the dashboard renders as a bar chart."""

    def test_histograms_become_distributions_with_percentiles(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        dists = {d["label"]: d for d in entry["distributions"]}
        assert set(dists) == {"mc_sim", "copula"}

        mc = dists["mc_sim"]
        assert mc["bins"] == [
            {"low": 60.0, "high": 80.0, "count": 118},
            {"low": 80.0, "high": 100.0, "count": 642},
        ]
        assert mc["percentiles"] == {"p5": 108.7, "p50": 194.2, "p95": 372.5}

    def test_price_histograms_are_labelled_with_their_unit(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        assert all(d["unit"] == "price" for d in entry["distributions"])

    def test_corr_sim_histogram_keeps_its_portfolio_value_unit(self):
        # corr_sim sums positions across up to three tickers, so its bins are
        # terminal *portfolio values*, not one ticker's price -- the unit must
        # survive into the module entry so the renderer cannot mislabel it.
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {
                    "corr_sim": {
                        "portfolio_value": 500.0,
                        "terminal_price_histogram": [
                            {"low": 400.0, "high": 500.0, "count": 12}
                        ],
                        "histogram_unit": "portfolio_value",
                    }
                },
            }
        )
        (dist,) = entry["distributions"]
        assert dist["label"] == "corr_sim"
        assert dist["unit"] == "portfolio_value"

    def test_missing_histogram_unit_is_reported_as_unknown_not_price(self):
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {
                    "mystery": {
                        "terminal_price_histogram": [
                            {"low": 1.0, "high": 2.0, "count": 3}
                        ]
                    }
                },
            }
        )
        assert entry["distributions"][0]["unit"] == "unknown"

    def test_median_falls_back_to_mean_when_absent(self):
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {
                    "mc_sim": {
                        "terminal_price_mean": 210.0,
                        "terminal_price_histogram": [
                            {"low": 1.0, "high": 2.0, "count": 3}
                        ],
                        "histogram_unit": "price",
                    }
                },
            }
        )
        assert entry["distributions"][0]["percentiles"]["p50"] == pytest.approx(210.0)

    def test_percentiles_are_omitted_rather_than_faked_when_unavailable(self):
        # corr_sim / price_dist publish a histogram but no terminal_price_p*
        # fields -- an absent percentile must not be invented from the bins.
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {
                    "corr_sim": {
                        "terminal_price_histogram": [
                            {"low": 1.0, "high": 2.0, "count": 3}
                        ],
                        "histogram_unit": "portfolio_value",
                    }
                },
            }
        )
        assert entry["distributions"][0]["percentiles"] == {}

    def test_sims_without_a_histogram_contribute_no_distribution(self):
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {"mc_sim": {"terminal_price_mean": 210.0}},
            }
        )
        assert entry["distributions"] == []

    def test_failed_sim_contributes_no_distribution(self):
        entry = _extract_sentiment(
            {
                "status": "partial",
                "scanners": {},
                "simulations": {
                    "corr_sim": {
                        "error": "no peers",
                        "terminal_price_histogram": [
                            {"low": 1.0, "high": 2.0, "count": 3}
                        ],
                    }
                },
            }
        )
        assert entry["distributions"] == []

    def test_malformed_bins_are_dropped_without_raising(self):
        entry = _extract_sentiment(
            {
                "status": "ok",
                "scanners": {},
                "simulations": {
                    "mc_sim": {
                        "terminal_price_histogram": [
                            {"low": 1.0, "high": 2.0, "count": 3},
                            "not a bin",
                            {"low": None, "high": 4.0, "count": 5},
                        ],
                        "histogram_unit": "price",
                    },
                    "copula": {"terminal_price_histogram": "not a list"},
                },
            }
        )
        (dist,) = entry["distributions"]
        assert dist["label"] == "mc_sim"
        assert dist["bins"] == [{"low": 1.0, "high": 2.0, "count": 3}]

    def test_distributions_key_is_present_even_when_empty(self):
        entry = _extract_sentiment(
            {"status": "ok", "scanners": {}, "simulations": {}}
        )
        assert entry["distributions"] == []

    def test_error_bundle_entry_also_carries_a_distributions_key(self):
        entry = _extract_sentiment(
            {
                "status": "error",
                "scanners": {},
                "simulations": {},
                "errors": ["boom"],
            }
        )
        assert entry["status"] == "error"
        assert entry["distributions"] == []

    def test_bundle_entry_with_distributions_is_schema_valid(self):
        entry = _extract_sentiment(_load("sentiment_result_market_signals.json"))
        validate_quant_summary(
            {
                "schema_version": 1,
                "run_id": "run-1",
                "ticker": "NVDA",
                "created_at_utc": "2026-08-12T00:00:00Z",
                "modules": [entry],
            }
        )


# ── build_run_summary ────────────────────────────────────────────────────


def _populate(run_dir: Path, files: dict) -> None:
    """Copy named fixtures into run_dir under their real marker filenames."""
    for marker_name, fixture_name in files.items():
        shutil.copy(FIXTURES / fixture_name, run_dir / marker_name)


class TestBuildRunSummary:
    def test_assembles_schema_valid_summary_from_all_four_markers(self, tmp_path):
        _populate(
            tmp_path,
            {
                "vol_result.json": "vol_result_ok.json",
                "options_result.json": "options_result_ok.json",
                "var_result.json": "var_result_ok.json",
                "sentiment_result.json": "sentiment_result_ok.json",
            },
        )

        summary = build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")

        assert summary["schema_version"] == 1
        assert summary["run_id"] == "run-abc"
        assert summary["ticker"] == "NVDA"
        assert summary["created_at_utc"]
        assert len(summary["modules"]) == 4
        validate_quant_summary(summary)  # raises on any schema violation

        by_module = {m["module"]: m for m in summary["modules"]}
        assert by_module["vol"]["status"] == "ok"
        assert by_module["options"]["status"] == "ok"
        assert by_module["var"]["status"] == "ok"
        assert by_module["sentiment"]["status"] == "ok"
        assert by_module["vol"]["source_result"] == "vol_result.json"

    def test_market_signals_bundle_marker_yields_ok_schema_valid_entry(self, tmp_path):
        _populate(
            tmp_path,
            {"sentiment_result.json": "sentiment_result_market_signals.json"},
        )

        summary = build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")

        validate_quant_summary(summary)
        entry = summary["modules"][0]
        assert entry["module"] == "sentiment"
        assert entry["status"] == "ok"
        assert entry["source_result"] == "sentiment_result.json"
        assert entry["metrics"]["max_pain_strike"] == 180.0

    def test_does_not_write_any_file_to_run_dir(self, tmp_path):
        _populate(tmp_path, {"vol_result.json": "vol_result_ok.json"})
        before = sorted(p.name for p in tmp_path.iterdir())

        build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")

        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after

    def test_missing_marker_file_produces_no_entry_for_that_module(self, tmp_path):
        _populate(tmp_path, {"vol_result.json": "vol_result_ok.json"})

        summary = build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")

        modules = [m["module"] for m in summary["modules"]]
        assert modules == ["vol"]
        validate_quant_summary(summary)

    def test_malformed_json_marker_yields_degraded_entry_not_a_crash(self, tmp_path):
        _populate(tmp_path, {"vol_result.json": "vol_result_malformed.json"})

        summary = build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")

        assert len(summary["modules"]) == 1
        assert summary["modules"][0]["status"] == "degraded"
        assert summary["modules"][0]["module"] == "vol"
        validate_quant_summary(summary)

    def test_empty_run_dir_yields_empty_but_schema_valid_modules_list(self, tmp_path):
        summary = build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")
        assert summary["modules"] == []
        validate_quant_summary(summary)

    def test_error_status_marker_propagates_as_error_module_entry(self, tmp_path):
        _populate(tmp_path, {"var_result.json": "var_result_error.json"})

        summary = build_run_summary(tmp_path, run_id="run-abc", ticker="NVDA")

        assert summary["modules"][0]["status"] == "error"
        validate_quant_summary(summary)

    def test_accepts_str_run_dir_as_well_as_path(self, tmp_path):
        _populate(tmp_path, {"vol_result.json": "vol_result_ok.json"})

        summary = build_run_summary(str(tmp_path), run_id="run-abc", ticker="NVDA")

        assert len(summary["modules"]) == 1


class TestBuildRunSummaryRunnableGate:
    """`module_registry` (optional -- Task 5's `MODULE_REGISTRY` shape) drives
    the runnable-gate short-circuit: a module with `runnable=False` never gets
    its result file read/extracted, it goes straight to `status: unsupported`.
    """

    def test_non_runnable_module_short_circuits_to_unsupported(self, tmp_path):
        # Deliberately populate options_result.json with an *ok* payload to
        # prove the gate skips extraction entirely rather than merely
        # overriding the outcome after the fact.
        _populate(tmp_path, {"options_result.json": "options_result_ok.json"})
        registry = [
            {
                "id": "options",
                "name": "Options",
                "suite": "options",
                "focus": "pricing",
                "runnable": False,
            },
        ]

        summary = build_run_summary(
            tmp_path, run_id="run-abc", ticker="NVDA", module_registry=registry
        )

        assert len(summary["modules"]) == 1
        entry = summary["modules"][0]
        assert entry["module"] == "options"
        assert entry["status"] == "unsupported"
        validate_quant_summary(summary)

    def test_non_runnable_module_yields_unsupported_even_without_result_file(
        self, tmp_path
    ):
        registry = [
            {
                "id": "options",
                "name": "Options",
                "suite": "options",
                "focus": "pricing",
                "runnable": False,
            },
        ]

        summary = build_run_summary(
            tmp_path, run_id="run-abc", ticker="NVDA", module_registry=registry
        )

        assert len(summary["modules"]) == 1
        assert summary["modules"][0]["status"] == "unsupported"

    def test_runnable_module_extracts_normally_through_registry(self, tmp_path):
        _populate(tmp_path, {"vol_result.json": "vol_result_ok.json"})
        registry = [
            {
                "id": "vol",
                "name": "Vol",
                "suite": "vol",
                "focus": "vol surface",
                "runnable": True,
            },
        ]

        summary = build_run_summary(
            tmp_path, run_id="run-abc", ticker="NVDA", module_registry=registry
        )

        assert summary["modules"][0]["status"] == "ok"

    def test_registry_restricts_module_set_to_its_own_entries(self, tmp_path):
        # var_result.json exists on disk but 'var' isn't in the registry --
        # only registry-listed modules are considered when a registry is given.
        _populate(
            tmp_path,
            {
                "vol_result.json": "vol_result_ok.json",
                "var_result.json": "var_result_ok.json",
            },
        )
        registry = [
            {
                "id": "vol",
                "name": "Vol",
                "suite": "vol",
                "focus": "vol surface",
                "runnable": True,
            },
        ]

        summary = build_run_summary(
            tmp_path, run_id="run-abc", ticker="NVDA", module_registry=registry
        )

        assert [m["module"] for m in summary["modules"]] == ["vol"]
