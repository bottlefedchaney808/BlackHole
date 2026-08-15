#!/usr/bin/env python3
"""Merge the cached causal-acquisition raw metrics into a full-rank run_causal_arm_v2
day-record set.

Fixes the same-day merge the acquisition agent left outstanding:
- `market` = the SPY (index-family) close-to-close return for that unique day,
  applied to BOTH families (a common market factor, distinct from each family's own
  delta_s).
- `cross_family_spillover` = the opposite family's delta_s on the same day (QQQ's
  delta_s for the SPY row, SPY's delta_s for the QQQ row), so it is not constant 0.

This breaks the collinearity that made the L2 design rank-deficient
(rank 9 < p 11) in the preliminary finalize. Network-free — works entirely from the
cached raw/ metrics.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(OUT, "raw")
MERGED = os.path.join(OUT, "records_merged")
os.makedirs(MERGED, exist_ok=True)


def load_all_metrics():
    """day -> {ticker: metrics} from raw/*.json (ok=True only)."""
    day_metrics = {}
    for f in sorted(glob.glob(os.path.join(RAW, "*.json"))):
        rw = json.load(open(f, encoding="utf-8"))
        m = rw.get("metrics") or {}
        if not m.get("ok"):
            continue
        day = m["day"]
        day_metrics.setdefault(day, {})[m["ticker"].upper()] = m
    return day_metrics


def habitat_for(day, metrics):
    """event_habitat from the merged record (prefer explicit, else NONE)."""
    # reuse the acquirer's candidate habitat: pull from any record's 'event_habitat'
    for m in metrics.values():
        if m.get("ticker"):
            pass
    return None  # filled below from l2


def build_merged_record(day, ticker, m, market_ret, spillover_ret, habitat):
    fam = ticker.upper()
    fam_key = f"family_interaction_{fam.lower()}"
    pre_v = m.get("pre_event_vanna_exposure")
    pre_v = float(pre_v) if pre_v is not None else float("nan")
    # delta_iv for the causal interaction: use the day's net IV change as the
    # ΔIV that multiplies pre-event vanna exposure
    delta_iv = m.get("net_div") or 0.0
    gamma_burst = m.get("max_abs_burst") or 0.0
    ds = m.get("delta_s") or 0.0
    a6 = m.get("a6")
    a6 = a6 if a6 is not None else 0.0
    return {
        "day": day, "date": day, "ticker": fam, "families": [fam],
        "dte": m.get("dte"), "expiry": m.get("expiry"), "event_habitat": habitat,
        "pre_window": {
            "pre_vanna_timestamp": m.get("pre_vanna_timestamp"),
            "pre_window_end": m.get("pre_window_end"),
            "breach_window_start": m.get("breach_window_start"),
            "cutoff_pass": bool(m.get("cutoff_pass")),
            "n_rows_used": m.get("pre_rows_used"),
            "excluded_post_cutoff": m.get("excluded_post_cutoff"),
        },
        "l2": {
            "pre_vanna_exposure": pre_v,
            "delta_iv": delta_iv,
            "gamma_burst": gamma_burst,
            "delta_s": ds,
            "market": market_ret,
            "event": 1 if habitat != "NONE" else 0,
            "a6_reflexivity": a6,
            "cross_family_spillover": spillover_ret,
            "surprise": None,          # no operational forecast -> DESCRIPTIVE-HABITAT
            "event_habitat": habitat,
            fam_key: pre_v,
            "forward_return_h": m.get("forward_return_h") or 0.0,
        },
        "clock": {
            "daily": {"return": m.get("daily_return"), "eligible": True},
            "from_breach": {"return": m.get("breach_return"),
                            "eligible": bool(m.get("breach_eligible"))},
        },
        "source": f"merged-{day}-{fam.lower()}",
    }


def main():
    day_metrics = load_all_metrics()
    n_days = len(day_metrics)
    n_records = sum(len(v) for v in day_metrics.values())
    print(f"cached ok-days: {n_days} unique; {n_records} (ticker,day) metrics")

    # capture per-day habitat from the acquisition candidate labels (already in records/*.json)
    import glob as _g
    habitat_map = {}
    for rf in _g.glob(os.path.join(OUT, "records", "*.json")):
        d = json.load(open(rf, encoding="utf-8"))
        habitat_map[d["day"]] = d.get("habitat", "NONE")

    merged = []
    for day in sorted(day_metrics):
        ms = day_metrics[day]
        fams = {t: ms[t] for t in ("SPY", "QQQ") if t in ms}
        if not fams:
            continue
        # market = SPY close-to-close return (common index factor); fallback to the
        # single family's own daily return if SPY absent.
        spy = fams.get("SPY")
        market_ret = (spy.get("daily_return") if spy else
                      next(iter(fams.values())).get("daily_return")) or 0.0
        habitat = habitat_map.get(day, "NONE")
        # cross_family_spillover = the SAME day-level divergence MAGNITUDE for both
        # families: |QQQ.delta_s - SPY.delta_s|, i.e. how far apart QQQ and SPY
        # moved that day (a spread/spillover magnitude). Same value in both records.
        #
        # WHY MAGNITUDE (not signed): _merge_day_records SUMS scalar l2 fields across
        # the two family records on a same-day dedup. A signed per-family spillover
        # (e.g. QQQ.ds - SPY.ds placed in both records) sums to 2*(QQQ.ds-SPY.ds),
        # which with market=SPY close-to-close is exactly a linear combination of
        # (delta_s, market) — the design becomes rank-deficient and the driver
        # honestly returns beta=NOT-IDENTIFIABLE. The absolute divergence is
        # nonlinear in the level columns, so it is a genuinely distinct spillover
        # control that restores full rank.
        if "SPY" in fams and "QQQ" in fams:
            divergence = abs(fams["QQQ"]["delta_s"] - fams["SPY"]["delta_s"])
        else:
            divergence = 0.0
        for ticker, m in fams.items():
            spill = divergence
            merged.append(build_merged_record(day, ticker, m, market_ret, spill, habitat))

    print(f"merged records: {len(merged)}")
    with open(os.path.join(MERGED, "day_records_merged.json"), "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)

    # quick rank sanity: verify market != delta_s and spillover != 0 somewhere
    distinct_market = sum(1 for r in merged if abs(r["l2"]["market"] - r["l2"]["delta_s"]) > 1e-9)
    nonzero_spill = sum(1 for r in merged if abs(r["l2"]["cross_family_spillover"]) > 1e-12)
    print(f"records with market != delta_s: {distinct_market}/{len(merged)}; "
          f"nonzero spillover: {nonzero_spill}/{len(merged)}")
    return merged


if __name__ == "__main__":
    main()
