"""test_auth.py

Covers Task 7 of docs/superpowers/plans/2026-08-01-quant-console.md:
`dashboard.auth.require_dispatch_configured`, the new per-request dependency
that fails closed (503) for the two dispatch/poll routes (Tasks 9 and 12)
when `DASHBOARD_API_KEY` is unset or left at its public default -- without
being able to take down the rest of the already-working dashboard process.

Depends on Task 1 (`load_env_once()` wired into `dashboard/app.py`'s import
chain) to make this fail-closed check meaningful in production: without
Task 1, `DASHBOARD_API_KEY` set only in `.env` never reaches
`os.environ`/`dashboard.auth.API_KEY` in the first place, so this check
would always see the default regardless of operator configuration. Task 1
is already covered by test_env_loading.py; this file assumes it's in place
and focuses purely on `require_dispatch_configured`'s own behavior.

Three things asserted, matching the plan's Step 1 bullets:
  1. A dispatch-decorated endpoint with a default/unset key returns 503 with
     the documented "dispatch disabled: DASHBOARD_API_KEY not configured"
     message.
  2. A pre-existing endpoint (GET /runs/{id}) is completely unaffected by a
     default/unset key in the same test run -- proves the check is scoped
     per-route, not process-wide.
  3. A properly-configured key passes through with no exception.

`require_dispatch_configured` is evaluated *per request* against the live
`dashboard.auth.API_KEY` module global (re-read on every call, not captured
once at import/decoration time) -- monkeypatching `dashboard.auth.API_KEY`
between requests in a single test process is exactly what these tests rely
on to prove that, and is also what makes it safe as a FastAPI dependency:
nothing about it runs at process startup, so it cannot crash the app over a
dispatch-only misconfiguration.
"""
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.auth as auth  # noqa: E402
import dashboard.app as dashboard_app  # noqa: E402

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# A tiny throwaway app standing in for Task 9/12's not-yet-built dispatch
# routes -- exercises require_dispatch_configured as a real FastAPI
# dependency (status code + response shape), not just as a bare function
# call, without needing those routes to exist yet.
# --------------------------------------------------------------------------

_dispatch_stub_app = FastAPI()


@_dispatch_stub_app.get(
    '/stub-dispatch',
    dependencies=[Depends(auth.require_dispatch_configured)],
)
def _stub_dispatch_route():
    return {'ok': True}


_dispatch_client = TestClient(_dispatch_stub_app)
_runs_client = TestClient(dashboard_app.app)


class TestRequireDispatchConfiguredDirect:
    """Calling the dependency function directly, no HTTP involved."""

    @pytest.mark.anyio
    async def test_raises_503_when_key_is_the_public_default(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', auth.DEFAULT_API_KEY)
        with pytest.raises(Exception) as exc_info:
            await auth.require_dispatch_configured(request=None)
        assert getattr(exc_info.value, 'status_code', None) == 503

    @pytest.mark.anyio
    async def test_raises_503_when_key_is_empty_string(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', '')
        with pytest.raises(Exception) as exc_info:
            await auth.require_dispatch_configured(request=None)
        assert getattr(exc_info.value, 'status_code', None) == 503

    @pytest.mark.anyio
    async def test_error_message_is_the_documented_message(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', auth.DEFAULT_API_KEY)
        with pytest.raises(Exception) as exc_info:
            await auth.require_dispatch_configured(request=None)
        detail = str(getattr(exc_info.value, 'detail', exc_info.value))
        assert 'dispatch disabled' in detail.lower()
        assert 'DASHBOARD_API_KEY' in detail

    @pytest.mark.anyio
    async def test_passes_through_with_a_real_configured_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', 'a-real-operator-configured-key')
        # Must not raise.
        result = await auth.require_dispatch_configured(request=None)
        assert result is None


class TestRequireDispatchConfiguredAsDependency:
    """Wired into an actual route via Depends(), asserting on HTTP responses."""

    def test_dispatch_route_returns_503_with_default_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', auth.DEFAULT_API_KEY)
        resp = _dispatch_client.get('/stub-dispatch')
        assert resp.status_code == 503
        body = resp.json()
        detail = str(body.get('detail', body))
        assert 'dispatch disabled' in detail.lower()
        assert 'DASHBOARD_API_KEY' in detail

    def test_dispatch_route_returns_503_with_unset_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', '')
        resp = _dispatch_client.get('/stub-dispatch')
        assert resp.status_code == 503

    def test_dispatch_route_passes_through_with_configured_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', 'a-real-operator-configured-key')
        resp = _dispatch_client.get('/stub-dispatch')
        assert resp.status_code == 200
        assert resp.json() == {'ok': True}


class TestPreExistingRouteUnaffected:
    """GET /runs/{run_id} does not use require_dispatch_configured -- a
    default/unset DASHBOARD_API_KEY must not change its behavior at all,
    proving the new check is scoped to the two dispatch/poll routes only
    and cannot take down (or otherwise gate) the rest of the app.
    """

    def test_runs_endpoint_unaffected_by_default_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', auth.DEFAULT_API_KEY)
        resp = _runs_client.get('/runs/definitely-not-a-real-run-xyz')
        assert resp.status_code == 404  # existing "no such run" behavior, not 503

    def test_runs_endpoint_unaffected_by_unset_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', '')
        resp = _runs_client.get('/runs/definitely-not-a-real-run-xyz')
        assert resp.status_code == 404

    def test_runs_endpoint_works_the_same_with_a_configured_key(self, monkeypatch):
        monkeypatch.setattr(auth, 'API_KEY', 'a-real-operator-configured-key')
        resp = _runs_client.get('/runs/definitely-not-a-real-run-xyz')
        assert resp.status_code == 404
