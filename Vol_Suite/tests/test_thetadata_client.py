"""
Network-free tests for thetadata_client.py.

This module is the foundation everything else in the suite sits on -- and it
had no test coverage at all, despite being the place where the project's most
expensive bugs have lived. Every bug encoded below is one that actually
happened and cost real debugging time:

  * a per-contract 404 aborting a whole multi-chunk pull, silently discarding
    real data from the chunks that would have succeeded;
  * single-contract responses carrying no strike/right column, so every row
    got dropped by a `try/except KeyError: continue` downstream and a run
    reported success over almost nothing;
  * an enumeration failure returning [] instead of raising, making "the fetch
    broke" indistinguishable from "there's nothing here";
  * transient 404/502/dropped-connection responses being trusted as final.

No network, no credentials: the controller is built with __new__ and its
`_get` is replaced with a scripted stub, so these exercise the real retry,
chunking and parsing code paths without touching api.potatohedge.com.
"""
import httpx
import pytest

import thetadata_client as tc
from thetadata_client import ThetaDataController, strike_from_theta, strike_to_theta


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class FakeResponse:
    """Minimal stand-in for httpx.Response covering what the client uses."""

    def __init__(self, status_code, rows=None):
        self.status_code = status_code
        # ThetaData's own convention: [headers_row, data_row, ...]
        self._payload = rows if rows is not None else []
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=None, response=None)


def make_controller(get_impl):
    """A controller with no credentials and no httpx.Client, whose _get is
    `get_impl`. Everything above _get -- retry, chunking, parsing, fan-out --
    is the real code."""
    td = ThetaDataController.__new__(ThetaDataController)
    td.base_url = "http://test"
    td.headers = {}
    td.client = None
    td._get = get_impl
    return td


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retry backoff is real time; tests don't need to spend it."""
    monkeypatch.setattr(tc.time, "sleep", lambda *_a, **_k: None)


def greeks_payload(date, ms_of_day, gamma=0.02, iv=0.2):
    """A response shaped like the confirmed-live single-contract
    hist/option/all_greeks payload: NOTE it carries no strike/right column,
    because those are path segments on that route. That absence is the whole
    point of _stamp_contract."""
    return [["ms_of_day", "date", "gamma", "implied_vol"],
            [ms_of_day, date, gamma, iv]]


# ---------------------------------------------------------------------------
# Strike scaling
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_strike_scaling_round_trips():
    for k in (0.5, 7.0, 745.0, 1000.0, 1234.5):
        assert strike_from_theta(strike_to_theta(k)) == pytest.approx(k)


@pytest.mark.unit
def test_strike_to_theta_is_integer_thousandths():
    assert strike_to_theta(745.0) == 745000
    assert strike_to_theta(0.95) == 950


# ---------------------------------------------------------------------------
# _get_with_retry
# ---------------------------------------------------------------------------

def _scripted(script):
    """Returns a _get that yields script[i] on call i (last item repeats).
    An Exception instance in the script is raised instead of returned."""
    calls = {"n": 0}

    def _get(path, params=None):
        i = calls["n"]
        calls["n"] += 1
        item = script[min(i, len(script) - 1)]
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    _get.calls = calls
    return _get


@pytest.mark.unit
def test_success_is_not_retried():
    g = _scripted([200])
    assert make_controller(g)._get_with_retry("/p").status_code == 200
    assert g.calls["n"] == 1


@pytest.mark.unit
@pytest.mark.parametrize("transient", [404, 502, 503, 504])
def test_transient_statuses_are_retried_and_recover(transient):
    """The proxy returns false-negative 404s (proven non-monotonically in
    diagnostics/diagnose_wide_range_chunks.py) and bursts of 502s when the
    upstream Terminal times out. Neither is authoritative on first sight."""
    g = _scripted([transient, 200])
    assert make_controller(g)._get_with_retry("/p").status_code == 200
    assert g.calls["n"] == 2


