"""dashboard/job_object.py

STUB -- Task 10 of docs/superpowers/plans/2026-08-01-quant-console.md
("Windows Job Object timeout enforcement") has not landed yet. Task 9's
dispatch route (`POST /runs/{run_id}/dispatch/{action}` in dashboard/app.py)
needs *something* to launch worker subprocesses through so its own control
flow -- and its tests -- are stable and testable ahead of Task 10, so this
module exists purely to satisfy that call signature today.

The REAL Task 10 implementation must:
  - create a Windows Job Object via pywin32 (`win32job`/`win32process`/
    `win32api`) at launch, with `CREATE_BREAKAWAY_FROM_JOB` disabled so
    descendants stay bound to the job (spec Phase 2 -- `claude -p` is
    agentic and can spawn its own tool-call subprocesses; a naive
    child-only kill would leave live descendants running past a timeout).
  - assign the spawned process to that job immediately at launch.
  - expose a `terminate()` (or equivalent) that kills the *whole job*
    (every descendant), not just the tracked PID.
  - fall back to `taskkill /F /T /PID <pid>` ONLY when Job Object
    creation/assignment itself fails, logging clearly when that fallback
    triggers (it is a degraded guarantee, not a co-equal alternative --
    see the design spec's Phase 2 section for why `taskkill /T`'s
    PID/PPID tree-walk has a known orphan/reparent gap).
  - read per-action timeouts from a small constant dict (already threaded
    through by Task 9 as `dashboard.app.DISPATCH_TIMEOUT_SEC`, passed in
    here as `timeout_sec`) and actually enforce them -- this stub accepts
    `timeout_sec` but does nothing with it.

THIS STUB DOES NONE OF THE ABOVE. It is a plain, synchronous
`subprocess.Popen` wrapper: no Job Object, no descendant tracking, no
timeout enforcement, no tree-kill on termination. It exists only so
Task 9's dispatch endpoint has a real function to call and mock in tests.
Task 10 must replace this module's internals -- while preserving the
`run_with_job_object(command, cwd, env, timeout_sec) -> subprocess.Popen`
call signature, since `dashboard/app.py`'s dispatch route already depends
on exactly that shape.
"""
from __future__ import annotations

import subprocess
from typing import List, Optional


def run_with_job_object(command: List[str], cwd: Optional[str], env: dict,
                         timeout_sec: int) -> subprocess.Popen:
    """STUB -- see module docstring. Task 10 replaces this.

    Launches *command* via a plain ``subprocess.Popen`` with stdout/stderr
    captured as text. ``timeout_sec`` is accepted (to match Task 10's real
    interface) but is otherwise unused here -- nothing in this stub kills
    the process, tracks elapsed time, or protects against orphaned
    descendants. Callers must not assume any timeout is actually enforced
    until Task 10 lands.
    """
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
