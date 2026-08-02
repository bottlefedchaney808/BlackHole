"""dashboard/job_object.py

Task 10 of docs/superpowers/plans/2026-08-01-quant-console.md ("Windows Job
Object timeout enforcement"). Task 9's dispatch route
(`POST /runs/{run_id}/dispatch/{action}` in dashboard/app.py) launches every
headless `claude -p` worker (interpret/investigate/explain) through this
module's `run_with_job_object()` instead of a bare `subprocess.Popen`.

Why a Job Object, not a plain child-process kill (spec Phase 2 / Error
Handling): `claude -p` is itself agentic and can spawn its own tool-call
subprocesses. This repo runs on Windows, where POSIX process-group kill
(`os.killpg`) doesn't exist, and `taskkill /F /T`'s PID/PPID tree-walk has a
known orphan/reparent gap -- an intermediate process exiting before spawning
its own child can reparent that child out of the tree `taskkill` would
otherwise kill. A Windows Job Object closes that gap: once a process is
assigned to a job, every descendant it spawns is bound to the same job
(unless the job explicitly allows breakaway, which this module never sets),
so terminating the *job* kills the whole tree regardless of how it
reparents. `taskkill /F /T /PID <pid>` is kept only as an explicit,
degraded fallback -- used solely when Job Object creation/assignment itself
fails (e.g. insufficient privilege) -- never as a co-equal alternative.

All `pywin32` usage (`win32job`/`win32api`/`win32con`) is isolated in this
one module so it is easy to find, easy to mock in tests
(dashboard/tests/test_job_object.py mocks these module-level names directly
rather than exercising a real OS-level Job Object), and easy to replace if
it ever needs to be.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from typing import Dict, List, Optional

import win32api
import win32con
import win32job

# Per-action timeout in seconds (plan Task 10 / spec Phase 2): interpret and
# explain are short, single-pass reads; investigate is a longer, code-
# touching pass in an isolated worktree. Read from this small constant dict
# by dashboard/app.py's dispatch route -- never hardcoded inline per call
# site.
DEFAULT_TIMEOUT_SEC: Dict[str, int] = {
    'interpret': 300,
    'explain': 300,
    'investigate': 1200,
}


class JobObjectProcess:
    """Wraps a `subprocess.Popen` assigned to a Windows Job Object.

    Delegates everything Popen-shaped (`.poll()`, `.pid`, `.stdout`,
    `.returncode`, `.communicate()`, ...) to the wrapped process via
    `__getattr__`, so callers that only need Popen's usual surface (e.g.
    `dashboard/app.py::_count_active_dispatch_jobs`'s `proc.poll()`) don't
    need to know this isn't a bare Popen.

    `.terminate()` is overridden: it kills the *whole job* (every
    descendant the worker process spawned), not just the tracked PID, via
    `win32job.TerminateJobObject` -- or, if Job Object creation/assignment
    failed at launch (`used_fallback` is True), via `taskkill /F /T /PID`.

    An internal watchdog `threading.Timer` fires `terminate()` after
    `timeout_sec` if the process is still running, and sets `timed_out`
    beforehand so callers (dashboard/app.py's dispatch wiring) can tell a
    timeout-kill apart from a normal exit or an operator-initiated kill.
    """

    def __init__(self, popen: subprocess.Popen, job_handle: Optional[object],
                 used_fallback: bool, timeout_sec: int) -> None:
        self._popen = popen
        self._job_handle = job_handle
        self.used_fallback = used_fallback
        self.timeout_sec = timeout_sec
        self.timed_out = False
        self._terminated = False
        self._terminate_lock = threading.Lock()

        self._timer = threading.Timer(timeout_sec, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    # -- watchdog -----------------------------------------------------

    def _on_timeout(self) -> None:
        """Watchdog callback: fires once, `timeout_sec` after launch. A
        no-op if the process already exited on its own by then.
        """
        if self._popen.poll() is None:
            self.timed_out = True
            print(f'  [job_object] worker pid={self.pid} exceeded its '
                  f'{self.timeout_sec}s timeout -- terminating job',
                  file=sys.stderr)
            self.terminate()

    # -- termination ----------------------------------------------------

    def terminate(self) -> None:
        """Kill the whole job (every descendant), not just the tracked PID.

        Idempotent -- a second call is a no-op, so both the watchdog timer
        and a caller can safely call this without coordinating.
        """
        with self._terminate_lock:
            if self._terminated:
                return
            self._terminated = True
            self._timer.cancel()

            if self._job_handle is not None and not self.used_fallback:
                win32job.TerminateJobObject(self._job_handle, 1)
                return

            # Degraded fallback: only reached when Job Object
            # creation/assignment itself failed at launch. Logged clearly --
            # this is not an equivalent guarantee (spec Error Handling /
            # plan Task 10: taskkill /T's PID/PPID tree-walk can miss a
            # reparented descendant).
            print(f'  [job_object] WARNING: using taskkill /F /T fallback '
                  f'for pid={self.pid} -- Job Object was unavailable for '
                  f'this worker, so descendant processes may survive',
                  file=sys.stderr)
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(self.pid)],
                capture_output=True, text=True,
            )

    # -- Popen-shaped surface --------------------------------------------

    def wait(self, timeout: Optional[float] = None):
        """Delegates to the wrapped Popen, then cancels the watchdog timer
        (the process has exited, whether or not it was this timer that
        caused it) so it doesn't linger as a background thread.
        """
        result = self._popen.wait(timeout=timeout)
        self._timer.cancel()
        return result

    def communicate(self, input=None, timeout: Optional[float] = None):
        """Delegates to the wrapped Popen's `communicate()`, then cancels
        the watchdog timer.

        Callers that need this process's captured stdout (dashboard/app.py's
        dispatch watcher, `_watch_dispatch_job`) must use this instead of
        `wait()` followed by a separate `stdout.read()` -- that combination
        is a documented Python deadlock hazard: if the child writes more
        than the OS pipe buffer before exiting, `wait()` blocks forever
        because nothing is draining the pipe. `communicate()` drains it
        concurrently, which is exactly why it exists.
        """
        result = self._popen.communicate(input=input, timeout=timeout)
        self._timer.cancel()
        return result

    def __getattr__(self, name: str):
        # Only reached for attributes not found on JobObjectProcess itself
        # (poll, pid, stdout, stderr, stdin, returncode, communicate, ...).
        return getattr(self._popen, name)


def _create_job_and_assign(popen: subprocess.Popen):
    """Creates a Windows Job Object and assigns *popen*'s process to it.

    Returns the job handle on success. Raises on any failure (job
    creation, limit-info setup, opening the process handle, or the
    assignment itself) -- callers catch this and fall back to
    `taskkill /F /T` for termination instead of retrying.
    """
    job_handle = win32job.CreateJobObject(None, "")

    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: if this process's handle to the
    # job is ever closed without an explicit TerminateJobObject (e.g. this
    # process crashes), Windows kills every process still in the job --
    # defense in depth against exactly the kind of orphaned-descendant
    # leak this module exists to prevent. CREATE_BREAKAWAY_FROM_JOB is
    # deliberately left unset (not requested anywhere in this module), so
    # descendants spawned by the worker process cannot escape the job.
    basic_limit_info = (0, 0, win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                         0, 0, 0, 0, 0, 0)
    extended_info = (basic_limit_info, (0, 0, 0, 0, 0), 0, 0, 0, 0)
    win32job.SetInformationJobObject(
        job_handle, win32job.JobObjectExtendedLimitInformation, extended_info)

    # AssignProcessToJobObject requires PROCESS_SET_QUOTA | PROCESS_TERMINATE
    # access on the process handle (not the pseudo-handle subprocess.Popen
    # already holds internally), so open a fresh handle by PID.
    process_handle = win32api.OpenProcess(
        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, popen.pid)
    win32job.AssignProcessToJobObject(job_handle, process_handle)
    return job_handle


def run_with_job_object(command: List[str], cwd: Optional[str], env: dict,
                         timeout_sec: int) -> JobObjectProcess:
    """Launch *command* and assign it to a Windows Job Object at launch.

    Returns a `JobObjectProcess` (Popen-shaped -- see its docstring) whose
    `.terminate()` kills the whole job (every descendant), not just the
    launched PID, and which self-terminates after `timeout_sec` if still
    running by then.

    If Job Object creation/assignment fails for any reason, the worker
    process is still launched and returned (a Job Object failure degrades
    the termination guarantee, it must never block the worker from
    running -- spec Error Handling) -- `.terminate()` on the returned
    wrapper falls back to `taskkill /F /T /PID` instead.
    """
    popen = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        job_handle = _create_job_and_assign(popen)
        used_fallback = False
    except Exception as e:
        print(f'  [job_object] WARNING: could not create/assign a Windows '
              f'Job Object for pid={popen.pid} ({type(e).__name__}: {e}) -- '
              f'falling back to taskkill /F /T on termination/timeout. This '
              f'is a degraded guarantee: descendant processes this worker '
              f'spawns may not all be killed.',
              file=sys.stderr)
        job_handle = None
        used_fallback = True

    return JobObjectProcess(popen, job_handle, used_fallback, timeout_sec)
