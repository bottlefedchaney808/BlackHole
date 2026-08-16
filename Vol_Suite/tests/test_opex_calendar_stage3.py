import copy
import hashlib
import json

import pytest
import run_task4_evaluation as evaluation
from run_task4_evaluation import EvaluationInvalid, evaluate_task4
from test_task4_evaluation import row


def _rebind(record, event_types):
    """Make a new hash-bound supplied calendar record, without resolving."""
    out = copy.deepcopy(record)
    binding = out["calendar_binding"]
    day = out["day"]
    event_ids = [f"{event_type}:{day}" for event_type in event_types]
    binding["event_ids"] = sorted(event_ids)
    binding["event_overlap"] = len(event_ids) > 1
    binding["event_window_id"] = f"{event_ids[0]}:OPEX_DAY"
    binding["event_windows"] = {
        event_id: {"event_type": event_id.split(":", 1)[0], "window_id": f"{event_id}:OPEX_DAY",
                   "window_start": binding["window_start"], "window_end": binding["window_end"],
                   "window_policy": binding["window_policy"]}
        for event_id in event_ids
    }
    binding["window_id"] = binding["event_window_id"]
    binding["calendar_binding_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in binding.items() if k != "calendar_binding_hash"}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    fields = ("calendar_hash", "calendar_policy_version", "resolver_code_hash", "snapshot_hash", "as_of",
              "event_ids", "event_window_id", "window_start", "window_end", "window_policy", "nominal_date",
              "observed_expiry_date", "session_id", "session_status", "regular_open", "regular_close",
              "early_close", "settlement_style", "settlement_timestamp", "timezone", "calendar_binding_hash")
    for field in fields:
        out[field] = binding[field]
    out["event_types"] = sorted(event_types)
    out["event_overlap"] = len(event_ids) > 1
    old_hash = out["comparison"]["artifact_hash"]
    manifest = out["artifact_registry"][old_hash]["artifact_manifest"]
    manifest.update({field: out[field] for field in fields})
    manifest.update(event_types=out["event_types"], event_overlap=out["event_overlap"], calendar_binding=binding)
    new_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    entry = out["artifact_registry"].pop(old_hash)
    entry.update(artifact_hash=new_hash, artifact_manifest=manifest, calendar_binding=binding,
                 **{field: out[field] for field in fields}, event_types=out["event_types"],
                 event_overlap=out["event_overlap"])
    out["artifact_registry"][new_hash] = entry
    out["comparison"]["artifact_hash"] = new_hash
    out["provenance"].update(artifact_hash=new_hash, record_artifact_hash=new_hash)
    out["artifact_registry"][new_hash]["artifact_hash"] = new_hash
    return out


