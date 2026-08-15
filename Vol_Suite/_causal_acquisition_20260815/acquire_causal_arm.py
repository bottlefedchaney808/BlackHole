#!/usr/bin/env python3
"""R10.3 causal-arm ≥29-day SEQUENTIAL acquisition (Dealer-Exposure-Dev).

Acquires GENUINELY NEW unique calendar days (Jan–Mar 2026 + early Apr 2026,
all before the held-date corpus which starts 2026-04-13), one day/ticker at a
time, THETADATA_HIST_CONCURRENCY=1. Builds the run_causal_arm_v2.py day-record
schema per day (pre_event_vanna_exposure level over pre-cutoff rows, l2
covariates incl. surprise/event_habitat/family interactions, both clock
outcomes) and persists raw + derived data to
Vol_Suite/_causal_acquisition_20260815/.

Model stays DESCRIPTIVE/CONDITIONAL. Positive beta = re-admission evidence only,
never auto-promotion. Honest beta-power (underpowered at 29 days), never 80%.

Resumable: a (day, ticker) already written to disk is skipped on re-run.
"""
import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import expiry_book_exposure as ebe            # noqa: E402
import run_causal_arm_v2 as ca                # noqa: E402  (compute_pre_event_vanna_exposure)
import run_dual_pipeline_gate as _g           # noqa: E402  (_stock_intraday, _rows_at, _corr, _eod_greeks_day)
import run_dual_pipeline_gate_v2 as _v2       # noqa: E402  (_robust_chain_intraday, _robust_oi_proxy, spy_band_rule)
from shared.thetadata import ThetaDataController, strike_to_theta, strike_from_theta  # noqa: E402

# Load main-tree .env creds (NEVER persisted/committed)
_env = r"C:/Users/bottl/FinancialDevelopment/.env"
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

os.environ["THETADATA_HIST_CONCURRENCY"] = "1"

OUT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(OUT, "raw")
RECORDS = os.path.join(OUT, "records")
os.makedirs(RAW, exist_ok=True)
os.makedirs(RECORDS, exist_ok=True)

DEADBAND = 0.01
IVL = 600000

# ---------------------------------------------------------------------------
# CANDIDATES: (day, expiry, event_habitat, label)  — all GENUINELY NEW unique
# calendar days before the held corpus (held starts 2026-04-13). Balanced event
# (FOMC/OpEx/earnings) + control days, mixed DTE 1-10, next-Friday expiries.
# Pre-registered set.
# ---------------------------------------------------------------------------
def _friday_expiry(day: str) -> str:
    """Next Friday expiry (Mon=4, Tue=3, Wed=2, Thu=1, Fri=7 -> following Fri)."""
    d = dt.datetime.strptime(day, "%Y%m%d").date()
    for _ in range(14):
        d += dt.timedelta(days=1)
        if d.weekday() == 4:  # Friday
            return d.strftime("%Y%m%d")
    return ""


