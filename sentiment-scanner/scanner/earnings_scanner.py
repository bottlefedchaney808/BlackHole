"""Earnings Options Flow Scanner — earnings-vol premium analysis.

For stocks with upcoming earnings, finds the closest options expiry
before and after the earnings date, reads ATM IV for both, and
computes the earnings-vol premium (IV shift attributable to the
earnings event).

A positive premium means the market prices higher IV post-earnings
(the earnings event itself adds vol).  Negative premium can flag
vol crushing after known-earnings names.

Outputs are fed to the correlation engine as EARNINGS_VOL_PREMIUM
when the premium exceeds the HIGH threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from scanner.options_scanner_base import VolSuiteImporter, get_td

vsi = VolSuiteImporter()

# ── Thresholds (in vol percentage points) ──────────────────────────────
HIGH_PREMIUM_THRESHOLD = 5.0   # ≥5pp premium → HIGH signal
MODERATE_PREMIUM_THRESHOLD = 2.0  # ≥2pp → MODERATE

# Calendar days before/after earnings date to anchor expiry searches
BEFORE_EARNINGS_DAYS = 7   # target expiry ~1 week before earnings
AFTER_EARNINGS_DAYS = 14   # target expiry ~2 weeks after earnings

# ── Hardcoded earnings dates (YYYY-MM-DD) for well-known tickers ──────
# Format: ticker -> date string (for now a future-looking estimate).
# These will be replaced with a live earnings calendar feed later.
EARNINGS_CALENDAR: Dict[str, str] = {
    "AAPL":  "2026-07-30",
    "MSFT":  "2026-07-28",
    "AMZN":  "2026-07-31",
    "GOOGL": "2026-07-29",
    "META":  "2026-07-29",
    "TSLA":  "2026-07-22",
    "NVDA":  "2026-08-20",
}


# ── Dataclass ──────────────────────────────────────────────────────────

@dataclass
class EarningsResult:
    ticker: str
    spot: float
    earnings_date: str
    expiry_before: str                         # closest expiry before earnings
    expiry_after: str                          # closest expiry after earnings
    iv_before_pct: float                       # ATM IV for expiry before
    iv_after_pct: float                        # ATM IV for expiry after
    premium_pct: float                         # iv_after - iv_before (pp)
    signal: str                                # "HIGH" | "MODERATE" | "LOW" | "UNKNOWN"
    num_strikes_before: int
    num_strikes_after: int
    timestamp: str
    error: Optional[str] = None


# ── EarningsScanner Class ──────────────────────────────────────────────

class EarningsScanner:
    """Scan tickers for earnings-vol premium between pre- and post-earnings
    options expiries."""

    def __init__(self, td) -> None:
        self.td = td

    # ------------------------------------------------------------------
    def get_earnings_date(self, ticker: str) -> Optional[str]:
        """Return upcoming earnings date string (YYYY-MM-DD) for *ticker*.

        Uses a hardcoded lookup dict for well-known tickers.  Returns
        ``None`` for tickers not in the calendar.
        """
        return EARNINGS_CALENDAR.get(ticker.upper())

    # ------------------------------------------------------------------
    def scan_earnings(self, tickers: List[str]) -> List[EarningsResult]:
        """Run the earnings-vol scan for a list of tickers.

        Parameters
        ----------
        tickers : List[str]
            Equity symbols to scan.

        Returns
        -------
        List[EarningsResult]
        """
        results: List[EarningsResult] = []
        ts = datetime.now(timezone.utc).isoformat()
        today = datetime.now(timezone.utc).date()

        for ticker in tickers:
            result = self._scan_one(ticker, today, ts)
            results.append(result)

        return results

    # ------------------------------------------------------------------
    def _scan_one(self, ticker: str, today: datetime.date,
                  ts: str) -> EarningsResult:
        """Run the earnings-vol scan for a single ticker."""

        # 1. Spot price
        try:
            spot = self.td.fetch_spot_price(ticker)
        except Exception as e:
            return EarningsResult(
                ticker=ticker, spot=0.0, earnings_date="",
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts, error=f"spot_fetch: {e}",
            )
        if spot <= 0:
            return EarningsResult(
                ticker=ticker, spot=0.0, earnings_date="",
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts, error="no_spot",
            )

        # 2. Earnings date
        earnings_date_str = self.get_earnings_date(ticker)
        if not earnings_date_str:
            return EarningsResult(
                ticker=ticker, spot=spot, earnings_date="",
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts, error="no_earnings_date",
            )

        try:
            earnings_dt = datetime.strptime(
                earnings_date_str, "%Y-%m-%d"
            ).date()
        except ValueError as e:
            return EarningsResult(
                ticker=ticker, spot=spot, earnings_date=earnings_date_str,
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts, error=f"bad_date_format: {e}",
            )

        # 3. List available expirations
        try:
            avail = self.td.list_expirations(ticker)
        except Exception as e:
            return EarningsResult(
                ticker=ticker, spot=spot, earnings_date=earnings_date_str,
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts, error=f"list_expirations: {e}",
            )

        if not avail:
            return EarningsResult(
                ticker=ticker, spot=spot, earnings_date=earnings_date_str,
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts, error="no_expirations",
            )

        # 4. Find closest expiry before and after earnings
        target_before = earnings_dt - timedelta(days=BEFORE_EARNINGS_DAYS)
        target_after = earnings_dt + timedelta(days=AFTER_EARNINGS_DAYS)

        expiry_before = ""
        expiry_after = ""
        min_days_before = 9999
        min_days_after = 9999

        for exp_str in avail:
            s = str(exp_str).strip()
            try:
                d = datetime.strptime(s, "%Y%m%d").date()
            except ValueError:
                continue
            dte = (d - today).days
            if dte < 0:
                continue  # skip expired

            # Expiry before earnings (closest to target_before)
            if d <= earnings_dt:
                diff = abs((d - target_before).days)
                if diff < min_days_before:
                    min_days_before = diff
                    expiry_before = s

            # Expiry after earnings (closest to target_after)
            if d > earnings_dt:
                diff = abs((d - target_after).days)
                if diff < min_days_after:
                    min_days_after = diff
                    expiry_after = s

        if not expiry_before or not expiry_after:
            return EarningsResult(
                ticker=ticker, spot=spot, earnings_date=earnings_date_str,
                expiry_before=expiry_before, expiry_after=expiry_after,
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp=ts,
                error="no_suitable_expiry_before_or_after",
            )

        # 5. Fetch ATM IV for both expiries
        iv_before, n_before = self._atm_iv(ticker, expiry_before)
        iv_after, n_after = self._atm_iv(ticker, expiry_after)

        # 6. Premium & signal
        premium = iv_after - iv_before
        if premium >= HIGH_PREMIUM_THRESHOLD:
            signal = "HIGH"
        elif premium >= MODERATE_PREMIUM_THRESHOLD:
            signal = "MODERATE"
        elif iv_before > 0 and iv_after > 0:
            signal = "LOW"
        else:
            signal = "UNKNOWN"

        return EarningsResult(
            ticker=ticker, spot=round(spot, 2),
            earnings_date=earnings_date_str,
            expiry_before=expiry_before,
            expiry_after=expiry_after,
            iv_before_pct=round(iv_before, 2),
            iv_after_pct=round(iv_after, 2),
            premium_pct=round(premium, 2),
            signal=signal,
            num_strikes_before=n_before,
            num_strikes_after=n_after,
            timestamp=ts,
        )

    # ------------------------------------------------------------------
    def _atm_iv(self, ticker: str, expiry: str
                ) -> Tuple[float, int]:
        """Compute ATM IV (median across strikes/rights) for a single
        expiry.  Returns ``(iv_pct, num_strikes)``."""
        try:
            greeks = self.td.option_bulk_greeks(ticker, expiry)
        except Exception:
            return 0.0, 0

        ivs = []
        for row in greeks:
            try:
                iv = float(row.get("implied_vol", 0) or 0)
                if iv > 0:
                    ivs.append(iv)
            except (ValueError, TypeError):
                continue

        if not ivs:
            return 0.0, 0

        atm_iv = float(np.median(ivs)) * 100.0
        return atm_iv, len(ivs)


# ── Standalone convenience functions ───────────────────────────────────

def scan_ticker(ticker: str, td=None) -> Optional[EarningsResult]:
    """Convenience wrapper — create an EarningsScanner and scan one ticker.

    Parameters
    ----------
    ticker : str
    td : optional ThetaDataController
        If not given, one is created/retrieved via ``get_td()``.

    Returns
    -------
    EarningsResult or None
    """
    if td is None:
        td = get_td()
    scanner = EarningsScanner(td)
    return scanner._scan_one(
        ticker,
        datetime.now(timezone.utc).date(),
        datetime.now(timezone.utc).isoformat(),
    )


def format_earnings(results: List[EarningsResult]) -> str:
    """Format a list of earnings scan results as a table string.

    Parameters
    ----------
    results : List[EarningsResult]

    Returns
    -------
    str
    """
    if not results:
        return "  (no earnings results)"

    lines: List[str] = []
    # Header
    lines.append(
        f"  {'Ticker':6s} {'Earnings':12s} {'ExpBefore':10s}"
        f" {'ExpAfter':10s} {'IVbef%':7s} {'IVaft%':7s}"
        f" {'Prem%':7s} {'Signal':10s}"
    )
    lines.append("  " + "-" * 74)

    for r in results:
        if r.error:
            lines.append(f"  {r.ticker:6s} | ERROR — {r.error}")
        else:
            lines.append(
                f"  {r.ticker:6s} {r.earnings_date:12s}"
                f" {r.expiry_before:10s} {r.expiry_after:10s}"
                f" {r.iv_before_pct:7.1f} {r.iv_after_pct:7.1f}"
                f" {r.premium_pct:+7.1f} {r.signal:10s}"
            )

    return "\n".join(lines)


def format_earnings_one(r: EarningsResult) -> str:
    """One-line summary string for a single earnings result."""
    if r.error:
        return f"  {r.ticker:6s} | EARN: ERROR — {r.error}"
    return (
        f"  {r.ticker:6s} | Earnings: {r.earnings_date} "
        f"| IV: {r.iv_before_pct:.1f}% → {r.iv_after_pct:.1f}% "
        f"({r.premium_pct:+.1f}pp) "
        f"| Exp: {r.expiry_before}/{r.expiry_after} "
        f"| Signal: {r.signal}"
    )


# ── Command-line entry ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "MSFT", "NVDA"]
    td = get_td()
    scanner = EarningsScanner(td)
    results = scanner.scan_earnings(tickers)
    print(format_earnings(results))