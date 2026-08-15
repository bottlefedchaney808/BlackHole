"""Task 4 network-free evaluation and falsifier harness.

This module consumes already-produced comparison/provenance records.  It never
acquires data, changes either engine, selects a lag, imputes an outcome, or
promotes a model.  The independent unit is one calendar day.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

TARGET_DAYS = 257
CORRELATIONAL_TARGET_DAYS = 29
CONTROL_COLUMNS = ("gamma_burst", "delta_s", "market", "a6_reflexivity", "event", "cross_family_spillover")


class EvaluationInvalid(ValueError):
    """Input cannot support a descriptive or causal evaluation."""


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise EvaluationInvalid(f"{name} outcome/control must be finite and present; no imputation is allowed")
    return float(value)


def _day(row: Mapping[str, Any]) -> str:
    value = row.get("day", row.get("date"))
    if not isinstance(value, str) or not value:
        raise EvaluationInvalid("calendar day is required")
    return value


def unique_calendar_days(rows: Iterable[Mapping[str, Any]]) -> int:
    return len({_day(row) for row in rows})


def _comparison(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("comparison", row.get("comparison_artifact"))
    if not isinstance(value, Mapping) or value.get("status") != "VALID":
        raise EvaluationInvalid("comparison artifact is missing or invalid")
    coverage = value.get("coverage", {})
    if coverage and coverage.get("common") != coverage.get("total"):
        raise EvaluationInvalid("comparison artifact has incomplete common-input coverage")
    return value


def _provenance(row: Mapping[str, Any]) -> bool:
    value = row.get("provenance", row.get("causal_provenance"))
    return isinstance(value, Mapping) and value.get("causal_status") == "CAUSAL_ELIGIBLE" and value.get("no_imputation") is True


def _mean_required(rows: list[Mapping[str, Any]], field: str) -> float:
    values = [_number(row.get(field), field) for row in rows]
    return sum(values) / len(values)


def _collapse(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        _comparison(row)
        _day(row)
        if not isinstance(row.get("ticker"), str) or not row["ticker"]:
            raise EvaluationInvalid("ticker is required for same-day clustering")
        groups[_day(row)].append(row)
    result = []
    required = ("forward_return_h", "daily_return", "from_breach_return", "pre_vanna", "delta_iv_pre_window", *CONTROL_COLUMNS)
    for day in sorted(groups):
        members = groups[day]
        # A same-day ticker contributes to the day only once; means are a
        # deterministic day summary, never additional independent observations.
        item: dict[str, Any] = {"day": day, "tickers": sorted({str(r["ticker"]) for r in members})}
        if any("event" not in r for r in members):
            raise EvaluationInvalid("event/no-firing indicator is required; no imputation is allowed")
        for field in required:
            item[field] = _mean_required(members, field)
        item["event"] = int(any(bool(r.get("event", 0)) for r in members))
        item["families"] = sorted({str(r.get("family", r["ticker"])).upper() for r in members})
        item["causal_eligible"] = all(_provenance(r) for r in members)
        item["placebo_return"] = _mean_required(members, "placebo_return") if all("placebo_return" in r for r in members) else None
        item["reverse_return"] = _mean_required(members, "reverse_return") if all("reverse_return" in r for r in members) else None
        item["opposite_vanna"] = _mean_required(members, "opposite_convention_vanna") if all("opposite_convention_vanna" in r for r in members) else None
        result.append(item)
    return result


def _residual_target(rows: list[Mapping[str, Any]], vanna_field: str = "pre_vanna") -> np.ndarray:
    target = np.asarray([_number(r[vanna_field], vanna_field) * _number(r["delta_iv_pre_window"], "delta_iv_pre_window") for r in rows])
    families = sorted({family for row in rows for family in row["families"]})
    z = np.asarray([[1.0, *[r["pre_vanna"] if family in r["families"] else 0.0 for family in families], r["delta_iv_pre_window"]] for r in rows])
    return target - z @ np.linalg.lstsq(z, target, rcond=None)[0]


def _fit(rows: list[Mapping[str, Any]], outcome: str) -> dict[str, Any]:
    target = _residual_target(rows)
    x = np.column_stack([np.ones(len(rows)), target, *[[r[c] for r in rows] for c in CONTROL_COLUMNS]])
    y = np.asarray([_number(r[outcome], outcome) for r in rows])
    rank = int(np.linalg.matrix_rank(x))
    cond = float(np.linalg.cond((x - x.mean(axis=0))[:, 1:])) if len(rows) > 1 and rank == x.shape[1] else float("inf")
    predictors = x[:, 1:]
    if predictors.shape[1] and len(rows) > 1:
        scales = predictors.std(axis=0)
        if np.any(scales <= 1e-15):
            max_vif = float("inf")
        else:
            corr = np.corrcoef(predictors, rowvar=False)
            max_vif = float(np.max(np.diag(np.linalg.pinv(corr))))
    else:
        max_vif = float("inf")
    out: dict[str, Any] = {"n": len(rows), "p": x.shape[1], "rank": rank, "condition": cond,
                           "vif": max_vif, "max_vif": max_vif, "vif_flag": max_vif > 50}
    if rank < x.shape[1] or not math.isfinite(cond) or len(rows) <= x.shape[1]:
        out.update(status="NOT-IDENTIFIABLE", beta=None, se=None)
        return out
    coef, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    dof = len(rows) - x.shape[1]
    se = math.sqrt(float(resid @ resid) / dof * float(np.linalg.inv(x.T @ x)[1, 1])) if dof > 0 else float("nan")
    out.update(status="IDENTIFIABLE", beta=float(coef[1]), se=se, residual_ss=float(resid @ resid), target="Vanna_orth × ΔIV_PRE_WINDOW")
    return out


def power_diagnostics(beta: float | None, se: float | None, n: int, p: int, target_days: int = TARGET_DAYS) -> dict[str, Any]:
    valid = beta is not None and se is not None and math.isfinite(float(beta)) and math.isfinite(float(se)) and float(se) > 0 and n > p
    if not valid:
        return {"n": n, "p": p, "power": None, "n_for_80": None, "underpowered": True, "reach_80": False,
                "correlational_target_days": CORRELATIONAL_TARGET_DAYS, "beta_target_days": target_days}
    z = abs(float(beta) / float(se))
    power = 0.5 * (1.0 + math.erf((z - 1.2815515655) / math.sqrt(2.0)))
    needed = None
    for candidate in range(max(n, p + 2), 5001):
        scaled = z * math.sqrt(candidate / n)
        if 0.5 * (1.0 + math.erf((scaled - 1.2815515655) / math.sqrt(2.0))) >= 0.8:
            needed = candidate
            break
    reach = bool(n >= target_days and power >= 0.8)
    return {"n": n, "p": p, "power": power, "n_for_80": needed, "underpowered": not reach,
            "reach_80": reach, "correlational_target_days": CORRELATIONAL_TARGET_DAYS, "beta_target_days": target_days}


def decision_ladder(*, descriptive_ok: bool, causal_ok: bool, falsifiers_ok: bool, powered: bool) -> str:
    if not descriptive_ok or not falsifiers_ok:
        return "WORSE"
    if not causal_ok or not powered:
        return "INDETERMINATE"
    return "BETTER"


def evaluate_task4(records: Iterable[Mapping[str, Any]], *, min_days: int = TARGET_DAYS) -> dict[str, Any]:
    records = list(records)
    rows = _collapse(records)
    if not rows:
        raise EvaluationInvalid("no evaluation records")
    # Comparison artifacts are descriptive evidence only; their agreement is
    # never used as the causal beta or as a promotion rule.
    artifact_agreements = []
    for record in records:
        aggregate = record.get("comparison", {}).get("aggregate", {})
        if isinstance(aggregate, Mapping) and isinstance(aggregate.get("sign_agreement"), (int, float)):
            artifact_agreements.append(float(aggregate["sign_agreement"]))
    descriptive = {"status": "VALID", "n": len(rows),
                   "agreement": (sum(artifact_agreements) / len(artifact_agreements)
                                  if artifact_agreements else float(np.mean([r["pre_vanna"] >= 0 for r in rows]))),
                   "role": "DESCRIPTIVE_ONLY"}
    causal_eligible = all(r["causal_eligible"] for r in rows)
    primary = _fit(rows, "forward_return_h") if causal_eligible else {"status": "CAUSAL_BLOCKED", "reason": "causal provenance is incomplete", "n": len(rows)}
    power = power_diagnostics(primary.get("beta"), primary.get("se"), len(rows), primary.get("p", 0)) if causal_eligible else power_diagnostics(None, None, len(rows), 0)
    falsifiers: dict[str, Any] = {}
    for name, field in (("placebo", "placebo_return"), ("reverse_lead_lag", "reverse_return")):
        available = all(r[field] is not None for r in rows)
        falsifiers[name] = {"status": "VALID" if available else "NOT_AVAILABLE", "fit": _fit(rows, field) if available else None,
                            "pre_registered": True, "best_lag_selection": False}
    opposite = [dict(r, pre_vanna=r["opposite_vanna"]) for r in rows] if all(r["opposite_vanna"] is not None for r in rows) else []
    sensitivity = {"opposite_convention": {"role": "SENSITIVITY", "fit": _fit(opposite, "forward_return_h") if opposite else None}}
    events = sum(r["event"] for r in rows)
    clock = {
        "daily_close_to_close": {"expected": "negative", "n": len(rows),
                                 "mean_return": float(np.mean([r["daily_return"] for r in rows])),
                                 "negative_days": sum(r["daily_return"] < 0 for r in rows)},
        "from_breach": {"expected": "positive", "n": len(rows),
                         "mean_return": float(np.mean([r["from_breach_return"] for r in rows])),
                         "positive_days": sum(r["from_breach_return"] > 0 for r in rows)},
    }
    descriptive_ok = descriptive["agreement"] >= 0.5
    falsifiers_ok = all(v["status"] == "VALID" and (v["fit"] is None or v["fit"]["status"] != "IDENTIFIABLE" or abs(v["fit"]["beta"] or 0) < abs(primary.get("beta") or 0)) for v in falsifiers.values())
    causal_ok = primary.get("status") == "IDENTIFIABLE"
    decision = decision_ladder(descriptive_ok=descriptive_ok, causal_ok=causal_ok, falsifiers_ok=falsifiers_ok, powered=not power["underpowered"])
    return {"status": "VALID", "decision": decision, "n_unique_days": len(rows), "days": rows,
            "descriptive": descriptive, "causal": primary, "power": power, "falsifiers": falsifiers,
            "sensitivity": sensitivity, "clocks": clock, "strata": {"event_days": events, "no_firing_days": len(rows) - events,
            "event_only": events, "control_only": len(rows) - events, "pooled": len(rows)},
            "diagnostics": {"rank": primary.get("rank"), "condition": primary.get("condition"), "vif": primary.get("vif")},
            "config": {"unit": "unique_calendar_day", "cluster": "same-day tickers", "no_imputation": True,
                       "best_lag_selection": False, "auto_promote": False, "causal_target": "Vanna_orth × ΔIV_PRE_WINDOW",
                       "min_days": min_days}}


__all__ = ["EvaluationInvalid", "decision_ladder", "evaluate_task4", "power_diagnostics", "unique_calendar_days"]