@pytest.mark.unit
def test_persistent_transient_status_is_returned_not_raised():
    """A 404 that survives retries is returned, not raised -- callers need to
    decide whether it means "genuinely no data for this contract/window"
    (skip it) or something worse."""
    g = _scripted([404])
    r = make_controller(g)._get_with_retry("/p")
    assert r.status_code == 404
    assert g.calls["n"] == tc._RETRY_ATTEMPTS


@pytest.mark.unit
def test_non_transient_error_is_not_retried():
    """A 400 is a real, deterministic rejection -- retrying it just wastes
    time against a shared proxy."""
    g = _scripted([400])
    assert make_controller(g)._get_with_retry("/p").status_code == 400
    assert g.calls["n"] == 1


@pytest.mark.unit
def test_transport_error_is_retried_then_recovers():
    """Seen live mid-pull: RemoteProtocolError, "Server disconnected without
    sending a response"."""
    g = _scripted([httpx.RemoteProtocolError("disconnected"), 200])
    assert make_controller(g)._get_with_retry("/p").status_code == 200


@pytest.mark.unit
def test_transport_error_that_never_recovers_raises():
    g = _scripted([httpx.RemoteProtocolError("disconnected")])
    with pytest.raises(httpx.TransportError):
        make_controller(g)._get_with_retry("/p")


@pytest.mark.unit
def test_transport_error_then_status_error_returns_the_response():
    """Guards a real unbound-variable trap: if the first attempt raises and a
    later one returns a retryable status, we must return that response rather
    than re-raising the stale exception."""
    g = _scripted([httpx.RemoteProtocolError("d"), 502, 502])
    assert make_controller(g)._get_with_retry("/p").status_code == 502


# ---------------------------------------------------------------------------
# Contract identity stamping -- the silent row-drop bug
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_stamp_contract_adds_identity_absent_from_the_response():
    rows = [{"ms_of_day": 1, "date": "20260710"}]
    out = ThetaDataController._stamp_contract(rows, 745000, "C")
    assert out[0]["strike"] == 745000
    assert out[0]["right"] == "C"


@pytest.mark.unit
def test_stamp_contract_does_not_clobber_values_the_api_supplied():
    rows = [{"strike": 999000, "right": "P", "date": "20260710"}]
    out = ThetaDataController._stamp_contract(rows, 745000, "C")
    assert out[0]["strike"] == 999000
    assert out[0]["right"] == "P"


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("row,expected", [
    ({"date": "20260710"}, "20260710"),
    ({"date": "2026-07-10"}, "20260710"),
    ({"created": "2026-07-10T17:15:06.172"}, "20260710"),   # hist/stock/eod's shape
    ({"datetime": "2026-07-10"}, "20260710"),
    ({"gamma": 0.02}, None),
    ({"date": ""}, None),
    ({"date": "garbage"}, None),
])
def test_normalize_date_handles_every_shape_seen_live(row, expected):
    assert ThetaDataController._normalize_date(row) == expected


@pytest.mark.unit
def test_last_bar_per_date_keeps_the_final_intraday_bar():
    rows = [{"ms_of_day": 100, "date": "20260710", "gamma": 0.1},
            {"ms_of_day": 900, "date": "20260710", "gamma": 0.9},
            {"ms_of_day": 500, "date": "20260710", "gamma": 0.5}]
    out = ThetaDataController._last_bar_per_date(rows)
    assert len(out) == 1
    assert out[0]["gamma"] == 0.9


@pytest.mark.unit
def test_last_bar_per_date_normalizes_and_writes_back_the_date():
    """Downstream consumers each re-derive the date and each silently drop
    rows in a format they don't recognize -- so normalize once, here."""
    out = ThetaDataController._last_bar_per_date(
        [{"ms_of_day": 50, "created": "2026-07-11T16:00:00", "gamma": 0.5}])
    assert out[0]["date"] == "20260711"


