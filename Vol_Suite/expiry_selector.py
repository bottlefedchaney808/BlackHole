#!/usr/bin/env python3
"""expiry_selector.py

Single, shared expiry-selection logic for the whole Variance Swap Module /
Volatility Suite.

BUG THIS REPLACES: variance_swap_live.py, variance_swap_screener.py, and
dealer_positioning.py each carried their own copy-pasted
`find_nearest_expiry[_thetadata]()` -- the same "ThetaDataController copied
four times" pattern thetadata_client.py's docstring already called out and
fixed for the client itself. The expiry-picking copies were never
consolidated, and two of the three disagreed on what "target_years" means:

    variance_swap_live.py       DEFAULT_A = 252  (trading days/yr)
    variance_swap_screener.py   DEFAULT_A = 252
    dealer_positioning.py       DEFAULT_A = 365  (calendar days/yr)

Feeding the SAME target_years=0.25 into all three during one
volatility_suite.py run could therefore silently resolve to *different*
expiries (0.25*252=63 calendar-day target vs 0.25*365=91.25 calendar-day
target) even though the suite's whole point is comparing those modules
against each other on one consistent view of "the ~3-month expiry". That
mismatch is the "problems on the other side of the project" this module
fixes.

This module standardizes on CALENDAR days (A=365) everywhere, since the
whole point of the new picker below is to line target_years up against real
calendar dates (weekly Friday expiries, monthly/OPEX third-Fridays) -- a
trading-day convention doesn't map onto "OPEX is 10/16" in any direct way.

ON TOP OF the bug fix, this module replaces "silently take the single
closest expiry" with an interactive choice: given a target_years input, it
finds the closest WEEKLY expiry and the closest MONTHLY/OPEX expiry to that
target date (they're usually different dates -- e.g. target lands near
10/2 but OPEX that month is 10/16) and lets the user pick the specific date
rather than have one get chosen for them.
"""
import calendar
from datetime import datetime, timedelta, timezone, date as _date
from typing import List, Optional, Tuple
from dataclasses import dataclass

DEFAULT_A = 365  # calendar days/year -- see module docstring for why this
                 # replaced the divergent 252/365 split across the suite.

EXPIRY_DATE_FMT = "%Y%m%d"


@dataclass
class ExpiryCandidate:
    exp_str: str          # raw ThetaData expiry string, e.g. "20261016"
    exp_date: _date
    dte: int              # calendar days from today
    T_years: float         # dte / DEFAULT_A
    kind: str              # "monthly" | "weekly" | "other"
    diff_days: int         # |dte - target_dte| -- how close to the target


def third_friday(year: int, month: int) -> _date:
    """Standard monthly-options-expiration convention: the third Friday of
    the month. This is what "OPEX" refers to unless a ticker is explicitly on
    a quarterly-only or non-standard cycle."""
    cal = calendar.Calendar()
    fridays = [d for d in cal.itermonthdates(year, month)
               if d.month == month and d.weekday() == calendar.FRIDAY]
    return fridays[2]


def classify_expiry(d: _date) -> str:
    """monthly = the standard 3rd-Friday OPEX date for its month; weekly =
    any other Friday; other = anything else (Mon/Wed short-dated listings,
    EOM listings that land on a non-Friday, etc.)."""
    if d.weekday() == calendar.FRIDAY and d == third_friday(d.year, d.month):
        return "monthly"
    if d.weekday() == calendar.FRIDAY:
        return "weekly"
    return "other"


def _parse_expiries(avail: List[str], today: Optional[_date] = None) -> List[Tuple[str, _date, int, str]]:
    today = today or datetime.now(timezone.utc).date()
    out = []
    for exp_str in avail:
        s = str(exp_str).strip()
        try:
            d = datetime.strptime(s, EXPIRY_DATE_FMT).date()
        except ValueError:
            continue
        dte = (d - today).days
        if dte < 0:
            continue
        out.append((s, d, dte, classify_expiry(d)))
    out.sort(key=lambda x: x[2])
    return out