CANDIDATES = [
    # (day, expiry, habitat, label)
    ("20260105", "20260109", "NONE", "control Mon"),
    ("20260106", "20260109", "NONE", "control Tue"),
    ("20260107", "20260109", "NONE", "control Wed"),
    ("20260108", "20260109", "NONE", "control Thu"),
    ("20260109", "20260116", "NONE", "pre-OpEx Fri"),
    ("20260112", "20260116", "NONE", "control Mon"),
    ("20260113", "20260116", "NONE", "control Tue"),
    ("20260114", "20260116", "NONE", "control Wed"),
    ("20260115", "20260116", "NONE", "control Thu"),
    ("20260116", "20260123", "OPEX", "January monthly OpEx Fri"),
    ("20260120", "20260123", "NONE", "control Tue"),
    ("20260121", "20260123", "EARNINGS", "Q4 earnings Wed"),
    ("20260122", "20260123", "EARNINGS", "Q4 earnings Thu"),
    ("20260123", "20260130", "EARNINGS", "Q4 earnings Fri"),
    ("20260126", "20260130", "NONE", "control Mon"),
    ("20260127", "20260130", "FOMC", "FOMC day-1 Tue"),
    ("20260128", "20260130", "FOMC", "FOMC decision Wed"),
    ("20260129", "20260130", "EARNINGS", "Q4 earnings Thu"),
    ("20260130", "20260206", "NONE", "control Fri"),
    ("20260202", "20260206", "NONE", "control Mon"),
    ("20260203", "20260206", "NONE", "control Tue"),
    ("20260204", "20260206", "NONE", "control Wed"),
    ("20260205", "20260206", "NONE", "control Thu"),
    ("20260206", "20260213", "NONE", "control Fri"),
    ("20260209", "20260213", "NONE", "control Mon"),
    ("20260210", "20260213", "NONE", "control Tue"),
    ("20260211", "20260213", "NONE", "control Wed"),
    ("20260212", "20260213", "NONE", "control Thu"),
    ("20260213", "20260220", "NONE", "control Fri"),
    ("20260217", "20260220", "NONE", "control Tue"),
    ("20260218", "20260220", "NONE", "control Wed"),
    ("20260219", "20260220", "NONE", "control Thu"),
    ("20260220", "20260227", "OPEX", "February monthly OpEx Fri"),
    ("20260223", "20260227", "NONE", "control Mon"),
    ("20260224", "20260227", "NONE", "control Tue"),
    ("20260225", "20260227", "NONE", "control Wed"),
    ("20260226", "20260227", "NONE", "control Thu"),
    ("20260227", "20260306", "NONE", "control Fri"),
    ("20260302", "20260306", "NONE", "control Mon"),
    ("20260303", "20260306", "NONE", "control Tue"),
    ("20260304", "20260306", "NONE", "control Wed"),
    ("20260305", "20260306", "NONE", "control Thu"),
    ("20260306", "20260313", "NONE", "control Fri"),
    ("20260309", "20260313", "NONE", "control Mon"),
    ("20260310", "20260313", "NONE", "control Tue"),
    ("20260311", "20260313", "NONE", "control Wed"),
    ("20260312", "20260313", "NONE", "control Thu"),
    ("20260313", "20260320", "NONE", "control Fri"),
    ("20260316", "20260320", "NONE", "control Mon"),
    ("20260317", "20260320", "FOMC", "FOMC day-1 Tue"),
    ("20260318", "20260320", "FOMC", "FOMC decision Wed"),
    ("20260319", "20260320", "NONE", "control Thu"),
    ("20260320", "20260327", "OPEX", "March monthly OpEx Fri"),
    ("20260323", "20260327", "NONE", "control Mon"),
    ("20260324", "20260327", "NONE", "control Tue"),
    ("20260325", "20260327", "NONE", "control Wed"),
    ("20260326", "20260327", "NONE", "control Thu"),
    ("20260406", "20260410", "NONE", "control Mon (post-GoodFri)"),
    ("20260407", "20260410", "NONE", "control Tue"),
    ("20260408", "20260410", "NONE", "control Wed"),
    ("20260409", "20260410", "NONE", "control Thu"),
    ("20260410", "20260417", "NONE", "pre-OpEx Fri"),
]

TICKERS = ["SPY", "QQQ"]

# Order: event days first (higher-value firing candidates), then controls, so a
# partial run still covers the event/control contrast. Event score: FOMC=3,
# EARNINGS=2, OPEX=1, NONE=0.
_EVENT_PRIO = {"FOMC": 3, "EARNINGS": 2, "OPEX": 1, "NONE": 0}
CANDIDATES.sort(key=lambda c: -_EVENT_PRIO[c[2]])


def _prev_close(ctl, ticker, day):
    """Close of the last trading day strictly before `day` (for the daily
    close-to-close clock). Returns float or None."""
    d = dt.datetime.strptime(day, "%Y%m%d").date()
    start = (d - dt.timedelta(days=12)).strftime("%Y%m%d")
    try:
        rows = ctl.hist_stock_eod(ticker, start, day)
    except Exception:
        return None
    closes = {}
    for r in rows:
        dg = "".join(ch for ch in str(r.get("date", "") or "") if ch.isdigit())[:8]
        c = r.get("close")
        if len(dg) == 8 and c not in (None, "", "0", 0):
            try:
                closes[dg] = float(c)
            except (TypeError, ValueError):
                continue
    prior = [dg for dg in sorted(closes) if dg < day]
    if not prior:
        return None
    return closes[prior[-1]]


