"""
Regression test for the ThetaData EOD date-key bug in sentiment_backtest.py.

Raw ``hist_stock_eod`` bars carry the date in ``created`` / ``last_trade``
(an ISO timestamp), NOT a ``date`` key, and include a trailing empty ``{}``
bar plus multiple intraday snapshots per day. Before the fix, those raw rows
went straight into ``_to_trading_dates`` (which filters on ``r.get("date")``),
so every forward return came back ``None`` and the whole backtest silently
produced no signal.

This test is network-free: it builds raw-shaped bars by hand and verifies
``_normalize_eod_rows`` collapses them to one close per trading day, that the
normalizer is idempotent on already-normalised rows, and that the end-to-end
path (raw -> normalise -> ``_to_trading_dates``) now yields real returns.
"""

import sentiment_backtest as sbt


def _raw_bars():
    """Realistic raw ThetaData EOD bars: ISO 'created', string closes, a
    second intraday snapshot on the first day, and a trailing empty {} bar."""
    return [
        {"created": "2026-08-10T17:15:52.100", "close": "100.0"},
        {"created": "2026-08-10T17:15:53.000", "close": "101.0"},  # same day -> 101 wins
        {"created": "2026-08-11T17:15:52.100", "close": "102.0"},
        {"created": "2026-08-12T17:15:52.100", "close": "104.0"},
        {"created": "2026-08-13T17:15:52.100", "close": "106.0"},
        {"created": "2026-08-14T17:15:52.100", "close": "108.0"},
        {"created": "2026-08-17T17:15:52.100", "close": "110.0"},
        {"created": "2026-08-18T17:15:52.100", "close": "112.0"},
        {"created": "2026-08-19T17:15:52.100", "close": "114.0"},
        {},  # trailing empty bar the endpoint appends
    ]


def test_normalize_collapses_to_one_close_per_day():
    rows = sbt._normalize_eod_rows(_raw_bars())
    # 8 trading days (08-10 .. 08-18), NOT 9 (the {} bar is dropped)
    assert len(rows) == 8
    # one row per day, sorted by date
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)
    # last intraday snapshot per day wins: 08-10 -> 101.0
    by_date = {r["date"]: r["close"] for r in rows}
    assert by_date["2026-08-10"] == 101.0
    assert by_date["2026-08-18"] == 112.0
    # every row now carries a 'date' key the helpers expect
    assert all("date" in r and "close" in r for r in rows)


def test_normalize_handles_empty_and_bad_rows():
    assert sbt._normalize_eod_rows([]) == []
    assert sbt._normalize_eod_rows([{}, {}, {}]) == []
    # bad close / missing date are skipped, good rows survive
    rows = sbt._normalize_eod_rows(
        [
            {"created": "2026-08-10T17:15:52.100", "close": "abc"},  # bad close
            {"created": "2026-08-10T17:15:52.100", "close": "0"},  # non-positive
            {"created": "2026-08-11T17:15:52.100", "close": "50"},  # good
            {"close": "75"},  # no date anywhere
        ]
    )
    assert rows == [{"date": "2026-08-11", "close": 50.0}]


def test_normalize_is_idempotent_on_normalised_rows():
    already = [
        {"date": "2026-08-10", "close": 100.0},
        {"date": "2026-08-11", "close": 102.0},
    ]
    assert sbt._normalize_eod_rows(already) == already


def test_end_to_end_raw_to_forward_return():
    """The actual bug: raw bars (no 'date' key) must now produce a real
    forward return, not (None, None)."""
    rows = sbt._normalize_eod_rows(_raw_bars())
    f5, f10 = sbt._to_trading_dates(rows, "2026-08-10", 5)
    # ref 08-10 close 101.0; 5 trading days later = 08-17 close 110.0
    assert f5 is not None
    assert abs(f5 - (110.0 - 101.0) / 101.0) < 1e-9
    # 10 trading days ahead is out of range for this 8-day series
    assert f10 is None


def test_to_trading_dates_reference_not_first_bar():
    rows = sbt._normalize_eod_rows(_raw_bars())
    # ref 08-12 close 104.0; 5 trading days later = 08-19 close 114.0
    f5, _ = sbt._to_trading_dates(rows, "2026-08-12", 5)
    assert f5 is not None
    assert abs(f5 - (114.0 - 104.0) / 104.0) < 1e-9


def test_to_trading_dates_insufficient_data_is_none():
    # only 2 days, can't reach 5 forward
    rows = [
        {"date": "2026-08-10", "close": 100.0},
        {"date": "2026-08-11", "close": 102.0},
    ]
    f5, f10 = sbt._to_trading_dates(rows, "2026-08-10", 5)
    assert f5 is None
    assert f10 is None
