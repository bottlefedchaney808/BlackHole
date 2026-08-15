import json
from pathlib import Path

import pytest

from Vol_Suite.dealer_exposure_universe import (
    DTE_STRATA,
    EVENT_HABITATS,
    PROBE_CHECKS,
    Candidate,
    ProbeResult,
    build_manifest,
    held_pairs_from_paths,
    normalize_candidate,
    validate_probe_result,
)


def test_normalize_candidate_is_canonical_and_excludes_reference_expansion_pool():
    candidate = normalize_candidate(
        {"ticker": " $aapl ", "sector": "Information Technology", "asset_type": "equity"},
        selection_date="2026-08-15",
        source_list="point-in-time-static",
    )
    assert candidate == Candidate(
        ticker="AAPL",
        sector="Information Technology",
        asset_type="equity",
        selection_date="2026-08-15",
        source_list="point-in-time-static",
        reference_family=None,
    )
    with pytest.raises(ValueError, match="reference"):
        normalize_candidate({"ticker": "SPY", "reference_family": "SPY"})


def test_held_pairs_scan_manifest_records_and_seed_corpora(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"unique_days": [{"day": "20260128", "families": ["SPY", "QQQ"]}]}),
        encoding="utf-8",
    )
    records = tmp_path / "records"
    records.mkdir()
    (records / "20260202.json").write_text(
        json.dumps([{"date": "20260202", "ticker": "AAPL"}]), encoding="utf-8"
    )
    (tmp_path / "seed_data_NVDA_20260817_short.json").write_text("{}", encoding="utf-8")
    assert held_pairs_from_paths([tmp_path]) == {
        ("SPY", "2026-01-28"),
        ("QQQ", "2026-01-28"),
        ("AAPL", "2026-02-02"),
        ("NVDA", "2026-08-17"),
    }


def test_probe_schema_requires_all_checks_and_never_allows_imputed_zero():
    result = ProbeResult(
        ticker="AAPL",
        day="2026-08-17",
        expiry="2026-08-21",
        dte=4,
        status="PASS",
        checks={name: "PASS" for name in PROBE_CHECKS},
        reasons=[],
        imputed_zero=False,
    )
    assert validate_probe_result(result) == result
    bad = result.__class__(**{**result.__dict__, "checks": {PROBE_CHECKS[0]: "PASS"}, "imputed_zero": True})
    with pytest.raises(ValueError):
        validate_probe_result(bad)


def test_manifest_enforces_held_exclusion_caps_quotas_and_stable_ordering():
    held = {("AAPL", "2026-08-17"), ("MSFT", "2026-08-18")}
    candidates = [
        {"ticker": "MSFT", "day": "2026-08-18", "sector": "Technology", "dte": 5, "event_habitat": "NONE"},
        {"ticker": "AAPL", "day": "2026-08-17", "sector": "Technology", "dte": 2, "event_habitat": "NONE"},
        {"ticker": "XLE", "day": "2026-08-19", "sector": "Energy", "dte": 9, "event_habitat": "FOMC"},
        {"ticker": "JPM", "day": "2026-08-20", "sector": "Financials", "dte": 6, "event_habitat": "NONE"},
    ]
    manifest = build_manifest(candidates, held_pairs=held, intended_units=2, selection_date="2026-08-15")
    assert [(u.ticker, u.day) for u in manifest.units] == [("XLE", "2026-08-19"), ("JPM", "2026-08-20")]
    assert manifest.exclusions["AAPL|2026-08-17"] == "held_ticker_day"
    assert manifest.quota_schema["dte_strata"] == [list(s) for s in DTE_STRATA]
    assert manifest.quota_schema["event_habitat_target"] == pytest.approx(1 / 3)
    assert manifest.quota_schema["event_habitats"] == list(EVENT_HABITATS)


def test_dte_zero_and_out_of_range_are_explicit_exclusions():
    manifest = build_manifest(
        [{"ticker": "AAPL", "day": "2026-08-17", "sector": "Technology", "dte": 0}],
        intended_units=1,
    )
    assert manifest.units == ()
    assert manifest.exclusions["AAPL|2026-08-17"] == "zero_dte"


def test_sector_and_ticker_caps_are_checked_on_unique_units():
    candidates = [
        {"ticker": "A", "day": "2026-08-01", "sector": "Tech", "dte": 2},
        {"ticker": "B", "day": "2026-08-02", "sector": "Tech", "dte": 2},
        {"ticker": "C", "day": "2026-08-03", "sector": "Tech", "dte": 2},
        {"ticker": "A", "day": "2026-08-04", "sector": "Health", "dte": 2},
    ]
    manifest = build_manifest(candidates, intended_units=4, selection_date="2026-08-15")
    assert len(manifest.units) == 1
    assert any(reason == "sector_cap" for reason in manifest.exclusions.values())
    assert any(reason == "ticker_cap" for reason in manifest.exclusions.values())


