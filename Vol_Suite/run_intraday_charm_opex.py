"""Tier-3A — Intraday charm-at-OpEx arm (ESC-3, the arbiter's live claim).

Karsan's claim (verified framework): charm clusters at OpEx — ~10x at 2-DTE vs 30-DTE,
most aggressive in the final 2 hours. Daily clock structurally cannot test it; this arm
uses intraday 10-min all_greeks from the ThetaData proxy (served, verified: 40 buckets/day,
9:30-16:00 ET, full charm/gamma/vanna/IV) + 1-min stock OHLC.

Design (pre-registered):
- 3 past OpEx weeks: May 2026 (05-15), Jun 2026 (06-19), Jul 2026 (07-17).
- Per week, T-1 (Thursday) and OpEx day (Friday) where available.
- Expiry pair per day: SHORT = the OpEx-week expiry (2-DTE on T-1, 1-DTE/0-DTE on OpEx day),
  LONG = next-month expiry (~30-DTE).
- ATM strike from the stock's EOD close of the same day (theta-int).
- Metric: mean |charm| and mean charm in the FINAL 2 HOURS (ms >= 50400000 = 14:00 ET)
  per (ticker, day, expiry), then ratio short/long per day.
- Bar: SUPPORTED if the short/long |charm| ratio in the final 2 hours is >= 5x in a
  majority of OpEx days, and the ratio is monotonically larger in the final 2 hours than
  in the first 2 hours (charm accelerates into the close). Else BOUNDED/REJECTED.
- Caveat: charm is annualized or per-day per the live model's convention; here we compare
  the raw proxy charm field between expiries on the SAME day, so the convention cancels.

Usage: python run_intraday_charm_opex.py [--tickers SPY,QQQ] [--force]
Writes: Vol_Suite/_intraday_cache/charm_opex_result.md
"""

import argparse
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.thetadata import ThetaDataController, strike_to_theta

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
FINAL_2H_MS = 50400000  # 14:00 ET
OPEN_2H_MS = 37800000  # 10:30 ET

OPEX_WEEKS = [
    {
        "name": "may2026",
        "opex": "20260515",
        "t1": "20260514",
        "short": "20260515",
        "long": "20260619",
    },
    {
        "name": "jun2026",
        "opex": "20260619",
        "t1": "20260618",
        "short": "20260619",
        "long": "20260717",
    },
    {
        "name": "jul2026",
        "opex": "20260717",
        "t1": "20260716",
        "short": "20260717",
        "long": "20260821",
    },
]