def target_date_from_years(target_years: float, today: Optional[_date] = None) -> _date:
    today = today or datetime.now(timezone.utc).date()
    return today + timedelta(days=round(target_years * DEFAULT_A))


def find_candidate_expiries(avail: List[str], target_years: float,
                            today: Optional[_date] = None) -> List[ExpiryCandidate]:
    """Return the closest weekly expiry, the closest monthly/OPEX expiry, and
    (if it's neither of those two) the closest expiry overall to
    target_years -- up to 3 candidates, de-duplicated, closest first.

    This is the core of the "give 2, maybe 3 of the closest expiry" request:
    weekly and monthly are surfaced as their own picks even when one of them
    isn't the single globally-nearest date, because a monthly/OPEX date
    carries very different liquidity/pinning behavior than a weekly one and
    the user wants both options on the table, not just whichever is
    numerically closest.
    """
    today = today or datetime.now(timezone.utc).date()
    parsed = _parse_expiries(avail, today)
    if not parsed:
        return []

    target_dte = round(target_years * DEFAULT_A)

    def _closest(kind_filter):
        pool = [p for p in parsed if kind_filter(p[3])] or parsed
        return min(pool, key=lambda p: abs(p[2] - target_dte))

    nearest_weekly = _closest(lambda k: k == "weekly")
    nearest_monthly = _closest(lambda k: k == "monthly")
    nearest_overall = min(parsed, key=lambda p: abs(p[2] - target_dte))

    picks = []
    seen = set()
    for exp_str, d, dte, kind in [nearest_weekly, nearest_monthly, nearest_overall]:
        if exp_str in seen:
            continue
        seen.add(exp_str)
        picks.append(ExpiryCandidate(
            exp_str=exp_str, exp_date=d, dte=dte, T_years=dte / DEFAULT_A,
            kind=kind, diff_days=abs(dte - target_dte),
        ))
    picks.sort(key=lambda c: c.diff_days)
    return picks


def nearest_expiry(td, ticker: str, target_years: float) -> Tuple[str, float]:
    """Non-interactive nearest-expiry lookup -- for batch/programmatic
    callers (e.g. the multi-ticker screener looping over 10 names) that
    can't stop and prompt a human for each one. Uses the same DEFAULT_A=365
    convention as the interactive picker below, so a batch run and an
    interactive run given the same target_years agree on what expiry that
    means."""
    avail = td.list_expirations(ticker)
    if not avail:
        raise ValueError(f"No options found for {ticker}")
    candidates = find_candidate_expiries(avail, target_years)
    if not candidates:
        raise ValueError("No suitable expiry.")
    best = min(candidates, key=lambda c: c.diff_days)
    return best.exp_str, best.T_years


def resolve_expiration(td, ticker: str, expiration: Optional[str],
                       target_years: float) -> Tuple[str, float]:
    """Shared helper for run_* functions that accept an optional pre-resolved
    `expiration` (so a caller like volatility_suite.py can force the exact
    same expiry across every module in one run) with a safe fallback.

    If `expiration` is given but isn't actually listed for this ticker (e.g.
    the focus ticker and the index leg don't share every listing), falls
    back to an independent nearest_expiry() lookup for THIS ticker and says
    so, rather than raising -- a stock and its sector ETF still trade on the
    same weekly/monthly calendar in the vast majority of cases, so this
    should be rare.
    """
    if expiration:
        avail = td.list_expirations(ticker)
        if expiration in {str(e).strip() for e in avail}:
            exp_date = datetime.strptime(expiration, EXPIRY_DATE_FMT).date()
            today = datetime.now(timezone.utc).date()
            actual_T = max((exp_date - today).days, 0) / DEFAULT_A
            return expiration, actual_T
        print(f"  [expiry] {expiration} isn't listed for {ticker}; "
              f"falling back to nearest match for T={target_years:.4f}yr.")
    return nearest_expiry(td, ticker, target_years)


