"""test_job_object.py

Covers Task 10 of docs/superpowers/plans/2026-08-01-quant-console.md:
`dashboard.job_object`, the Windows Job Object wrapper Task 9's dispatch
route (`POST /runs/{run_id}/dispatch/{action}`) launches every headless
`claude -p` worker through.

Why this exists (design spec Phase 2 / Error Handling, plan Task 10):
`claude -p` is itself agentic and can spawn its own tool-call subprocesses.
A naive "kill the tracked PID" timeout would leave live descendants running
past the timeout (and, for `investigate`, a locked git worktree). Windows
has no `os.killpg`, and `taskkill /F /T`'s PID/PPID tree-walk has a known
orphan/reparent gap when an intermediate process exits before spawning its
own child. The Windows Job Object API is the primary mechanism: assign the
spawned process to a job at launch, and killing the *job* kills every
descendant regardless of how the tree reparents. `taskkill /F /T /PID` is
kept only as an explicit fallback, used solely when Job Object creation/
assignment itself fails.

Per the task's own test step, this must NOT exercise a real OS-level Job
Object -- `win32job`/`win32process`/`win32api` are mocked throughout, and
`subprocess.Popen` itself is mocked so no real child process is ever
spawned by this test file.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.job_object as job_object  # noqa: E402

pytestmark = pytest.mark.unit


def _fake_popen(pid=4242, poll_returns=None, returncode=0):
    """A MagicMock standing in for the `subprocess.Popen` instance
    `run_with_job_object` creates internally.
    """
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = poll_returns
    proc.returncode = returncode
    proc.stdout = MagicMock()
    proc.stdout.read.return_value = 'partial output so far'
    return proc


@pytest.fixture(autouse=True)
def _mock_pywin32_and_popen(monkeypatch):
    """Every test in this file gets a fresh set of mocked `win32job`/
    `win32process`/`win32api`/`win32con` module references (so no real
    Job Object is ever created) plus a mocked `subprocess.Popen` (so no
    real child process is ever spawned). Individual tests further
    configure these mocks as needed and can still reach the *real* objects
    via the `_orig_*` attributes stashed here if ever needed.
    """
    mock_win32job = MagicMock()
    mock_win32api = MagicMock()
    mock_win32con = MagicMock()

    # Give the constants real-ish sentinel values so equality/bitwise use
    # in the implementation doesn't blow up under a MagicMock.
    mock_win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    mock_win32job.JobObjectExtendedLimitInformation = 9
    mock_win32con.PROCESS_SET_QUOTA = 0x0100
    mock_win32con.PROCESS_TERMINATE = 0x0001

    monkeypatch.setattr(job_object, 'win32job', mock_win32job)
    monkeypatch.setattr(job_object, 'win32api', mock_win32api)
    monkeypatch.setattr(job_object, 'win32con', mock_win32con)

    fake_popen_instance = _fake_popen()
    mock_popen_cls = MagicMock(return_value=fake_popen_instance)
    monkeypatch.setattr(job_object.subprocess, 'Popen', mock_popen_cls)

    # No real subprocess.run(taskkill ...) should ever fire in these tests.
    mock_run = MagicMock()
    monkeypatch.setattr(job_object.subprocess, 'run', mock_run)

    yield {
        'win32job': mock_win32job,
        'win32api': mock_win32api,
        'win32con': mock_win32con,
        'popen_cls': mock_popen_cls,
        'popen_instance': fake_popen_instance,
        'subprocess_run': mock_run,
    }


def _launch(monkeypatch, timeout_sec=300):
    """Launch via run_with_job_object with the internal watchdog Timer
    disabled (so tests are deterministic and don't wait on a real timer) --
    timeout behavior itself is tested separately via `_on_timeout()`.
    """
    monkeypatch.setattr(job_object.threading, 'Timer', MagicMock())
    return job_object.run_with_job_object(
        ['claude', '-p', 'do a thing', '--output-format', 'json'],
        cwd=str(REPO_ROOT), env={'PATH': '/x'}, timeout_sec=timeout_sec)


# --------------------------------------------------------------------------
# assignment at launch
# --------------------------------------------------------------------------

class TestAssignmentAtLaunch:
    def test_process_is_assigned_to_a_job_at_launch(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        wrapper = _launch(monkeypatch)

        mocks['win32job'].CreateJobObject.assert_called_once()
        mocks['win32api'].OpenProcess.assert_called_once()
        # The process handle opened for the spawned PID must be the one
        # assigned into the job that was just created.
        job_handle = mocks['win32job'].CreateJobObject.return_value
        process_handle = mocks['win32api'].OpenProcess.return_value
        mocks['win32job'].AssignProcessToJobObject.assert_called_once_with(
            job_handle, process_handle)
        assert wrapper.used_fallback is False

    def test_command_is_launched_via_popen_with_given_cwd_and_env(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        command = ['claude', '-p', 'evaluate', '--output-format', 'json']
        job_object.run_with_job_object(command, cwd='C:\\repo', env={'PATH': 'x'}, timeout_sec=300)

        _, kwargs = mocks['popen_cls'].call_args
        args, _ = mocks['popen_cls'].call_args
        assert args[0] == command
        assert kwargs['cwd'] == 'C:\\repo'
        assert kwargs['env'] == {'PATH': 'x'}

    def test_wrapper_exposes_popen_shaped_interface(self, monkeypatch, _mock_pywin32_and_popen):
        wrapper = _launch(monkeypatch)
        # _count_active_dispatch_jobs() in dashboard/app.py calls .poll();
        # the dispatch-poll route (Task 12) will need .pid/.returncode too.
        assert wrapper.poll() is None
        assert wrapper.pid == 4242


# --------------------------------------------------------------------------
# terminate() kills the whole job, not just the tracked PID
# --------------------------------------------------------------------------

class TestTerminate:
    def test_terminate_calls_job_kill_api_not_popen_kill(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        wrapper = _launch(monkeypatch)

        wrapper.terminate()

        job_handle = mocks['win32job'].CreateJobObject.return_value
        mocks['win32job'].TerminateJobObject.assert_called_once_with(job_handle, 1)
        mocks['popen_instance'].kill.assert_not_called()
        mocks['popen_instance'].terminate.assert_not_called()
        mocks['subprocess_run'].assert_not_called()

    def test_terminate_is_safe_to_call_twice(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        wrapper = _launch(monkeypatch)

        wrapper.terminate()
        wrapper.terminate()

        assert mocks['win32job'].TerminateJobObject.call_count == 1


# --------------------------------------------------------------------------
# fallback path: only reached when Job Object creation/assignment fails
# --------------------------------------------------------------------------

class TestFallback:
    def test_fallback_not_used_on_successful_job_creation(self, monkeypatch, _mock_pywin32_and_popen):
        wrapper = _launch(monkeypatch)
        assert wrapper.used_fallback is False

    def test_create_job_object_failure_triggers_fallback(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        mocks['win32job'].CreateJobObject.side_effect = RuntimeError('access denied')

        wrapper = _launch(monkeypatch)

        assert wrapper.used_fallback is True
        mocks['win32job'].AssignProcessToJobObject.assert_not_called()

    def test_assign_process_failure_also_triggers_fallback(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        mocks['win32job'].AssignProcessToJobObject.side_effect = RuntimeError('boom')

        wrapper = _launch(monkeypatch)

        assert wrapper.used_fallback is True

    def test_process_still_launched_even_if_job_creation_fails(self, monkeypatch, _mock_pywin32_and_popen):
        """A Job Object failure must not prevent the worker from running --
        it only degrades the termination guarantee (spec Error Handling).
        """
        mocks = _mock_pywin32_and_popen
        mocks['win32job'].CreateJobObject.side_effect = RuntimeError('access denied')

        _launch(monkeypatch)

        mocks['popen_cls'].assert_called_once()

    def test_terminate_uses_taskkill_fallback_when_job_creation_failed(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        mocks['win32job'].CreateJobObject.side_effect = RuntimeError('access denied')
        wrapper = _launch(monkeypatch)

        wrapper.terminate()

        mocks['win32job'].TerminateJobObject.assert_not_called()
        mocks['subprocess_run'].assert_called_once()
        taskkill_args = mocks['subprocess_run'].call_args.args[0]
        assert taskkill_args[:2] == ['taskkill', '/F']
        assert '/T' in taskkill_args
        assert '/PID' in taskkill_args
        assert str(wrapper.pid) in taskkill_args


# --------------------------------------------------------------------------
# per-action timeout: read from a constant dict, actually enforced
# --------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_dict_has_short_interpret_explain_and_longer_investigate(self):
        timeouts = job_object.DEFAULT_TIMEOUT_SEC
        assert timeouts['interpret'] == timeouts['explain']
        assert timeouts['investigate'] > timeouts['interpret']

    def test_on_timeout_marks_timed_out_and_terminates_if_still_running(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        wrapper = _launch(monkeypatch, timeout_sec=1)
        mocks['popen_instance'].poll.return_value = None  # still running

        wrapper._on_timeout()

        assert wrapper.timed_out is True
        job_handle = mocks['win32job'].CreateJobObject.return_value
        mocks['win32job'].TerminateJobObject.assert_called_once_with(job_handle, 1)

    def test_on_timeout_is_a_noop_if_process_already_exited(self, monkeypatch, _mock_pywin32_and_popen):
        mocks = _mock_pywin32_and_popen
        wrapper = _launch(monkeypatch, timeout_sec=1)
        mocks['popen_instance'].poll.return_value = 0  # already exited

        wrapper._on_timeout()

        assert wrapper.timed_out is False
        mocks['win32job'].TerminateJobObject.assert_not_called()

    def test_launch_starts_a_watchdog_timer_for_timeout_sec(self, monkeypatch, _mock_pywin32_and_popen):
        mock_timer_cls = MagicMock()
        monkeypatch.setattr(job_object.threading, 'Timer', mock_timer_cls)

        wrapper = job_object.run_with_job_object(
            ['claude', '-p', 'x'], cwd=str(REPO_ROOT), env={}, timeout_sec=42)

        mock_timer_cls.assert_called_once()
        args, _ = mock_timer_cls.call_args
        assert args[0] == 42
        assert args[1] == wrapper._on_timeout
        mock_timer_cls.return_value.start.assert_called_once()
        assert mock_timer_cls.return_value.daemon is True

    def test_wait_cancels_the_watchdog_timer(self, monkeypatch, _mock_pywin32_and_popen):
        mock_timer_cls = MagicMock()
        monkeypatch.setattr(job_object.threading, 'Timer', mock_timer_cls)

        wrapper = job_object.run_with_job_object(
            ['claude', '-p', 'x'], cwd=str(REPO_ROOT), env={}, timeout_sec=42)
        wrapper.wait()

        mock_timer_cls.return_value.cancel.assert_called_once()

    def test_communicate_cancels_the_watchdog_timer_and_returns_output(
            self, monkeypatch, _mock_pywin32_and_popen):
        """`communicate()` (not `wait()` + a separate `stdout.read()`) is
        the deadlock-safe way to drain a PIPE'd subprocess -- `wait()`
        followed by reading stdout separately can hang forever if the
        child writes more than the OS pipe buffer before exiting. The
        dispatch watcher (dashboard/app.py::_watch_dispatch_job) relies on
        `communicate()` for exactly this reason, so it must also cancel
        the watchdog timer, same as `wait()` does.
        """
        mocks = _mock_pywin32_and_popen
        mocks['popen_instance'].communicate.return_value = ('all the output', None)
        mock_timer_cls = MagicMock()
        monkeypatch.setattr(job_object.threading, 'Timer', mock_timer_cls)

        wrapper = job_object.run_with_job_object(
            ['claude', '-p', 'x'], cwd=str(REPO_ROOT), env={}, timeout_sec=42)
        result = wrapper.communicate()

        assert result == ('all the output', None)
        mock_timer_cls.return_value.cancel.assert_called_once()