def _fetch_day_chain(ctl, root, exp, day, strike_theta, ivl=600000):
    """Intraday all_greeks for ONE contract on ONE day -> list of rows."""
    path = f"/api/theta/hist/option/all_greeks/{root}/{exp}/{strike_theta}/C"
    try:
        r = ctl._get_with_retry(
            path, params={"start_date": day, "end_date": day, "ivl": ivl}
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return ctl._parse_rows(r)
    except Exception as e:
        print(
            f"    [warn] {root} {exp} {strike_theta}C {day}: {type(e).__name__}: {str(e)[:100]}"
        )
        return []


def _stock_close(ctl, root, day):
    try:
        r = ctl._get_with_retry(
            f"/api/theta/hist/stock/eod/{root}",
            params={"start_date": day, "end_date": day},
        )
        r.raise_for_status()
        rows = ctl._parse_rows(r)
        for row in reversed(rows):
            c = row.get("close")
            if c not in (None, "", "0", 0):
                try:
                    return float(c)
                except (TypeError, ValueError):
                    continue
    except Exception:
        pass
    return None


def _summarize_charm(rows):
    """Return (final2h_mean_abs_charm, final2h_mean_charm, open2h_mean_abs_charm, n)."""
    fin_abs, fin_raw, opn_abs, n_fin, n_opn = [], [], [], 0, 0
    for x in rows:
        ms = float(x.get("ms_of_day", 0) or 0)
        ch = x.get("charm")
        if ch is None:
            continue
        try:
            ch = float(ch)
        except (TypeError, ValueError):
            continue
        if ms >= FINAL_2H_MS:
            fin_abs.append(abs(ch))
            fin_raw.append(ch)
            n_fin += 1
        elif ms < OPEN_2H_MS:
            opn_abs.append(abs(ch))
            n_opn += 1
    if not fin_abs:
        return (None, None, None, 0)
    return (
        sum(fin_abs) / len(fin_abs),
        sum(fin_raw) / len(fin_raw),
        (sum(opn_abs) / len(opn_abs)) if opn_abs else None,
        n_fin,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="SPY,QQQ")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, "charm_opex_result.md")
    if os.path.exists(out) and not args.force:
        print(f"cached: {out} (use --force to recompute)")
        return

    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append("# Tier-3A — Intraday Charm-at-OpEx (ESC-3)\n")
    lines.append(
        f"**Date:** {dt.date.today().isoformat()}  **Tickers:** {','.join(tickers)}\n"
    )
    lines.append(
        "Karsan claim tested: charm clusters at OpEx (~10x at 2-DTE vs 30-DTE, "
        "most aggressive final 2 hours). Intraday 10-min all_greeks via proxy.\n"
    )
    lines.append(
        "| Week | Day | Ticker | Expiry | DTE | final2h \\|charm\\| | final2h charm | "
        "open2h \\|charm\\| | n |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")

    per_week = {}
    for wk in OPEX_WEEKS:
        for day_label, day in (("T-1", wk["t1"]), ("OPEX", wk["opex"])):
            for tk in tickers:
                close = _stock_close(ctl, tk, day)
                if not close:
                    continue
                # snap to the listed strike grid ($5 increments for SPY/QQQ — 404 otherwise)
                k = strike_to_theta(round(close / 5.0) * 5.0)
                for exp_label, exp in (("short", wk["short"]), ("long", wk["long"])):
                    dte = None
                    try:
                        dte = (
                            dt.datetime.strptime(exp, "%Y%m%d").date()
                            - dt.datetime.strptime(day, "%Y%m%d").date()
                        ).days
                    except Exception:
                        pass
                    rows = _fetch_day_chain(ctl, tk, exp, day, k)
                    fin_abs, fin_raw, opn_abs, n = _summarize_charm(rows)
                    cell = f"{fin_abs:+.4f}" if fin_abs is not None else "—"
                    cell2 = f"{fin_raw:+.4f}" if fin_raw is not None else "—"
                    cell3 = f"{opn_abs:+.4f}" if opn_abs is not None else "—"
                    lines.append(
                        f"| {wk['name']} | {day_label} | {tk} | {exp} | {dte} | "
                        f"{cell} | {cell2} | {cell3} | {n} |"
                    )
                    key = (wk["name"], day_label, tk)
                    per_week.setdefault(key, {})[exp_label] = (
                        fin_abs,
                        fin_raw,
                        n,
                        opn_abs,
                    )

    # ratios short/long final-2h
    lines.append("\n### Short/Long |charm| ratio in the final 2 hours\n")
    lines.append(
        "| Week | Day | Ticker | short\\|charm\\| | long\\|charm\\| | ratio | verdict |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    n_supported = 0
    n_days = 0
    final_ratios = []
    open_ratios = []
    for (wk, day_label, tk), d in sorted(per_week.items()):
        if "short" not in d or "long" not in d:
            continue
        s_abs, s_raw, s_n, s_opn = d["short"]
        l_abs, l_raw, l_n, l_opn = d["long"]
        if not s_abs or not l_abs or s_abs == 0:
            continue
        ratio = s_abs / l_abs
        n_days += 1
        supported = ratio >= 5.0
        n_supported += int(supported)
        final_ratios.append(ratio)
        if s_opn and l_opn and l_opn != 0:
            open_ratios.append(s_opn / l_opn)
        lines.append(
            f"| {wk} | {day_label} | {tk} | {s_abs:+.4f} | {l_abs:+.4f} | "
            f"{ratio:.1f}x | {'SUPPORTED' if supported else 'below 5x'} |"
        )

    lines.append("\n### Verdict")
    if n_days:
        frac = n_supported / n_days
        lines.append(
            f"- {n_supported}/{n_days} OpEx days show short/long |charm| ratio >= 5x "
            f"in the final 2 hours ({frac:.0%})."
        )
        # Pre-registered acceleration condition: the final-2h short/long
        # |charm| ratio must exceed the open-2h short/long |charm| ratio
        # (charm clustering *accelerates* into the close, not just elevated
        # all day) before SUPPORTED can be reported.
        if open_ratios:
            avg_final_ratio = sum(final_ratios) / len(final_ratios)
            avg_open_ratio = sum(open_ratios) / len(open_ratios)
            accel_confirmed = avg_final_ratio > avg_open_ratio
            lines.append(
                f"- acceleration check: avg final2h ratio {avg_final_ratio:.1f}x vs "
                f"avg open2h ratio {avg_open_ratio:.1f}x "
                f"({'confirmed' if accel_confirmed else 'NOT confirmed'})."
            )
            if frac >= 0.5 and accel_confirmed:
                lines.append(
                    "- **SUPPORTED**: charm-at-OpEx clusters confirmed on intraday data "
                    "(Karsan's ESC-3 claim)."
                )
            else:
                lines.append(
                    "- **BOUNDED/REJECTED**: the charm-clustering claim does not replicate "
                    "across OpEx weeks at this ratio bar and/or fails the acceleration check."
                )
        else:
            lines.append(
                "- acceleration leg not evaluated (no usable open-2h |charm| data) — "
                "not reporting SUPPORTED without it."
            )
    else:
        lines.append("- no usable days (data unavailable).")
    lines.append(f"\n[t3a] total {time.time() - t0:.1f}s")

    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines[-12:]))


if __name__ == "__main__":
    main()
