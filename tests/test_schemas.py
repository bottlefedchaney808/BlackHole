"""Tests for `shared.schemas.validate_quant_summary`.

`quant_summary.json` is the new (Phase 1, Task 2 of the Quant Console plan)
sixth-plus-one inter-suite artifact: one structured summary per orchestrator/
suite run, written by `shared/summary.py::build_run_summary()` (a later task)
and rendered by the dashboard's `/quant` module-card view. This module only
covers the schema validator -- the extractor/writer land in later tasks.

Mirrors the style of the five existing validators' tests (see
`tests/test_suite_validation.py`'s payload-factory pattern): a minimal-but-
valid payload factory, then targeted mutations per failure case.
"""

import pytest

from shared.schemas import (
    QUANT_SUMMARY_MODULE_STATUSES,
    QUANT_SUMMARY_SCHEMA_VERSION,
    validate_quant_summary,
)

TS = "2026-08-01T12:00:00Z"


# ── payload factory ──────────────────────────────────────────────────────

def quant_summary_payload(modules=None, **over):
    """A minimal but schema-valid quant_summary.json payload."""
    payload = {
        "schema_version": QUANT_SUMMARY_SCHEMA_VERSION,
        "run_id": "run-20260801-120000",
        "ticker": "NVDA",
        "created_at_utc": TS,
        "modules": modules if modules is not None else [module_entry()],
    }
    payload.update(over)
    return payload


def module_entry(**over):
    """A minimal but schema-valid `modules[]` entry."""
    entry = {
        "module": "vol",
        "status": "ok",
        "headline": "IV rich vs. fair vol by 4.2pts",
        "metrics": {"vol_spread_pts": 4.2},
        "warnings": [],
        "source_result": "vol_result.json",
    }
    entry.update(over)
    return entry


# ── valid payload ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_valid_payload_passes():
    validate_quant_summary(quant_summary_payload())  # raises on violation


@pytest.mark.unit
def test_valid_payload_with_multiple_modules_of_every_status_passes():
    modules = [
        module_entry(module="vol", status="ok"),
        module_entry(module="options", status="unsupported"),
        module_entry(module="var", status="degraded"),
        module_entry(module="sentiment", status="error"),
    ]
    validate_quant_summary(quant_summary_payload(modules=modules))


# ── top-level required fields ───────────────────────────────────────────

