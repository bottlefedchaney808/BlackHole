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
    from .dealer_exposure_expansion import compare_expansion_common_input
    from .run_live_vs_expiry_book_common_input import (
        ComparisonInvalid,
        make_canonical_input,
        validate_causal_eligibility,
    )
except ImportError:
    from dealer_exposure_expansion import compare_expansion_common_input
    from run_live_vs_expiry_book_common_input import (
        ComparisonInvalid,
        make_canonical_input,
        validate_causal_eligibility,
    )

STATUSES = {"BETTER", "WORSE", "INDETERMINATE", "COMPARISON_INVALID", "CAUSAL_BLOCKED"}


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
        raise ComparisonInvalid("canonical rows/source hashes are missing", structured_invalid=True)
    return make_canonical_input(
        str(source.get("ticker", unit.get("ticker", ""))),
        str(source.get("calendar_day", unit.get("calendar_day", ""))),
        str(source.get("expiry", unit.get("expiry", ""))),
        int(source.get("dte", unit.get("dte", 0))),
        float(source.get("spot", source.get("spot_price"))),
        str(source.get("iv_source_ts", unit.get("iv_source_ts", ""))),
        rows, source_hashes, str(source.get("chain_source", "offline-canonical")),
    )


def _status_from_evaluation(evaluation: Mapping[str, Any] | None) -> str:
    if not evaluation:
        return "INDETERMINATE"
    decision = str(evaluation.get("decision", "INDETERMINATE")).upper()
    return decision if decision in {"BETTER", "WORSE", "INDETERMINATE"} else "INDETERMINATE"


def run_universe_causal_comparison(
    manifest: Mapping[str, Any] | str | Path,
    evidence: Mapping[str, Any] | str | Path,
    *,
    live_runner: Callable[[Any], Any] | None = None,
    new_runner: Callable[[Any], Any] | None = None,
    task4_evaluator: Callable[[Iterable[Mapping[str, Any]]], Mapping[str, Any]] | None = None,
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
    intended = int(manifest.get("intended_unique_day_denominator", manifest.get("intended_units", len(units))))
    try:
        causal = validate_causal_eligibility(
            units, intended_units=intended,
            intended_corpus_manifest={"units": manifest.get("units", units)},
            artifact_registry=registry,
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        artifact.update(status="COMPARISON_INVALID", reason=f"causal evidence/schema failure: {exc}")
        return _write(artifact, output_path)
    artifact["causal_gate"] = causal
    if causal.get("causal_status") != "CAUSAL_ELIGIBLE":
        artifact.update(status="CAUSAL_BLOCKED", reason="100% PRE_WINDOW coverage and registry validation are required")
        return _write(artifact, output_path)
    if not callable(live_runner) or not callable(new_runner):
        artifact["reason"] = "both injected live_runner and new_runner are required"
        return _write(artifact, output_path)
    records: list[dict[str, Any]] = []
    try:
        for unit in sorted(units, key=lambda row: (str(row.get("calendar_day")), str(row.get("ticker")))):
            canonical = _unit_input(unit)
            comparison = compare_expansion_common_input(
                canonical, live_runner=live_runner, new_runner=new_runner,
                provenance_units=[unit], artifact_registry=registry,
                intended_units=1, intended_corpus_manifest={"units": [unit]},
            )
            if not isinstance(comparison, Mapping) or comparison.get("status") != "VALID":
                raise ComparisonInvalid("runner received missing or invalid comparison output", structured_invalid=True)
            item = {"candidate_key": unit.get("candidate_key"), "ticker": unit.get("ticker"),
                    "calendar_day": unit.get("calendar_day"), "comparison": dict(comparison),
                    "unit": dict(unit)}
            records.append(item)
            day = str(unit.get("calendar_day"))
            artifact["same_day_units"].setdefault(day, []).append(item["candidate_key"])
    except ComparisonInvalid as exc:
        artifact.update(status="COMPARISON_INVALID", reason=str(exc), invalid_result=exc.invalid_result)
        return _write(artifact, output_path)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        artifact.update(status="COMPARISON_INVALID", reason=f"runner output/schema failure: {exc}")
        return _write(artifact, output_path)
    artifact["comparisons"] = records
    for day, keys in artifact["same_day_units"].items():
        artifact["same_day_units"][day] = {"candidate_keys": keys, "aggregation_rule": "preserve ticker-specific values; no averaging"}
    evaluation = None
    if callable(task4_evaluator):
        evaluation = dict(task4_evaluator(records))
    artifact["task4_evaluation"] = evaluation
    artifact["status"] = _status_from_evaluation(evaluation)
    return _write(artifact, output_path)


def _write(artifact: dict[str, Any], output_path: str | Path | None) -> dict[str, Any]:
    if artifact.get("status") not in STATUSES:
        artifact["status"] = "COMPARISON_INVALID"
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        artifact["artifact_path"] = str(path)
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("evidence")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = run_universe_causal_comparison(args.manifest, args.evidence, output_path=args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in STATUSES else 2


__all__ = ["STATUSES", "main", "run_universe_causal_comparison"]

if __name__ == "__main__":
    raise SystemExit(main())