def test_manifest_serialization_is_deterministic():
    rows = [{"ticker": "IBM", "day": "2026-08-02", "sector": "ETF", "dte": 4}]
    assert build_manifest(rows, selection_date="2026-08-15").to_dict() == build_manifest(list(reversed(rows)), selection_date="2026-08-15").to_dict()


@pytest.mark.unit
def test_no_network_dependency_in_contract_module():
    assert PROBE_CHECKS == (
        "chain_listing", "historical_greeks_iv", "open_interest", "spot_ohlc",
        "timestamp_granularity", "expiry_dte", "post_window_returns",
    )
    assert DTE_STRATA == ((1, 3), (4, 7), (8, 10))
    assert EVENT_HABITATS == ("FOMC", "EARNINGS", "OPEX")
    assert Path("Vol_Suite/dealer_exposure_universe.py").exists()


def test_probe_status_values_are_closed():
    result = ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "HARD_GAP", {n: "HARD_GAP" for n in PROBE_CHECKS}, ["missing"], False)
    assert validate_probe_result(result).status == "HARD_GAP"
    with pytest.raises(ValueError):
        validate_probe_result(result.__class__(**{**result.__dict__, "status": "UNKNOWN"}))


def test_candidate_requires_source_and_selection_date():
    with pytest.raises(ValueError):
        normalize_candidate({"ticker": "AAPL", "sector": "Tech"})


def test_candidate_day_clustering_counts_a_date_once():
    manifest = build_manifest([
        {"ticker": "A", "day": "2026-08-01", "sector": "Tech", "dte": 2},
        {"ticker": "B", "day": "2026-08-01", "sector": "Health", "dte": 2},
    ], intended_units=2, selection_date="2026-08-15")
    assert manifest.unique_days == ("2026-08-01",)
    assert sum(manifest.sector_counts.values()) == 2


def test_probe_requires_dte_stratum_and_positive_dte():
    result = ProbeResult("AAPL", "2026-08-17", "2026-08-21", 0, "INELIGIBLE", {n: "INELIGIBLE" for n in PROBE_CHECKS}, ["zero DTE"], False)
    with pytest.raises(ValueError):
        validate_probe_result(result)


def test_manifest_records_all_candidate_exclusions():
    manifest = build_manifest([
        {"ticker": "AAPL", "day": "2026-08-17", "sector": "Tech", "dte": 2},
        {"ticker": "AAPL", "day": "2026-08-17", "sector": "Tech", "dte": 2},
    ], held_pairs={("AAPL", "2026-08-17")}, intended_units=2, selection_date="2026-08-15")
    assert len(manifest.exclusions) == 2
    assert set(manifest.exclusions) == {"AAPL|2026-08-17", "AAPL|2026-08-17#1"}


def test_manifest_probe_results_are_serializable():
    result = ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False)
    manifest = build_manifest([], probe_results=[result])
    assert manifest.to_dict()["probe_results"][0]["status"] == "PASS"


def test_build_manifest_does_not_mutate_input():
    row = {"ticker": " AAPL ", "day": "2026-08-17", "sector": "Tech", "dte": 4}
    original = dict(row)
    build_manifest([row], selection_date="2026-08-15")
    assert row == original


def test_unknown_event_is_rejected():
    with pytest.raises(ValueError):
        build_manifest([{"ticker": "AAPL", "day": "2026-08-17", "sector": "Tech", "dte": 4, "event_habitat": "SURPRISE"}], selection_date="2026-08-15")


def test_probe_result_reasons_are_required_for_nonpass():
    result = ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "INELIGIBLE", {n: "INELIGIBLE" for n in PROBE_CHECKS}, [], False)
    with pytest.raises(ValueError):
        validate_probe_result(result)


def test_manifest_caps_are_fraction_of_intended_units():
    manifest = build_manifest([], intended_units=10)
    assert manifest.quota_schema["sector_cap_fraction"] == 0.20
    assert manifest.quota_schema["ticker_cap_fraction"] == 0.10
    assert manifest.quota_schema["event_habitat_target"] == pytest.approx(1 / 3)


def test_probe_result_to_dict_has_closed_schema():
    result = ProbeResult("AAPL", "2026-08-17", "2026-08-21", 4, "PASS", {n: "PASS" for n in PROBE_CHECKS}, [], False)
    assert set(result.to_dict()) == {"ticker", "day", "expiry", "dte", "status", "checks", "reasons", "imputed_zero"}
