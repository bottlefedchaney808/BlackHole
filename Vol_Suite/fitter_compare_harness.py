#!/usr/bin/env python3
"""fitter_compare_harness.py — compare the falsifier's accumulated arm under
the three reference fitters on a given ticker. Jason's directive 2026-08-13:
"fixed sabr, newsbr, SVI in a compare harness."

Runs the SAME single-ticker OOS falsifier (backtest_accumulation_falsifier)
under VOL_SURFACE_FITTER in {svi, sabr, sabr_market} and prints the snapshot
corr, accumulated corr, delta R2, acc t-stat, and verdict side by side. This
isolates the sign-source effect (SVI vs old-pinned-SABR vs fixed-market-SABR)
on the vannaflow accumulation arm.

Usage (from Vol_Suite/):  python fitter_compare_harness.py SPY
"""
import os
import sys

sys.path.insert(0, os.path.abspath('.'))
import backtest_accumulation_falsifier as baf

FITTERS = ("svi", "sabr", "sabr_market")


def main() -> int:
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "SPY"
    # FALSIFIER_FORCE=1 forces recompute of ALL legs (~15-20 min each); leave
    # unset to load cached per-fitter results (cache key includes fitter) and
    # only compute what's missing. Pass --force as arg 2 to force recompute.
    force = (len(sys.argv) > 2 and sys.argv[2] == "--force")
    results = {}
    # Write each leg's result to a JSON file as it completes, so an app crash
    # mid-run doesn't lose finished legs (this app has crashed twice today).
    import json as _json
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "outputs", f"fitter_compare_{ticker}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    for fitter in FITTERS:
        os.environ["VOL_SURFACE_FITTER"] = fitter
        if force:
            os.environ["FALSIFIER_FORCE"] = "1"
        print(f"\n=== running falsifier under VOL_SURFACE_FITTER={fitter} "
              f"({'force' if force else 'cache-or-compute'}) ===", flush=True)
        try:
            res = baf.run_falsifier_cached(
                ticker, use_cached=True, seed_mode='replication')
            results[fitter] = {
                "snapshot_corr": res.snapshot_corr,
                "accumulated_corr": res.accumulated_corr,
                "delta_r2": res.delta_r2,
                "accumulated_coef_tstat": res.accumulated_coef_tstat,
                "n_days": res.n_days, "verdict": res.verdict,
                "lead_lag": res.lead_lag_corr, "best_lag": res.best_lag,
            }
            with open(out_path, "w", encoding="utf-8") as fh:
                _json.dump({"ticker": ticker, "results": results}, fh, indent=2)
            print(f"  {fitter}: snap={res.snapshot_corr:+.4f} "
                  f"acc={res.accumulated_corr:+.4f} dR2={res.delta_r2:+.4f} "
                  f"t={res.accumulated_coef_tstat:+.3f} n={res.n_days} "
                  f"verdict={res.verdict} [saved]", flush=True)
        except Exception as e:
            print(f"  {fitter}: ERROR {type(e).__name__}: {str(e)[:120]}", flush=True)
        os.environ.pop("FALSIFIER_FORCE", None)

    print("\n=== SIDE-BY-SIDE (accumulated arm, same data) ===")
    print(f"{'fitter':<13} {'snap_corr':>9} {'acc_corr':>9} {'dR2':>8} {'acc_t':>7} {'n':>5} {'verdict':>14}")
    for fitter in FITTERS:
        res = results.get(fitter)
        if res is None:
            print(f"{fitter:<13}   (failed)")
            continue
        print(f"{fitter:<13} {res.snapshot_corr:>9.4f} {res.accumulated_corr:>9.4f} "
              f"{res.delta_r2:>8.4f} {res.accumulated_coef_tstat:>7.2f} "
              f"{res.n_days:>5} {res.verdict:>14}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
