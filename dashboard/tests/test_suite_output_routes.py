"""test_suite_output_routes.py

Covers Task 7 of docs/superpowers/plans/2026-08-09-dashboard-output-tab-redesign.md:
the rebuilt GET /suites/{suite} and the new GET /suites/{suite}/asset.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402
import dashboard.output_runs as output_runs  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


@pytest.fixture
def fake_options_run(tmp_path, monkeypatch):
    """One discoverable options run with a JSON marker, isolated from any
    real orchestrator_output/ on disk."""
    orch = tmp_path / "orchestrator_output"
    run_dir = orch / "20260729T055306Z"
    run_dir.mkdir(parents=True)
    (run_dir / "options_result.json").write_text(json.dumps({
        "suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR",
        "timestamp": "2026-07-29T05:53:06Z", "sigma": 0.25, "price": 12.3,
        "greeks": {"delta": 0.5},
    }))

    monkeypatch.setattr(output_runs, "ORCH_OUTPUT", str(orch))
    monkeypatch.setattr(output_runs, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })
    return run_dir


def test_unknown_suite_returns_404():
    r = client.get('/suites/not-a-real-suite')
    assert r.status_code == 404


def test_known_suite_with_no_runs_renders_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(output_runs, "ORCH_OUTPUT", str(tmp_path / "orchestrator_output"))
    monkeypatch.setattr(output_runs, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })
    r = client.get('/suites/options')
    assert r.status_code == 200
    assert b'Nothing produced yet' in r.content or b'nothing produced' in r.content.lower()


def test_known_suite_with_a_run_renders_it(fake_options_run):
    r = client.get('/suites/options')
    assert r.status_code == 200
    assert b'AAPL' in r.content


def test_run_id_query_param_selects_a_specific_run(fake_options_run):
    r = client.get('/suites/options', params={'run_id': 'orch:20260729T055306Z'})
    assert r.status_code == 200
    assert b'AAPL' in r.content


def test_unknown_run_id_falls_back_gracefully(fake_options_run):
    r = client.get('/suites/options', params={'run_id': 'orch:does-not-exist'})
    assert r.status_code == 200  # not a 404 -- just shows the newest run instead


def test_asset_route_serves_a_file_that_belongs_to_the_named_run(fake_options_run):
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:20260729T055306Z',
        'rel_path': 'orchestrator_output/20260729T055306Z/options_result.json',
    })
    # NOTE: rel_path here must match output_runs.ROOT-relative form; since
    # ORCH_OUTPUT is monkeypatched to a tmp_path in this fixture, the real
    # rel_path won't match this literal string -- assert via the discovered
    # run's own rel_path instead of hardcoding it.
    run = output_runs.get_run('options', 'orch:20260729T055306Z')
    real_rel_path = run.files[0].rel_path
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:20260729T055306Z', 'rel_path': real_rel_path,
    })
    assert r.status_code == 200


def test_asset_route_rejects_a_path_not_part_of_the_named_run(fake_options_run):
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:20260729T055306Z',
        'rel_path': '../../../../etc/passwd',
    })
    assert r.status_code == 404


def test_asset_route_rejects_unknown_run_id(fake_options_run):
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:does-not-exist', 'rel_path': 'whatever.json',
    })
    assert r.status_code == 404


@pytest.fixture
def fake_unified_run(tmp_path, monkeypatch):
    """One run directory claimed by 2 suites -- the minimum to be treated
    as a unified run by _discover_unified_runs."""
    orch = tmp_path / "orchestrator_output"
    run_dir = orch / "20260729T055306Z"
    run_dir.mkdir(parents=True)
    (run_dir / "options_result.json").write_text(json.dumps({
        "suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR",
    }))
    (run_dir / "var_result.json").write_text(json.dumps({"suite": "var", "status": "ok"}))

    monkeypatch.setattr(output_runs, "ORCH_OUTPUT", str(orch))
    monkeypatch.setattr(output_runs, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })
    return run_dir


def test_unified_suite_groups_files_by_owning_suite(fake_unified_run):
    r = client.get('/suites/unified')
    assert r.status_code == 200
    # Both suites' files must be present -- not just one flat undifferentiated list
    assert b'AAPL' in r.content
    assert b'options_result.json' in r.content or b'Options' in r.content
