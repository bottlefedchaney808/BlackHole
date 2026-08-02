"""test_worker_env.py

Covers Task 8 of docs/superpowers/plans/2026-08-01-quant-console.md:
`dashboard.worker_env.build_worker_env`, the explicit environment allowlist
every worker subprocess (Task 9's `claude -p` dispatch for interpret/
investigate/explain) is launched with instead of the inherited
`os.environ`.

Per the plan's Global Constraints (and spec R2-F1/R3-F2, the single most
load-bearing finding across all three CARL rounds): this dashboard process's
environment is *structurally guaranteed* to contain `DASHBOARD_API_KEY`
(dashboard/auth.py reads it on every request) and, once Task 1's
`load_env_once()` runs, every credential in the root `.env`
(`THETADATA_CF_ACCESS_CLIENT_ID`/`_SECRET`, etc). `subprocess.Popen`
inherits the full parent `os.environ` unless `env=` is explicitly
overridden -- so if `build_worker_env()` ever regresses toward a blocklist,
or toward passing `os.environ` through unfiltered, a worker subprocess (one
with live network egress, for `explain`) gets a straight line to exfiltrate
`DASHBOARD_API_KEY` via prompt injection. That makes
`test_excludes_credentials_explicitly` below the single highest-value test
in the whole plan, per the task's own framing -- it asserts the *absence*
of specific credential keys from the returned dict, not merely that the
function runs without raising.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.worker_env as worker_env  # noqa: E402

pytestmark = pytest.mark.unit


# A fake "post-load_env_once()" environment: PATH/TEMP/USERPROFILE are the
# ordinary process environment; DASHBOARD_API_KEY and the THETADATA_* pair
# stand in for what shared.config.load_env_once() would have added from the
# root .env by the time a dispatch route calls build_worker_env(). SWAPS_DB_PATH
# is a third, unrelated credential-adjacent var included for good measure.
def _fake_environ():
    return {
        "PATH": r"C:\Windows\System32;C:\Windows",
        "TEMP": r"C:\Users\bottl\AppData\Local\Temp",
        "USERPROFILE": r"C:\Users\bottl",
        "DASHBOARD_API_KEY": "super-secret-dashboard-key",
        "THETADATA_CF_ACCESS_CLIENT_ID": "theta-client-id-12345",
        "THETADATA_CF_ACCESS_CLIENT_SECRET": "theta-client-secret-67890",
        "SWAPS_DB_PATH": r"C:\Users\bottl\FinancialDevelopment\swaps.db",
    }


class TestBuildWorkerEnvAllowlist:
    def test_returns_only_the_allowlisted_keys(self, monkeypatch):
        monkeypatch.setattr(worker_env.os, "environ", _fake_environ())
        result = worker_env.build_worker_env()
        assert set(result.keys()) == {"PATH", "TEMP", "USERPROFILE"}

    def test_excludes_credentials_explicitly(self, monkeypatch):
        """The single highest-value test in the plan (spec R2-F1, critical):
        assert DASHBOARD_API_KEY and THETADATA_* are absent from the
        returned dict, not just that the function runs.
        """
        monkeypatch.setattr(worker_env.os, "environ", _fake_environ())
        result = worker_env.build_worker_env()
        assert "DASHBOARD_API_KEY" not in result
        assert "THETADATA_CF_ACCESS_CLIENT_ID" not in result
        assert "THETADATA_CF_ACCESS_CLIENT_SECRET" not in result
        assert "SWAPS_DB_PATH" not in result
        # Also assert none of the *values* leaked in under a different key.
        assert "super-secret-dashboard-key" not in result.values()
        assert "theta-client-id-12345" not in result.values()
        assert "theta-client-secret-67890" not in result.values()

    def test_allowlisted_values_are_copied_through_correctly(self, monkeypatch):
        fake = _fake_environ()
        monkeypatch.setattr(worker_env.os, "environ", fake)
        result = worker_env.build_worker_env()
        assert result["PATH"] == fake["PATH"]
        assert result["TEMP"] == fake["TEMP"]
        assert result["USERPROFILE"] == fake["USERPROFILE"]

    def test_does_not_pull_in_anything_load_env_once_would_add(self, monkeypatch):
        """Simulates the environment *after* shared.config.load_env_once()
        has already run and populated os.environ from the root .env --
        build_worker_env() must still only surface the fixed allowlist, not
        whatever load_env_once() happened to add.
        """
        post_load_env_once = _fake_environ()
        post_load_env_once["POTATOHEDGE_BASE_URL"] = "https://api.potatohedge.com"
        post_load_env_once["LOG_LEVEL"] = "DEBUG"
        monkeypatch.setattr(worker_env.os, "environ", post_load_env_once)
        result = worker_env.build_worker_env()
        assert set(result.keys()) == {"PATH", "TEMP", "USERPROFILE"}
        assert "POTATOHEDGE_BASE_URL" not in result
        assert "LOG_LEVEL" not in result

    def test_home_used_when_userprofile_absent(self, monkeypatch):
        env = {"PATH": "/usr/bin", "TEMP": "/tmp", "HOME": "/home/operator"}
        monkeypatch.setattr(worker_env.os, "environ", env)
        result = worker_env.build_worker_env()
        assert result == {"PATH": "/usr/bin", "TEMP": "/tmp", "HOME": "/home/operator"}

    def test_missing_optional_keys_are_simply_omitted(self, monkeypatch):
        """No HOME and no USERPROFILE set -- must not raise, must just omit
        both rather than including an empty/None value.
        """
        env = {"PATH": "/usr/bin", "TEMP": "/tmp"}
        monkeypatch.setattr(worker_env.os, "environ", env)
        result = worker_env.build_worker_env()
        assert result == {"PATH": "/usr/bin", "TEMP": "/tmp"}
        assert "HOME" not in result
        assert "USERPROFILE" not in result

    def test_empty_environ_returns_empty_dict_without_raising(self, monkeypatch):
        monkeypatch.setattr(worker_env.os, "environ", {})
        result = worker_env.build_worker_env()
        assert result == {}

    def test_returns_a_fresh_dict_not_a_view_of_os_environ(self, monkeypatch):
        fake = _fake_environ()
        monkeypatch.setattr(worker_env.os, "environ", fake)
        result = worker_env.build_worker_env()
        result["PATH"] = "mutated"
        assert worker_env.os.environ["PATH"] != "mutated"

    def test_two_calls_return_independent_dict_objects(self, monkeypatch):
        monkeypatch.setattr(worker_env.os, "environ", _fake_environ())
        first = worker_env.build_worker_env()
        second = worker_env.build_worker_env()
        assert first == second
        assert first is not second
