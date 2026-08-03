"""dashboard/worker_worktree.py

Task 11 of docs/superpowers/plans/2026-08-01-quant-console.md ("Git
worktree isolation for `investigate`"). Task 9's dispatch route
(`POST /runs/{run_id}/dispatch/{action}` in dashboard/app.py) uses
`create_worker_worktree` to give every dispatched `investigate` job an
isolated `cwd`: a fresh `git worktree` under `.worker_worktrees/`, checked
out on its own branch, so a headless worker can leave an uncommitted diff
behind without touching the operator's actual working tree or branch.

Design points (spec Error Handling / Phase 2, plan Global Constraints):
  - The worktree is created via `git worktree add -b quant-worker-{job_id}
    .worker_worktrees/quant-worker-{job_id} HEAD`. Both the branch name and
    the directory name carry the `quant-worker-` prefix -- this repo's
    orphan-identification convention: a worktree left behind by a crashed
    dispatch job is identifiable by that prefix in `git worktree list`.
  - No automatic cleanup function exists here, deliberately: a worktree can
    hold an uncommitted diff the user still needs to review, so deleting it
    automatically would be a data-loss risk. See "Manual cleanup" below.
  - This module only creates the worktree; it is not responsible for the
    "do not commit or push" instruction in the `investigate` prompt --
    that's Task 9's `dashboard.app._build_dispatch_prompt`. Both
    dashboard/tests/test_dispatch.py (dispatch-route level) and
    dashboard/tests/test_worker_worktree.py (direct against
    `_build_dispatch_prompt`) verify that instruction is actually present
    in the constructed prompt string, not merely assumed by convention.

`create_worker_worktree` shells out to a real `git worktree add` -- there's
no meaningfully simpler behavior to fake here, since the dispatch route
needs a real, usable directory to set `cwd` to. dashboard/tests/
test_dispatch.py monkeypatches this function entirely so its dispatch-
route-level tests never shell out to real git or leave real worktrees
behind; dashboard/tests/test_worker_worktree.py is the one place that
exercises the real `git worktree add` call, against a disposable git repo
under `tmp_path` (never this repo's own `.git`), so `pytest -m unit` still
never creates or leaves behind a real `.worker_worktrees/` directory here.

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
    """Create an isolated git worktree for a dispatched `investigate` job.

    Runs ``git worktree add -b quant-worker-<job_id> <path> HEAD`` under
    ``.worker_worktrees/quant-worker-<job_id>`` and returns that path for
    use as the dispatched `investigate` subprocess's ``cwd``.

    *run_id* is accepted (matching this module's documented interface, and
    useful for a future branch-naming scheme that includes it) but is not
    currently used in the branch/dir name -- only *job_id* is, since job
    ids are already unique per dispatch and this keeps naming simple.

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