@pytest.mark.unit
def test_last_bar_per_date_drops_undated_rows():
    assert ThetaDataController._last_bar_per_date([{"gamma": 0.7}]) == []


# ---------------------------------------------------------------------------
# Chunking + the per-chunk 404 data-loss regression
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_single_contract_history_chunks_into_28_day_spans():
    seen = []

    def _get(path, params=None):
        seen.append((params["start_date"], params["end_date"]))
        return FakeResponse(200, greeks_payload(params["start_date"], 57600000))

    make_controller(_get).option_hist_all_greeks_single(
        "SPY", "20261016", 745000, "C", "20260101", "20260401")

    assert len(seen) == 4, seen
    assert seen[0] == ("20260101", "20260129")
    assert seen[-1][1] == "20260401"          # never overruns the end date
    starts = [s for s, _ in seen]
    assert starts == sorted(starts)           # no gaps, no repeats


@pytest.mark.unit
def test_a_404_chunk_does_not_discard_data_from_later_chunks():
    """THE data-loss bug.

    A contract's strike range grows over its life, so an early chunk can
    legitimately have no data while later ones do. The original code called
    raise_for_status() per chunk, so one early 404 killed the entire pull for
    that contract and threw away real rows it had yet to fetch.
    """
    def _get(path, params=None):
        if params["start_date"] == "20260101":
            return FakeResponse(404)
        return FakeResponse(200, greeks_payload(params["start_date"], 57600000))

    rows = make_controller(_get).option_hist_all_greeks_single(
        "SPY", "20261016", 745000, "C", "20260101", "20260401")

    assert len(rows) == 3, "later chunks must survive an early 404"
    assert all(r["strike"] == 745000 and r["right"] == "C" for r in rows)


@pytest.mark.unit
def test_all_chunks_404_yields_no_rows_rather_than_an_error():
    """A contract that genuinely never traded in the window is an empty
    result, not a failure."""
    rows = make_controller(lambda p, params=None: FakeResponse(404)) \
        .option_hist_all_greeks_single("SPY", "20261016", 745000, "C",
                                       "20260101", "20260201")
    assert rows == []


@pytest.mark.unit
def test_a_real_error_status_still_raises():
    """Only the known-transient statuses get swallowed. A 400 means the
    request itself is wrong and must not be reported as 'no data'."""
    with pytest.raises(httpx.HTTPStatusError):
        make_controller(lambda p, params=None: FakeResponse(400)) \
            .option_hist_all_greeks_single("SPY", "20261016", 745000, "C",
                                           "20260101", "20260201")


@pytest.mark.unit
def test_open_interest_history_is_also_stamped_and_chunked():
    def _get(path, params=None):
        assert "ivl" not in (params or {}), "OI is daily, not interval-bucketed"
        return FakeResponse(200, [["date", "open_interest"],
                                  [params["start_date"], 1234]])

    rows = make_controller(_get).option_hist_open_interest_single(
        "SPY", "20261016", 745000, "P", "20260101", "20260201")
    assert rows and all(r["strike"] == 745000 and r["right"] == "P" for r in rows)


# ---------------------------------------------------------------------------
# Whole-chain fan-out: enumeration failures must not look like empty results
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_greeks_enumeration_failure_raises_instead_of_returning_empty():
    """Returning [] here says "I checked the whole chain and found nothing",
    which is a claim we cannot make when the call that tells us what's IN the
    chain just failed. A transient 502 on this one request previously got
    reported upward as a clean empty result."""
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.option_bulk_greeks = lambda root, exp: (_ for _ in ()).throw(
        httpx.HTTPStatusError("502", request=None, response=None))

    with pytest.raises(httpx.HTTPStatusError):
        td.option_bulk_hist_greeks("SPY", "20261016", "20260101", "20260201")


