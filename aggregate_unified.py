"""aggregate_unified.py — pull key metrics from the latest unified runs for
each ticker and write a digest JSON for the Quant handoff."""
import json, glob, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "orchestrator_output")

TICKERS = sys.argv[1:] or ["NVDA", "GME", "KOS", "NBIS", "MSTX", "SMTC"]

def latest_run_dir(ticker):
    dirs = glob.glob(os.path.join(OUT, "*"))
    hits = []
    for d in dirs:
        ctx = os.path.join(d, "suite_context.json")
        if not os.path.exists(ctx):
            continue
        try:
            c = json.load(open(ctx, encoding="utf-8"))
        except Exception:
            continue
        if str((c.get("focus") or {}).get("ticker", "")).upper() == ticker:
            hits.append((os.path.getmtime(ctx), d))
    hits.sort(reverse=True)
    return hits[0][1] if hits else None

digest = {"timestamp": None, "runs": {}}
for t in TICKERS:
    d = latest_run_dir(t)
    if not d:
        digest["runs"][t] = {"run_dir": None, "error": "no run dir found"}
        continue
    entry = {"run_dir": d}
    # suite_context
    try:
        ctx = json.load(open(os.path.join(d, "suite_context.json"), encoding="utf-8"))
        foc = ctx.get("focus") or {}
        entry["spot"] = foc.get("spot") or ctx.get("spot")
        entry["expiry"] = foc.get("expiration_date") or ctx.get("expiry")
        entry["status"] = ctx.get("status")
        entry["strike"] = foc.get("strike")
        entry["option_type"] = foc.get("option_type")
    except Exception as e:
        entry["ctx_error"] = str(e)
    # vol_result
    vp = os.path.join(d, "vol_result.json")
    if os.path.exists(vp):
        try:
            v = json.load(open(vp, encoding="utf-8"))
            vs = v.get("vol_surface", {})
            focus = vs.get("focus", {})
            entry["vol"] = {
                "status": v.get("status"),
                "S0": focus.get("S0"),
                "atm_iv_pct": focus.get("atm_iv_pct"),
                "fair_vol_pct": focus.get("fair_vol_pct"),
                "vrp_pct": focus.get("vrp_pct"),
                "errors": [e.get("step") for e in v.get("errors", [])],
            }
        except Exception as e:
            entry["vol_error"] = str(e)
    # options_result
    op = os.path.join(d, "options_result.json")
    if os.path.exists(op):
        try:
            o = json.load(open(op, encoding="utf-8"))
            entry["options"] = {
                "status": o.get("status"),
                "model_price": o.get("model_price") or o.get("price"),
                "sigma": o.get("sigma"),
                "delta": o.get("delta"),
                "gamma": o.get("gamma"),
                "vega": o.get("vega"),
                "theta": o.get("theta"),
            }
        except Exception as e:
            entry["options_error"] = str(e)
    # var_result
    vr = os.path.join(d, "var_result.json")
    if os.path.exists(vr):
        try:
            x = json.load(open(vr, encoding="utf-8"))
            entry["var"] = {
                "status": x.get("status"),
                "var": x.get("var") or x.get("VaR"),
                "cvar": x.get("cvar") or x.get("CVaR") or x.get("es"),
                "confidence": x.get("confidence"),
                "horizon": x.get("horizon"),
            }
        except Exception as e:
            entry["var_error"] = str(e)
    # unified pdf?
    pdfs = glob.glob(os.path.join(d, "unified_report_*.pdf"))
    entry["unified_pdf"] = os.path.basename(pdfs[0]) if pdfs else None
    digest["runs"][t] = entry

out_path = os.path.join(OUT, "quant_handoff_digest.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(digest, f, indent=2, default=str)
print("WROTE", out_path)
print(json.dumps(digest, indent=1, default=str)[:4000])