def _reattest_binding(record, mutate):
    """Rehash a mutated binding and registry, without changing row identity."""
    out = copy.deepcopy(record)
    binding = out["calendar_binding"]
    mutate(binding)
    binding["calendar_binding_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in binding.items() if k != "calendar_binding_hash"}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    old_hash = out["comparison"]["artifact_hash"]
    manifest = out["artifact_registry"][old_hash]["artifact_manifest"]
    manifest["calendar_binding"] = binding
    new_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    entry = out["artifact_registry"].pop(old_hash)
    entry.update(artifact_hash=new_hash, artifact_manifest=manifest, calendar_binding=binding)
    out["artifact_registry"][new_hash] = entry
    out["comparison"]["artifact_hash"] = new_hash
    out["provenance"].update(artifact_hash=new_hash, record_artifact_hash=new_hash)
    return out


def test_rehashed_calendar_binding_must_match_row_ticker_day_expiry_and_dte():
    record = row("2026-06-01", "SPY")
    bad = _reattest_binding(record, lambda binding: binding.update(
        ticker="QQQ", calendar_day="2026-06-02", expiry="2026-06-20", dte=19,
    ))
    with pytest.raises(EvaluationInvalid, match="identity|ticker|calendar day|expiry|DTE"):
        evaluate_task4([bad], min_days=1)


def test_rehashed_nested_event_window_must_match_top_level_window_metadata():
    record = row("2026-06-01", "SPY")
    event_id = record["calendar_binding"]["event_ids"][0]
    bad = _reattest_binding(record, lambda binding: binding["event_windows"][event_id].update(
        window_start="2026-06-01T10:00:00-05:00", window_policy="WRONG_POLICY",
    ))
    with pytest.raises(EvaluationInvalid, match="event window|window|policy"):
        evaluate_task4([bad], min_days=1)


def test_missing_calendar_binding_invalidates_before_fitting():
    record = row("2026-06-01", "SPY")
    record.pop("calendar_binding")
    with pytest.raises(EvaluationInvalid, match="calendar_binding"):
        evaluate_task4([record], min_days=1)


def test_forged_calendar_binding_hash_invalidates():
    record = row("2026-06-01", "SPY")
    record["calendar_binding"]["calendar_binding_hash"] = "f" * 64
    with pytest.raises(EvaluationInvalid, match="registry|calendar_binding_hash"):
        evaluate_task4([record], min_days=1)


def test_registry_calendar_mutation_invalidates():
    record = row("2026-06-01", "SPY")
    entry = next(iter(record["artifact_registry"].values()))
    entry["calendar_policy_version"] = "forged-policy"
    with pytest.raises(EvaluationInvalid, match="calendar registry"):
        evaluate_task4([record], min_days=1)


def test_same_day_collapse_preserves_nested_metadata_and_unions_overlap_events():
    first = row("2026-06-01", "SPY")
    second = _rebind(row("2026-06-01", "QQQ"), ["FOMC", "OPEX"])
    result = evaluate_task4([second, first], min_days=1)
    day = result["days"][0]
    assert [item["ticker"] for item in day["calendar_metadata"]] == ["QQQ", "SPY"]
    assert day["event_ids"] == ["FOMC:2026-06-01", "OPEX:2026-06-01"]
    assert day["event_types"] == ["FOMC", "OPEX"]
    assert day["event_overlap"] is True
    assert "OVERLAP" in day["calendar_labels"]


def test_early_close_and_shifted_labels_remain_in_sensitivity_metadata():
    record = row("2026-06-01", "SPY")
    record["calendar_binding"]["early_close"] = True
    record["early_close"] = True
    record["calendar_binding"]["observed_expiry"] = "2026-06-02"
    record["calendar_binding"]["observed_expiry_date"] = "2026-06-02"
    record["observed_expiry_date"] = "2026-06-02"
    # Mutation of the supplied binding is intentionally rejected unless its hash is
    # re-attested; this proves sensitivity rows cannot bypass identity validation.
    with pytest.raises(EvaluationInvalid):
        evaluate_task4([record], min_days=1)


def test_task4_does_not_call_calendar_resolver(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("resolver must not be called by Task 4")

    monkeypatch.setattr(evaluation, "resolve_opex", fail, raising=False)
    monkeypatch.setattr(evaluation, "calendar_for_probe", fail, raising=False)
    result = evaluate_task4([row("2026-06-01", "SPY")], min_days=1)
    assert result["status"] == "VALID"
    assert result["days"][0]["calendar_metadata"][0]["calendar_hash"]


def test_calendar_source_hash_mutation_invalidates():
    record = row("2026-06-01", "SPY")
    record["calendar_binding"]["source_hashes"] = ["0" * 64]
    with pytest.raises(EvaluationInvalid, match="source|calendar|registry"):
        evaluate_task4([record], min_days=1)


def test_calendar_field_mutation_invalidates_before_fit():
    record = row("2026-06-01", "SPY")
    record["session_status"] = "HOLIDAY_CLOSED"
    with pytest.raises(EvaluationInvalid, match="calendar registry|session"):
        evaluate_task4([record], min_days=1)


def test_no_firing_days_remain_in_pooled_denominator_with_calendar_records():
    records = [row("2026-06-01", "SPY"), row("2026-06-02", "QQQ")]
    records[0]["event"] = 1
    records[1]["event"] = 0
    result = evaluate_task4(records, min_days=1)
    assert result["strata"]["pooled"]["n"] == 2
    assert result["strata"]["pooled"]["no_firing_days"] == 1
    assert result["n_unique_days"] == 2


__all__ = []
