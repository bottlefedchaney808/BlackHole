"""Offline end-to-end runner for the Dealer-Exposure-Dev evidence loop.

This module is orchestration only: acquisition, canonical-input construction,
comparison validation, and Task 4 estimation remain owned by their respective
modules.  Adapters are injected so this runner never acquires network data.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

try:
    from . import run_task4_evaluation
    from .dealer_exposure_expansion import compare_expansion_common_input
    from .run_live_vs_expiry_book_common_input import (
        ComparisonInvalid,
        make_canonical_input,
        validate_causal_eligibility,
    )
except ImportError:
    import run_task4_evaluation
    from dealer_exposure_expansion import compare_expansion_common_input
    from run_live_vs_expiry_book_common_input import (
        ComparisonInvalid,
        make_canonical_input,
        validate_causal_eligibility,
    )

# Only a completed Task 4 decision is a successful CLI result.  In particular,
# INDETERMINATE is deliberately not a successful exit, even though it is a
# valid scientific conclusion.
COMPLETED_STATUSES = {"BETTER", "WORSE"}
STATUSES = COMPLETED_STATUSES | {
    "INDETERMINATE",
    "COMPARISON_INVALID",
    "CAUSAL_BLOCKED",
    "HARD_GAP",
    "FAILED_EXECUTION",
}


def _default_task4_evaluator(records: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Compose Task 4 from the validated Task 3 records on the CLI path."""
    return run_task4_evaluation.evaluate_task4(records)


def _unique_days(units: Iterable[Mapping[str, Any]]) -> set[str]:
    days = {str(unit.get("calendar_day", "")) for unit in units}
    if "" in days:
        raise ValueError("every causal unit must declare calendar_day")
    return days


