#!/usr/bin/env python3
"""R10.6 PRE_WINDOW sequential re-acquisition (Dealer-Exposure-Dev, 2026-08-15).

Acquires the GENUINELY NEW unique calendar days not in ANY existing corpus:
  * the committed 62-day corpus (Jan 5 - Apr 10 2026, associational-only),
  * the v4/v5 held list,
  * the _scratch_tier2 / _scratch_tier2b / Vol_Suite seed corpora
    (which already hold SPY+QQQ for Apr 24 - Aug 14 2026).

After that full dedup, the honest genuinely-new-day pool is the Apr 13-23
gap: 7 unique calendar days (all control, DTE 1-4).

Reuses the acquirer's block 7b path (acquire_causal_arm._acq_day +
build_day_record) unchanged, so each new day computes and persists:
  delta_iv_provenance ('PRE_WINDOW' only if breach_eligible AND cutoff < breach
  AND both ATM-IV anchors resolve strictly before breach, else
  'ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS'),
  delta_iv_pre_window = ATM-IV@cutoff - ATM-IV@prior_bucket (or None),
  iv_source_ts, iv_cutoff_ts, breach_window_start_prov.

NEVER imputes a missing IV/timestamp. A day failing the gate is associational-only
and excluded from the causal corpus. Gaps are recorded explicitly.

PERSISTS per-run provenance census (PRE_WINDOW n/N, exclusions, gaps) and FAILS
LOUDLY if PRE_WINDOW coverage drops below 100% of the intended causal units
(R2 adopted synergy insight). Output schema mirrors the acquirer's; raw + derived
persisted to Vol_Suite/_prewindow_acquisition_20260815/.

Model stays DESCRIPTIVE/CONDITIONAL. A positive residualized β is re-admission
evidence only, never auto-promotion.
"""
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import acquire_causal_arm as acq  # noqa: E402  (block 7b: _acq_day, build_day_record)
from shared.thetadata import ThetaDataController  # noqa: E402

os.environ["THETADATA_HIST_CONCURRENCY"] = "1"

OUT = os.path.dirname(os.path.abspath(__file__))
PW = os.path.join(OUT, "_prewindow_acquisition_20260815")
RAW = os.path.join(PW, "raw")
RECORDS = os.path.join(PW, "records")
os.makedirs(RAW, exist_ok=True)
os.makedirs(RECORDS, exist_ok=True)


def _friday_expiry(day: str) -> str:
    d = dt.datetime.strptime(day, "%Y%m%d").date()
    for _ in range(14):
        d += dt.timedelta(days=1)
        if d.weekday() == 4:  # Friday
            return d.strftime("%Y%m%d")
    return ""


# The 7 GENUINELY NEW unique calendar days (Apr 13-23 2026 gap), all controls.
# next-Friday expiries => DTE 1-4 (mixed within the locked 1-10 band).
NEW_DAYS = [
    ("20260414", "NONE", "control Tue (Apr 13-23 gap)"),
    ("20260415", "NONE", "control Wed (Apr 13-23 gap)"),
    ("20260416", "NONE", "control Thu (Apr 13-23 gap)"),
    ("20260420", "NONE", "control Mon (Apr 13-23 gap)"),
    ("20260421", "NONE", "control Tue (Apr 13-23 gap)"),
    ("20260422", "NONE", "control Wed (Apr 13-23 gap)"),
    ("20260423", "NONE", "control Thu (Apr 13-23 gap)"),
]
TICKERS = ["SPY", "QQQ"]

# Intended causal-unit denominator = genuinely-new unique calendar days.
INTENDED_CAUSAL_DAYS = len(NEW_DAYS)


