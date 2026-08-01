#!/usr/bin/env python3
"""expiry_selector.py  (Monte-Carlo-American-Pricer-Greeks)

Pick a concrete listed expiry from a target time-to-maturity, and -- critically --
derive T from the RESOLVED CALENDAR DATE, never by passing the typed target (e.g.
0.33) straight through to the pricer.

WHY THIS EXISTS (the bug it fixes):
  The old flow typed T=0.33, found a "nearest" expiry, then computed the T used for
  pricing as `(expiry - datetime.now()).days / 365`. Because datetime.now() carries
  the time of day, a 122-calendar-day expiry evaluated at 16:01 truncates to 121
  days -> T=0.3315 instead of the true 0.3342. First-order Greeks barely notice, but
  near-the-money vanna and vomma are proportional to d2 and d1*d2 (both ~0 at ATM),
  so that one-day T error -- together with any drift/vol mismatch it rides in with --
  swings them 40-60% versus the vendor's own d1/d2. Deriving T from the DATE
  (calendar-day difference, UTC, /365) removes the truncation and lines the model's
  d1/d2 up with ThetaData's.

WHAT IT IMPROVES over the sibling Variance_Swap_Module/expiry_selector.py:
  That one could surface a near-term weekly next to a target-distance monthly (two
  dates far apart). This returns the OPEX (third-Friday) closest to the target AND
  the weekly closest to the target, so both bracket the input date (e.g. target near
  10/12 -> weekly 10/9 + OPEX 10/16), which is what you actually want to choose
  between.

Self-contained (stdlib only); `td` just needs a `.list_expirations(ticker)` method
returning YYYYMMDD strings, which ThetaDataController provides.
"""
import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date as _date
from typing import List, Optional, Tuple

DAYS_PER_YEAR = 365.0
EXPIRY_FMT = "%Y%m%d"


@dataclass
class ExpiryCandidate:
    exp_str: str        # "20261016"
    exp_date: _date
    dte: int            # calendar days from today (UTC)
    T_years: float      # dte / 365  -- the T pricing MUST use for this contract
    kind: str           # "monthly" | "weekly" | "other"
    diff_days: int      # |dte - target_dte|


def third_friday(year: int, month: int) -> _date:
    """The standard monthly-OPEX date: the third Friday of the month."""
    fridays = [d for d in calendar.Calendar().itermonthdates(year, month)
               if d.month == month and d.weekday() == calendar.FRIDAY]
    return fridays[2]


def classify_expiry(d: _date) -> str:
    """monthly = the 3rd-Friday OPEX date; weekly = any other Friday; other =
    non-Friday listings (Mon/Wed short-dated, EOM, quarterlies on odd days)."""
    if d.weekday() == calendar.FRIDAY and d == third_friday(d.year, d.month):
        return "monthly"
    if d.weekday() == calendar.FRIDAY:
        return "weekly"
    return "other"


def _future_expiries(avail: List[str], today: _date) -> List[Tuple[str, _date, int, str]]:
    out = []
    for exp_str in avail:
        s = str(exp_str).strip()
        try:
            d = datetime.strptime(s, EXPIRY_FMT).date()
        except ValueError:
            continue
        dte = (d - today).days
        if dte < 0:
            continue
        out.append((s, d, dte, classify_expiry(d)))
    out.sort(key=lambda x: x[2])
    return out


def _mk(entry, target_dte) -> ExpiryCandidate:
    s, d, dte, kind = entry
    return ExpiryCandidate(exp_str=s, exp_date=d, dte=dte, T_years=dte / DAYS_PER_YEAR,
                           kind=kind, diff_days=abs(dte - target_dte))


def target_date_from_years(target_years: float, today: Optional[_date] = None) -> _date:
    today = today or datetime.now(timezone.utc).date()
    return today + timedelta(days=round(target_years * DAYS_PER_YEAR))