def _cluster_metadata(units: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    clusters: dict[str, list[str]] = {}
    for unit in units:
        day = str(unit.get("calendar_day", ""))
        ticker = str(unit.get("ticker", ""))
        clusters.setdefault(day, []).append(ticker)
    return {
        day: {
            "tickers": sorted(set(tickers)),
            "n_tickers": len(set(tickers)),
            "aggregation_rule": "preserve_ticker_values_v1",
            "independent_unit": "calendar_day",
        }
        for day, tickers in sorted(clusters.items())
    }


def _load(value: Any) -> Any:
    if isinstance(value, (str, Path)):
        return json.loads(Path(value).read_text(encoding="utf-8"))
    return value


def _unit_input(unit: Mapping[str, Any]):
    source = unit.get("canonical_input", unit)
    if not isinstance(source, Mapping):
        raise ComparisonInvalid("canonical input is missing", structured_invalid=True)
    rows = source.get("rows", source.get("chain_rows"))
    source_hashes = source.get("source_hashes")
    if rows is None or source_hashes is None:
        raise ComparisonInvalid(
            "canonical rows/source hashes are missing", structured_invalid=True
        )
    return make_canonical_input(
        str(source.get("ticker", unit.get("ticker", ""))),
        str(source.get("calendar_day", unit.get("calendar_day", ""))),
        str(source.get("expiry", unit.get("expiry", ""))),
        int(source.get("dte", unit.get("dte", 0))),
        float(source.get("spot", source.get("spot_price"))),
        str(source.get("iv_source_ts", unit.get("iv_source_ts", ""))),
        rows,
        source_hashes,
        str(source.get("chain_source", "offline-canonical")),
    )


def _status_from_evaluation(evaluation: Mapping[str, Any] | None) -> str:
    if not evaluation:
        return "INDETERMINATE"
    declared = str(evaluation.get("status", "")).upper()
    if declared in {
        "COMPARISON_INVALID",
        "CAUSAL_BLOCKED",
        "HARD_GAP",
        "FAILED_EXECUTION",
    }:
        return declared
    decision = str(evaluation.get("decision", "INDETERMINATE")).upper()
    return (
        decision
        if decision in {"BETTER", "WORSE", "INDETERMINATE"}
        else "INDETERMINATE"
    )


def run_universe_causal_comparison(
    manifest: Mapping[str, Any] | str | Path,
    evidence: Mapping[str, Any] | str | Path,
    *,
    live_runner: Callable[[Any], Any] | None = None,
    new_runner: Callable[[Any], Any] | None = None,
    task4_evaluator: Callable[[Iterable[Mapping[str, Any]]], Mapping[str, Any]]
    | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the complete offline pipeline and emit one auditable verdict.

    ``evidence`` must contain Task 3 canonical units and the persisted registry.
    The causal gate is evaluated before adapters are called; therefore a
    partial/mixed PRE_WINDOW corpus cannot produce a headline comparison.
    """
    manifest = _load(manifest)
    evidence = _load(evidence)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "runner": "run_universe_causal_comparison",
        "status": "COMPARISON_INVALID",
        "network_fetch_allowed": False,
        "comparisons": [],
        "same_day_units": {},
    }
    if not isinstance(manifest, Mapping) or not isinstance(evidence, Mapping):
        artifact["reason"] = "manifest and evidence mappings are required"
        return _write(artifact, output_path)
    units = evidence.get("units")
    registry = evidence.get("artifact_registry")
    if not isinstance(units, list) or not isinstance(registry, Mapping):
        artifact["reason"] = "missing canonical units or persisted artifact registry"
        return _write(artifact, output_path)
    try:
        units_days = _unique_days(units)
        declared_days = manifest.get("intended_unique_day_denominator")
        if (
            isinstance(declared_days, bool)
            or not isinstance(declared_days, int)
            or declared_days < 0
        ):
            raise ValueError(
                "intended_unique_day_denominator must be a non-negative integer"
            )
        if declared_days != len(units_days):
            artifact.update(
                status="COMPARISON_INVALID",
                reason="intended_unique_day_denominator does not match unique calendar days",
                intended_unique_day_denominator=declared_days,
                observed_unique_calendar_days=len(units_days),
            )
            return _write(artifact, output_path)
    except (TypeError, ValueError) as exc:
        artifact.update(
            status="COMPARISON_INVALID",
            reason=f"calendar-day denominator failure: {exc}",
        )
        return _write(artifact, output_path)

    artifact["same_day_units"] = _cluster_metadata(units)
    artifact["unique_calendar_days"] = sorted(units_days)
    # The causal validator counts ticker×day evidence records, while the
    # declared denominator above counts independent calendar-day clusters.
    # Keep those quantities explicit and never pass a ticker row as an
    # independent Task 3 corpus.
    try:
        causal = validate_causal_eligibility(
            units,
            intended_units=len(units),
            intended_corpus_manifest={"units": manifest.get("units", units)},
            artifact_registry=registry,
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        artifact.update(
            status="COMPARISON_INVALID", reason=f"causal evidence/schema failure: {exc}"
        )
        return _write(artifact, output_path)
    artifact["causal_gate"] = causal
    if causal.get("causal_status") != "CAUSAL_ELIGIBLE":
        artifact.update(
            status="CAUSAL_BLOCKED",
            reason="100% PRE_WINDOW coverage and registry validation are required",
        )
        return _write(artifact, output_path)

    # Task 3's adapter accepts one CanonicalInput, not a clustered universe.
    # Refuse rather than silently running one comparison per ticker/day with
    # intended_units=1.  A future corpus adapter can replace this explicit
    # blocked branch without changing the causal denominator contract.
    if len(units) != 1:
        artifact.update(
            status="CAUSAL_BLOCKED",
            reason="Task 3 adapter contract cannot represent a clustered causal corpus",
            blocked_stage="TASK3_CLUSTER_ADAPTER",
            intended_unique_day_denominator=len(units_days),
            supplied_ticker_day_units=len(units),
        )
        return _write(artifact, output_path)
    if not callable(live_runner) or not callable(new_runner):
        artifact.update(
            status="FAILED_EXECUTION",
            reason="both injected live_runner and new_runner are required",
        )
        return _write(artifact, output_path)

    unit = units[0]
    records: list[dict[str, Any]] = []
    try:
        canonical = _unit_input(unit)
        comparison = compare_expansion_common_input(
            canonical,
            live_runner=live_runner,
            new_runner=new_runner,
            provenance_units=[unit],
            artifact_registry=registry,
            intended_units=1,
            intended_corpus_manifest={"units": [unit]},
        )
        if not isinstance(comparison, Mapping) or comparison.get("status") != "VALID":
            raise ComparisonInvalid(
                "runner received missing or invalid comparison output",
                structured_invalid=True,
            )
        # Task 4 expects its own validated record shape.  Preserve the full
        # Task 3 unit/provenance/registry and add the comparison artifact.
        item = dict(unit)
        item.update(
            {
                "day": unit.get("calendar_day"),
                "comparison": dict(comparison),
                "artifact_registry": registry,
            }
        )
        records.append(item)
    except ComparisonInvalid as exc:
        artifact.update(
            status="COMPARISON_INVALID",
            reason=str(exc),
            invalid_result=exc.invalid_result,
        )
        return _write(artifact, output_path)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        artifact.update(
            status="COMPARISON_INVALID", reason=f"runner output/schema failure: {exc}"
        )
        return _write(artifact, output_path)
    artifact["comparisons"] = records

    evaluator = (
        task4_evaluator if task4_evaluator is not None else _default_task4_evaluator
    )
    if not callable(evaluator):
        artifact.update(
            status="FAILED_EXECUTION", reason="Task 4 evaluator is unavailable"
        )
        return _write(artifact, output_path)
    try:
        evaluation = dict(evaluator(records))
    except ComparisonInvalid as exc:
        artifact.update(status="COMPARISON_INVALID", reason=str(exc))
        return _write(artifact, output_path)
    except run_task4_evaluation.EvaluationInvalid as exc:
        artifact.update(status="COMPARISON_INVALID", reason=str(exc))
        return _write(artifact, output_path)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        artifact.update(
            status="FAILED_EXECUTION", reason=f"Task 4 evaluation failed: {exc}"
        )
        return _write(artifact, output_path)
    artifact["task4_evaluation"] = evaluation
    artifact["status"] = _status_from_evaluation(evaluation)
    return _write(artifact, output_path)


def _write(artifact: dict[str, Any], output_path: str | Path | None) -> dict[str, Any]:
    if artifact.get("status") not in STATUSES:
        artifact["status"] = "FAILED_EXECUTION"
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        artifact["artifact_path"] = str(path)
        try:
            serialized = (
                json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n"
            )
        except ValueError as exc:
            reason = f"artifact contains non-finite value(s), cannot serialize: {exc}"
            fallback = {
                "schema_version": artifact.get("schema_version"),
                "runner": artifact.get("runner"),
                "status": "FAILED_EXECUTION",
                "reason": reason,
                "artifact_path": artifact.get("artifact_path"),
            }
            artifact["status"] = "FAILED_EXECUTION"
            artifact["reason"] = reason
            serialized = (
                json.dumps(fallback, indent=2, sort_keys=True, allow_nan=False) + "\n"
            )
        path.write_text(serialized, encoding="utf-8")
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("evidence")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = run_universe_causal_comparison(
        args.manifest, args.evidence, output_path=args.output
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in COMPLETED_STATUSES else 2


__all__ = ["COMPLETED_STATUSES", "STATUSES", "main", "run_universe_causal_comparison"]

if __name__ == "__main__":
    raise SystemExit(main())
