#!/usr/bin/env python3
"""Finalize the R10.3 causal-arm acquisition (R10.4 driver/merge corrections applied).

Loads the merged day-records, runs run_causal_arm_v2.py, and writes the acquisition
result MD with the single decision table + honest power caveat. Network-free.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))
MERGED = os.path.join(OUT, "records_merged", "day_records_merged.json")
RESULT_MD = os.path.join(os.path.dirname(OUT), "_intraday_cache", "causal_arm_v2_acquisition_RESULT.md")


def main():
    records = json.load(open(MERGED, encoding="utf-8"))
    n_td = len(records)
    n_days = ca.unique_day_count(records)
    n_breach = sum(1 for r in records if r["clock"]["from_breach"].get("eligible") is True)
    n_event = sum(1 for r in records if r["l2"].get("event_habitat", "NONE") != "NONE")
    n_cutoff = sum(1 for r in records if r["pre_window"].get("cutoff_pass") is True)

    table = ca.run_causal_arm(records, config={"locked_h_breach": 1, "locked_h_daily": 1})
    md = ca.render_table_md(table)

    lines = []
    lines.append("# Causal-Arm v2 — ≥29-Day Acquisition Result (ROUND-10.4 finalize)")
    lines.append("")
    lines.append(f"**Date:** 2026-08-15  **Source:** `Vol_Suite/_causal_acquisition_20260815/records_merged/` (network-free finalize)")
    lines.append("")
    lines.append(f"- Flattened day-records: **{n_td}** (ticker,day) across **{n_days} unique calendar days** (≥29 target: {'REACHED' if n_days >= 29 else 'NOT REACHED'})")
    lines.append(f"- cutoff_pass=True: {n_cutoff}/{n_td}")
    lines.append(f"- breach-eligible records: {n_breach}")
    lines.append(f"- event-habitat records: {n_event}")
    lines.append("")
    lines.append("## Single Decision Table")
    lines.append("")
    lines.extend(md.splitlines())
    lines.append("")
    lines.append("## Honest power caveat (per R10.3/R10.4 disclosure)")
    lines.append("")
    lines.append(f"- effective unique-day n = {table['effective_n_unique_days']}. The **≥29-day target sizes the "
                 "correlational MDE arm (md≤0.5), NOT the causal L2 interaction β.**")
    lines.append(f"- β-specific one-sided power = {table['beta_power']:.3f} (reach_80={table['beta_reach_80']}, "
                 f"n_for_80={table['beta_n_for_80']}). This is the honest causal-arm power; it is NOT an 80% claim "
                 f"unless `beta_reach_80` is True.")
    lines.append("- A positive/identifiable β is re-admission-to-evidence evidence only (after reverse/placebo/A6/"
                 "gamma/market/spillover/opposite/family/both-clock rules + fresh R1→R2→R3 Cem). It NEVER auto-promotes. "
                 "The model stays descriptive/conditional throughout.")
    os.makedirs(os.path.dirname(RESULT_MD), exist_ok=True)
    with open(RESULT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[saved] {RESULT_MD}")


if __name__ == "__main__":
    main()
