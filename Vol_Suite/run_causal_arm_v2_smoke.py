#!/usr/bin/env python3
"""Deterministic in-memory smoke for run_causal_arm_v2 (ROUND-10.2).

Exercises the SAME callable core the CLI uses, with a synthetic 31-unique-day
fixture that mixes SPY+QQQ families (dedup), event days with operational
surprise, varying pre-event vanna / controls, and both clocks eligible. No
network, no acquisition. Deterministic seed.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402


def build_day(day, fam, pre_vanna, div, resp, pre_ts, breach_ts, **kw):
    fam_key = f"family_interaction_{fam.lower()}"
    # propagate surprise + event_habitat into l2 (R10.3 fix: R1 mechanism panel
    # confirmed build_day previously dropped these, so the smoke never exercised
    # the OPERATIONAL-surprise path and the READY L5 reconciliation was wrong)
    surprise = kw.pop("surprise", None)
    event_habitat = kw.pop("event_habitat", "NONE")
    return {
        "day": day, "date": day, "ticker": fam, "families": [fam],
        "pre_window": {
            "pre_vanna_timestamp": pre_ts, "pre_window_end": pre_ts,
            "breach_window_start": breach_ts, "cutoff_pass": pre_ts < breach_ts,
            "n_rows_used": kw.pop("n_rows_used", 25),
            "excluded_post_cutoff": kw.pop("excluded_post_cutoff", 0),
        },
        "l2": {
            "pre_vanna_exposure": pre_vanna,
            "delta_iv": kw.get("div", div),
            "gamma_burst": kw.get("gamma", 0.0),
            "delta_s": kw.get("ds", 0.0),
            "market": kw.get("mkt", 0.0),
            "event": kw.get("event", 0),
            "a6_reflexivity": kw.get("a6", 0.0),
            "cross_family_spillover": kw.get("spill", 0.0),
            "surprise": surprise,
            "event_habitat": event_habitat,
            fam_key: pre_vanna,
            "forward_return_h": resp,
        },
        "clock": {
            "daily": {"return": kw.get("daily_ret"), "eligible": kw.get("daily_eligible", False)},
            "from_breach": {"return": kw.get("breach_ret"), "eligible": kw.get("breach_eligible", False)},
        },
    }


def build_fixture(n_days=31):
    rng = np.random.RandomState(20260815)
    recs = []
    for i in range(n_days):
        day = f"2026{100 + i:03d}"
        fam = "SPY" if i % 2 == 0 else "QQQ"          # alternate families
        pre_vanna = rng.uniform(0.5, 2.5)
        div = rng.choice([-0.02, -0.01, 0.01, 0.02])
        gamma = rng.normal(0.0, 0.04)
        ds = rng.normal(0.0, 0.002)
        mkt = rng.normal(0.0, 0.002)
        a6 = rng.normal(0.0, 0.03)
        spill = rng.normal(0.0, 0.02)
        event = 1 if i in (5, 12, 19, 26) else 0      # 4 event days
        resp = 2.0 * pre_vanna * div + 0.3 * gamma + rng.normal(0.0, 0.2)
        # pre-event cutoff strictly before the breach window
        pre_ts = 300_000 + i * 100                    # e.g. 09:05-ish
        breach_ts = pre_ts + 600_000                  # 10 min later
        recs.append(build_day(
            day, fam, pre_vanna, div, resp, pre_ts, breach_ts,
            gamma=gamma, ds=ds, mkt=mkt, event=event, a6=a6, spill=spill,
            daily_ret=-0.01 if i % 3 == 0 else 0.005, daily_eligible=True,
            breach_ret=0.015 if i % 2 == 0 else -0.008, breach_eligible=True,
            n_rows_used=25, excluded_post_cutoff=0,
            **({"surprise": 0.4 if event else 0.0, "event_habitat": "FOMC" if event else "NONE"}
               if event else {}),
        ))
    return recs


def main():
    recs = build_fixture(31)
    table = ca.run_causal_arm(recs, config={"locked_h_breach": 1, "locked_h_daily": 1,
                                            "horizon_sweep": [1]})
    md = ca.render_table_md(table)
    print(md)
    print("\n===== STRUCTURED SUMMARY =====")
    print(json.dumps({
        "effective_n_unique_days": table["effective_n_unique_days"],
        "unique_day_target": table["unique_day_target"],
        "reach_unique_day_target": table["reach_unique_day_target"],
        "locked_h_breach": table["locked_h_breach"],
        "horizon_locked": table["horizon_locked"],
        "cutoff_integrity_ok": table["cutoff_integrity_ok"],
        "solver": table["solver"],
        "rank": table["rank"], "p": table["p"], "cond": table["cond"],
        "max_vif": table["max_vif"], "vif_flag": table["vif_flag"],
        "beta": table["beta"], "beta_se": table["beta_se"],
        "beta_status": table["beta_status"],
        "beta_power": table["beta_power"],
        "beta_power_available": table["beta_power_available"],
        "beta_n_for_80": table["beta_n_for_80"],
        "beta_reach_80": table["beta_reach_80"],
        "family_rule": table["family_rule"],
        "both_clock": table["both_clock"]["value"],
        "n_confirmed_days": table["both_clock"]["n_confirmed_days"],
        "n_eligible_days": table["both_clock"]["n_eligible_days"],
    }, indent=2))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_intraday_cache", "causal_arm_v2_smoke_decision_table.md")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
