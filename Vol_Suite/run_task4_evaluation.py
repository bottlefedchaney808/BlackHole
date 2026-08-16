"""Task 4 network-free evaluation and falsifier harness.

The harness consumes already-produced Task 3 artifacts only.  It does not
acquire data, alter a live/master model, select a lag, impute missing values,
or auto-promote a model.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

try:
    from .provenance_contract import (
        SHA256_RE,
        canonical_json_bytes,
        canonical_sha256,
        sha256_bytes,
    )
except ImportError:
    from provenance_contract import (
        SHA256_RE,
        canonical_json_bytes,
        canonical_sha256,
        sha256_bytes,
    )

TARGET_DAYS = 257
CORRELATIONAL_TARGET_DAYS = 29
CONTROL_COLUMNS = ("gamma_burst", "delta_s", "market", "a6_reflexivity", "event", "cross_family_spillover")
SHA256_HEX = 64
# Task 4 evidence-mix gate.  These are deliberately explicit and deterministic:
# each arm needs at least two unique days, each arm must cover 20% of the
# pooled unique-day denominator, and neither arm may exceed a 2:1 size ratio.
MIN_MIX_DAYS = 2
MIN_MIX_COVERAGE = 0.20
MAX_MIX_IMBALANCE = 2.0
CI_LEVEL = 0.90
MIN_CI_CLUSTERS = 3


class EvaluationInvalid(ValueError):
    """Input cannot support a descriptive or causal evaluation.

    ``status`` is intentionally machine-readable so callers can publish the
    failure as a comparison-contract invalidation rather than confusing it
    with a merely causally blocked appendix.
    """

    status = "COMPARISON_INVALID"


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise EvaluationInvalid(f"{name} outcome/control must be finite and present; no imputation is allowed")
    return float(value)


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise EvaluationInvalid(f"{name} must be a SHA-256 hash")
    return value.lower()


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
        raise EvaluationInvalid("comparison artifact schema/status is invalid")
    coverage = value.get("coverage")
    if not isinstance(coverage, Mapping) or {"live", "new", "common", "total"} - set(coverage):
        raise EvaluationInvalid("comparison artifact coverage is incomplete")
    try:
        counts = {key: int(coverage[key]) for key in ("live", "new", "common", "total")}
    except (TypeError, ValueError) as exc:
        raise EvaluationInvalid("comparison artifact coverage is invalid") from exc
    if any(counts[key] < 0 for key in counts) or len(set(counts.values())) != 1:
        raise EvaluationInvalid("comparison artifact has incomplete common-input coverage")
    input_hash = _hash(value.get("input_hash"), "comparison input_hash")
    artifact_hash = _hash(value.get("artifact_hash"), "comparison artifact_hash")
    source_hashes = value.get("source_hashes")
    if not isinstance(source_hashes, (list, tuple)) or not source_hashes:
        raise EvaluationInvalid("comparison source_hashes are required")
    normalized_sources = tuple(_hash(item, "comparison source_hash") for item in source_hashes)
    return {**value, "input_hash": input_hash, "artifact_hash": artifact_hash,
            "source_hashes": normalized_sources, "coverage": counts}


def _canonical_json(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationInvalid("canonical provenance contains non-JSON values") from exc


def _registry(row: Mapping[str, Any], comparison: Mapping[str, Any]) -> Mapping[str, Any]:
    registry = row.get("artifact_registry", comparison.get("artifact_registry"))
    if not isinstance(registry, Mapping) or not registry:
        raise EvaluationInvalid("Task 3 artifact registry is required")
    return registry


def _registry_payload_hash(entry: Mapping[str, Any]) -> str:
    payload = entry.get("payload_bytes", entry.get("payload"))
    if payload is None:
        raise EvaluationInvalid("registry entry lacks raw/source payload")
    raw = payload if isinstance(payload, bytes) else payload.encode("utf-8") if isinstance(payload, str) else _canonical_json(payload)
    return sha256_bytes(raw)


def _provenance(row: Mapping[str, Any], comparison: Mapping[str, Any]) -> bool:
    value = row.get("provenance", row.get("causal_provenance"))
    if not isinstance(value, Mapping):
        raise EvaluationInvalid("COMPARISON_INVALID: provenance must be a mapping")
    input_hash = _hash(value.get("input_hash"), "provenance input_hash")
    canonical_input_hash = _hash(row.get("canonical_input_hash"), "canonical input hash")
    artifact_hash = _hash(value.get("artifact_hash"), "provenance artifact_hash")
    raw_hash = _hash(value.get("raw_payload_hash"), "provenance raw_payload_hash")
    record_artifact_hash = _hash(value.get("record_artifact_hash"), "record artifact hash")
    candidate = row.get("candidate_key", value.get("candidate_key"))
    if not isinstance(candidate, str) or not candidate.strip():
        raise EvaluationInvalid("candidate identity is required")
    sources = tuple(_hash(item, "provenance source hash") for item in value.get("source_hashes", ()))
    if (input_hash != comparison["input_hash"] or canonical_input_hash != input_hash
            or artifact_hash != comparison["artifact_hash"] or record_artifact_hash != artifact_hash
            or raw_hash != value.get("raw_payload_hash") or sources != comparison["source_hashes"]):
        raise EvaluationInvalid("record-artifact/provenance identity mismatch")
    registry = _registry(row, comparison)
    entry = registry.get(artifact_hash)
    if not isinstance(entry, Mapping):
        raise EvaluationInvalid("artifact registry entry lookup failed")
    manifest = entry.get("artifact_manifest", entry.get("manifest"))
    if not isinstance(manifest, Mapping):
        raise EvaluationInvalid("artifact registry entry manifest is incomplete")
    if _hash(entry.get("artifact_hash"), "registry artifact_hash") != artifact_hash:
        raise EvaluationInvalid("registry artifact identity is forged")
    if _hash(entry.get("raw_payload_hash"), "registry raw_payload_hash") != raw_hash:
        raise EvaluationInvalid("registry raw/source identity mismatch")
    if _registry_payload_hash(entry) != raw_hash:
        raise EvaluationInvalid("registry raw payload hash mismatch")
    if canonical_sha256(manifest) != artifact_hash:
        raise EvaluationInvalid("registry canonical manifest hash mismatch")
    expected = {"candidate_key": candidate, "ticker": row.get("ticker"),
                "calendar_day": row.get("day", row.get("date")),
                "canonical_input_hash": canonical_input_hash, "source_hashes": list(sources)}
    for field, expected_value in expected.items():
        actual = manifest.get(field)
        if field == "source_hashes":
            actual = tuple(_hash(item, "manifest source hash") for item in actual or ())
            expected_value = sources
        if actual != expected_value or entry.get(field) != (list(sources) if field == "source_hashes" else expected_value):
            raise EvaluationInvalid(f"registry {field} does not bind to evaluation record")
    _validate_calendar_metadata(row, manifest, entry)
    if value.get("no_imputation") is not True:
        raise EvaluationInvalid("COMPARISON_INVALID: provenance no_imputation must be true")
    if value.get("causal_status") == "CAUSAL_ELIGIBLE":
        return True
    if value.get("causal_status") in {"ASSOCIATIONAL", "NON_CAUSAL"}:
        reasons = value.get("reasons")
        if (not isinstance(reasons, (list, tuple)) or not reasons
                or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)):
            raise EvaluationInvalid("COMPARISON_INVALID: associational provenance reasons are required")
        return False
    raise EvaluationInvalid(
        "COMPARISON_INVALID: provenance must declare CAUSAL_ELIGIBLE or explicit associational status"
    )


_CALENDAR_FIELDS = (
    "calendar_hash", "calendar_policy_version", "resolver_code_hash", "snapshot_hash", "as_of",
    "event_ids", "event_types", "event_overlap", "event_window_id", "window_start", "window_end",
    "window_policy", "nominal_date", "observed_expiry_date", "session_id", "session_status",
    "regular_open", "regular_close", "early_close", "settlement_style", "settlement_timestamp",
    "timezone", "calendar_binding_hash",
)


def _validate_calendar_metadata(row: Mapping[str, Any], manifest: Mapping[str, Any], entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate supplied Task-3 calendar evidence without importing/resolving."""
    binding = row.get("calendar_binding")
    if not isinstance(binding, Mapping):
        raise EvaluationInvalid("COMPARISON_INVALID: supplied calendar_binding is required")
    for field in ("calendar_hash", "calendar_policy_version", "resolver_code_hash", "snapshot_hash",
                  "as_of", "event_ids", "event_window_id", "window_start", "window_end",
                  "window_policy", "nominal_date", "observed_expiry_date", "session_id",
                  "session_status", "regular_open", "regular_close", "early_close",
                  "settlement_style", "settlement_timestamp", "timezone", "calendar_binding_hash"):
        if field not in binding or binding[field] in (None, "", []):
            raise EvaluationInvalid(f"COMPARISON_INVALID: calendar binding missing {field}")
    for field in ("calendar_hash", "snapshot_hash", "resolver_code_hash", "calendar_binding_hash"):
        _hash(binding[field], f"calendar {field}")
    if binding["calendar_hash"] != binding["snapshot_hash"]:
        raise EvaluationInvalid("calendar hash does not match snapshot hash")
    source_hashes = binding.get("source_hashes")
    if not isinstance(source_hashes, (list, tuple)) or not source_hashes:
        raise EvaluationInvalid("calendar source hashes are required")
    tuple(_hash(v, "calendar source hash") for v in source_hashes)
    event_ids = binding["event_ids"]
    if not isinstance(event_ids, (list, tuple)) or not event_ids or list(event_ids) != sorted(event_ids) or len(set(event_ids)) != len(event_ids):
        raise EvaluationInvalid("calendar event IDs must be unique and deterministically sorted")
    windows = binding.get("event_windows")
    if not isinstance(windows, Mapping) or set(windows) != set(event_ids):
        raise EvaluationInvalid("calendar event windows are detached from event IDs")
    event_types = sorted(str(windows[event_id].get("event_type")) for event_id in event_ids if isinstance(windows[event_id], Mapping))
    if len(event_types) != len(event_ids) or any(event_type not in {"OPEX", "FOMC", "EARNINGS"} for event_type in event_types):
        raise EvaluationInvalid("calendar event types are incomplete")
    if bool(binding.get("event_overlap")) != (len(event_ids) > 1):
        raise EvaluationInvalid("calendar overlap flag is inconsistent")
    if binding["event_window_id"] not in {windows[event_id].get("window_id") for event_id in event_ids}:
        raise EvaluationInvalid("calendar event window identity is detached")
    if binding.get("observed_expiry") != binding["observed_expiry_date"]:
        raise EvaluationInvalid("calendar observed expiry fields disagree")
    if binding.get("calendar_binding_hash") != canonical_sha256({key: value for key, value in binding.items() if key != "calendar_binding_hash"}):
        raise EvaluationInvalid("calendar_binding_hash mismatch")
    for field in _CALENDAR_FIELDS:
        if field not in manifest or manifest[field] != row.get(field, binding.get(field)):
            raise EvaluationInvalid(f"calendar registry field {field} is detached or mutated")
    if manifest.get("calendar_binding") != dict(binding):
        raise EvaluationInvalid("calendar binding is not registry-bound")
    if tuple(manifest.get("source_hashes", ())) != tuple(row.get("source_hashes", ())):
        raise EvaluationInvalid("calendar/source hashes are detached")
    for field in (*_CALENDAR_FIELDS, "calendar_binding"):
        if field not in entry or entry[field] != manifest.get(field):
            raise EvaluationInvalid(f"calendar registry top-level field {field} is detached")
    return binding


