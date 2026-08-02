"""test_dispatch.py

Covers Task 9 of docs/superpowers/plans/2026-08-01-quant-console.md:
`POST /runs/{run_id}/dispatch/{action}`, the headless `claude -p` worker
dispatch endpoint (interpret/investigate/explain) against a completed run.

Everything that would actually shell out is mocked:
  - `dashboard.job_object.run_with_job_object` (Task 10 stub today; the real
    subprocess launch point either way) stands in for `subprocess.Popen` --
    mocking it here is equivalent to mocking `subprocess.Popen` directly per
    the plan's Testing convention, one layer up at the boundary Task 9
    actually calls through.
  - `dashboard.worker_worktree.create_worker_worktree` (Task 11 stub today)
    is mocked so `investigate` tests never shell out to real `git worktree
    add` / leave real worktrees behind.

Depends on Task 7 (`require_dispatch_configured`) and Task 8
(`build_worker_env`), both already landed and imported directly here.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402
import dashboard.auth as auth  # noqa: E402
from dashboard.worker_env import build_worker_env  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)

AUTH_HEADERS = {'Authorization': 'Bearer a-real-operator-configured-key'}


@pytest.fixture(autouse=True)
def _isolate_dispatch_state(monkeypatch):
    """Give each test a clean `_RUNS` / `_DISPATCH_JOBS` /
    `_DISPATCH_IDEMPOTENCY` slate, and a real (non-default) configured API
    key so `require_dispatch_configured` doesn't 503 tests that aren't
    specifically exercising that path.
    """
    saved_runs = dict(dashboard_app._RUNS)
    saved_jobs = dict(dashboard_app._DISPATCH_JOBS)
    saved_idem = dict(dashboard_app._DISPATCH_IDEMPOTENCY)
    dashboard_app._RUNS.clear()
    dashboard_app._DISPATCH_JOBS.clear()
    dashboard_app._DISPATCH_IDEMPOTENCY.clear()
    monkeypatch.setattr(auth, 'API_KEY', 'a-real-operator-configured-key')

    yield

    dashboard_app._RUNS.clear()
    dashboard_app._RUNS.update(saved_runs)
    dashboard_app._DISPATCH_JOBS.clear()
    dashboard_app._DISPATCH_JOBS.update(saved_jobs)
    dashboard_app._DISPATCH_IDEMPOTENCY.clear()
    dashboard_app._DISPATCH_IDEMPOTENCY.update(saved_idem)


def _make_done_run(tmp_path, run_key='test-dispatch-1', with_results=True):
    """A completed run in `_RUNS` with a real output_dir on disk, optionally
    containing a quant_summary.json + one *_result.json (the evidence paths
    the dispatch prompt is supposed to reference).
    """
    output_dir = tmp_path / run_key
    output_dir.mkdir()
    if with_results:
        (output_dir / 'quant_summary.json').write_text('{"schema_version": 1}', encoding='utf-8')
        (output_dir / 'vol_result.json').write_text('{"status": "ok"}', encoding='utf-8')
    dashboard_app._RUNS[run_key] = {
        'status': 'ok', 'output_dir': str(output_dir),
    }
    return run_key, output_dir


def _fake_proc(poll_returns=None):
    """A MagicMock standing in for the subprocess.Popen-shaped object
    `job_object.run_with_job_object` returns. `poll()` returns
    `poll_returns` (None == still running, matching real Popen.poll()).
    """
    proc = MagicMock()
    proc.poll.return_value = poll_returns
    proc.pid = 4242
    return proc


def _patch_launch(monkeypatch, proc=None):
    """Mock `dashboard.app.job_object.run_with_job_object` and return the
    MagicMock so callers can assert on call args.
    """
    mock_launch = MagicMock(return_value=proc or _fake_proc())
    monkeypatch.setattr(dashboard_app.job_object, 'run_with_job_object', mock_launch)
    return mock_launch


def _patch_worktree(monkeypatch, tmp_path):
    mock_worktree = MagicMock(return_value=tmp_path / 'fake-worktree')
    monkeypatch.setattr(dashboard_app.worker_worktree, 'create_worker_worktree', mock_worktree)
    return mock_worktree


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------

class TestHappyPath:
    def test_interpret_returns_202_with_job_id(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        _patch_launch(monkeypatch)

        resp = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})

        assert resp.status_code == 202
        body = resp.json()
        assert body['run_id'] == run_key
        assert body['action'] == 'interpret'
        assert body['job_id']
        assert body['status'] == 'running'
        assert body['poll'] == f'/runs/{run_key}/dispatch/{body["job_id"]}'

    def test_job_is_tracked_in_dispatch_registry(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        _patch_launch(monkeypatch)

        resp = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})
        job_id = resp.json()['job_id']

        assert job_id in dashboard_app._DISPATCH_JOBS
        assert dashboard_app._DISPATCH_JOBS[job_id]['run_id'] == run_key
        assert dashboard_app._DISPATCH_JOBS[job_id]['action'] == 'interpret'


# --------------------------------------------------------------------------
# validation / not-found / not-done
# --------------------------------------------------------------------------

class TestValidation:
    def test_unknown_action_returns_400(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        resp = client.post(f'/runs/{run_key}/dispatch/summon', headers=AUTH_HEADERS, json={})

        assert resp.status_code == 400
        launch.assert_not_called()

    def test_unknown_run_id_returns_404(self, monkeypatch):
        launch = _patch_launch(monkeypatch)

        resp = client.post('/runs/definitely-not-a-real-run/dispatch/interpret',
                           headers=AUTH_HEADERS, json={})

        assert resp.status_code == 404
        launch.assert_not_called()

    def test_running_run_returns_409_not_400(self, monkeypatch):
        dashboard_app._RUNS['test-still-running'] = {'status': 'running'}
        launch = _patch_launch(monkeypatch)

        resp = client.post('/runs/test-still-running/dispatch/interpret',
                           headers=AUTH_HEADERS, json={})

        assert resp.status_code == 409
        launch.assert_not_called()

    def test_queued_run_also_returns_409(self, monkeypatch):
        dashboard_app._RUNS['test-still-queued'] = {'status': 'queued'}
        launch = _patch_launch(monkeypatch)

        resp = client.post('/runs/test-still-queued/dispatch/interpret',
                           headers=AUTH_HEADERS, json={})

        assert resp.status_code == 409
        launch.assert_not_called()


# --------------------------------------------------------------------------
# auth (Task 7's require_dispatch_configured layered on verify_api_key)
# --------------------------------------------------------------------------

class TestAuth:
    def test_missing_api_key_returns_401(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        resp = client.post(f'/runs/{run_key}/dispatch/interpret', json={})

        assert resp.status_code == 401
        launch.assert_not_called()

    def test_default_key_returns_503(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)
        monkeypatch.setattr(auth, 'API_KEY', auth.DEFAULT_API_KEY)

        resp = client.post(f'/runs/{run_key}/dispatch/interpret',
                           headers={'Authorization': f'Bearer {auth.DEFAULT_API_KEY}'},
                           json={})

        assert resp.status_code == 503
        assert 'dispatch disabled' in resp.json()['detail'].lower()
        launch.assert_not_called()


# --------------------------------------------------------------------------
# env allowlist -- the single highest-value assertion in this file
# --------------------------------------------------------------------------

class TestEnvAllowlist:
    def test_env_passed_to_launch_is_exactly_build_worker_env_output(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        monkeypatch.setenv('DASHBOARD_API_KEY', 'super-secret-value')
        monkeypatch.setenv('THETADATA_CF_ACCESS_CLIENT_SECRET', 'theta-secret')
        launch = _patch_launch(monkeypatch)

        resp = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})
        assert resp.status_code == 202

        _, kwargs = launch.call_args
        assert kwargs['env'] == build_worker_env()
        assert 'DASHBOARD_API_KEY' not in kwargs['env']
        assert 'THETADATA_CF_ACCESS_CLIENT_SECRET' not in kwargs['env']


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------

class TestIdempotency:
    def test_duplicate_idempotency_key_returns_same_job_launches_once(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        first = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS,
                            json={'idempotency_key': 'double-click-guard'})
        second = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS,
                             json={'idempotency_key': 'double-click-guard'})

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()['job_id'] == second.json()['job_id']
        assert launch.call_count == 1

    def test_different_idempotency_key_launches_a_new_job(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        first = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS,
                            json={'idempotency_key': 'key-one'})
        second = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS,
                             json={'idempotency_key': 'key-two'})

        assert first.json()['job_id'] != second.json()['job_id']
        assert launch.call_count == 2


# --------------------------------------------------------------------------
# concurrency cap
# --------------------------------------------------------------------------

class TestConcurrencyCap:
    def test_over_cap_returns_429_and_does_not_launch(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        # Pre-populate two still-running dispatch jobs (poll() -> None) to
        # saturate MAX_CONCURRENT_DISPATCH_JOBS before this request.
        for i in range(dashboard_app.MAX_CONCURRENT_DISPATCH_JOBS):
            dashboard_app._DISPATCH_JOBS[f'preexisting-{i}'] = {
                'job_id': f'preexisting-{i}', 'run_id': run_key, 'action': 'interpret',
                'status': 'running', 'proc': _fake_proc(poll_returns=None),
            }

        resp = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})

        assert resp.status_code == 429
        launch.assert_not_called()

    def test_completed_jobs_do_not_count_against_the_cap(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        # These have exited (poll() -> 0), so they must not count as active.
        for i in range(dashboard_app.MAX_CONCURRENT_DISPATCH_JOBS):
            dashboard_app._DISPATCH_JOBS[f'finished-{i}'] = {
                'job_id': f'finished-{i}', 'run_id': run_key, 'action': 'interpret',
                'status': 'completed', 'proc': _fake_proc(poll_returns=0),
            }

        resp = client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})

        assert resp.status_code == 202
        launch.assert_called_once()


# --------------------------------------------------------------------------
# per-action tool scoping / prompt construction
# --------------------------------------------------------------------------

class TestPerActionScoping:
    def test_interpret_and_investigate_get_no_network_tools(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        _patch_worktree(monkeypatch, tmp_path)
        launch = _patch_launch(monkeypatch)

        client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})
        command = launch.call_args.args[0] if launch.call_args.args else launch.call_args.kwargs['command']
        joined = ' '.join(command)
        assert 'WebSearch' in joined and 'WebFetch' in joined
        assert '--disallowedTools' in command

    def test_explain_gets_network_tools_and_no_bash(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        client.post(f'/runs/{run_key}/dispatch/explain', headers=AUTH_HEADERS, json={})
        command = launch.call_args.args[0] if launch.call_args.args else launch.call_args.kwargs['command']
        joined = ' '.join(command)
        assert 'Bash' in joined
        assert 'WebSearch' not in joined
        assert 'WebFetch' not in joined

    def test_prompt_references_result_paths_not_inlined(self, tmp_path, monkeypatch):
        run_key, output_dir = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})
        command = launch.call_args.args[0] if launch.call_args.args else launch.call_args.kwargs['command']
        prompt = command[command.index('-p') + 1]

        assert str(output_dir / 'quant_summary.json') in prompt
        assert str(output_dir / 'vol_result.json') in prompt
        # The raw file *contents* must not be inlined into the prompt.
        assert '"schema_version": 1' not in prompt

    def test_investigate_prompt_instructs_no_commit_no_push(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        _patch_worktree(monkeypatch, tmp_path)
        launch = _patch_launch(monkeypatch)

        client.post(f'/runs/{run_key}/dispatch/investigate', headers=AUTH_HEADERS, json={})
        command = launch.call_args.args[0] if launch.call_args.args else launch.call_args.kwargs['command']
        prompt = command[command.index('-p') + 1]

        assert 'do not commit' in prompt.lower() or 'not commit' in prompt.lower()
        assert 'push' in prompt.lower()

    def test_investigate_uses_worktree_path_as_cwd(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        worktree_mock = _patch_worktree(monkeypatch, tmp_path)
        launch = _patch_launch(monkeypatch)

        client.post(f'/runs/{run_key}/dispatch/investigate', headers=AUTH_HEADERS, json={})

        worktree_mock.assert_called_once()
        _, kwargs = launch.call_args
        assert kwargs['cwd'] == str(tmp_path / 'fake-worktree')

    def test_interpret_uses_repo_root_as_cwd(self, tmp_path, monkeypatch):
        run_key, _ = _make_done_run(tmp_path)
        launch = _patch_launch(monkeypatch)

        client.post(f'/runs/{run_key}/dispatch/interpret', headers=AUTH_HEADERS, json={})

        _, kwargs = launch.call_args
        assert kwargs['cwd'] == dashboard_app.ROOT
