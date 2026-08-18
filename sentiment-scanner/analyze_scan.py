#!/usr/bin/env python3
"""analyze_scan.py — re-print a directional_scan_*.json result file as a
sorted summary table (severity, signal count, CNS, scanner health).

`main.py --universe TICKER,TICKER,...` already prints a one-line-per-ticker
summary as it runs and a "N strong: ..." line at the end; this is for
re-viewing a *past* run's full sorted table without re-scanning, e.g. after
noticing something interesting in a scheduled run's saved JSON.

Usage:
    python analyze_scan.py                # most recent outputs/directional_scan_*.json
    python analyze_scan.py PATH/TO/FILE.json
"""
import glob
import json
import os
import sys

_OUTPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

HIGHISH = ("DIRECTIONAL_BET_FORMING", "VOL_EVENT_DETECTED", "GAMMA_SQUEEZE_RISK",
           "OI_SURGE_WITH_NARRATIVE", "RICH_VOL_PLUS_NARRATIVE", "EXTREME_SKEW_PLUS_NARRATIVE",
           "PIN_ACTION_WITH_NARRATIVE", "FAR_FROM_PAIN_PLUS_NARRATIVE",
           "DISPERSION_SETUP_PLUS_NARRATIVE", "EARNINGS_VOL_PLUS_NARRATIVE")

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _latest_scan_path():
    matches = sorted(glob.glob(os.path.join(_OUTPUTS_DIR, "directional_scan_*.json")))
    if not matches:
        raise SystemExit(
            f"No directional_scan_*.json files in {_OUTPUTS_DIR} -- "
            "run `python main.py --universe TICKER,TICKER,...` first.")
    return matches[-1]


def summarize(results: dict) -> list[dict]:
    """Turn a directional-scan results dict into sorted summary rows."""
    rows = []
    for ticker, r in results.items():
        sigs = r.get("signals", [])
        sev = r.get("severity", "LOW")
        narr = r.get("narrative") or {}
        scanners = r.get("scanners", {})
        ok_scanners = [k for k, v in scanners.items() if v.get("status") == "ok"]
        err_scanners = [k for k, v in scanners.items() if v.get("status") == "error"]
        highish = [s for s in sigs if s in HIGHISH]
        rows.append({
            "ticker": ticker, "severity": sev, "n_signals": len(sigs),
            "n_highish": len(highish), "cns": narr.get("cns") or 0,
            "war": narr.get("war") or 0, "n_ok": len(ok_scanners),
            "n_err": len(err_scanners), "oi_ok": "error" not in r.get("oi", {}),
            "signals": sigs, "scanners_ok": sorted(ok_scanners),
        })
    rows.sort(key=lambda r: (_SEVERITY_ORDER.get(r["severity"], 9),
                              -r["n_highish"], -r["n_signals"], -r["n_ok"]))
    return rows


def print_table(rows: list[dict]) -> None:
    print(f"{'TICK':6s} {'SEV':7s} {'SIG':3s} {'HI':3s} {'CNS':4s} {'WAR':5s} "
          f"{'OK':3s} {'ERR':3s} {'OI':3s}  SIGNALS")
    for r in rows:
        print(f"{r['ticker']:6s} {r['severity']:7s} {r['n_signals']:3d} {r['n_highish']:3d} "
              f"{r['cns']:4d} {r['war']:5.2f} {r['n_ok']:3d} {r['n_err']:3d} "
              f"{'Y' if r['oi_ok'] else 'N':3s}  {', '.join(r['signals'])[:110]}")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else _latest_scan_path()
    print("FILE:", path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    print_table(summarize(data["results"]))


if __name__ == "__main__":
    main()