@pytest.mark.unit
def test_oi_enumeration_failure_raises_instead_of_returning_empty():
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.option_bulk_oi = lambda root, exp: (_ for _ in ()).throw(
        httpx.HTTPStatusError("502", request=None, response=None))

    with pytest.raises(httpx.HTTPStatusError):
        td.option_bulk_hist_oi("SPY", "20261016", "20260101", "20260201")


@pytest.mark.unit
def test_whole_chain_pull_covers_every_contract_and_keys_them_correctly():
    """End-to-end over the fan-out: enumerate the universe, pull each
    contract, and come back with rows that are attributable to a specific
    (strike, right) -- which is the whole reason the stamping exists."""
    universe = [{"strike": 740000, "right": "C"}, {"strike": 740000, "right": "P"},
                {"strike": 745000, "right": "C"}, {"strike": 745000, "right": "P"}]
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.option_bulk_greeks = lambda root, exp: universe

    def _one(root, exp, k_theta, right, start, end, ivl=900000):
        return ThetaDataController._stamp_contract(
            [{"ms_of_day": 57600000, "date": "20260115", "gamma": 0.02}], k_theta, right)

    td.option_hist_all_greeks_single = _one
    rows = td.option_bulk_hist_greeks("SPY", "20261016", "20260101", "20260201")

    assert len(rows) == 4
    assert {(r["strike"], r["right"]) for r in rows} == \
           {(740000, "C"), (740000, "P"), (745000, "C"), (745000, "P")}


@pytest.mark.unit
def test_duplicate_contracts_in_the_universe_are_only_pulled_once():
    universe = [{"strike": 745000, "right": "C"}] * 3
    pulled = []
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.option_bulk_greeks = lambda root, exp: universe

    def _one(root, exp, k_theta, right, start, end, ivl=900000):
        pulled.append((k_theta, right))
        return []

    td.option_hist_all_greeks_single = _one
    td.option_bulk_hist_greeks("SPY", "20261016", "20260101", "20260201")
    assert pulled == [(745000, "C")]


@pytest.mark.unit
def test_one_contract_failing_does_not_abort_the_whole_chain():
    """442 contracts per expiry; a single bad one must not cost the run."""
    universe = [{"strike": 740000, "right": "C"}, {"strike": 745000, "right": "C"}]
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.option_bulk_greeks = lambda root, exp: universe

    def _one(root, exp, k_theta, right, start, end, ivl=900000):
        if k_theta == 740000:
            raise httpx.RemoteProtocolError("Server disconnected")
        return ThetaDataController._stamp_contract(
            [{"ms_of_day": 1, "date": "20260115"}], k_theta, right)

    td.option_hist_all_greeks_single = _one
    rows = td.option_bulk_hist_greeks("SPY", "20261016", "20260101", "20260201")
    assert len(rows) == 1 and rows[0]["strike"] == 745000


# ---------------------------------------------------------------------------
# Response-shape parsing
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_rows_handles_thetadatas_headers_first_convention():
    td = make_controller(lambda p, params=None: None)
    r = FakeResponse(200, [["a", "b"], [1, 2], [3, 4]])
    assert td._parse_rows(r) == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


@pytest.mark.unit
@pytest.mark.parametrize("payload", [[], [["headers", "only"]], "not a list", {}])
def test_parse_rows_returns_empty_for_payloads_with_no_data_rows(payload):
    td = make_controller(lambda p, params=None: None)
    assert td._parse_rows(FakeResponse(200, payload)) == []


@pytest.mark.unit
@pytest.mark.parametrize("data,expected_len", [
    ([["a", "b"], [1, 2]], 1),                                       # list-of-lists
    ({"header": {"format": ["a", "b"]}, "response": [[1, 2]]}, 1),   # nested (dividends)
    ({"close": [1, 2], "open": [3, 4]}, 2),                          # columnar
    ([{"a": 1}], 1),                                                 # already dicts
    ("nonsense", 0),
])
def test_rows_from_any_normalizes_all_three_live_response_shapes(data, expected_len):
    assert len(ThetaDataController._rows_from_any(data)) == expected_len


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [
    ("1.5", 1.5), (2, 2.0), (None, None), ("abc", None), (float("nan"), None),
])
def test_coerce_number_rejects_junk_and_nan(value, expected):
    assert ThetaDataController._coerce_number(value) == expected