def _acq_day(ctl, ticker, day, exp):
    """Fetch + compute the full payload/metrics for one (ticker, day). Returns
    a metrics dict (with gaps recorded), or None on a hard gap that blocks."""
    gaps = []
    # 1. stock intraday
    try:
        spot_min = _g._stock_intraday(ctl, ticker, day)
    except Exception as e:
        gaps.append({"ticker": ticker, "day": day, "endpoint": "stock/ohlc",
                     "status": "exc", "note": str(e)[:120]})
        return {"gaps": gaps, "ok": False}
    if not spot_min:
        gaps.append({"ticker": ticker, "day": day, "endpoint": "stock/ohlc",
                     "status": "empty", "note": "no intraday stock rows"})
        return {"gaps": gaps, "ok": False}

    ms_all = sorted(spot_min)
    open_spot = spot_min[ms_all[0]]
    close_spot = spot_min[ms_all[-1]]
    prev_close = _prev_close(ctl, ticker, day)

    # 2. chain grid
    base = round(open_spot / 5.0) * 5.0
    grid = [strike_to_theta(base + i * 5.0) for i in range(-12, 13)]
    chain_c, fail_c = _v2._robust_chain_intraday(ctl, ticker, exp, day, grid, "C")
    chain_p, fail_p = _v2._robust_chain_intraday(ctl, ticker, exp, day, grid, "P")
    for fk in fail_c + fail_p:
        gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                     "status": "5xx", "note": f"theta-strike {fk} transient proxy error"})
    if len(chain_c) < 10 or len(chain_p) < 10:
        gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                     "status": "thin", "note": f"C={len(chain_c)} P={len(chain_p)} strikes served"})
        return {"gaps": gaps, "ok": False, "spot_min": spot_min}

    # 3. OI
    oi_c, of_c = _v2._robust_oi_proxy(ctl, ticker, exp, day, list(chain_c.keys()), "C")
    oi_p, of_p = _v2._robust_oi_proxy(ctl, ticker, exp, day, list(chain_p.keys()), "P")
    for fk in of_c + of_p:
        gaps.append({"ticker": ticker, "day": day, "endpoint": "open_interest",
                     "status": "5xx", "note": f"theta-strike {fk} transient proxy error"})

    # 4. EOD greeks (for provenance/context; not used in the pre-event level)
    eod_g = _g._eod_greeks_day(ctl, ticker, exp, day)

    T = max((dt.datetime.strptime(exp, "%Y%m%d").date()
             - dt.datetime.strptime(day, "%Y%m%d").date()).days / 365.0, 0.01)
    dte = max((dt.datetime.strptime(exp, "%Y%m%d").date()
               - dt.datetime.strptime(day, "%Y%m%d").date()).days, 1)

    # 5. day-anchored execution locus
    rows_open = _g._rows_at(chain_c, chain_p, oi_c, oi_p, ms_all[0])
    tol = _v2.spy_band_rule(ticker)
    try:
        locus = ebe.execution_locus(rows_open, open_spot, T=T, tolerance_pct=tol)
    except Exception as e:
        gaps.append({"ticker": ticker, "day": day, "endpoint": "execution_locus",
                     "status": "exc", "note": str(e)[:120]})
        return {"gaps": gaps, "ok": False, "spot_min": spot_min}

    # 6. bucket loop: deltaIV, burst, forward return, firing buckets
    all_ms = set()
    for k, krows in chain_c.items():
        for x in krows:
            all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
    for k, krows in chain_p.items():
        for x in krows:
            all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
    bucket_ms = sorted(all_ms)

    firing = []            # list of {ms, div, burst, fwd, atm_iv}
    bucket_divs, bucket_fwds = [], []
    prev_atm_iv = None
    net_div = 0.0
    max_abs_burst = 0.0
    for ms in bucket_ms:
        spot_now = None
        for sms in ms_all:
            if sms <= ms:
                spot_now = spot_min[sms]
            else:
                break
        if not spot_now:
            continue
        rows_at = _g._rows_at(chain_c, chain_p, oi_c, oi_p, ms)
        if len(rows_at) < 20:
            continue
        ivs = [r_["implied_vol"] for r_ in rows_at if r_["implied_vol"] not in (None, 0)]
        if not ivs:
            continue
        atm_iv = min(((r_["strike"], r_["implied_vol"]) for r_ in rows_at
                      if r_["implied_vol"] not in (None, 0)),
                     key=lambda p: abs(p[0] - spot_now))[1]
        div = 0.0 if prev_atm_iv is None else atm_iv - prev_atm_iv
        prev_atm_iv = atm_iv
        net_div += div
        burst = ebe.hedge_flow_at(locus, spot_now)
        max_abs_burst = max(max_abs_burst, abs(burst))
        # forward 10-min return
        fwd = 0.0
        for sms in ms_all:
            if sms > ms:
                fwd = (spot_min[sms] - spot_now) / spot_now if spot_now else 0.0
                break
        bucket_divs.append(div)
        bucket_fwds.append(fwd)
        if abs(div) > DEADBAND and burst != 0.0:
            firing.append({"ms": ms, "div": div, "burst": burst, "fwd": fwd, "atm_iv": atm_iv})

    # 7. breach + cutoff windows
    if firing:
        first_firing = firing[0]
        breach_ts = first_firing["ms"]
        # cutoff = largest bucket ms strictly before breach, else opening ms
        prior_buckets = [ms for ms in bucket_ms if ms < breach_ts]
        cutoff_ts = prior_buckets[-1] if prior_buckets else ms_all[0]
        # pre-event rows at the cutoff bucket
        pre_rows = _g._rows_at(chain_c, chain_p, oi_c, oi_p, cutoff_ts)
        for r_ in pre_rows:
            r_["ms_of_day"] = cutoff_ts
        pre_expo = ca.compute_pre_event_vanna_exposure(
            pre_rows, spot_min[max([sms for sms in ms_all if sms <= cutoff_ts] or [ms_all[0]])],
            T, cutoff_ts, breach_ts, ticker=ticker)
        forward_return_h = first_firing["fwd"]
        breach_eligible = True
        breach_ret = first_firing["fwd"]
    else:
        # no-firing control day: cutoff at opening bucket, breach sentinel EOD
        breach_ts = bucket_ms[-1] + 600000 if bucket_ms else ms_all[-1] + 600000
        cutoff_ts = ms_all[0]
        pre_rows = _g._rows_at(chain_c, chain_p, oi_c, oi_p, cutoff_ts)
        for r_ in pre_rows:
            r_["ms_of_day"] = cutoff_ts
        pre_expo = ca.compute_pre_event_vanna_exposure(
            pre_rows, open_spot, T, cutoff_ts, breach_ts, ticker=ticker)
        forward_return_h = bucket_fwds[0] if bucket_fwds else 0.0
        breach_eligible = False
        breach_ret = None

    # 7b. PRE_WINDOW ΔIV provenance (Cem's causal gate): persist a timestamped
    #     IV snapshot strictly before the breach/response window, and the ΔIV
    #     measured from it. `net_div` (day-level sum over all buckets) is kept
    #     as DESCRIPTIVE ONLY — never relabeled as pre-window.
    #     iv_source_ts = the cutoff bucket timestamp (strictly < breach_ts);
    #     iv_before_ts  = the prior bucket (or opening) IV anchor;
    #     delta_iv_pre_window = ATM-IV(at iv_source_ts) - ATM-IV(iv_before_ts),
    #     both strictly before breach_ts.
    #     If iv_source_ts >= breach_ts or no IV can be anchored pre-window, the
    #     record is marked ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS and excluded from the
    #     causal β arm (the driver enforces this via delta_iv_provenance).
    if breach_eligible:
        pre_window_ok = cutoff_ts < breach_ts
        iv_source_ts = cutoff_ts
        # find the ATM IV at the cutoff bucket (strictly pre-window)
        _iv_at_cutoff = None
        _pre_rows = _g._rows_at(chain_c, chain_p, oi_c, oi_p, cutoff_ts)
        _spot_cutoff = None
        for sms in ms_all:
            if sms <= cutoff_ts:
                _spot_cutoff = spot_min[sms]
            else:
                break
        if _spot_cutoff is not None and len(_pre_rows) >= 20:
            _ivs = [r_["implied_vol"] for r_ in _pre_rows if r_["implied_vol"] not in (None, 0)]
            if _ivs:
                _iv_at_cutoff = min(((r_["strike"], r_["implied_vol"]) for r_ in _pre_rows
                                     if r_["implied_vol"] not in (None, 0)),
                                    key=lambda p: abs(p[0] - _spot_cutoff))[1]
        # IV anchor strictly before cutoff (the previous bucket, or opening)
        _iv_anchor = None
        _iv_anchor_ts = None
        _prior_buckets = [ms for ms in bucket_ms if ms < cutoff_ts]
        _anchor_ts = _prior_buckets[-1] if _prior_buckets else None
        if _anchor_ts is not None:
            _anchor_rows = _g._rows_at(chain_c, chain_p, oi_c, oi_p, _anchor_ts)
            _spot_anchor = None
            for sms in ms_all:
                if sms <= _anchor_ts:
                    _spot_anchor = spot_min[sms]
                else:
                    break
            if _spot_anchor is not None and len(_anchor_rows) >= 20:
                _ivs_a = [r_["implied_vol"] for r_ in _anchor_rows if r_["implied_vol"] not in (None, 0)]
                if _ivs_a:
                    _iv_anchor = min(((r_["strike"], r_["implied_vol"]) for r_ in _anchor_rows
                                      if r_["implied_vol"] not in (None, 0)),
                                     key=lambda p: abs(p[0] - _spot_anchor))[1]
                    _iv_anchor_ts = _anchor_ts
        if pre_window_ok and _iv_at_cutoff is not None and _iv_anchor is not None:
            delta_iv_pre_window = _iv_at_cutoff - _iv_anchor
            iv_provenance = "PRE_WINDOW"
        else:
            delta_iv_pre_window = None
            iv_provenance = "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"
    else:
        # no-firing control day: no breach to anchor a pre-window IV against; the
        # ΔIV is associational-only (there is no response window to be causal for)
        pre_window_ok = False
        delta_iv_pre_window = None
        iv_provenance = "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"
        iv_source_ts = None

    # 8. A6 reflexivity corr(div, fwd) on the day's buckets
    a6 = _g._corr(bucket_divs, bucket_fwds) if len(bucket_divs) >= 4 else float("nan")

    # 9. daily clock: close-to-close (prev close -> close), fallback open->close
    if prev_close and prev_close > 0:
        daily_ret = (close_spot - prev_close) / prev_close
        daily_ret_src = "close-to-close"
    else:
        daily_ret = (close_spot - open_spot) / open_spot
        daily_ret_src = "open-to-close(fallback)"

    delta_s = (close_spot - open_spot) / open_spot

    return {
        "ok": True,
        "ticker": ticker, "day": day, "expiry": exp, "dte": dte, "T": round(T, 5),
        "open_spot": round(open_spot, 3), "close_spot": round(close_spot, 3),
        "prev_close": prev_close,
        "spot_min": spot_min,
        "bucket_ms": bucket_ms,
        "n_buckets": len(bucket_ms),
        "firing": firing,
        "n_firing": len(firing),
        "net_div": round(net_div, 6),
        "max_abs_burst": round(max_abs_burst, 6),
        "a6": (None if math.isnan(a6) else round(a6, 6)),
        "pre_event_vanna_exposure": pre_expo.get("pre_event_vanna_exposure"),
        "pre_vanna_timestamp": pre_expo.get("pre_vanna_timestamp"),
        "pre_window_end": pre_expo.get("pre_window_end"),
        "breach_window_start": pre_expo.get("breach_window_start"),
        "cutoff_pass": pre_expo.get("cutoff_pass"),
        "pre_rows_used": pre_expo.get("n_rows_used"),
        "excluded_post_cutoff": pre_expo.get("excluded_post_cutoff"),
        "forward_return_h": round(forward_return_h, 6),
        "breach_eligible": breach_eligible,
        "breach_return": (round(breach_ret, 6) if breach_ret is not None else None),
        "daily_return": round(daily_ret, 6),
        "daily_return_src": daily_ret_src,
        "delta_s": round(delta_s, 6),
        "gaps": gaps,
        # --- PRE_WINDOW ΔIV provenance (Cem's causal gate) ---
        "delta_iv_provenance": iv_provenance,
        "delta_iv_pre_window": (round(delta_iv_pre_window, 6)
                                if delta_iv_pre_window is not None else None),
        "iv_source_ts": iv_source_ts,
        "iv_cutoff_ts": cutoff_ts,
        "breach_window_start_prov": breach_ts,
    }


