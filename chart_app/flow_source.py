"""The whale tape: `scanner_trades` per session date, cached per day.

Lived in `server.py` until 2026-09-21. Moved so the equity sleeve can pull the
SAME flow the chart and tester score with -- before this the sleeve never saw
whale at all, and the tester saw only the last 5 sessions, so a tune and the
sleeve running it were scoring two different engines.
"""

from __future__ import annotations

import os


def _flow_days_from_env(default: int = 400) -> int:
    """How many session dates one refresh may pull flow for.

    Each day is a paginated `scanner_trades` sweep of the full tape, so this
    is the knob that trades billed provider calls against how far back the
    whale dots reach. Raise it when you want deeper coverage on a 30d chart
    and are willing to pay for the pulls.
    """
    raw = os.environ.get("CHART_APP_FLOW_DAYS")
    if not raw:
        return default
    try:
        return max(1, min(400, int(raw)))
    except ValueError:
        return default


def _flow_min_premium_from_env(default: float = 10_000.0) -> float:
    """Server-side premium floor for the flow pull.

    This is the single most important number in the whale path. Measured on
    SPY for 2026-09-17: the full tape is **1,387,630 prints in one day**, and
    the distribution is overwhelmingly dust.

        floor        rows kept   premium kept
        >=   $1,000    18.78%       94.98%
        >=  $10,000     1.94%       82.36%
        >=  $25,000     0.69%       77.32%

    So a $10k floor discards 98% of the rows and keeps 82% of the premium,
    and every whale-sized print is still in the result. Lower it toward $1,000
    when you want the flow pane's net-premium series to be near-exact and are
    willing to pay ~10x the rows for the last 13 percentage points.
    """
    raw = os.environ.get("CHART_APP_FLOW_MIN_PREMIUM")
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


# One cache entry per (root, session date). Keyed by DAY, not by chart window:
# the window key includes the last bar's timestamp, so every new bar used to
# invalidate the whole cache and re-pull all five sessions. A day, once
# fetched, never changes -- except today's, which is dropped on each refresh
# by `_flow_day_cache_drop_today`.
_FLOW_DAY_CACHE: dict[tuple, list] = {}
_FLOW_DAY_CACHE_MAX = 400
# Rows, not days, is what bounds memory: one SPY day at the $10k floor is
# ~24k rows (6.6s to pull), one SMR day is ~29. Measured 2026-09-21.
_FLOW_CACHE_MAX_ROWS = 400_000


def _flow_row_budget_from_env(default: int = 150_000) -> int:
    """How many whale rows one pull may gather, newest session first.

    This replaced a flat 5-session cap (2026-09-21). The cap meant the tester
    scored whale on the last 5 sessions only and the sleeve not at all, so a
    tune's P&L came from an engine nothing else ran. A row budget gives a thin
    name (XE, SMR: tens of rows a day) its WHOLE history in seconds, while SPY
    stops after about a week instead of a 14-minute, multi-GB pull.
    """
    raw = os.environ.get("CHART_APP_FLOW_ROW_BUDGET")
    if not raw:
        return default
    try:
        return max(1_000, int(raw))
    except ValueError:
        return default


def _evict_flow_cache() -> None:
    total = sum(len(v) for v in _FLOW_DAY_CACHE.values())
    for stale in list(_FLOW_DAY_CACHE):
        if (
            total <= _FLOW_CACHE_MAX_ROWS
            and len(_FLOW_DAY_CACHE) <= _FLOW_DAY_CACHE_MAX
        ):
            break
        total -= len(_FLOW_DAY_CACHE.pop(stale))


def _production_flow_fn(root, start_dt, end_dt, min_premium):
    """`scanner_trades`, paginated, once per session date — never per bar.

    Two bugs fixed here on 2026-09-18, which together are the whole of "whale
    symbols hardly ever show up":

    1. **`min_premium=0`.** The docstring used to read "always full tape
       (passed arg ignored)". On SPY that is 1.39M prints a day and **402
       seconds** to page down; five sessions is over half an hour, so the
       fetch never finished inside any poll on ANY timeframe. It now passes a
       real floor (`_flow_min_premium_from_env`).

    2. **`if not days or len(days) > 5: return []`.** Every chart lookback
       except 5d spans more than five weekdays (10m=10d, 30m=20d, 1h/4h=30d,
       1d=1y), so on five of the eight timeframes this returned an empty list
       before making a single call — not "no whales today", but "never
       asked". It now walks back from the newest session until
       `CHART_APP_FLOW_ROW_BUDGET` rows are gathered (was: 5 sessions), and `flow_stamp.whale_coverage` reports sessions-covered
       against sessions-charted so a partly-stamped chart says so on screen.

    Still never called per bar, and still single-threaded: the repo rule is
    that ThetaData pulls are serialised, and a concurrent second caller here
    reliably drove the first into `PHTimeoutError` while this was measured.
    """
    from datetime import datetime, timedelta

    from chart_app.crypto_source import is_crypto
    from chart_app.flow_stamp import rows_from_flow_payload
    from Direction.indicator import _thread_client

    # There is no US options tape for a perp. Asking anyway burns a provider
    # call per session date to be told so.
    if is_crypto(root):
        return []

    def _as_dt(value):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    start = _as_dt(start_dt)
    end = _as_dt(end_dt)
    days = []
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    if not days:
        return []
    # Most recent sessions first-priority: a partly-covered chart should be
    # stamped at the right-hand edge, where the user is looking.
    days = days[-_flow_days_from_env() :]
    budget = _flow_row_budget_from_env()
    client = _thread_client()
    floor = _flow_min_premium_from_env()
    today = datetime.now().date()  # noqa: DTZ005 -- local session date, as before the move
    per_day: list[list] = []
    gathered = 0
    LIMIT = 10000
    # Newest first, until the row budget is spent: coverage grows leftward
    # from the edge the user is looking at.
    for day in reversed(days):
        if gathered >= budget:
            break
        key = (root, day.isoformat(), floor)
        # Today's session is still accumulating prints, so it is never served
        # from cache. Every earlier day is final.
        if day != today and key in _FLOW_DAY_CACHE:
            per_day.append(_FLOW_DAY_CACHE[key])
            gathered += len(per_day[-1])
            continue
        ymd = day.strftime("%Y%m%d")
        offset = 0
        day_rows: list = []
        while True:
            try:
                env = client.flow.scanner_trades(
                    root=root,
                    start_date=ymd,
                    end_date=ymd,
                    min_premium=floor,
                    limit=LIMIT,
                    offset=offset,
                )
                page = rows_from_flow_payload(getattr(env, "data", None))
                day_rows.extend(page)
                if len(page) < LIMIT:
                    break
                offset += LIMIT
            except Exception:  # noqa: BLE001 — per-day degrade; never raise
                # Keeps the original contract.
                # A partial day is still stamped, and `whale_coverage` is what
                # tells the user the chart is only partly covered.
                break
        if day != today:
            _FLOW_DAY_CACHE[key] = day_rows
            _evict_flow_cache()
        per_day.append(day_rows)
        gathered += len(day_rows)
    rows: list = []
    for day_rows in reversed(per_day):
        rows.extend(day_rows)
    return rows
