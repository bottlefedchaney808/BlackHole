"""Convention-free "as-is" broker-book control from chain-scan CSVs.

AGGREGATION NOTE (2026-08-16): ongoing corpus for a forward-return accuracy test of the
dealer model. KEEP re-running as new chain scans land -- globs every *chain_scan*.csv
under orchestrator_output/ and Vol_Suite/outputs/ automatically. Power target + rules in
_broker_control/README.md.

Principle (Jason 2026-08-16): the sign is BUILT INTO each greek's raw value -- call delta
is +, put delta is -, vanna is moneyness-signed, etc. We do NOT assign dealer direction and
do NOT multiply by a -1 convention. Net = sum(raw_greek * OI), taking each value as it is.

  delta : sum(delta * OI)          -- right-signed by the data itself
  vanna : sum(vanna * OI)          -- moneyness-signed by the data itself
  charm : sum(charm * OI)
  theta : sum(theta * OI)          -- all-long (raw long-option theta is negative)
  vega  : sum(vega * OI)           -- all-long
  gamma : sum(gamma * OI)          -- ALWAYS positive (raw gamma has no sign on either right);
                                    shown as-is, and a signed variant (call - put) is added
                                    ONLY to make explicit that any signed gamma must come
                                    from an external convention, never from the data.
"""
import glob
import os
import re
import sys
import datetime as dt

import pandas as pd

ROOTS = [
    r"C:/Users/bottl/FinancialDevelopment/orchestrator_output",
    r"C:/Users/bottl/FinancialDevelopment/Vol_Suite/outputs",
]
GREEKS = ["delta", "vanna", "charm", "theta", "vega", "gamma"]
FILENAME_RE = re.compile(r"^(?P<ticker>[A-Z]+)_(?P<expiry>\d{8})_chain_scan_(?P<ts>\d{8}_\d{6})\.csv$")


def main():
    files = []
    for root in ROOTS:
        files.extend(glob.glob(os.path.join(root, "**", "*chain_scan*.csv"), recursive=True))

    rows = []
    for path in sorted(files):
        m = FILENAME_RE.match(os.path.basename(path))
        if not m:
            continue
        df = pd.read_csv(path)
        oi = pd.to_numeric(df.get("oi"), errors="coerce").fillna(0)
        rights = df.get("right")
        is_call = (rights is not None) & rights.map(lambda r: str(r).strip().upper() == "C")
        rec = {"ticker": m.group("ticker"), "expiry": m.group("expiry"),
               "scan_dt": dt.datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")}
        for g in GREEKS:
            col = df.get(g)
            if col is None:
                rec[f"net_{g}"] = float("nan")
                continue
            gv = pd.to_numeric(col, errors="coerce")
            mask = (oi > 0) & gv.notna()
            rec[f"net_{g}"] = float((gv[mask] * oi[mask]).sum())
            if g == "gamma":
                # GEX-style reference ONLY (Jason 2026-08-16): calls +, puts -.
                # gamma is the ONE greek with no sign in the data, so this sign is
                # imported for reference; net_delta / net_vanna / net_charm get NO
                # direction assignment (raw value taken as-is).
                cmask = mask & is_call
                pmask = mask & ~is_call
                rec["net_gamma_gex"] = float(
                    (gv[cmask] * oi[cmask]).sum() - (gv[pmask] * oi[pmask]).sum())
        rows.append(rec)

    out = pd.DataFrame(rows).sort_values(["ticker", "scan_dt"])
    out_path = r"C:/Users/bottl/FinancialDevelopment/_broker_control/broker_book_control.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_csv(out_path, index=False)

    print(f"wrote {len(out)} rows -> {out_path}\n")
    print(f"net_gamma (as-is, no sign) positive on: {(out.net_gamma>0).sum()}/{len(out)}  (always positive -- confirms raw gamma has no sign)")
    print(f"net_gamma_gex (calls+, puts-, REFERENCE only) short rows: {(out.net_gamma_gex<0).sum()}/{len(out)}  (sign imported, not in data)")
    print("net_delta / net_vanna / net_charm: NO direction assigned -- raw signed value taken as-is\n")

    show = out[["ticker", "expiry", "scan_dt", "net_delta", "net_vanna", "net_charm", "net_theta", "net_vega", "net_gamma", "net_gamma_gex"]].copy()
    for c in ["net_delta", "net_vanna", "net_charm", "net_theta", "net_vega", "net_gamma", "net_gamma_gex"]:
        show[c] = show[c].map(lambda v: f"{v:,.3g}" if pd.notna(v) else "NA")
    print(show.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
