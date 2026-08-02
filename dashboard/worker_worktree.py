"""dashboard/worker_worktree.py

STUB -- Task 11 of docs/superpowers/plans/2026-08-01-quant-console.md
("Git worktree isolation for `investigate`") has not landed yet. Task 9's
dispatch route (`POST /runs/{run_id}/dispatch/{action}` in dashboard/app.py)
needs an `investigate` job's `cwd` to be an isolated worktree *today* so its
own control flow -- and its tests -- are stable ahead of Task 11, so this
module exists purely to satisfy that call signature now.

The REAL Task 11 implementation must:
  - create the worktree via `git worktree add` at a fresh path such as
    `.worker_worktrees/quant-worker-{job_id}` -- prefixed `quant-worker-`
    on the branch/dir name, which is this repo's orphan-identification
    convention (spec Error Handling: orphaned worktrees left behind after
    a crash must be identifiable by that prefix).
  - have NO automatic cleanup function -- explicit non-goal per the design
    spec (a worktree can hold an uncommitted diff the user still needs).
    Document the manual `git worktree remove <path>` command in this
    module's docstring instead of automating deletion (done below).
  - Task 9's dispatch route is responsible for the `investigate` prompt's
    explicit "do not commit or push" instruction; Task 11's own tests
    verify that instruction is actually present in the constructed prompt
    string, not merely assumed by convention.

THIS STUB shells out to a real `git worktree add` -- unlike job_object.py's
stub, there is no meaningfully simpler placeholder behavior here, since
Task 9's dispatch route needs a real, usable directory to set `cwd` to for
`investigate`. Task 9's own tests monkeypatch this function rather than
exercising real git during automated test runs (see dashboard/tests/
test_dispatch.py) specifically so `pytest -m unit` never creates or leaves
behind real worktrees. Task 11 must replace/extend this module's internals
(branch naming, any additional bookkeeping) if it needs to differ from
this minimal version -- the call signature below must stay stable, since
dashboard/app.py's dispatch route already depends on exactly this shape.

Manual cleanup (not automated by this module or any other part of this
plan): once the operator is done reviewing an `investigate` diff,
`git worktree remove <path>` removes it; `git worktree list` surfaces any
orphans (identifiable by the `quant-worker-` prefix) left behind by a
crashed dispatch job.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKTREE_ROOT = REPO_ROOT / '.worker_worktrees'


def create_worker_worktree(run_id: str, job_id: str) -> Path:
    """STUB -- see module docstring. Task 11 replaces this.

    Runs ``git worktree add -b quant-worker-<job_id> <path> HEAD`` under
    ``.worker_worktrees/quant-worker-<job_id>`` and returns that path for
    use as the dispatched `investigate` subprocess's ``cwd``.

    *run_id* is accepted (matching Task 11's documented interface, and
    useful for a future branch-naming scheme that includes it) but is not
    currently used in the branch/dir name -- only *job_id* is, since job
    ids are already unique per dispatch and this stub keeps naming simple.

    Raises ``subprocess.CalledProcessError`` if `git worktree add` fails --
    callers (Task 9's dispatch route) must not swallow this: a failed
    worktree means `investigate` has no safe, isolated `cwd` to run in.
    """
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    branch_name = f'quant-worker-{job_id}'
    worktree_path = WORKTREE_ROOT / branch_name
    subprocess.run(
        ['git', 'worktree', 'add', '-b', branch_name, str(worktree_path), 'HEAD'],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path