def build_day_record(metrics):
    """Build the run_causal_arm_v2.py day-record schema for one (day, ticker)."""
    fam = metrics["ticker"].upper()
    fam_key = f"family_interaction_{fam.lower()}"
    habitat = {d: h for d, _, h, _ in CANDIDATES}.get(metrics["day"], "NONE")
    pre_v = metrics["pre_event_vanna_exposure"]
    if pre_v is None:
        pre_v = float("nan")
    pre_v = float(pre_v)
    l2 = {
        "pre_vanna_exposure": pre_v,
        "delta_iv": metrics["net_div"],
        # --- PRE_WINDOW ΔIV provenance (Cem's causal gate) ---
        # delta_iv_provenance drives the driver's associational-vs-causal label.
        # delta_iv_pre_window is the timestamped ΔIV strictly before the breach
        # window (None -> associational-only). net_div stays descriptive-only.
        "delta_iv_provenance": metrics.get("delta_iv_provenance",
                                           "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"),
        "delta_iv_pre_window": metrics.get("delta_iv_pre_window"),
        "iv_source_ts": metrics.get("iv_source_ts"),
        "iv_cutoff_ts": metrics.get("iv_cutoff_ts"),
        "breach_window_start_prov": metrics.get("breach_window_start_prov"),
        "gamma_burst": metrics["max_abs_burst"],
        "delta_s": metrics["delta_s"],
        "market": metrics["delta_s"],  # filled below at merge if SPY available
        "event": 1 if habitat != "NONE" else 0,
        "a6_reflexivity": metrics["a6"] if metrics["a6"] is not None else 0.0,
        "cross_family_spillover": 0.0,  # filled at merge
        "surprise": None,               # no operational forecast -> DESCRIPTIVE-HABITAT
        "event_habitat": habitat,
        fam_key: pre_v,
        "forward_return_h": metrics["forward_return_h"],
    }
    return {
        "day": metrics["day"], "date": metrics["day"], "ticker": fam,
        "families": [fam], "dte": metrics["dte"], "expiry": metrics["expiry"],
        "event_habitat": habitat,
        "pre_window": {
            "pre_vanna_timestamp": metrics["pre_vanna_timestamp"],
            "pre_window_end": metrics["pre_window_end"],
            "breach_window_start": metrics["breach_window_start"],
            "cutoff_pass": bool(metrics["cutoff_pass"]),
            "n_rows_used": metrics["pre_rows_used"],
            "excluded_post_cutoff": metrics["excluded_post_cutoff"],
        },
        "l2": l2,
        "clock": {
            "daily": {"return": metrics["daily_return"], "eligible": True},
            "from_breach": {"return": metrics["breach_return"],
                            "eligible": metrics["breach_eligible"]},
        },
        "source": f"acq-{metrics['day']}-{fam.lower()}",
    }