@pytest.mark.unit
def test_missing_schema_version_fails():
    payload = quant_summary_payload()
    del payload["schema_version"]
    with pytest.raises(ValueError, match="schema_version"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_wrong_schema_version_fails():
    payload = quant_summary_payload(schema_version=2)
    with pytest.raises(ValueError, match="schema_version"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_missing_run_id_fails():
    payload = quant_summary_payload()
    del payload["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_empty_run_id_fails():
    payload = quant_summary_payload(run_id="   ")
    with pytest.raises(ValueError, match="run_id"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_missing_ticker_fails():
    payload = quant_summary_payload()
    del payload["ticker"]
    with pytest.raises(ValueError, match="ticker"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_missing_created_at_utc_fails():
    payload = quant_summary_payload()
    del payload["created_at_utc"]
    with pytest.raises(ValueError, match="created_at_utc"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_missing_modules_fails():
    payload = quant_summary_payload()
    del payload["modules"]
    with pytest.raises(ValueError, match="modules"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_modules_not_a_list_fails():
    payload = quant_summary_payload(modules={"module": "vol"})
    with pytest.raises(ValueError, match="modules"):
        validate_quant_summary(payload)


@pytest.mark.unit
def test_not_a_dict_fails():
    with pytest.raises(ValueError, match="object"):
        validate_quant_summary(["not", "a", "dict"])


# ── modules[] entry validation ──────────────────────────────────────────

@pytest.mark.unit
def test_module_status_outside_enum_fails():
    modules = [module_entry(status="pending")]
    with pytest.raises(ValueError, match="status"):
        validate_quant_summary(quant_summary_payload(modules=modules))


@pytest.mark.unit
def test_module_status_enum_matches_documented_four_values():
    assert QUANT_SUMMARY_MODULE_STATUSES == ("ok", "error", "degraded", "unsupported")


@pytest.mark.unit
def test_modules_entry_missing_module_field_fails():
    entry = module_entry()
    del entry["module"]
    with pytest.raises(ValueError, match="module"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_missing_status_field_fails():
    entry = module_entry()
    del entry["status"]
    with pytest.raises(ValueError, match="status"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_missing_headline_fails():
    entry = module_entry()
    del entry["headline"]
    with pytest.raises(ValueError, match="headline"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_missing_metrics_fails():
    entry = module_entry()
    del entry["metrics"]
    with pytest.raises(ValueError, match="metrics"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_missing_warnings_fails():
    entry = module_entry()
    del entry["warnings"]
    with pytest.raises(ValueError, match="warnings"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_missing_source_result_fails():
    entry = module_entry()
    del entry["source_result"]
    with pytest.raises(ValueError, match="source_result"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_not_an_object_fails():
    with pytest.raises(ValueError, match=r"modules\[0\]"):
        validate_quant_summary(quant_summary_payload(modules=["not-an-object"]))


@pytest.mark.unit
def test_modules_entry_metrics_not_an_object_fails():
    entry = module_entry(metrics=["not", "a", "dict"])
    with pytest.raises(ValueError, match="metrics"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_warnings_not_a_list_of_strings_fails():
    entry = module_entry(warnings="basket weights defaulted to 1.0")
    with pytest.raises(ValueError, match="warnings"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_warnings_with_non_string_element_fails():
    entry = module_entry(warnings=["ok", 42])
    with pytest.raises(ValueError, match="warnings"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_headline_must_be_string():
    entry = module_entry(headline=12345)
    with pytest.raises(ValueError, match="headline"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_modules_entry_source_result_must_be_string():
    entry = module_entry(source_result=None)
    with pytest.raises(ValueError, match="source_result"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_empty_modules_list_is_valid():
    # A run where every module short-circuited before extraction (or the run
    # itself failed before any suite ran) still produces a schema-valid
    # summary with an empty modules list -- this is not the same failure mode
    # as a missing/malformed `modules` key.
    validate_quant_summary(quant_summary_payload(modules=[]))


@pytest.mark.unit
def test_second_modules_entry_error_is_identified_by_index():
    modules = [module_entry(), module_entry(status="not-a-real-status")]
    with pytest.raises(ValueError, match=r"modules\[1\]"):
        validate_quant_summary(quant_summary_payload(modules=modules))


@pytest.mark.unit
def test_missing_schema_version_warns_and_defaults_to_v1_treatment():
    # Matches the other five validators' backward-compatibility path: a
    # missing schema_version warns rather than raising outright, but the
    # required-field check below it (run_id, etc.) still applies.
    payload = quant_summary_payload()
    del payload["schema_version"]
    with pytest.raises(ValueError):
        with pytest.warns(UserWarning, match="schema_version"):
            validate_quant_summary(payload)


# ── modules[].distributions (Task 11) ────────────────────────────────────

def _distribution(**over):
    """A minimal but schema-valid `modules[].distributions[]` entry."""
    dist = {
        "label": "mc_sim",
        "unit": "price",
        "bins": [{"low": 60.0, "high": 80.0, "count": 118}],
        "percentiles": {"p5": 108.7, "p50": 194.2, "p95": 372.5},
    }
    dist.update(over)
    return dist


def _with_distributions(*dists):
    return quant_summary_payload(modules=[module_entry(distributions=list(dists))])


def _distribution_payload_with_unit(unit):
    return _with_distributions(_distribution(unit=unit))


@pytest.mark.unit
def test_modules_entry_distributions_is_optional():
    entry = module_entry()
    assert "distributions" not in entry
    validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_valid_distributions_pass():
    validate_quant_summary(_with_distributions(_distribution()))


@pytest.mark.unit
def test_portfolio_value_unit_is_accepted():
    validate_quant_summary(_distribution_payload_with_unit("portfolio_value"))


@pytest.mark.unit
def test_distributions_must_be_a_list():
    entry = module_entry(distributions={"label": "mc_sim"})
    with pytest.raises(ValueError, match="distributions"):
        validate_quant_summary(quant_summary_payload(modules=[entry]))


@pytest.mark.unit
def test_distribution_entry_missing_label_fails():
    dist = _distribution()
    del dist["label"]
    with pytest.raises(ValueError, match="label"):
        validate_quant_summary(_with_distributions(dist))


@pytest.mark.unit
def test_distribution_entry_missing_bins_fails():
    dist = _distribution()
    del dist["bins"]
    with pytest.raises(ValueError, match="bins"):
        validate_quant_summary(_with_distributions(dist))


@pytest.mark.unit
def test_distribution_bins_must_be_a_list_of_objects():
    with pytest.raises(ValueError, match="bins"):
        validate_quant_summary(_with_distributions(_distribution(bins=["not a bin"])))


@pytest.mark.unit
def test_distribution_unit_enum_is_enforced():
    # A bogus unit must not slip through: the renderer picks between
    # "terminal price" and "terminal portfolio value" labelling from it, so an
    # unrecognized value would silently mislabel corr_sim's chart.
    with pytest.raises(ValueError, match="unit"):
        validate_quant_summary(_distribution_payload_with_unit("dollars-ish"))


@pytest.mark.unit
def test_distribution_percentiles_must_be_an_object_of_numbers():
    with pytest.raises(ValueError, match="percentiles"):
        validate_quant_summary(
            _with_distributions(_distribution(percentiles={"p50": "high-ish"}))
        )