def _mean_required(rows: list[Mapping[str, Any]], field: str) -> float:
    values = [_number(row.get(field), field) for row in rows]
    return sum(values) / len(values)


def _collapse(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        comparison = _comparison(row)
        _day(row)
        if not isinstance(row.get("ticker"), str) or not row["ticker"]:
            raise EvaluationInvalid("ticker is required for same-day clustering")
        _provenance(row, comparison)
        groups[_day(row)].append(row)
    result = []
    required = ("forward_return_h", "daily_return", "from_breach_return", "pre_vanna", "delta_iv_pre_window", *CONTROL_COLUMNS)
    for day in sorted(groups):
        members = sorted(groups[day], key=lambda r: (str(r["ticker"]), str(r.get("family", r["ticker"]))))
        item: dict[str, Any] = {"day": day, "tickers": sorted({str(r["ticker"]) for r in members})}
        for field in required:
            item[field] = _mean_required(members, field)
        item["event"] = int(any(bool(r.get("event", 0)) for r in members))
        item["families"] = sorted({str(r.get("family", r["ticker"])).upper() for r in members})
        # Calendar facts are descriptive identities, never averaged. Preserve
        # ticker-level records and form only deterministic event unions.
        item["calendar_metadata"] = [
            {"ticker": str(member["ticker"]), **{field: member.get(field) for field in _CALENDAR_FIELDS},
             "calendar_binding": dict(member["calendar_binding"])}
            for member in members
        ]
        item["event_ids"] = sorted({event_id for member in members for event_id in member["event_ids"]})
        item["event_types"] = sorted({event_type for member in members for event_type in member["event_types"]})
        item["event_overlap"] = len(item["event_ids"]) > 1
        item["calendar_labels"] = sorted(set(item["event_types"]) | ({"OVERLAP"} if item["event_overlap"] else set()))
        item["early_close"] = any(bool(member["early_close"]) for member in members)
        item["shifted_opex"] = any(member.get("nominal_date") != member.get("observed_expiry_date") for member in members)
        item["causal_eligible"] = all(_provenance(r, _comparison(r)) for r in members)
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


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Evaluate the continued fraction used by the regularized beta function."""
    max_iterations = 200
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = max(abs(d), tiny) if d == 0.0 else d
    d = 1.0 / d
    h = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = max(abs(d), tiny) if d == 0.0 else d
        c = 1.0 + aa / c
        c = max(abs(c), tiny) if c == 0.0 else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = max(abs(d), tiny) if d == 0.0 else d
        c = 1.0 + aa / c
        c = max(abs(c), tiny) if c == 0.0 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (a * math.log(x) + b * math.log1p(-x)
                 - math.lgamma(a) - math.lgamma(b) + math.lgamma(a + b))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _beta_continued_fraction(a, b, x) / a
    return 1.0 - math.exp(log_front) * _beta_continued_fraction(b, a, 1.0 - x) / b


def _student_t_cdf_positive(value: float, df: int) -> float:
    x = df / (df + value * value)
    return 1.0 - 0.5 * _regularized_beta(x, df / 2.0, 0.5)


def _student_t_critical(confidence: float, df: int) -> float:
    """Return the two-sided finite-sample Student-t critical value."""
    if df < 1 or not 0.0 < confidence < 1.0:
        raise ValueError("Student-t critical value requires positive df and a confidence in (0, 1)")
    target = 0.5 + confidence / 2.0
    low, high = 0.0, 100.0
    for _ in range(80):
        midpoint = (low + high) / 2.0
        if _student_t_cdf_positive(midpoint, df) < target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _fit(rows: list[Mapping[str, Any]], outcome: str, weights: np.ndarray | None = None) -> dict[str, Any]:
    def unavailable(n: int, p: int = 0, *, status: str = "NOT_AVAILABLE", reason: str = "no rows"):
        return {"n": n, "p": p, "rank": 0, "condition": None, "vif": None,
                "status": status, "beta": None, "se": None,
                "ci_low": None, "ci_high": None, "ci_level": CI_LEVEL,
                "cluster_count": len({r.get("day") for r in rows}),
                "ci_status": "UNAVAILABLE", "ci_method": "CR1_CLUSTERED_STUDENT_T_FINITE_SAMPLE",
                "ci_df": max(len({r.get("day") for r in rows}) - 1, 0),
                "ci_critical_value": None, "ci_reason": reason}
    if not rows:
        return unavailable(0, reason="no rows")
    target = _residual_target(rows)
    x = np.column_stack([np.ones(len(rows)), target, *[[r[c] for r in rows] for c in CONTROL_COLUMNS]])
    y = np.asarray([_number(r[outcome], outcome) for r in rows])
    weights = np.ones(len(rows)) if weights is None else np.asarray(weights, dtype=float)
    root = np.sqrt(weights / np.mean(weights))
    wx, wy = x * root[:, None], y * root
    rank = int(np.linalg.matrix_rank(wx))
    cond = float(np.linalg.cond((wx - wx.mean(axis=0))[:, 1:])) if len(rows) > 1 and rank == x.shape[1] else float("inf")
    predictors = x[:, 1:]
    if predictors.shape[1] and len(rows) > 1 and np.all(predictors.std(axis=0) > 1e-15):
        max_vif = float(np.max(np.diag(np.linalg.pinv(np.corrcoef(predictors, rowvar=False)))))
    else:
        max_vif = float("inf")
    out: dict[str, Any] = {"n": len(rows), "p": x.shape[1], "rank": rank, "condition": cond, "vif": max_vif, "max_vif": max_vif, "vif_flag": max_vif > 50}
    clusters = sorted({str(r.get("day", i)) for i, r in enumerate(rows)})
    cluster_count = len(clusters)
    out.update(ci_level=CI_LEVEL, cluster_count=cluster_count,
               ci_df=max(cluster_count - 1, 0), ci_critical_value=None,
               ci_status="UNAVAILABLE", ci_method="CR1_CLUSTERED_STUDENT_T_FINITE_SAMPLE",
               ci_low=None, ci_high=None)
    if rank < x.shape[1] or not math.isfinite(cond) or len(rows) <= x.shape[1]:
        out.update(status="NOT-IDENTIFIABLE", beta=None, se=None,
                   ci_reason="fit is not identifiable")
        return out
    coef, _, _, _ = np.linalg.lstsq(wx, wy, rcond=None)
    resid = y - x @ coef
    dof = len(rows) - x.shape[1]
    covariance = np.linalg.pinv(wx.T @ wx)
    se = math.sqrt(float(resid @ (weights * resid)) / dof * float(covariance[1, 1])) if dof > 0 else float("nan")
    beta = float(coef[1])
    out.update(status="IDENTIFIABLE", beta=beta, se=se,
               residual_ss=float(resid @ (weights * resid)),
               target="Vanna_orth × ΔIV_PRE_WINDOW")
    # Same-day records are the independent units.  CR1 is the declared
    # finite-sample clustered sandwich correction; t critical values use
    # G-1 cluster degrees of freedom.  With too few clusters the interval is
    # explicitly unavailable rather than manufactured from pooled n.
    if cluster_count < MIN_CI_CLUSTERS or not math.isfinite(se) or se <= 0:
        out.update(status="INDETERMINATE", se=None, ci_status="UNAVAILABLE",
                   ci_reason=f"insufficient identifiable clusters ({cluster_count}<{MIN_CI_CLUSTERS})")
        return out
    inv = np.linalg.pinv(wx.T @ wx)
    scores = (wx * (wy - wx @ coef)[:, None])
    meat = np.zeros((x.shape[1], x.shape[1]))
    for cluster in clusters:
        idx = [i for i, r in enumerate(rows) if str(r.get("day", i)) == cluster]
        s = scores[idx].sum(axis=0)[:, None]
        meat += s @ s.T
    df_resid = max(len(rows) - x.shape[1], 1)
    cr1 = (cluster_count / (cluster_count - 1)) * ((len(rows) - 1) / df_resid)
    robust_var = cr1 * inv @ meat @ inv
    robust_variance = float(robust_var[1, 1])
    out.update(ci_df=cluster_count - 1, ci_critical_value=None,
               ci_method="CR1_CLUSTERED_STUDENT_T_FINITE_SAMPLE")
    if not math.isfinite(robust_variance) or robust_variance <= 0.0:
        out.update(status="INDETERMINATE", se=None, ci_low=None, ci_high=None,
                   ci_status="UNAVAILABLE",
                   ci_reason="robust variance is non-finite, negative, or insufficient")
        return out
    robust_se = math.sqrt(robust_variance)
    ci_df = cluster_count - 1
    critical = _student_t_critical(CI_LEVEL, ci_df)
    out.update(se=robust_se, ci_critical_value=critical,
               ci_low=beta - critical * robust_se,
               ci_high=beta + critical * robust_se, ci_status="AVAILABLE",
               ci_reason="CR1 clustered sandwich; Student-t critical value with cluster_count-1 df")
    return out


def power_diagnostics(beta: float | None, se: float | None, n: int, p: int, target_days: int = TARGET_DAYS) -> dict[str, Any]:
    valid = beta is not None and se is not None and math.isfinite(float(beta)) and math.isfinite(float(se)) and float(se) > 0 and n > p
    if not valid:
        return {"n": n, "p": p, "power": None, "n_for_80": None, "underpowered": True, "reach_80": False, "correlational_target_days": CORRELATIONAL_TARGET_DAYS, "beta_target_days": target_days}
    z = abs(float(beta) / float(se))
    power = 0.5 * (1.0 + math.erf((z - 1.2815515655) / math.sqrt(2.0)))
    needed = next((candidate for candidate in range(max(n, p + 2), 5001) if 0.5 * (1.0 + math.erf((z * math.sqrt(candidate / n) - 1.2815515655) / math.sqrt(2.0))) >= 0.8), None)
    reach = bool(n >= target_days and power >= 0.8)
    return {"n": n, "p": p, "power": power, "n_for_80": needed, "underpowered": not reach, "reach_80": reach, "correlational_target_days": CORRELATIONAL_TARGET_DAYS, "beta_target_days": target_days}


def decision_ladder(*, descriptive_ok: bool, causal_ok: bool, falsifiers_ok: bool, powered: bool) -> str:
    # The primary fit is a prerequisite for every comparison, including WORSE.
    if not causal_ok or not powered:
        return "INDETERMINATE"
    if not descriptive_ok or not falsifiers_ok:
        return "WORSE"
    return "BETTER"


def _clock(rows: list[dict[str, Any]], field: str, expected: str) -> dict[str, Any]:
    values = [r[field] for r in rows]
    return {"expected": expected, "n": len(values), "mean_return": float(np.mean(values)) if values else None,
            "negative_days": sum(value < 0 for value in values),
            "positive_days": sum(value > 0 for value in values),
            "zero_days": sum(value == 0 for value in values)}


def _stratum(rows: list[dict[str, Any]], *, total_days: int, min_days: int) -> dict[str, Any]:
    n = len(rows)
    coverage = n / total_days if total_days else 0.0
    descriptive = {"status": "VALID" if rows else "NOT_AVAILABLE", "n": n, "agreement": float(np.mean([r["pre_vanna"] >= 0 for r in rows])) if rows else None, "role": "DESCRIPTIVE_ONLY"}
    daily = _clock(rows, "daily_return", "negative")
    breach = _clock(rows, "from_breach_return", "positive")
    eligible = bool(rows) and all(r["causal_eligible"] for r in rows)
    causal = _fit(rows, "forward_return_h") if eligible else {"status": "CAUSAL_BLOCKED" if rows else "NOT_AVAILABLE", "n": n, "p": 0, "beta": None, "se": None, "ci_low": None, "ci_high": None, "ci_level": CI_LEVEL, "cluster_count": n, "ci_status": "UNAVAILABLE", "ci_method": "CR1_CLUSTERED_FINITE_SAMPLE", "ci_reason": "causal provenance is incomplete"}
    power = power_diagnostics(causal.get("beta"), causal.get("se"), n, causal.get("p", 0))
    status = "VALID" if rows else "NOT_AVAILABLE"
    if rows and not eligible:
        status = "CAUSAL_BLOCKED"
    return {"status": status, "n": n, "coverage": coverage, "power": power, "descriptive": descriptive, "daily": daily, "from_breach": breach, "causal": causal, "no_firing_days": total_days - n if total_days else 0, "min_days": min_days}


def _mix_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    event = sum(bool(r["event"]) for r in rows)
    control = total - event
    event_coverage = event / total if total else 0.0
    control_coverage = control / total if total else 0.0
    ratio = max(event, control) / min(event, control) if min(event, control) else float("inf")
    passed = (event >= MIN_MIX_DAYS and control >= MIN_MIX_DAYS
              and event_coverage >= MIN_MIX_COVERAGE
              and control_coverage >= MIN_MIX_COVERAGE
              and ratio <= MAX_MIX_IMBALANCE)
    return {"status": "PASS" if passed else "INDETERMINATE", "gate_pass": passed,
            "event_days": event, "control_days": control, "total_days": total,
            "event_coverage": event_coverage, "control_coverage": control_coverage,
            "imbalance_ratio": ratio, "requirements": {"min_days_per_arm": MIN_MIX_DAYS,
            "min_coverage_per_arm": MIN_MIX_COVERAGE, "max_imbalance_ratio": MAX_MIX_IMBALANCE}}


def _balanced_weights(rows: list[dict[str, Any]]) -> np.ndarray:
    counts = defaultdict(int)
    for row in rows:
        for family in row["families"]:
            counts[family] += 1
    return np.asarray([sum(1.0 / counts[family] for family in row["families"]) / len(row["families"]) for row in rows])


def evaluate_task4(records: Iterable[Mapping[str, Any]], *, min_days: int = TARGET_DAYS) -> dict[str, Any]:
    records = list(records)
    rows = _collapse(records)
    if not rows:
        raise EvaluationInvalid("no evaluation records")
    artifact_agreements = [float(record["comparison"]["aggregate"]["sign_agreement"]) for record in records if isinstance(record.get("comparison", {}).get("aggregate"), Mapping) and isinstance(record["comparison"]["aggregate"].get("sign_agreement"), (int, float))]
    descriptive = {"status": "VALID", "n": len(rows), "agreement": sum(artifact_agreements) / len(artifact_agreements) if artifact_agreements else float(np.mean([r["pre_vanna"] >= 0 for r in rows])), "role": "DESCRIPTIVE_ONLY"}
    causal_eligible = all(r["causal_eligible"] for r in rows)
    primary = _fit(rows, "forward_return_h") if causal_eligible else {"status": "CAUSAL_BLOCKED", "reason": "causal provenance is incomplete", "n": len(rows), "p": 0, "beta": None, "se": None, "ci_low": None, "ci_high": None, "ci_level": CI_LEVEL, "cluster_count": len(rows), "ci_status": "UNAVAILABLE", "ci_method": "CR1_CLUSTERED_FINITE_SAMPLE", "ci_reason": "causal provenance is incomplete"}
    power = power_diagnostics(primary.get("beta"), primary.get("se"), len(rows), primary.get("p", 0))
    falsifiers: dict[str, Any] = {}
    falsifier_failure = False
    for name, field in (("placebo", "placebo_return"), ("reverse_lead_lag", "reverse_return")):
        available = all(r[field] is not None for r in rows)
        fit = _fit(rows, field) if available else None
        fit_power = power_diagnostics(fit.get("beta"), fit.get("se"), fit.get("n", 0), fit.get("p", 0), target_days=min_days) if fit else None
        primary_finite = all(primary.get(field) is not None
                             and math.isfinite(float(primary[field]))
                             for field in ("beta", "se"))
        primary_powered = bool(power.get("reach_80"))
        prerequisites = (available and fit is not None and primary.get("status") == "IDENTIFIABLE"
                         and primary_finite and primary_powered
                         and fit.get("status") == "IDENTIFIABLE" and fit_power is not None
                         and fit_power["reach_80"] and fit.get("ci_status") == "AVAILABLE"
                         and primary.get("ci_status") == "AVAILABLE")
        observed_failure = bool(prerequisites and abs(float(fit["beta"])) >= abs(float(primary["beta"]))
                                and math.isfinite(float(fit["beta"])) and math.isfinite(float(primary["beta"])))
        falsifier_failure = falsifier_failure or observed_failure
        interpretation = "OBSERVED_FAILURE" if observed_failure else ("VALID" if prerequisites else ("NOT_AVAILABLE" if not available else "INDETERMINATE"))
        falsifiers[name] = {"status": "VALID" if available else "NOT_AVAILABLE", "fit": fit, "power": fit_power,
                            "pre_registered": True, "best_lag_selection": False, "interpretation": interpretation,
                            "drives_decision": observed_failure}
    opposite = [dict(r, pre_vanna=r["opposite_vanna"]) for r in rows] if all(r["opposite_vanna"] is not None for r in rows) else []
    sensitivity = {"opposite_convention": {"role": "SENSITIVITY", "fit": _fit(opposite, "forward_return_h") if opposite else None}, "balanced_panel": {"role": "SENSITIVITY", "weighting": "equal-family/day", "primary_replacement": False, "fit": _fit(rows, "forward_return_h", _balanced_weights(rows)) if causal_eligible else None}}
    strata = {"event_only": _stratum([r for r in rows if r["event"]], total_days=len(rows), min_days=min_days), "control_only": _stratum([r for r in rows if not r["event"]], total_days=len(rows), min_days=min_days), "pooled": _stratum(rows, total_days=len(rows), min_days=min_days)}
    strata["pooled"]["no_firing_days"] = sum(not r["event"] for r in rows)
    mix_gate = _mix_gate(rows)
    descriptive_ok = descriptive["agreement"] >= 0.5
    primary_finite = all(primary.get(field) is not None
                         and math.isfinite(float(primary[field]))
                         for field in ("beta", "se"))
    primary_usable = (primary.get("status") == "IDENTIFIABLE" and primary_finite
                      and primary.get("ci_status") == "AVAILABLE")
    decision = decision_ladder(descriptive_ok=descriptive_ok and mix_gate["gate_pass"], causal_ok=primary_usable, falsifiers_ok=not falsifier_failure, powered=bool(power.get("reach_80")))
    if not mix_gate["gate_pass"]:
        decision = "INDETERMINATE"
    return {"status": "VALID", "decision": decision, "evaluation_gate": mix_gate, "n_unique_days": len(rows), "days": rows, "descriptive": descriptive, "causal": primary, "primary_all_eligible": primary, "power": power, "falsifiers": falsifiers, "sensitivity": sensitivity, "clocks": {"daily_close_to_close": _clock(rows, "daily_return", "negative"), "from_breach": _clock(rows, "from_breach_return", "positive")}, "strata": strata, "diagnostics": {"rank": primary.get("rank"), "condition": primary.get("condition"), "vif": primary.get("vif"), "ci_low": primary.get("ci_low"), "ci_high": primary.get("ci_high"), "ci_level": primary.get("ci_level"), "cluster_count": primary.get("cluster_count"), "ci_df": primary.get("ci_df"), "ci_critical_value": primary.get("ci_critical_value"), "ci_status": primary.get("ci_status"), "ci_method": primary.get("ci_method")}, "config": {"unit": "unique_calendar_day", "cluster": "same-day tickers", "no_imputation": True, "best_lag_selection": False, "auto_promote": False, "causal_target": "Vanna_orth × ΔIV_PRE_WINDOW", "min_days": min_days, "primary_universe": "all_eligible"}}


__all__ = ["EvaluationInvalid", "decision_ladder", "evaluate_task4", "power_diagnostics", "unique_calendar_days"]