def main() -> int:
    ctl = ThetaDataController()
    t0 = time.time()
    gaps_all = []
    census = []
    prewindow_pass = 0
    associational = 0
    hard_gap = 0
    done = 0
    try:
        for day, habitat, label in NEW_DAYS:
            exp = _friday_expiry(day)
            day_records = []
            day_gaps = []
            for ticker in TICKERS:
                key = f"{day}_{ticker}"
                out_raw = os.path.join(RAW, f"{key}.json")
                if os.path.exists(out_raw):
                    try:
                        saved = json.load(open(out_raw, encoding="utf-8"))
                        if saved.get("ok"):
                            day_records.append(saved["record"])
                            day_gaps.extend(saved.get("gaps", []))
                            print(f"[resume] {key}", flush=True)
                            continue
                    except Exception:
                        pass
                print(f"\n=== SESSION {key} ({ticker} {day}->{exp} {label}) ===", flush=True)
                m = acq._acq_day(ctl, ticker, day, exp)
                if not m.get("ok"):
                    day_gaps.extend(m.get("gaps", []))
                    print(f"  [GAP] {key}: {m['gaps']}", flush=True)
                    with open(out_raw, "w", encoding="utf-8") as fh:
                        json.dump({"ok": False, "day": day, "ticker": ticker,
                                   "gaps": m.get("gaps", [])}, fh, default=str)
                    continue
                record = acq.build_day_record(m)
                day_records.append(record)
                day_gaps.extend(m.get("gaps", []))
                payload = {"day": day, "ticker": ticker, "expiry": exp,
                           "metrics": m, "record": record,
                           "source": f"prewindow-acq-{day}-{ticker.lower()}"}
                with open(out_raw, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, default=str)
                print(f"  [ok] pre_vanna={m['pre_event_vanna_exposure']} "
                      f"firing={m['n_firing']}/{m['n_buckets']} net_div={m['net_div']} "
                      f"breach_elig={m['breach_eligible']} prov={m['delta_iv_provenance']} "
                      f"delta_iv_pre_window={m['delta_iv_pre_window']} dte={m['dte']}", flush=True)
                done += 1
                time.sleep(0.3)
            gaps_all.extend(day_gaps)
            # Unit-level provenance: PRE_WINDOW if BOTH families (same-day = 1 unit)
            # carry PRE_WINDOW; any associational/hard-gap family downgrades the unit.
            if day_records:
                with open(os.path.join(RECORDS, f"{day}.json"), "w", encoding="utf-8") as fh:
                    json.dump({"day": day, "expiry": exp, "habitat": habitat,
                               "label": label, "records": day_records}, fh, indent=2)
                fam_prov = {r["ticker"]: r["l2"].get("delta_iv_provenance")
                            for r in day_records}
                n_firing = max((r["l2"].get("_n_firing", 0) for r in day_records),
                               default=0)
                nf = []
                for r in day_records:
                    rf = os.path.join(RAW, f"{day}_{r['ticker']}.json")
                    try:
                        rw = json.load(open(rf, encoding="utf-8"))
                        nf.append(rw["metrics"].get("n_firing", 0))
                    except Exception:
                        pass
                n_firing = max(nf, default=0)
                unit_pass = all(
                    v == "PRE_WINDOW" for v in fam_prov.values()) and len(fam_prov) == 2
                status = "PRE_WINDOW" if unit_pass else "ASSOCIATIONAL-ONLY"
                if unit_pass:
                    prewindow_pass += 1
                else:
                    associational += 1
                census.append({
                    "day": day, "expiry": exp, "dte": (dt.datetime.strptime(exp, "%Y%m%d")
                                                        - dt.datetime.strptime(day, "%Y%m%d")).days,
                    "habitat": habitat, "label": label, "families": sorted(fam_prov),
                    "family_provenance": fam_prov, "n_firing": n_firing,
                    "unit_status": status,
                    "delta_iv_pre_window": {r["ticker"]: r["l2"].get("delta_iv_pre_window")
                                            for r in day_records},
                })
            else:
                hard_gap += 1
                census.append({"day": day, "expiry": exp, "habitat": habitat,
                               "label": label, "families": [], "family_provenance": {},
                               "n_firing": 0, "unit_status": "HARD-GAP",
                               "delta_iv_pre_window": {}})
            with open(os.path.join(PW, "gaps.json"), "w", encoding="utf-8") as fh:
                json.dump(gaps_all, fh, indent=2)
    finally:
        ctl.close()

    census_doc = {
        "as_of": "2026-08-15",
        "run": "PRE_WINDOW sequential re-acquisition",
        "intended_causal_units": INTENDED_CAUSAL_DAYS,
        "prewindow_n": prewindow_pass,
        "prewindow_N": INTENDED_CAUSAL_DAYS,
        "prewindow_coverage": round(prewindow_pass / INTENDED_CAUSAL_DAYS, 4)
        if INTENDED_CAUSAL_DAYS else 0.0,
        "associational_exclusions": [c for c in census
                                     if c["unit_status"] == "ASSOCIATIONAL-ONLY"],
        "gaps": gaps_all,
        "hard_gap_units": hard_gap,
        "units": census,
    }
    with open(os.path.join(PW, "provenance_census.json"), "w", encoding="utf-8") as fh:
        json.dump(census_doc, fh, indent=2)

    print("\n================ PROVENANCE CENSUS ================")
    print(f"intended causal units : {INTENDED_CAUSAL_DAYS}")
    print(f"PRE_WINDOW n/N        : {prewindow_pass}/{INTENDED_CAUSAL_DAYS}")
    print(f"coverage              : {census_doc['prewindow_coverage']}")
    print(f"associational exclusions: {associational}")
    print(f"hard gaps             : {hard_gap}")
    for c in census:
        print(f"  {c['day']} [{c['habitat']}] dte={c.get('dte')} "
              f"firing={c['n_firing']} status={c['unit_status']} "
              f"dIV_pre={c.get('delta_iv_pre_window')}")
    print(f"time: {time.time()-t0:.0f}s")

    # FAIL LOUDLY if PRE_WINDOW coverage < 100% of intended causal units (R2 synergy).
    if prewindow_pass < INTENDED_CAUSAL_DAYS:
        print(f"\n[FAIL-LOUD] PRE_WINDOW coverage {prewindow_pass}/{INTENDED_CAUSAL_DAYS} "
              f"< 100% of intended causal units. Causal corpus reduced to {prewindow_pass} "
              f"unit(s); {associational} associational + {hard_gap} hard-gap excluded. "
              f"Causal β available only on the {prewindow_pass}-unit PRE_WINDOW corpus.")
    else:
        print("\n[OK] PRE_WINDOW coverage 100% of intended causal units.")
    return prewindow_pass


if __name__ == "__main__":
    raise SystemExit(main())