def main():
    ctl = ThetaDataController()
    t0 = time.time()
    gaps_all = []
    done = 0
    try:
        for day, exp, habitat, label in CANDIDATES:
            day_records = []
            day_gaps = []
            for ticker in TICKERS:
                key = f"{day}_{ticker}"
                out_raw = os.path.join(RAW, f"{key}.json")
                # resume: skip if already fetched
                if os.path.exists(out_raw):
                    try:
                        saved = json.load(open(out_raw, encoding="utf-8"))
                        day_records.append(saved["record"])
                        day_gaps.extend(saved.get("gaps", []))
                        print(f"[resume] {key} (previously acquired)", flush=True)
                        continue
                    except Exception:
                        pass
                print(f"\n=== SESSION {key} ({ticker} {day}->{exp} {label}) ===", flush=True)
                m = _acq_day(ctl, ticker, day, exp)
                if not m.get("ok"):
                    day_gaps.extend(m.get("gaps", []))
                    print(f"  [GAP] {key}: {m['gaps']}", flush=True)
                    # still save the gap marker
                    with open(out_raw, "w", encoding="utf-8") as fh:
                        json.dump({"ok": False, "day": day, "ticker": ticker,
                                   "gaps": m.get("gaps", [])}, fh)
                    continue
                record = build_day_record(m)
                day_records.append(record)
                day_gaps.extend(m.get("gaps", []))
                # persist raw payload
                payload = {"day": day, "ticker": ticker, "expiry": exp,
                           "metrics": m, "record": record,
                           "source": f"acq-{day}-{ticker.lower()}"}
                with open(out_raw, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, default=str)
                print(f"  [ok] pre_vanna={m['pre_event_vanna_exposure']} "
                      f"firing={m['n_firing']}/{m['n_buckets']} net_div={m['net_div']} "
                      f"breach_elig={m['breach_eligible']} dte={m['dte']}", flush=True)
                done += 1
                time.sleep(0.3)  # separated-session pacing
            gaps_all.extend(day_gaps)
            if day_records:
                with open(os.path.join(RECORDS, f"{day}.json"), "w", encoding="utf-8") as fh:
                    json.dump({"day": day, "expiry": exp, "habitat": habitat,
                               "label": label, "records": day_records}, fh, indent=2)
            # persist running gaps log
            with open(os.path.join(OUT, "gaps.json"), "w", encoding="utf-8") as fh:
                json.dump(gaps_all, fh, indent=2)
    finally:
        ctl.close()

    # Write acquisition manifest
    unique_days = []
    for day, exp, habitat, label in CANDIDATES:
        rr = os.path.join(RECORDS, f"{day}.json")
        ok = os.path.exists(rr)
        n_firing = None
        breach_elig = None
        pre_v = None
        fams = []
        if ok:
            d = json.load(open(rr, encoding="utf-8"))
            fams = [r["ticker"] for r in d["records"]]
            m0 = d["records"][0]
            breach_elig = any(r["clock"]["from_breach"]["eligible"] for r in d["records"])
            n_firing = max((r["l2"].get("_n_firing", 0) for r in d["records"]), default=None)
            pre_v = d["records"][0]["l2"]["pre_vanna_exposure"]
            # read n_firing from raw
            nf = []
            for r in d["records"]:
                rf = os.path.join(RAW, f"{day}_{r['ticker']}.json")
                try:
                    rw = json.load(open(rf, encoding="utf-8"))
                    nf.append(rw["metrics"].get("n_firing", 0))
                except Exception:
                    pass
            n_firing = max(nf, default=None)
        unique_days.append({
            "day": day, "expiry": exp, "dte": (dt.datetime.strptime(exp, "%Y%m%d")
                                               - dt.datetime.strptime(day, "%Y%m%d")).days,
            "habitat": habitat, "label": label,
            "acquired": ok, "families": fams,
            "breach_eligible": breach_elig, "n_firing": n_firing,
        })
    n_acquired = sum(1 for u in unique_days if u["acquired"])
    n_breach = sum(1 for u in unique_days if u["acquired"] and u["breach_eligible"])
    with open(os.path.join(OUT, "acquisition_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"as_of": dt.date.today().isoformat(),
                   "n_unique_days_acquired": n_acquired,
                   "n_breach_eligible": n_breach,
                   "unique_days": unique_days, "gaps": gaps_all},
                  fh, indent=2)
    print(f"\n[manifest] {n_acquired} unique days acquired "
          f"({n_breach} breach-eligible), {len(gaps_all)} gaps, {time.time()-t0:.0f}s")
    return n_acquired


if __name__ == "__main__":
    main()
