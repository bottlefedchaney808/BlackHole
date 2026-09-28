"""The whale pull walks back from today until its row budget is spent."""
# ruff: noqa: DTZ001 -- flow session dates are naive exchange-local, like the real tape

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from chart_app import flow_source


class _Flow:
    def __init__(self, per_day: int):
        self.per_day = per_day
        self.days: list[str] = []

    def scanner_trades(self, *, root, start_date, end_date, min_premium, limit, offset):
        self.days.append(start_date)
        rows = [
            {
                "datetime": f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}T10:00:00",
                "premium": 20000.0,
                "trade_right": "C",
            }
        ] * self.per_day
        return SimpleNamespace(data=rows)


@pytest.fixture
def fake(monkeypatch):
    def make(per_day):
        flow = _Flow(per_day)
        import Direction.indicator as ind

        monkeypatch.setattr(ind, "_thread_client", lambda: SimpleNamespace(flow=flow))
        monkeypatch.setattr(flow_source, "_FLOW_DAY_CACHE", {})
        return flow

    return make


def test_thin_name_gets_its_whole_window(fake, monkeypatch):
    monkeypatch.setattr(
        "chart_app.flow_stamp.rows_from_flow_payload", lambda d: list(d or [])
    )
    flow = fake(30)
    rows = flow_source._production_flow_fn(
        "XE", datetime(2026, 6, 1), datetime(2026, 8, 28), 0
    )
    assert len(flow.days) == 65  # every weekday, not the old 5
    assert len(rows) == 65 * 30


def test_heavy_name_stops_at_the_row_budget_newest_first(fake, monkeypatch):
    monkeypatch.setattr(
        "chart_app.flow_stamp.rows_from_flow_payload", lambda d: list(d or [])
    )
    monkeypatch.setenv("CHART_APP_FLOW_ROW_BUDGET", "5000")
    flow = fake(2000)
    rows = flow_source._production_flow_fn(
        "SPY", datetime(2026, 6, 1), datetime(2026, 8, 28), 0
    )
    assert flow.days == ["20260828", "20260827", "20260826"]
    assert len(rows) == 6000
    # Returned oldest -> newest, whatever order they were pulled in.
    assert rows[0]["datetime"].startswith("2026-08-26")