# ---------------------------------------------------------------------------
# fetch_spot_price -- three-layer fallback (quote -> trade -> daily close)
#
# Regression coverage for a real bug that existed in one of the two pre-merge
# client implementations and was fixed in the other: `if key in quote and
# quote[key]` treats the STRING '0.0000' as truthy (non-empty string), so it
# happily returned 0.0 as if it were a found price whenever bid/ask were both
# the string '0.0000' -- confirmed live to be exactly what this vendor's
# quote snapshot returns for every ticker outside regular trading hours. The
# merge into shared/thetadata.py must not silently reintroduce this.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fetch_spot_price_rejects_the_stringified_zero_quote_bug():
    """The '0.0000' string must NOT be treated as a valid price."""
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.stock_snapshot_quote = lambda root: {"mid": "0.0000", "bid": "0.0000",
                                             "ask": "0.0000", "last": "0.0000"}
    td.stock_snapshot_trade = lambda root: {"price": "123.45"}
    assert td.fetch_spot_price("SPY") == pytest.approx(123.45)


@pytest.mark.unit
def test_fetch_spot_price_uses_live_quote_when_usable():
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.stock_snapshot_quote = lambda root: {"mid": "450.10"}
    assert td.fetch_spot_price("SPY") == pytest.approx(450.10)


@pytest.mark.unit
def test_fetch_spot_price_falls_back_to_trade_when_quote_is_empty():
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.stock_snapshot_quote = lambda root: {}
    td.stock_snapshot_trade = lambda root: {"price": "99.99"}
    assert td.fetch_spot_price("SPY") == pytest.approx(99.99)


@pytest.mark.unit
def test_fetch_spot_price_falls_back_to_daily_close_when_quote_and_trade_fail():
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.stock_snapshot_quote = lambda root: (_ for _ in ()).throw(
        httpx.HTTPStatusError("502", request=None, response=None))
    td.stock_snapshot_trade = lambda root: {}
    td.hist_stock_eod = lambda root, start, end: [
        {"close": "10.0"}, {"close": "0"}, {"close": "11.5"}]
    assert td.fetch_spot_price("SPY") == pytest.approx(11.5)


@pytest.mark.unit
def test_fetch_spot_price_returns_zero_when_every_layer_fails():
    td = make_controller(lambda p, params=None: FakeResponse(200))
    td.stock_snapshot_quote = lambda root: {}
    td.stock_snapshot_trade = lambda root: {}
    td.hist_stock_eod = lambda root, start, end: []
    assert td.fetch_spot_price("SPY") == 0.0


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_missing_credentials_fails_immediately_and_says_so(monkeypatch):
    """Fail at construction, not on the first request -- every call site is
    written to catch this and fall back.

    Directly deletes the env vars and patches both shared modules' load_env_once
    to ensure env vars from .env don't interfere.
    """
    # Nuke the env vars
    monkeypatch.delenv("THETADATA_CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("THETADATA_CF_ACCESS_CLIENT_SECRET", raising=False)

    # Patch both places load_env_once could be called from
    import shared.thetadata as _st
    monkeypatch.setattr(_st, "load_env_once", lambda: None)

    # Verify they're gone
    import os
    assert os.environ.get("THETADATA_CF_ACCESS_CLIENT_ID") is None
    assert os.environ.get("THETADATA_CF_ACCESS_CLIENT_SECRET") is None

    with pytest.raises(RuntimeError, match="credentials"):
        ThetaDataController()
