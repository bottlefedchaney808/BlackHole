#!/usr/bin/env python3
"""CLI wrapper over svi_rp.calibrate_ssvi — plots the SVI-RP smile + marking.

Fetch is network (dense EOD, same as seed_flip_compare). Calibration + marking
live in the importable module Vol_Suite/svi_rp.py (reusable). Run from Vol_Suite/.
"""
import datetime
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.thetadata import ThetaDataController
import expiry_selector
import replication_reference as rr
from seed_flip_compare import _build_payload
import svi_rp


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    td = ThetaDataController()
    try:
        expiry, _ = expiry_selector.resolve_expiration(td, ticker, None, 0.25)
        greeks, oi, spot_rows = _build_payload(td, ticker, expiry)
        spot = {}
        for r in spot_rows:
            d = rr._parse_hist_date(r)
            if d:
                spot[d] = float(r.get("close", 0))
        last_date = sorted(spot)[-1]
        S = spot[last_date]
        exp_dt = datetime.datetime.strptime(expiry, "%Y%m%d").date()
        T = max((exp_dt - datetime.datetime.strptime(last_date, "%Y%m%d").date()).days, 1) / 365.0
        iv = {}
        for r in greeks:
            d = rr._parse_hist_date(r)
            if d == last_date:
                k = float(r["strike"]) / 1000.0 if float(r["strike"]) > 1000 else float(r["strike"])
                iv[(k, r["right"])] = float(r["implied_vol"])
        oi_by = {}
        for r in oi:
            d = rr._parse_hist_date(r)
            if d == last_date:
                k = float(r["strike"]) / 1000.0 if float(r["strike"]) > 1000 else float(r["strike"])
                oi_by[(k, r["right"])] = int(float(r.get("open_interest", 0)))
        w = rr._otm_leg_weights(iv, S, T)
        if not w:
            print(f"[svi-rp] no OTM weights for {ticker}")
            return 2
        # restrict to the OTM set
        chain_iv = {kv: iv[kv] for kv in w}
        ref = svi_rp.calibrate_ssvi(chain_iv, S, T, oi_by=oi_by, otm_weights=w)
    finally:
        td.close()

    print(f"=== {ticker} {last_date} spot={S:.1f} T={T:.3f} {ref.summary()}")
    marks = ref.mark_chain(chain_iv, oi_by)
    short_oi, long_oi, net = ref.seed(chain_iv, oi_by)
    print(f"{'strike':>6} {'rt':>3} {'mktIV':>6} {'svi_ref':>7} {'diff':>7} {'mark':>5} {'OI':>7}")
    for (k, r, sig, refv, diff, mark, oi) in marks:
        if abs(k - S) < 20 or k % 100 < 5:
            print(f"{k:6.0f} {r:>3} {sig:6.3f} {refv:7.3f} {diff:+7.3f} {mark:>5} {oi:7d}")
    n_short = sum(1 for m in marks if m[5] == "SHORT")
    n_long = sum(1 for m in marks if m[5] == "LONG")
    print(f"\nmarking: SHORT(rich)={n_short} strikes/{short_oi} OI, "
          f"LONG(cheap)={n_long} strikes/{long_oi} OI")
    print(f"net SVI-RP seed = {net:,.0f} (long {long_oi:,} - short {short_oi:,})")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.figure(figsize=(11, 6))
    Karr = np.array([m[0] for m in marks])
    Iarr = np.array([m[2] for m in marks])
    Rarr = np.array([m[3] for m in marks])
    cols = ["red" if m[5] == "SHORT" else "green" for m in marks]
    plt.scatter(Karr, Iarr, c=cols, s=25, label="market IV (red=rich/SHORT, green=cheap/LONG)")
    plt.plot(Karr, Rarr, "b-", linewidth=2, label=f"SVI-RP ref (sigma_swap={ref.sigma_swap:.3f})")
    plt.axhline(ref.sigma_swap, color="gray", linewidth=0.8, linestyle=":", label=f"sigma_swap={ref.sigma_swap:.3f}")
    plt.axvline(S, color="black", linewidth=1, alpha=0.5, label=f"spot={S:.0f}")
    plt.xlabel("strike"); plt.ylabel("implied vol")
    plt.title(f"{ticker} SVI-RP-consistent reference smile ({last_date})")
    plt.legend(); plt.grid(alpha=0.3)
    out = f"/tmp/{ticker}_svi_rp_smile.png"
    plt.savefig(out, dpi=110, bbox_inches="tight")
    print(f"\nchart: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
