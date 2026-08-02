"""test_env_loading.py

Covers Task 1 of docs/superpowers/plans/2026-08-01-quant-console.md: importing
dashboard.app must call shared.config.load_env_once() before dashboard.auth
is imported, so a DASHBOARD_API_KEY set only in PROJECT_ROOT/.env actually
reaches dashboard.auth.API_KEY instead of silently falling back to
dashboard.auth.DEFAULT_API_KEY.

Runs the assertion in a fresh subprocess rather than in-process, for two
reasons: (1) dashboard.app/dashboard.auth may already be cached in
sys.modules by other test collection in this session, so a bare `import`
here wouldn't re-execute their module-level code; (2) it lets us point
shared.config.PROJECT_ROOT at a throwaway temp directory (via a one-line
monkeypatch executed *inside* the subprocess, before dashboard.app is
imported) instead of touching this repo's real, gitignored .env file.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Sentinel value written to a fake PROJECT_ROOT/.env in each subprocess --
# distinctive enough that it can't collide with dashboard.auth.DEFAULT_API_KEY
# ("dev-key-change-in-production") or anything a real .env would plausibly set.
_CUSTOM_KEY = "quant-console-task1-test-key-9f3c2a"

_CHILD_SCRIPT = textwrap.dedent(
    """\
    import sys
    from pathlib import Path
    sys.path.insert(0, {repo_root!r})

    # Point the shared .env loader at our throwaway temp dir *before*
    # dashboard.app (and therefore dashboard.auth) is imported, so whatever
    # dashboard.app's import chain does with load_env_once() reads from
    # here rather than this repo's real .env.
    import shared.config as config
    config.PROJECT_ROOT = Path({env_dir!r})

    import dashboard.app  # noqa: F401  (import side effect is what's under test)
    import dashboard.auth as auth

    print(auth.API_KEY)
    """
)


def _run_child(env_dir: Path, dotenv_contents: str) -> str:
    """Run _CHILD_SCRIPT in a fresh subprocess and return dashboard.auth.API_KEY.

    The subprocess's own environment never carries DASHBOARD_API_KEY, so the
    only way the custom value can reach dashboard.auth.API_KEY is via the
    .env file this helper writes plus dashboard.app actually calling
    load_env_once() at import time.
    """
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / ".env").write_text(dotenv_contents, encoding="utf-8")

    script_path = env_dir / "_child.py"
    script_path.write_text(
        _CHILD_SCRIPT.format(repo_root=str(REPO_ROOT), env_dir=str(env_dir)),
        encoding="utf-8",
    )

    child_env = dict(os.environ)
    child_env.pop("DASHBOARD_API_KEY", None)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(REPO_ROOT),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"child process failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip().splitlines()[-1]


@pytest.mark.unit
def test_dashboard_app_import_loads_custom_dashboard_api_key_from_dotenv(tmp_path):
    """A DASHBOARD_API_KEY set only in .env must reach dashboard.auth.API_KEY.

    This is the core Task 1 regression test: before the fix, nothing in
    dashboard.app's import chain calls shared.config.load_env_once(), so
    dashboard.auth.API_KEY silently falls back to DEFAULT_API_KEY even when
    an operator has correctly followed .env.example.
    """
    api_key = _run_child(
        env_dir=tmp_path / "envdir",
        dotenv_contents=f"DASHBOARD_API_KEY={_CUSTOM_KEY}\n",
    )
    assert api_key == _CUSTOM_KEY
    assert api_key != "dev-key-change-in-production"


@pytest.mark.unit
def test_dashboard_app_import_falls_back_to_default_without_dotenv_key(tmp_path):
    """Sanity check: with no DASHBOARD_API_KEY anywhere, the documented
    hardcoded default is still what's visible -- load_env_once() must not
    invent a value, only surface one that's actually configured.
    """
    api_key = _run_child(
        env_dir=tmp_path / "envdir_empty",
        dotenv_contents="# no DASHBOARD_API_KEY here\n",
    )
    assert api_key == "dev-key-change-in-production"
