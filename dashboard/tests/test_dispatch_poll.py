"""test_dispatch_poll.py

Covers Task 12 of docs/superpowers/plans/2026-08-01-quant-console.md:
`GET /runs/{run_id}/dispatch/{job_id}` -- the poll route for a dispatch job
launched by `POST /runs/{run_id}/dispatch/{action}` (Task 9), plus the
worker-report write path (`orchestrator_output/<run_id>/quant_worker_<action>_
<job_id>.json`, temp-file + atomic rename, same pattern as `_write_quant_summary`
from Task 4) that `_watch_dispatch_job` (Task 10) is responsible for once a
dispatch job finishes.

No auth (this dashboard is localhost-only, single-user -- dashboard/auth.py).

Everything that would actually shell out is mocked, same boundary
test_dispatch.py already established: `dashboard.job_object.run_with_job_object`
stands in for the real subprocess launch.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)

# No auth required (dashboard/auth.py) -- kept as a no-op dict so call
# sites below don't need touching if that ever changes.
AUTH_HEADERS = {}


@pytest.fixture(autouse=True)
def _isolate_dispatch_state(monkeypatch):
    """Clean `_RUNS` / `_DISPATCH_JOBS` / `_DISPATCH_IDEMPOTENCY` slate.

    Also stubs `_insert_run_row` -- see test_dispatch.py's fixture docstring
    for why (this file's dispatch-launch helper calls the same route and
    was contributing to the same real-DB pollution).
    """
    saved_runs = dict(dashboard_app._RUNS)
    saved_jobs = dict(dashboard_app._DISPATCH_JOBS)
    saved_idem = dict(dashboard_app._DISPATCH_IDEMPOTENCY)
    dashboard_app._RUNS.clear()
    dashboard_app._DISPATCH_JOBS.clear()
    dashboard_app._DISPATCH_IDEMPOTENCY.clear()
    monkeypatch.setattr(dashboard_app, '_insert_run_row', MagicMock(return_value=None))

    yield

    dashboard_app._RUNS.clear()
    dashboard_app._RUNS.update(saved_runs)
    dashboard_app._DISPATCH_JOBS.clear()
    dashboard_app._DISPATCH_JOBS.update(saved_jobs)
    dashboard_app._DISPATCH_IDEMPOTENCY.clear()
    dashboard_app._DISPATCH_IDEMPOTENCY.update(saved_idem)


def _fake_proc(poll_returns=None, returncode=0, stdout_text='', timed_out=False):
    proc = MagicMock()
    proc.poll.return_value = poll_returns
    proc.communicate.return_value = (stdout_text, None)
    proc.returncode = returncode
    proc.pid = 4242
    proc.timed_out = timed_out
    return proc


def _patch_launch(monkeypatch, proc=None):
    mock_launch = MagicMock(return_value=proc or _fake_proc())
    monkeypatch.setattr(dashboard_app.job_object, 'run_with_job_object', mock_launch)
    return mock_launch


def _make_done_run(tmp_path, run_key='test-poll-1'):
    output_dir = tmp_path / run_key
    output_dir.mkdir()
    (output_dir / 'quant_summary.json').write_text('{"schema_version": 1}', encoding='utf-8')
    dashboard_app._RUNS[run_key] = {'status': 'ok', 'output_dir': str(output_dir)}
    return run_key, output_dir


def _insert_job(run_key, output_dir, job_id='job-fixed-1', action='interpret',
                status='running', stdout='', proc=None, **extra):
    """Insert a dispatch job directly into `_DISPATCH_JOBS`, bypassing the
    POST route -- lets poll-route tests set up any state combination
    (queued/running/completed/failed/timed_out) without needing a real
    dispatch round trip for every case.
    """
    job = {
        'job_id': job_id, 'run_id': run_key, 'action': action,
        'status': status, 'proc': proc or _fake_proc(),
        'output_dir': str(output_dir), 'cwd': dashboard_app.ROOT,
        'queued_at': '2026-08-01T00:00:00Z', 'started_at': '2026-08-01T00:00:01Z',
        'completed_at': None, 'result': None, 'stdout': stdout,
    }
    job.update(extra)
    dashboard_app._DISPATCH_JOBS[job_id] = job
    return job_id


def _write_report(output_dir, action, job_id, payload):
    path = Path(output_dir) / f'quant_worker_{action}_{job_id}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


# --------------------------------------------------------------------------
# no auth (this dashboard is localhost-only, single-user -- dashboard/auth.py)
# --------------------------------------------------------------------------

class TestNoAuth:
    def test_poll_succeeds_with_no_authorization_header_at_all(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir)

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}')

        assert resp.status_code == 200


# --------------------------------------------------------------------------
# not found
# --------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_job_id_returns_404(self, tmp_path):
        run_key, _ = _make_done_run(tmp_path)

        resp = client.get(f'/runs/{run_key}/dispatch/no-such-job', headers=AUTH_HEADERS)

        assert resp.status_code == 404

    def test_job_id_belonging_to_a_different_run_returns_404(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir)

        resp = client.get(f'/runs/some-other-run/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 404


# --------------------------------------------------------------------------
# response shape at each state -- mirrors GET /runs/{run_id}'s precedent
# (status/done/stdout/result), per the design spec's own wording.
# --------------------------------------------------------------------------

class TestResponseShape:
    def test_queued_job_shape(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='queued',
                             proc=_fake_proc(poll_returns=None))

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['job_id'] == job_id
        assert body['run_id'] == run_key
        assert body['status'] == 'queued'
        assert body['done'] is False
        assert body['result'] is None

    def test_running_job_shape(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='running')

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'running'
        assert body['done'] is False
        assert body['result'] is None

    def test_completed_job_with_report_returns_result(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='completed',
                             stdout='{"result": "looks fine"}',
                             completed_at='2026-08-01T00:05:00Z')
        report = {
            'schema_version': 1, 'worker': 'claude', 'action': 'interpret',
            'job_id': job_id, 'status': 'completed', 'headline': 'looks fine',
            'detail': 'looks fine', 'created_at_utc': '2026-08-01T00:05:00Z',
        }
        _write_report(output_dir, 'interpret', job_id, report)

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'completed'
        assert body['done'] is True
        assert body['result'] == report

    def test_failed_job_shape(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='failed',
                             stdout='some error output',
                             completed_at='2026-08-01T00:05:00Z')

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'failed'
        assert body['done'] is True
        assert 'some error output' in body['stdout']

    def test_timed_out_job_shape_retains_partial_stdout(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='timed_out',
                             stdout='partial output before the kill',
                             completed_at='2026-08-01T00:05:00Z')

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'timed_out'
        assert body['done'] is True
        assert 'partial output before the kill' in body['stdout']

    def test_stdout_is_tailed_not_unbounded(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        huge = 'x' * 50000
        job_id = _insert_job(run_key, output_dir, status='completed', stdout=huge,
                             completed_at='2026-08-01T00:05:00Z')

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        assert len(resp.json()['stdout']) < len(huge)


# --------------------------------------------------------------------------
# degraded handling -- malformed/missing worker report file must not crash
# the poll (Task 3's "degraded" pattern is the model to follow here).
# --------------------------------------------------------------------------

class TestDegradedReport:
    def test_malformed_report_file_degrades_instead_of_crashing(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='completed',
                             completed_at='2026-08-01T00:05:00Z')
        report_path = Path(output_dir) / f'quant_worker_interpret_{job_id}.json'
        report_path.write_text('{not valid json', encoding='utf-8')

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'completed'
        assert body['done'] is True
        assert body['result'] is not None
        assert body['result'].get('status') == 'degraded'

    def test_missing_report_file_does_not_crash_poll(self, tmp_path):
        run_key, output_dir = _make_done_run(tmp_path)
        job_id = _insert_job(run_key, output_dir, status='completed',
                             completed_at='2026-08-01T00:05:00Z')
        # No quant_worker_*.json written at all (e.g. the write itself failed).

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'completed'
        assert body['done'] is True
        assert body['result'] is None

    def test_missing_output_dir_does_not_crash_poll(self, tmp_path):
        run_key = 'no-output-dir-run'
        job_id = _insert_job(run_key, tmp_path / 'never-created', status='completed',
                             completed_at='2026-08-01T00:05:00Z')

        resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)

        assert resp.status_code == 200
        body = resp.json()
        assert body['result'] is None


# --------------------------------------------------------------------------
# end-to-end: _watch_dispatch_job actually writes the report file the poll
# route above reads, via a real POST dispatch + mocked launch. TestClient
# runs BackgroundTasks synchronously, so no polling loop is needed here.
# --------------------------------------------------------------------------

class TestWorkerReportWritePath:
    def test_completed_dispatch_writes_report_file_atomically_readable_by_poll(
            self, tmp_path, monkeypatch):
        run_key, output_dir = _make_done_run(tmp_path)
        proc = _fake_proc(poll_returns=0, returncode=0,
                          stdout_text='{"result": "worker interpretation text"}')
        _patch_launch(monkeypatch, proc=proc)

        post_resp = client.post(f'/runs/{run_key}/dispatch/interpret',
                                headers=AUTH_HEADERS, json={})
        job_id = post_resp.json()['job_id']

        report_path = Path(output_dir) / f'quant_worker_interpret_{job_id}.json'
        assert report_path.is_file()
        on_disk = json.loads(report_path.read_text(encoding='utf-8'))
        assert on_disk['worker'] == 'claude'
        assert on_disk['action'] == 'interpret'
        assert on_disk['job_id'] == job_id
        assert on_disk['status'] == 'completed'
        assert 'created_at_utc' in on_disk
        assert 'worker interpretation text' in on_disk.get('detail', '')

        # No leftover temp file from the atomic-rename dance.
        leftovers = list(Path(output_dir).glob(f'quant_worker_interpret_{job_id}.json.tmp-*'))
        assert leftovers == []

        poll_resp = client.get(f'/runs/{run_key}/dispatch/{job_id}', headers=AUTH_HEADERS)
        assert poll_resp.status_code == 200
        assert poll_resp.json()['result'] == on_disk

    def test_failed_dispatch_writes_report_with_failed_status(self, tmp_path, monkeypatch):
        run_key, output_dir = _make_done_run(tmp_path)
        proc = _fake_proc(poll_returns=1, returncode=1, stdout_text='boom, worker crashed')
        _patch_launch(monkeypatch, proc=proc)

        post_resp = client.post(f'/runs/{run_key}/dispatch/interpret',
                                headers=AUTH_HEADERS, json={})
        job_id = post_resp.json()['job_id']

        report_path = Path(output_dir) / f'quant_worker_interpret_{job_id}.json'
        assert report_path.is_file()
        on_disk = json.loads(report_path.read_text(encoding='utf-8'))
        assert on_disk['status'] == 'failed'

    def test_investigate_report_includes_worktree_path(self, tmp_path, monkeypatch):
        run_key, output_dir = _make_done_run(tmp_path)
        worktree_dir = tmp_path / 'fake-worktree'
        monkeypatch.setattr(dashboard_app.worker_worktree, 'create_worker_worktree',
                            MagicMock(return_value=worktree_dir))
        proc = _fake_proc(poll_returns=0, returncode=0, stdout_text='{"result": "investigated"}')
        _patch_launch(monkeypatch, proc=proc)

        post_resp = client.post(f'/runs/{run_key}/dispatch/investigate',
                                headers=AUTH_HEADERS, json={})
        job_id = post_resp.json()['job_id']

        report_path = Path(output_dir) / f'quant_worker_investigate_{job_id}.json'
        on_disk = json.loads(report_path.read_text(encoding='utf-8'))
        assert on_disk.get('worktree_path') == str(worktree_dir)


# --------------------------------------------------------------------------
# quant.html wiring -- same "mechanically checkable without a browser"
# posture test_quant_view.py already established for Task 6 (this route's
# own UI checklist item is a manual smoke test, not TDD-with-fixtures; see
# that file's module docstring). Cheap regression guard against a typo'd
# path/action name in the template's dispatch JS, not a substitute for the
# manual round trip.
# --------------------------------------------------------------------------

class TestQuantViewDispatchWiring:
    def test_quant_route_wires_dispatch_buttons_for_all_three_actions(self):
        resp = client.get('/quant')
        html = resp.text
        for action in ('interpret', 'investigate', 'explain'):
            assert f'data-action="{action}"' in html, action

    def test_quant_route_client_js_calls_dispatch_endpoints(self):
        resp = client.get('/quant')
        html = resp.text
        assert '/dispatch/' in html
        assert 'dispatchWorker' in html
        assert 'pollDispatchJob' in html

    def test_render_worker_report_prefers_report_status_over_job_status(self):
        """Fix for a review finding on this task: a malformed worker report
        file makes the backend return `result.status == "degraded"` while
        `job.status` itself stays "completed" (the *process* exited fine --
        it's the report file that's bad). `renderWorkerReport` must read
        `report.status` first, falling back to `job.status` only when there
        is no report (done-but-no-report-file case) -- otherwise a degraded
        report silently renders with "completed" styling/text, defeating the
        whole point of the `status-degraded` CSS treatment already defined
        in this file. String/structure assertion, same "mechanically
        checkable without a browser" posture as this class's other tests.
        """
        resp = client.get('/quant')
        html = resp.text
        assert "var status = (report && report.status) || job.status || 'running';" in html

    def test_run_pill_classes_cover_dispatch_job_vocabulary(self):
        """Second half of the same finding: RUN_PILL_CLASSES was built for
        the orchestrator run-status vocabulary (ok/partial/running/queued/
        error/timeout/failed) and didn't recognize 'completed' or
        'timed_out' -- the dispatch job vocabulary this task introduces --
        so both fell back to the neutral `.pill.plain` treatment. They must
        now map onto a non-plain pill class (and 'degraded' must map onto
        the existing amber `.pill.degraded` rule modulePillClass already
        uses), so a completed/timed-out/degraded dispatch job's status pill
        is visually distinct from the "not run yet" default.
        """
        resp = client.get('/quant')
        html = resp.text
        assert "completed: 'ok'" in html
        assert "timed_out: 'failed'" in html
        assert "degraded: 'degraded'" in html
