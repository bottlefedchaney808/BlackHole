"""test_quant_summary_route.py

Covers Task 4 of docs/superpowers/plans/2026-08-01-quant-console.md:

  * `_execute_run()` calling `shared.summary.build_run_summary()` at the end
    of a run and atomically writing `quant_summary.json` into that run's
    output directory (`_write_quant_summary`).
  * The new `GET /runs/{run_id}/summary` route reading and schema-validating
    that file.

`dashboard.app` is imported in-process (unlike test_env_loading.py's
subprocess isolation -- these tests don't touch DASHBOARD_API_KEY/.env
loading, so there's no reason to pay the subprocess cost). Every
`orchestrator.build_context`/`run_suite`/`run_unified` call is monkeypatched
per test, so no suite subprocess or ThetaData/network call ever actually
runs. Run ids used here are non-numeric strings on purpose: `run_id.lstrip
('-').isdigit()` in both `_execute_run`'s callers and the new route treats a
non-digit id as an in-memory-only key, which means these tests never touch
the real swaps.db `orchestrator_runs` table.
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
import orchestrator  # noqa: E402
from shared.schemas import validate_quant_summary  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


@pytest.fixture(autouse=True)
def _isolate_runs_registry():
    """`_RUNS` is a module-level dict shared across the whole test process --
    give each test a clean slate and restore whatever was there before
    (defensive; in practice nothing else has populated it yet at this point).
    """
    saved = dict(dashboard_app._RUNS)
    dashboard_app._RUNS.clear()
    yield
    dashboard_app._RUNS.clear()
    dashboard_app._RUNS.update(saved)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding='utf-8')


def _vol_result_ok(ticker: str = 'NVDA') -> dict:
    return {
        'status': 'ok',
        'vol_surface': {
            'focus_ticker': ticker,
            'index_ticker': 'SPY',
            'focus': {'fair_vol_pct': 42.0},
            'index': {'fair_vol_pct': 18.0},
            'vol_spread_pts': 24.0,
        },
        'dealer_positioning': {'available': False},
        'gamma_records': [],
    }


def _var_result_ok() -> dict:
    return {
        'status': 'ok', 'module': 'corr_sim', 'var': 1000.0, 'cvar': 1500.0,
        'confidence': 0.99, 'horizon_days': 1,
    }


# --------------------------------------------------------------------------
# _execute_run() -> _write_quant_summary()
# --------------------------------------------------------------------------

class TestExecuteRunWritesQuantSummary:
    def test_completed_suite_run_writes_schema_valid_summary(self, tmp_path, monkeypatch):
        output_dir = tmp_path / 'run1'
        output_dir.mkdir()
        _write_json(output_dir / 'vol_result.json', _vol_result_ok())

        fake_context = {'output_dir': str(output_dir), 'run_id': 'ctx-run-1'}
        monkeypatch.setattr(orchestrator, 'build_context', lambda focus: fake_context)
        monkeypatch.setattr(orchestrator, 'run_suite',
                            lambda name, ctx, timeout=1800: _vol_result_ok())

        dashboard_app._execute_run('test-run-1', 'vol', {'ticker': 'NVDA'})

        summary_path = output_dir / 'quant_summary.json'
        assert summary_path.is_file()

        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        validate_quant_summary(summary)  # raises on any schema violation
        assert summary['run_id'] == 'test-run-1'
        assert summary['ticker'] == 'NVDA'
        assert len(summary['modules']) == 1
        assert summary['modules'][0]['module'] == 'vol'
        assert summary['modules'][0]['status'] == 'ok'

    def test_failed_suite_run_still_gets_a_summary_not_a_missing_file(self, tmp_path, monkeypatch):
        """A run whose suite subprocess failed (run_suite returns an `error`
        dict, no marker file ever gets written) still gets a
        quant_summary.json -- an empty-but-schema-valid `modules` list, not
        a missing file.
        """
        output_dir = tmp_path / 'run2'
        output_dir.mkdir()

        fake_context = {'output_dir': str(output_dir), 'run_id': 'ctx-run-2'}
        monkeypatch.setattr(orchestrator, 'build_context', lambda focus: fake_context)
        monkeypatch.setattr(orchestrator, 'run_suite',
                            lambda name, ctx, timeout=1800: {'error': 'boom'})

        dashboard_app._execute_run('test-run-2', 'vol', {'ticker': 'NVDA'})

        summary_path = output_dir / 'quant_summary.json'
        assert summary_path.is_file()

        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        validate_quant_summary(summary)
        assert summary['modules'] == []

    def test_unified_run_reads_output_dir_from_its_own_result(self, tmp_path, monkeypatch):
        output_dir = tmp_path / 'run3'
        output_dir.mkdir()
        _write_json(output_dir / 'var_result.json', _var_result_ok())

        monkeypatch.setattr(
            orchestrator, 'run_unified',
            lambda focus: {'status': 'ok', 'output_dir': str(output_dir)})

        dashboard_app._execute_run('test-run-3', 'unified', {'ticker': 'AAPL'})

        summary_path = output_dir / 'quant_summary.json'
        assert summary_path.is_file()
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        assert summary['ticker'] == 'AAPL'
        assert [m['module'] for m in summary['modules']] == ['var']

    def test_no_output_dir_is_a_silent_noop_not_a_crash(self, monkeypatch):
        """orchestrator.build_context() itself raising (e.g. bad focus) means
        no output_dir ever existed -- _write_quant_summary must be a no-op,
        not raise, and _execute_run's own status/result recording must still
        have happened.
        """
        def _boom(focus):
            raise ValueError('focus.ticker is required')

        monkeypatch.setattr(orchestrator, 'build_context', _boom)

        dashboard_app._execute_run('test-run-4', 'vol', {'ticker': 'NVDA'})

        with dashboard_app._RUNS_LOCK:
            entry = dict(dashboard_app._RUNS['test-run-4'])
        assert entry['status'] == 'error'
        assert 'focus.ticker is required' in entry['result']['error']


# --------------------------------------------------------------------------
# GET /runs/{run_id}/summary
# --------------------------------------------------------------------------

class TestRunSummaryRoute:
    def test_completed_run_summary_is_fetchable_and_schema_valid(self, tmp_path):
        output_dir = tmp_path / 'served'
        output_dir.mkdir()
        summary = {
            'schema_version': 1, 'run_id': 'test-served', 'ticker': 'NVDA',
            'created_at_utc': '2026-08-01T00:00:00Z', 'modules': [],
        }
        _write_json(output_dir / 'quant_summary.json', summary)

        dashboard_app._RUNS['test-served'] = {
            'status': 'ok', 'output_dir': str(output_dir),
        }

        resp = client.get('/runs/test-served/summary')
        assert resp.status_code == 200
        body = resp.json()
        assert body['run_id'] == 'test-served'
        assert body['ticker'] == 'NVDA'

    def test_unknown_run_id_returns_404(self):
        resp = client.get('/runs/definitely-not-a-real-run/summary')
        assert resp.status_code == 404
        assert 'no run' in resp.json()['error'].lower()

    def test_running_run_returns_404_distinct_from_unknown_run(self):
        dashboard_app._RUNS['test-running'] = {'status': 'running'}

        running_resp = client.get('/runs/test-running/summary')
        unknown_resp = client.get('/runs/definitely-not-a-real-run/summary')

        assert running_resp.status_code == 404
        assert unknown_resp.status_code == 404
        assert 'not done yet' in running_resp.json()['error'].lower()
        assert running_resp.json()['error'] != unknown_resp.json()['error']

    def test_queued_run_also_returns_the_not_done_404(self):
        dashboard_app._RUNS['test-queued'] = {'status': 'queued'}
        resp = client.get('/runs/test-queued/summary')
        assert resp.status_code == 404
        assert 'not done yet' in resp.json()['error'].lower()

    def test_done_run_with_no_summary_file_returns_404(self, tmp_path):
        output_dir = tmp_path / 'nofile'
        output_dir.mkdir()
        dashboard_app._RUNS['test-nofile'] = {
            'status': 'ok', 'output_dir': str(output_dir),
        }

        resp = client.get('/runs/test-nofile/summary')
        assert resp.status_code == 404
        assert 'quant_summary.json' in resp.json()['error']

    def test_done_run_with_no_known_output_dir_returns_404(self):
        dashboard_app._RUNS['test-nodir'] = {'status': 'ok'}
        resp = client.get('/runs/test-nodir/summary')
        assert resp.status_code == 404

    def test_corrupt_summary_file_returns_500_not_silently_served(self, tmp_path):
        output_dir = tmp_path / 'corrupt'
        output_dir.mkdir()
        # Valid JSON, but missing every required quant_summary field.
        (output_dir / 'quant_summary.json').write_text('{"not": "valid"}', encoding='utf-8')
        dashboard_app._RUNS['test-corrupt'] = {
            'status': 'ok', 'output_dir': str(output_dir),
        }

        resp = client.get('/runs/test-corrupt/summary')
        assert resp.status_code == 500

    def test_unified_run_summary_served_via_result_output_dir(self, tmp_path):
        """A unified run doesn't set live['output_dir'] directly -- only
        live['result']['output_dir'] -- so the route must fall back to that.
        """
        output_dir = tmp_path / 'unified'
        output_dir.mkdir()
        summary = {
            'schema_version': 1, 'run_id': 'test-unified', 'ticker': 'AAPL',
            'created_at_utc': '2026-08-01T00:00:00Z', 'modules': [],
        }
        _write_json(output_dir / 'quant_summary.json', summary)

        dashboard_app._RUNS['test-unified'] = {
            'status': 'ok', 'result': {'output_dir': str(output_dir)},
        }

        resp = client.get('/runs/test-unified/summary')
        assert resp.status_code == 200
        assert resp.json()['ticker'] == 'AAPL'
