"""Regression test for _get_with_retry's status-code loss on retry exhaustion.

_get_with_retry catches PHClientError, and on a retryable failure (or one of
_RETRY_STATUSES) retries up to _RETRY_ATTEMPTS times before giving up. When
every attempt fails with a PHClientError, it used to return _V2Response(None)
with no way to recover the error's real status -- _V2Response.status_code
hardcoded that case to 500 regardless of whether the underlying failure was
actually a persistent 404 (a legitimate "no data for this contract" case
every caller already special-cases with `if r.status_code == 404: ...`) or a
genuine 502/503/504 server error.

The practical symptom (see trading_journal/desk_note_20260818.md and the
`option_bulk_hist_eod`/`option_bulk_hist_oi_by_day` skip logs): a contract
whose data genuinely doesn't exist (illiquid strike, no trades that day) hit
a persistent 404, got reported as status_code=500, sailed past the caller's
`if r.status_code == 404` check, then blew up on `.json()` with a generic
`TypeError: v2 payload is None` -- misclassified as a "genuine error"
instead of the same "no data, skip it" case a first-try 404 already handles
cleanly. This masqueraded as a broken dealer-positioning/expiry_book feature
when the underlying data pull was actually working as intended; the whole
chain just couldn't recognize an exhausted-retry 404 as a 404.
"""
import pytest

from potatohedge.errors import PHClientError
from shared.thetadata import ThetaDataController, _V2Response


def _bare_controller():
    """A ThetaDataController with __init__ skipped (no credentials/network
    needed) -- _get_with_retry only touches self._get, which we stub."""
    return object.__new__(ThetaDataController)


@pytest.mark.unit
def test_retry_exhausted_404_preserves_status_code(monkeypatch):
    controller = _bare_controller()
    err = PHClientError("not found", status=404)
    err.retryable = False

    def _always_404(path, params=None):
        raise err

    monkeypatch.setattr(controller, "_get", _always_404)
    monkeypatch.setattr("shared.thetadata.time.sleep", lambda _: None)

    r = controller._get_with_retry("/some/path")

    assert r.status_code == 404, (
        f"exhausted-retry 404 must report status_code=404 (not the old "
        f"hardcoded 500), got {r.status_code} -- callers' "
        f"`if r.status_code == 404` checks depend on this to treat a "
        f"persistent 404 as 'no data', not a generic error"
    )


@pytest.mark.unit
def test_retry_exhausted_502_preserves_status_code(monkeypatch):
    controller = _bare_controller()
    err = PHClientError("bad gateway", status=502)
    err.retryable = True

    def _always_502(path, params=None):
        raise err

    monkeypatch.setattr(controller, "_get", _always_502)
    monkeypatch.setattr("shared.thetadata.time.sleep", lambda _: None)

    r = controller._get_with_retry("/some/path")

    assert r.status_code == 502, (
        f"a persistent 502 must stay distinguishable from a 404 -- got "
        f"{r.status_code}"
    )
    with pytest.raises(TypeError):
        r.json()


@pytest.mark.unit
def test_v2response_status_override_defaults_preserved():
    """No override passed -> unchanged legacy behavior (data present -> 200,
    data None -> 500), so non-retry-exhaustion callers are unaffected."""
    assert _V2Response({"ok": True}).status_code == 200
    assert _V2Response(None).status_code == 500