def find_candidates(avail: List[str], target_years: float,
                    today: Optional[_date] = None) -> List[ExpiryCandidate]:
    """Return up to two candidates BOTH closest to the target date: the OPEX
    (third-Friday) nearest the target and the weekly (other-Friday) nearest the
    target, de-duplicated, closest-first. If a ticker lists no weeklies (or no
    monthlies), falls back to the nearest expiry of any kind so a candidate is
    always returned when expiries exist.
    """
    today = today or datetime.now(timezone.utc).date()
    parsed = _future_expiries(avail, today)
    if not parsed:
        return []
    target_dte = round(target_years * DAYS_PER_YEAR)

    monthlies = [p for p in parsed if p[3] == "monthly"]
    weeklies = [p for p in parsed if p[3] == "weekly"]

    picks: List[ExpiryCandidate] = []
    seen = set()

    def _add(pool):
        if not pool:
            return
        best = min(pool, key=lambda p: abs(p[2] - target_dte))
        if best[0] not in seen:
            seen.add(best[0])
            picks.append(_mk(best, target_dte))

    _add(monthlies)   # OPEX nearest the target
    _add(weeklies)    # weekly nearest the target
    if not picks:     # neither Friday type listed -> nearest overall (e.g. Wed-only)
        _add(parsed)

    picks.sort(key=lambda c: c.diff_days)
    return picks


def nearest_expiry(td, ticker: str, target_years: float,
                   today: Optional[_date] = None) -> Tuple[str, float]:
    """Programmatic (non-interactive) pick: the candidate closest to the target,
    returning (expiry_str, T_from_dates). Used by batch/background callers and as
    the default when a run isn't prompting a human."""
    avail = td.list_expirations(ticker)
    if not avail:
        raise ValueError(f"No listed expiries for {ticker}")
    cands = find_candidates(avail, target_years, today)
    if not cands:
        raise ValueError(f"No suitable expiry for {ticker}")
    best = cands[0]
    return best.exp_str, best.T_years


def _describe(c: ExpiryCandidate) -> str:
    label = {"monthly": "MONTHLY/OPEX", "weekly": "WEEKLY", "other": "OTHER"}[c.kind]
    return (f"{c.exp_date.isoformat()} ({c.exp_date.strftime('%a')}, {label})  "
            f"DTE={c.dte}  T={c.T_years:.4f}yr")


def choose_expiry(td, ticker: str, target_years: float,
                  interactive: bool = True,
                  today: Optional[_date] = None) -> Tuple[str, float]:
    """Resolve target_years to a concrete (expiry_str, T_from_dates).

    Interactive: shows the OPEX and weekly nearest the target (both bracketing the
    input date) and lets the user pick; default is the closest of the two.
    Non-interactive: returns the closest candidate. Either way, T comes from the
    resolved date, so the pricer never re-uses the typed target as T.
    """
    avail = td.list_expirations(ticker)
    if not avail:
        raise ValueError(f"No listed expiries for {ticker}")
    cands = find_candidates(avail, target_years, today)
    if not cands:
        raise ValueError(f"No suitable expiry for {ticker}")

    tgt = target_date_from_years(target_years, today)
    if not interactive or len(cands) == 1:
        chosen = cands[0]
        print(f"[Expiry] target T={target_years:.4f}yr (~{tgt.isoformat()}) -> {_describe(chosen)}")
        return chosen.exp_str, chosen.T_years

    print(f"\n[Expiry] Target for T={target_years:.4f}yr is ~{tgt.isoformat()}. Closest listed expiries:")
    for i, c in enumerate(cands, 1):
        print(f"   {i}) {_describe(c)}")
    choice = input(f"   Choose expiry by number (default 1 = closest, {cands[0].exp_date.isoformat()}): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(cands):
        chosen = cands[int(choice) - 1]
    else:
        chosen = cands[0]
    print(f"   -> Using {_describe(chosen)}")
    return chosen.exp_str, chosen.T_years
