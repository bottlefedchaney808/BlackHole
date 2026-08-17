"""Compare convention-free "control" nets against the model's oi_heuristic convention nets,
computed on the SAME chain-scan rows (network-free, exact-derivation).

Conventions:
  control (raw provider sign)     : Net_g = sum(raw_greek * OI)          -- right/moneyness signed, no dealer assumption
  model  (_dealer_sign, oi_heur)  : Net_g = sum(_dealer_sign(right)*greek*OI)  where _dealer_sign = +1(C)/-1(P)

For delta, raw call delta>0 and put delta<0, so control == model BY CONSTRUCTION.
For gamma, raw gamma is always positive, so control is always +; only model nets to a signed value.
For vanna, raw vanna is moneyness-signed, model right-signed -> the two genuinely differ.
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
GREEKS = ["delta", "vanna", "charm", "gamma"]
FILENAME_RE = re.compile(r"^(?P<ticker>[A-Z]+)_(?P<expiry>\d{8})_chain_scan_(?P<ts>\d{8}_\d{6})\.csv$")


def sign(right):
    return 1.0 if right == "C" else -1.0


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
        if rights is None:
            continue
        sgn = rights.map(lambda r: sign(str(r).strip().upper()))
        rec = {"ticker": m.group("ticker"), "expiry": m.group("expiry"),
               "scan_dt": dt.datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")}
        for g in GREEKS:
            col = df.get(g)
            if col is None:
                rec[f"{g}_control"] = rec[f"{g}_model"] = float("nan")
                continue
            gv = pd.to_numeric(col, errors="coerce")
            mask = (oi > 0) & gv.notna()
            rec[f"{g}_control"] = float((gv[mask] * oi[mask]).sum())
            rec[f"{g}_model"] = float((sgn[mask] * gv[mask] * oi[mask]).sum())
        rows.append(rec)

    out = pd.DataFrame(rows).sort_values(["ticker", "scan_dt"])
    out_path = r"C:/Users/bottl/FinancialDevelopment/_broker_control/control_vs_model.csv"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_csv(out_path, index=False)

    # Sign agreement summaries
    def agrees(a, b):
        return pd.notna(a) and pd.notna(b) and (a == 0 or b == 0 or (a > 0) == (b > 0))

    print(f"wrote {len(out)} rows -> {out_path}\n")
    print("=== per-row model gamma sign (short gamma when negative) ===")
    print("  short_gamma_rows:", int((out.gamma_model < 0).sum()), "/", len(out))
    print("  long_gamma_rows :", int((out.gamma_model > 0).sum()))
    print("  (control gamma is always positive by construction -> no sign to compare)\n")

    for g in ["delta", "vanna", "charm"]:
        agree = sum(agrees(a, b) for a, b in zip(out[f"{g}_control"], out[f"{g}_model"]))
        diff = sum(1 for a, b in zip(out[f"{g}_control"], out[f"{g}_model"])
                   if pd.notna(a) and pd.notna(b) and a != 0 and b != 0 and (a > 0) != (b > 0))
        print(f"=== {g}: control vs model sign agreement ===")
        print(f"  rows where BOTH signable: {sum(1 for a,b in zip(out[f'{g}_control'],out[f'{g}_model']) if pd.notna(a) and pd.notna(b) and a!=0 and b!=0)}")
        print(f"  sign AGREE: {agree}   sign DISAGREE: {diff}\n")

    # show a compact table
    show = out[["ticker", "expiry", "scan_dt",
                "gamma_control", "gamma_model",
                "vanna_control", "vanna_model",
                "delta_control"]].copy()
    for c in ["gamma_control", "gamma_model", "vanna_control", "vanna_model", "delta_control"]:
        show[c] = show[c].map(lambda v: f"{v:,.3g}" if pd.notna(v) else "NA")
    print(show.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
