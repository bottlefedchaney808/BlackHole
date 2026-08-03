"""test_worker_worktree.py

Covers Task 11 of docs/superpowers/plans/2026-08-01-quant-console.md:
`dashboard.worker_worktree.create_worker_worktree`, the git-worktree
isolation layer Task 9's dispatch route uses as the `investigate` job's
subprocess `cwd`.

Unlike test_dispatch.py -- which monkeypatches this module's function
entirely so its dispatch-route-level tests never shell out to real git or
leave real worktrees behind -- this file exercises the real
`git worktree add` call end-to-end. It never touches this repo's own
`.git`/`.worker_worktrees`: `REPO_ROOT`/`WORKTREE_ROOT` are monkeypatched
per test to point into a disposable git repo created fresh under pytest's
`tmp_path`, and any worktree created during a test is removed via
`git worktree remove` in the `fake_repo` fixture's teardown so nothing
lingers even though `tmp_path` itself is disposed of by pytest regardless.

The no-commit/no-push instruction in the `investigate` prompt is Task 9's
code (`dashboard.app._build_dispatch_prompt`), already asserted at the
dispatch-route level in test_dispatch.py
(`test_investigate_prompt_instructs_no_commit_no_push`). Per this task's
brief, that specific claim is re-verified here too, directly against the
prompt-building function rather than via a full HTTP round trip, since
Task 11 is explicitly responsible for that claim being true from this
module's perspective (the worktree exists precisely so `investigate` has
somewhere safe to leave an uncommitted diff).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402
import dashboard.worker_worktree as worker_worktree  # noqa: E402

pytestmark = pytest.mark.unit


def _run_git(args, cwd):
    return subprocess.run(
        ['git', *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    )


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A throwaway git repo (one commit, so `HEAD` resolves) under
    `tmp_path`, wired up as `worker_worktree.REPO_ROOT`/`WORKTREE_ROOT` so
    `create_worker_worktree`'s real `git worktree add` call runs against
    it instead of this repo's own `.git`. Yields `(repo_root,
    created_paths)`; append any path returned by `create_worker_worktree`
    to `created_paths` so teardown can `git worktree remove` it.
    """
    repo_root = tmp_path / 'fake_repo'
    repo_root.mkdir()
    _run_git(['init'], repo_root)
    _run_git(['config', 'user.email', 'test@example.com'], repo_root)
    _run_git(['config', 'user.name', 'Test'], repo_root)
    (repo_root / 'README.md').write_text('placeholder\n', encoding='utf-8')
    _run_git(['add', 'README.md'], repo_root)
    _run_git(['commit', '-m', 'initial'], repo_root)

    worktree_root = repo_root / '.worker_worktrees'
    monkeypatch.setattr(worker_worktree, 'REPO_ROOT', repo_root)
    monkeypatch.setattr(worker_worktree, 'WORKTREE_ROOT', worktree_root)

    created_paths = []
    yield repo_root, created_paths

    for path in created_paths:
        if Path(path).exists():
            subprocess.run(
                ['git', 'worktree', 'remove', '--force', str(path)],
                cwd=str(repo_root), check=False, capture_output=True, text=True,
            )


class TestCreateWorkerWorktree:
    def test_creates_worktree_at_expected_path(self, fake_repo):
        repo_root, created = fake_repo

        path = worker_worktree.create_worker_worktree('run-1', 'job-abc123')
        created.append(path)

        assert path == repo_root / '.worker_worktrees' / 'quant-worker-job-abc123'
        assert path.is_dir()
        assert (path / '.git').exists()  # worktree marker (file, not dir)

    def test_branch_and_dir_name_carry_quant_worker_prefix(self, fake_repo):
        _repo_root, created = fake_repo

        path = worker_worktree.create_worker_worktree('run-1', 'job-xyz')
        created.append(path)

        assert path.name == 'quant-worker-job-xyz'

        branch = subprocess.run(
            ['git', 'branch', '--show-current'],
            cwd=str(path), check=True, capture_output=True, text=True,
        ).stdout.strip()
        assert branch == 'quant-worker-job-xyz'

    def test_worktree_is_actually_registered_with_git(self, fake_repo):
        repo_root, created = fake_repo

        path = worker_worktree.create_worker_worktree('run-1', 'job-reg')
        created.append(path)

        listing = subprocess.run(
            ['git', 'worktree', 'list'],
            cwd=str(repo_root), check=True, capture_output=True, text=True,
        ).stdout
        # `git worktree list` normalizes to forward slashes on Windows even
        # though `path` (from Path / operators) uses backslashes -- compare
        # via as_posix() rather than a raw substring-of-str check.
        assert path.as_posix() in listing

    def test_raises_when_git_worktree_add_fails(self, fake_repo):
        _repo_root, created = fake_repo

        path = worker_worktree.create_worker_worktree('run-1', 'job-dup')
        created.append(path)

        # Same job_id -> same branch/dir name -> git worktree add refuses
        # the second call (branch + path both already exist). This must
        # surface as a real, unswallowed CalledProcessError: a failed
        # worktree means `investigate` has no safe cwd to run in.
        with pytest.raises(subprocess.CalledProcessError):
            worker_worktree.create_worker_worktree('run-1', 'job-dup')


class TestInvestigatePromptNoCommitInstruction:
    """Task 11's own verification (distinct from test_dispatch.py's
    dispatch-route-level assertion of the same claim) that Task 9's
    prompt-building code really does bake a no-commit/no-push instruction
    into the `investigate` prompt -- checked directly against
    `dashboard.app._build_dispatch_prompt`, not via an HTTP round trip.
    """

    def test_investigate_prompt_forbids_commit_and_push(self):
        prompt = dashboard_app._build_dispatch_prompt(
            'investigate', 'some-run-id', ['/tmp/quant_summary.json'],
        )
        lowered = prompt.lower()
        assert 'do not commit' in lowered or 'not commit' in lowered
        assert 'push' in lowered

    def test_interpret_prompt_has_no_such_instruction(self):
        """Sanity check the instruction is investigate-specific, not
        boilerplate leaking into every action's prompt.
        """
        prompt = dashboard_app._build_dispatch_prompt(
            'interpret', 'some-run-id', ['/tmp/quant_summary.json'],
        )
        assert 'commit' not in prompt.lower()