def _describe(c: ExpiryCandidate) -> str:
    weekday = c.exp_date.strftime("%a")
    label = {"monthly": "MONTHLY/OPEX", "weekly": "WEEKLY", "other": "OTHER"}[c.kind]
    return (f"{c.exp_date.isoformat()} ({weekday}, {label})  "
            f"DTE={c.dte}  T={c.T_years:.4f}yr")


def choose_expiry_interactive(td, ticker: str, target_years: Optional[float] = None,
                              prompt_prefix: str = "Target time-to-expiry in years (e.g. 0.25, default 0.25): "
                              ) -> Tuple[str, float]:
    """Prompt for target_years exactly like the old single-expiry flow (a
    number like 0.33 / 0.15 / 0.25), but instead of silently carrying that
    number forward into one auto-picked expiry, resolve it to 2-3 concrete
    calendar dates -- closest weekly, closest monthly/OPEX, and (if
    different from both) closest overall -- and make the user pick the
    specific date.
    """
    if target_years is None:
        ty_input = input(prompt_prefix).strip()
        target_years = float(ty_input) if ty_input else 0.25

    avail = td.list_expirations(ticker)
    if not avail:
        raise ValueError(f"No options found for {ticker}")
    candidates = find_candidate_expiries(avail, target_years)
    if not candidates:
        raise ValueError(f"No suitable expiry found for {ticker}.")

    target_date = target_date_from_years(target_years)
    print(f"\n  Target date for T={target_years:.4f}yr: {target_date.isoformat()}")
    for i, c in enumerate(candidates, 1):
        print(f"    {i}) {_describe(c)}")

    if len(candidates) == 1:
        return candidates[0].exp_str, candidates[0].T_years

    choice = input(f"  Choose expiry by number (default 1 -- closest overall): ").strip()
    if not choice:
        idx = 0
    elif choice.isdigit() and 1 <= int(choice) <= len(candidates):
        idx = int(choice) - 1
    else:
        print("  Unrecognized input -- using closest overall.")
        idx = 0
    chosen = candidates[idx]
    print(f"  -> Using {chosen.exp_str} ({_describe(chosen)})")
    return chosen.exp_str, chosen.T_years


def choose_expiry_noninteractive(td, ticker: str,
                                  expiry: Optional[str] = None,
                                  target_years: Optional[float] = None
                                  ) -> Tuple[str, float]:
    """Non-interactive expiry selection for CLI-driven runs.

    If *expiry* is provided (YYYYMMDD or YYYY-MM-DD), use it directly and
    derive T_years from today.  If only *target_years* is provided, pick the
    closest overall expiry.  If neither is provided, raise ValueError so the
    caller knows to supply one.
    """
    avail = td.list_expirations(ticker)
    if not avail:
        raise ValueError(f"No options found for {ticker}")

    # Normalise expiry to compact YYYYMMDD.
    if expiry:
        compact = str(expiry).replace("-", "")
        today = datetime.now(timezone.utc).date()
        try:
            exp_date = datetime.strptime(compact, "%Y%m%d").date()
        except ValueError:
            raise ValueError(f"Bad expiry format '{expiry}' -- use YYYYMMDD or YYYY-MM-DD")
        dte = (exp_date - today).days
        if dte < 0:
            raise ValueError(f"Expiry {expiry} is in the past")
        t_years = dte / DEFAULT_A
        print(f"  Using expiry {compact} (T={t_years:.4f}yr, {dte}DTE)")
        return compact, t_years

    if target_years is None:
        raise ValueError("Need --expiry or --target-years for non-interactive expiry selection")

    candidates = find_candidate_expiries(avail, target_years)
    if not candidates:
        raise ValueError(f"No suitable expiry found for {ticker}")

    chosen = candidates[0]   # closest overall
    print(f"  -> Auto-selected {chosen.exp_str} ({_describe(chosen)})")
    return chosen.exp_str, chosen.T_years
