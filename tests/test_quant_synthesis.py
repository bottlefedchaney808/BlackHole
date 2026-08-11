from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from quant_synthesis import _publish_text, synthesize_run
from shared.worker_contracts import WorkerResult


def put(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_all_modules_and_provenance(tmp_path: Path):
    run = tmp_path / "20260801T120000Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name, "focus": {"ticker": "SPY"}})
    put(run / "vol_result.json", {"fair_vol": .2, "atm_iv": .25, "vrp": .05, "signal": "rich", "score": 8})
    put(run / "options_result.json", {"model": "bs", "sigma": .24, "price": 12.3, "greeks": {"delta": .5, "gamma": .1, "vega": .2, "theta": -.1, "rho": .03}})
    put(run / "var_result.json", {"var": 100, "cvar": 130, "confidence": .99, "horizon_days": 10})
    put(run / "sentiment_result.json", {"signal": "bullish", "gex": 2, "iv_rank": 40, "vrp": .02, "skew": -.1, "max_pain": 500})
    put(run / "dtcc_result.json", {"swap_count": 4, "scrape_status": "ok", "date_range": {"start": "2026-07-01", "end": "2026-07-31"}, "errors": []})
    put(run / "backtesting_result.json", {"returns": .1, "sharpe": 1.2, "hit_rate": .6, "forward_returns": [.02, -.01]})
    result = synthesize_run(run)
    assert result.worker == "quant" and result.worker_identity.profile == "quant"
    assert result.status == "complete" and result.source_run_id == run.name
    assert set(result.metrics) == {"vol", "options", "var", "sentiment", "dtcc", "backtesting"}
    assert result.metrics["vol"]["fair_vol"] == .2
    assert result.metrics["options"]["delta"] == .5
    assert result.metrics["var"]["horizon"] == 10
    assert result.metrics["sentiment"]["iv"] == 40
    assert result.metrics["dtcc"]["swap_count"] == 4
    assert result.metrics["backtesting"]["sharpe"] == 1.2
    refs = {a.path: a.sha256 for a in result.provenance.source_artifacts}
    assert refs["vol_result.json"] == hashlib.sha256((run / "vol_result.json").read_bytes()).hexdigest()
    assert (run / "quant_summary.json").exists() and (run / "quant_summary.md").exists()
    assert str(run) not in (run / "quant_summary.md").read_text(encoding="utf-8")


def test_unified_run_without_optional_modules_is_complete(tmp_path: Path):
    run = tmp_path / "20260801T120020Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name})
    put(run / "vol_result.json", {"fair_vol": .2, "atm_iv": .25, "vrp": .05, "signal": "rich", "score": 8})
    put(run / "options_result.json", {"model": "bs", "sigma": .24, "price": 12.3})
    put(run / "var_result.json", {"var": 100, "cvar": 130, "confidence": .99, "horizon_days": 10})
    result = synthesize_run(run)
    assert result.status == "complete"  # absent optional modules must not degrade
    assert set(result.metrics) == {"vol", "options", "var"}


def test_missing_modules_degrade_and_warn(tmp_path: Path):
    run = tmp_path / "20260801T120001Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name, "focus": {"ticker": "QQQ"}})
    put(run / "vol_result.json", {"atm_iv": .3})
    result = synthesize_run(run, module="vol")
    assert result.status == "degraded"
    assert any("options" in warning for warning in result.risks)
    assert all(str(run) not in warning for warning in result.risks)
    payload = json.loads((run / "quant_summary.json").read_text(encoding="utf-8"))
    assert payload["worker_identity"] == {"worker": "quant", "profile": "quant"}


def test_worker_reports_exclude_prompts(tmp_path: Path):
    run = tmp_path / "20260801T120002Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name})
    report = run / "quant_coder_report.json"
    put(report, {"worker": "coder", "status": "succeeded", "prompt_summary": "secret prompt text", "headline": "Fixed parser"})
    result = synthesize_run(run, worker_reports=(report,))
    text = (run / "quant_summary.json").read_text(encoding="utf-8")
    assert result.metrics["worker_reports"][0]["worker"] == "coder"
    assert "secret prompt" not in text and "prompt_summary" not in text


def test_filter_and_unknown_module(tmp_path: Path):
    run = tmp_path / "20260801T120003Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name})
    put(run / "vol_result.json", {"fair_vol": .2}); put(run / "var_result.json", {"var": 3})
    result = synthesize_run(run, module="var")
    assert set(result.metrics) == {"var"} and result.metrics["var"]["var"] == 3
    assert {a.path for a in result.provenance.source_artifacts} == {"suite_context.json", "var_result.json"}
    assert {e.artifact.path for e in result.evidence} == {"suite_context.json", "var_result.json"}
    try:
        synthesize_run(run, module="unknown")
    except ValueError as exc:
        assert "module" in str(exc)
    else:
        raise AssertionError("unknown module must be rejected")


def test_synthesis_ignores_prefix_named_unknown_module_files(tmp_path: Path):
    run = tmp_path / "20260801T120010Z"; run.mkdir()
    put(run / "vol_secret.json", {"fair_vol": .2})
    result = synthesize_run(run, module="vol")
    assert "vol" not in result.metrics
    assert all(artifact.path != "vol_secret.json" for artifact in result.provenance.source_artifacts)


@pytest.mark.skipif(os.name == "nt", reason="os.mkfifo is POSIX-only")
def test_synthesis_ignores_fifo_recognized_input(tmp_path: Path):
    run = tmp_path / "20260801T120011Z"; run.mkdir()
    os.mkfifo(run / "vol_result.json")
    result = synthesize_run(run, module="vol")
    assert result.status == "failed"
    assert "missing vol input" in result.risks


def test_synthesis_ignores_unvalidated_explicit_worker_report(tmp_path: Path):
    run = tmp_path / "20260801T120012Z"; run.mkdir()
    report = run / "notes.json"
    put(report, {"headline": "not a worker report"})
    result = synthesize_run(run, worker_reports=(report,))
    assert "worker_reports" not in result.metrics
    assert all(artifact.path != "notes.json" for artifact in result.provenance.source_artifacts)


@pytest.mark.skipif(os.name == "nt", reason="os.mkfifo is POSIX-only")
def test_synthesis_does_not_read_fifo_explicit_worker_report(tmp_path: Path):
    run = tmp_path / "20260801T120013Z"; run.mkdir()
    report = run / "worker-report.json"
    os.mkfifo(report)
    result = synthesize_run(run, worker_reports=(report,))
    assert "worker_reports" not in result.metrics


def test_synthesis_rejects_malformed_worker_report_status(tmp_path: Path):
    run = tmp_path / "20260801T120014Z"; run.mkdir()
    report = run / "worker-report.json"
    put(report, {"worker": ["coder"], "status": ["succeeded"], "headline": "bad"})
    result = synthesize_run(run, worker_reports=(report,))
    assert "worker_reports" not in result.metrics


def test_synthesis_requires_exact_case_module_filenames(tmp_path: Path):
    run = tmp_path / "20260801T120015Z"; run.mkdir()
    put(run / "VOL_RESULT.JSON", {"fair_vol": .2})
    result = synthesize_run(run, module="vol")
    assert "vol" not in result.metrics


def test_valid_worker_report_named_like_module_is_not_module_input(tmp_path: Path):
    run = tmp_path / "20260801T120016Z"; run.mkdir()
    report = run / "vol_result.json"
    put(report, {"worker": "coder", "status": "succeeded", "headline": "done"})
    result = synthesize_run(run, worker_reports=(report,))
    assert "vol" not in result.metrics
    assert result.metrics["worker_reports"][0]["worker"] == "coder"


def test_malformed_worker_report_named_like_module_is_ignored(tmp_path: Path):
    run = tmp_path / "20260801T120017Z"; run.mkdir()
    report = run / "vol_result.json"
    put(report, {"fair_vol": .2, "status": ["not-valid"]})
    result = synthesize_run(run, worker_reports=(report,))
    assert "vol" not in result.metrics
    assert "worker_reports" not in result.metrics


def test_worker_only_reports_are_not_complete_and_warn_about_missing_modules(tmp_path: Path):
    run = tmp_path / "20260801T120005Z"; run.mkdir()
    report = run / "worker-report.json"
    put(report, {"worker": "coder", "status": "succeeded", "headline": "done"})
    result = synthesize_run(run, worker_reports=(report,))
    assert result.status in {"failed", "degraded"}
    assert any("missing vol input" in warning for warning in result.risks)


def test_worker_only_reports_use_nonzero_exit_code_when_failed(tmp_path: Path):
    run = tmp_path / "20260801T120008Z"; run.mkdir()
    result = synthesize_run(run)
    assert result.status == "failed"
    assert result.exit_code != 0
    assert result.failure is not None
    payload = json.loads((run / "quant_summary.json").read_text(encoding="utf-8"))
    assert payload["exit_code"] != 0


def test_degraded_synthesis_uses_zero_exit_code(tmp_path: Path):
    run = tmp_path / "20260801T120009Z"; run.mkdir()
    put(run / "vol_result.json", {"atm_iv": .3})
    result = synthesize_run(run, module="vol")
    assert result.status == "degraded"
    assert result.exit_code == 0


def test_publish_leaves_no_temp_litter_when_random_name_generation_fails(tmp_path: Path, monkeypatch):
    run = tmp_path / "run"; run.mkdir()
    monkeypatch.setattr("quant_synthesis.os.urandom", lambda _: (_ for _ in ()).throw(OSError("random failure")))
    with pytest.raises(OSError, match="random failure"):
        _publish_text(run, "summary.md", "text\n")
    assert list(run.glob("*.tmp")) == []


def test_publish_leaves_no_temp_litter_when_temp_creation_fails(tmp_path: Path, monkeypatch):
    run = tmp_path / "run"; run.mkdir()
    real_open = os.open

    def failing_temp_open(path, flags, mode=0o600):
        if str(path).endswith(".tmp"):
            raise OSError("injected temp creation failure")
        return real_open(path, flags, mode)

    monkeypatch.setattr("quant_synthesis.os.open", failing_temp_open)
    with pytest.raises(OSError, match="injected"):
        _publish_text(run, "summary.md", "text\n")
    assert list(run.glob("*.tmp")) == []


def test_publish_closes_fd_and_cleans_temp_when_fsync_fails(tmp_path: Path, monkeypatch):
    run = tmp_path / "run"; run.mkdir()
    monkeypatch.setattr("quant_synthesis.os.fsync", lambda *_: (_ for _ in ()).throw(OSError("fsync failure")))
    with pytest.raises(OSError, match="fsync failure"):
        _publish_text(run, "summary.md", "text\n")
    # The failed write must leave no temp litter and never publish the file.
    assert list(run.glob("*.tmp")) == []
    assert not (run / "summary.md").exists()


def test_synthesis_treats_recognized_directory_as_unreadable_input(tmp_path: Path):
    run = tmp_path / "run"; run.mkdir()
    (run / "vol_result.json").mkdir()
    result = synthesize_run(run, module="vol")
    assert result.status == "failed"
    assert "unreadable vol input" in result.risks


def test_serialized_synthesis_output_validates_as_worker_result(tmp_path: Path):
    run = tmp_path / "20260801T120006Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name})
    put(run / "var_result.json", {"var": 3})
    synthesize_run(run, module="var")
    payload = json.loads((run / "quant_summary.json").read_text(encoding="utf-8"))
    validated = WorkerResult.model_validate(payload)
    assert validated.worker == "quant"


@pytest.mark.parametrize("value", [
    "eyJhbG...ture",
    "a" * 64,
    "bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "«redacted:sk-…»",
    "redacted:sk-...",
    "-----BEGIN PRIVATE KEY-----",
])
def test_synthesis_redacts_opaque_credential_like_values(tmp_path: Path, value: str):
    run = tmp_path / "20260801T120007Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name})
    put(run / "sentiment_result.json", {"signal": value, "label": "bullish", "score": "ok"})
    result = synthesize_run(run, module="sentiment")
    assert value not in json.dumps(result.model_dump(mode="json"))
    assert result.metrics["sentiment"].get("label") == "bullish"
    assert result.metrics["sentiment"].get("score") == "ok"


def test_deterministic(tmp_path: Path):
    run = tmp_path / "20260801T120004Z"; run.mkdir()
    put(run / "suite_context.json", {"run_id": run.name}); put(run / "vol_result.json", {"score": 1})
    assert synthesize_run(run).model_dump_json() == synthesize_run(run).model_dump_json()
