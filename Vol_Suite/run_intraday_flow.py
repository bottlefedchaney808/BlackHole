"""Tier-3B — From-breach intraday clock (the arbiter's ONLY clean clock; ESC-1c). v2.

Fixes vs v1: (1) pull BOTH C and P for every strike (execution_locus needs both sides);
(2) anchor the execution locus ONCE per day at the opening bucket (band = open spot +- 1%,
fixed for the day — the Karsan construct: the dealer's band is anchored to the position,
not re-anchored every 10 min); (3) burst fires when intraday spot breaches the FIXED band.

Estimand (pre-registered, round-4b on the intraday clock):
  signed_flow = -sign(ΔIV_i) x burst_i   (burst = hedge_flow_at at the day-anchored locus)
  Support:    ALL 10-min buckets with nonzero ΔIV AND nonzero burst (de-gated)
  Response:   FORWARD 10-min return from the breach bucket
  Prediction: from-breach/intraday EXPECT POSITIVE (direct vanna push; only a null here refutes)
  Index-primary: SPY/QQQ one family; cluster-bootstrap 90% CI; tanh md; n_firing_index >= 20.
Writes: Vol_Suite/_intraday_cache/flow_from_breach_result.md
"""
import datetime as dt
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import expiry_book_exposure as ebe  # noqa: E402
from shared.thetadata import ThetaDataController, strike_to_theta, strike_from_theta  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
DEADBAND = 0.01
IVL = 600000  # 10-min
DAYS = [("20260716", "20260717"), ("20260717", "20260717"), ("20260731", "20260803")]


def _stock_intraday(ctl, root, day):
    r = ctl._get_with_retry(f"/api/theta/hist/stock/ohlc/{root}",
                            params={"start_date": day, "end_date": day})
    r.raise_for_status()
    rows = ctl._parse_rows(r)
    out = {}
    for x in rows:
        ms = int(float(x.get("ms_of_day", 0) or 0))
        c = x.get("close")
        if c not in (None, "", "0", 0):
            try:
                out[ms] = float(c)
            except (TypeError, ValueError):
                continue
    return out


def _chain_intraday(ctl, root, exp, day, strikes_theta, right):
    out = {}
    for k in strikes_theta:
        r = ctl._get_with_retry(f"/api/theta/hist/option/all_greeks/{root}/{exp}/{k}/{right}",
                                params={"start_date": day, "end_date": day, "ivl": IVL})
        if r.status_code == 404:
            continue
        r.raise_for_status()
        rows = ctl._parse_rows(r)
        if rows:
            out[k] = rows
    return out


def _oi_proxy(ctl, root, exp, day, strikes_theta, right):
    out = {}
    for k in strikes_theta:
        try:
            r = ctl._get_with_retry(
                f"/api/theta/hist/option/open_interest/{root}/{exp}/{k}/{right}",
                params={"start_date": day, "end_date": day})
            if r.status_code == 404:
                continue
            r.raise_for_status()
            rows = ctl._parse_rows(r)
            if rows:
                out[k] = int(float(rows[-1].get("open_interest", 0) or 0))
        except Exception:
            continue
    return out


def _rows_at(chain_c, chain_p, oi_c, oi_p, ms):
    """All C+P rows at the bucket <= ms."""
    rows = []
    for k, krows in chain_c.items():
        best = None
        for x in krows:
            xms = int(float(x.get("ms_of_day", 0) or 0))
            if xms <= ms and (best is None or xms > int(float(best.get("ms_of_day", 0) or 0))):
                best = x
        if best is not None:
            iv = best.get("implied_vol")
            if iv not in (None, "", 0):
                try:
                    rows.append({"strike": strike_from_theta(k), "right": "C",
                                 "oi": oi_c.get(k, 0), "implied_vol": float(iv)})
                except (TypeError, ValueError):
                    pass
    for k, krows in chain_p.items():
        best = None
        for x in krows:
            xms = int(float(x.get("ms_of_day", 0) or 0))
            if xms <= ms and (best is None or xms > int(float(best.get("ms_of_day", 0) or 0))):
                best = x
        if best is not None:
            iv = best.get("implied_vol")
            if iv not in (None, "", 0):
                try:
                    rows.append({"strike": strike_from_theta(k), "right": "P",
                                 "oi": oi_p.get(k, 0), "implied_vol": float(iv)})
                except (TypeError, ValueError):
                    pass
    return rows


def _de_mean(x):
    m = sum(x) / len(x) if x else 0.0
    return [v - m for v in x]


def _corr(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma = sum(a) / n; mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def main():
    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append("# Tier-3B — From-breach intraday clock (ΔIV-signed vanna flow vs forward 10-min returns) — v2\n")
    lines.append(f"**Date:** {dt.date.today().isoformat()}  **Days:** {[d[0] for d in DAYS]}\n")
    lines.append("Prediction (Karsan two-clock): from-breach/intraday EXPECT POSITIVE — only a null "
                 "HERE refutes the mechanism. Locus anchored once per day at the opening bucket "
                 "(C+P chain, real OI), band = open spot ±1% fixed for the day.\n")

    clusters = {}
    detail = []
    for day, exp in DAYS:
        for tk in ("SPY", "QQQ"):
            spot_min = _stock_intraday(ctl, tk, day)
            if not spot_min:
                continue
            open_spot = spot_min[min(spot_min)]
            base = round(open_spot / 5.0) * 5.0
            strikes = [strike_to_theta(base + i * 5.0) for i in range(-12, 13)]
            chain_c = _chain_intraday(ctl, tk, exp, day, strikes, "C")
            chain_p = _chain_intraday(ctl, tk, exp, day, strikes, "P")
            if len(chain_c) < 10 or len(chain_p) < 10:
                print(f"[warn] {tk} {day}: C={len(chain_c)} P={len(chain_p)} strikes served")
                continue
            oi_c = _oi_proxy(ctl, tk, exp, day, list(chain_c.keys()), "C")
            oi_p = _oi_proxy(ctl, tk, exp, day, list(chain_p.keys()), "P")

            # day-anchored locus from the opening bucket
            rows_open = _rows_at(chain_c, chain_p, oi_c, oi_p, min(spot_min))
            T = max((dt.datetime.strptime(exp, "%Y%m%d").date()
                     - dt.datetime.strptime(day, "%Y%m%d").date()).days / 365.0, 0.01)
            if len(rows_open) < 20:
                print(f"[warn] {tk} {day}: only {len(rows_open)} open rows")
                continue
            try:
                locus = ebe.execution_locus(rows_open, open_spot, T=T)
            except Exception as e:
                print(f"[warn] {tk} {day}: locus EXC {type(e).__name__}: {str(e)[:100]}")
                continue

            # all buckets (union of ms)
            all_ms = set()
            for k, krows in chain_c.items():
                for x in krows:
                    all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
            for k, krows in chain_p.items():
                for x in krows:
                    all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
            bucket_ms = sorted(all_ms)

            sf_list, div_list, resp_list, burst_list = [], [], [], []
            vf_list = []       # live-model-equivalent ΔIV-signed vannaflow per firing bucket
            day_div_sum = 0.0
            prev_atm_iv = None
            prev_spot = None
            day_div_acc = 0.0
            for ms in bucket_ms:
                spot_now = None
                for sms in sorted(spot_min):
                    if sms <= ms:
                        spot_now = spot_min[sms]
                    else:
                        break
                if not spot_now:
                    continue
                rows_at = _rows_at(chain_c, chain_p, oi_c, oi_p, ms)
                if len(rows_at) < 20:
                    continue
                # ATM IV from rows_at (C+P, nearest strike to spot)
                ivs = []
                for r_ in rows_at:
                    if r_["implied_vol"] not in (None, 0):
                        ivs.append((r_["strike"], r_["implied_vol"]))
                if not ivs:
                    continue
                atm_iv = min(ivs, key=lambda p: abs(p[0] - spot_now))[1]
                div = 0.0 if prev_atm_iv is None else atm_iv - prev_atm_iv
                day_div_acc += div
                # burst vs the FIXED day-anchored locus
                burst = ebe.hedge_flow_at(locus, spot_now)
                # forward 10-min return
                fwd = 0.0
                nxt = None
                for sms in sorted(spot_min):
                    if sms > ms:
                        nxt = spot_min[sms]
                        break
                if nxt:
                    fwd = (nxt - spot_now) / spot_now if spot_now else 0.0
                if div != 0.0 and burst != 0.0:
                    # live-model-equivalent ΔIV-signed vannaflow on the SAME bucket:
                    # vanna_flow(ne, d_iv) = Σ signed_vanna·OI·100·VANNA_PP_SCALE·(dIV/0.01),
                    # the exact term the live model's vanna_call+put shares reduce to on
                    # identical rows in the same dealer frame (rec.vanna = −1×BS).
                    try:
                        ne_bucket = ebe.build_net_exposure(rows_at, spot_now, ticker=tk)
                        vf = ebe.vanna_flow(ne_bucket, div)
                    except Exception:
                        vf = 0.0
                    sf_list.append(-math.copysign(1.0, div) * burst)
                    div_list.append(div)
                    resp_list.append(fwd)
                    burst_list.append(burst)
                    vf_list.append(vf)
                prev_atm_iv = atm_iv
                prev_spot = spot_now
            day_div_sum = day_div_acc
            if len(sf_list) >= 4:
                # store: (sf_gamma, div, resp, burst, vf_vanna, day_net_div)
                clusters[(tk, day)] = (sf_list, div_list, resp_list, burst_list,
                                       vf_list, day_div_sum)
                zg = getattr(locus, "zero_gamma", float("nan"))
                bl, bu = getattr(locus, "band_lower", 0), getattr(locus, "band_upper", 0)
                detail.append(f"| {tk} | {day} | {exp} | {len(sf_list)} | {len(bucket_ms)} | "
                              f"zg={zg:.0f} band=[{bl:.1f},{bu:.1f}] |")

    lines.append("\n### Firing buckets per (ticker, day)")
    lines.append("| Ticker | Day | Expiry | firing | buckets | locus |")
    lines.append("|---|---|---|---|---|---|")
    lines.extend(detail)

    # ROUND-2: effective-n = number of (ticker, day) clusters; SPY/QQQ one family
    n_eff_idx = len({(tk, d) for (tk, d) in clusters if tk in ("SPY", "QQQ")})
    idx_n = sum(len(v[0]) for (tk, d), v in clusters.items() if tk in ("SPY", "QQQ"))
    lines.append("\n### INDEX PRIMARY (SPY/QQQ = one family)  [R2-5: effective-n bars]")
    lines.append(f"firing buckets (from-breach): {idx_n}  effective clusters: {n_eff_idx}")

    # (a) NEW-model gamma-burst signed flow (the round-1 conflated leg, recomputed)
    def _pooled_corr_cis(x_lists, y_lists, seed=7, n_iter=2000):
        rng = random.Random(seed)
        K = len(x_lists)
        xall, yall = [], []
        for x, y in zip(x_lists, y_lists):
            xall.extend(_de_mean(x)); yall.extend(_de_mean(y))
        r_obs = _corr(xall, yall)
        stats = []
        for _ in range(n_iter):
            xs, ys = [], []
            for _ in range(K):
                j = rng.randrange(K)
                xs.extend(_de_mean(x_lists[j])); ys.extend(_de_mean(y_lists[j]))
            if len(xs) >= 4:
                stats.append(_corr(xs, ys))
        stats.sort()
        if len(stats) < 100:
            return (0.0, 0.0, r_obs)
        return (stats[int(0.05 * len(stats))], stats[int(0.95 * len(stats)) - 1], r_obs)

    def _verdict(r, lo, hi, n_eff, n):
        md = math.tanh(2.8016 / math.sqrt(max(n_eff - 3, 1)))  # R2-5: md at EFFECTIVE-n
        if (lo > 0 or hi < 0) and abs(r) >= md and n_eff >= 3:
            return (f"**SUPPORTED**" if r > 0 else f"**RULED_OUT (wrong sign)**") + \
                   f" (CI excludes 0, |r|={abs(r):.4f} >= md={md:.3f} at eff-n={n_eff})", md
        if lo > 0 or hi < 0:
            return f"underpowered (CI excludes 0 but |r|={abs(r):.4f} < md={md:.3f} at eff-n={n_eff})", md
        return f"**BOUNDED** (CI [{lo:+.3f},{hi:+.3f}] straddles 0 at eff-n={n_eff})", md

    if idx_n >= 4:
        # --- channel (a): new-model gamma burst ---
        cl_a = [(sf, resp) for (tk, d), (sf, div, resp, b, vf, ndiv) in clusters.items()
                if tk in ("SPY", "QQQ")]
        lo_a, hi_a, r_a = _pooled_corr_cis([x for x, _ in cl_a], [y for _, y in cl_a])
        v_a, md_a = _verdict(r_a, lo_a, hi_a, n_eff_idx, idx_n)
        lines.append(f"\n  (a) NEW gamma-burst signed flow corr = {r_a:+.4f}  90% CI [{lo_a:+.4f}, {hi_a:+.4f}]  "
                     f"n={idx_n} eff-n={n_eff_idx}")
        lines.append(f"      verdict: {v_a}")

        # --- channel (b): LIVE-equivalent ΔIV-signed vannaflow (R2-2: the one object never measured intraday) ---
        cl_b = [(vf, resp) for (tk, d), (sf, div, resp, b, vf, ndiv) in clusters.items()
                if tk in ("SPY", "QQQ")]
        n_vf_nonzero = 0
        for (tk, d), (sf, div, resp, b, vf, ndiv) in clusters.items():
            if tk in ("SPY", "QQQ"):
                n_vf_nonzero += sum(1 for v_ in vf if v_ != 0.0)
        lines.append(f"\n  (b) LIVE ΔIV-signed vannaflow — nonzero-vf buckets: {n_vf_nonzero}/{idx_n} "
                     f"(0 would mean the vanna call silently failed)")
        lo_b, hi_b, r_b = _pooled_corr_cis([x for x, _ in cl_b], [y for _, y in cl_b])
        v_b, md_b = _verdict(r_b, lo_b, hi_b, n_eff_idx, idx_n)
        lines.append(f"  (b) LIVE ΔIV-signed vannaflow corr (EXPECT POSITIVE) = {r_b:+.4f}  90% CI [{lo_b:+.4f}, {hi_b:+.4f}]")
        lines.append(f"      verdict: {v_b}")

        # --- channel (c): pairwise sign-agreement per firing bucket (R2-3) ---
        tot_agree = 0; tot_buckets = 0
        for (tk, d), (sf, div, resp, b, vf, ndiv) in clusters.items():
            if tk not in ("SPY", "QQQ"):
                continue
            for s_, v_ in zip(sf, vf):
                if s_ != 0.0 and v_ != 0.0:
                    tot_buckets += 1
                    tot_agree += int((s_ > 0) == (v_ > 0))
        if tot_buckets:
            lines.append(f"\n  (c) PAIRED two-model sign-agreement on firing buckets: {tot_agree}/{tot_buckets} "
                         f"= {tot_agree/tot_buckets:.1%}  (coherence, NOT predictiveness)")

        # --- channel (d): shadow-leak sub-sample (R2-4) — buckets whose intraday ΔIV
        #     is OPPOSITE the day's net ΔIV direction ---
        leak_x, leak_y, leak_n = [], [], 0
        for (tk, d), (sf, div, resp, b, vf, ndiv) in clusters.items():
            if tk not in ("SPY", "QQQ"):
                continue
            for i_, (s_, v_, dv) in enumerate(zip(sf, vf, div)):
                if s_ == 0.0 or v_ == 0.0:
                    continue
                if (dv > 0) != (ndiv > 0) and ndiv != 0.0:
                    leak_x.append(v_)
                    leak_y.append(resp[i_])
                    leak_n += 1
        if leak_n >= 4:
            r_l = _corr(leak_x, leak_y)
            md_l = math.tanh(2.8016 / math.sqrt(max(leak_n - 3, 1)))
            lines.append(f"\n  (d) SHADOW-LEAK SUB-SAMPLE (intraday ΔIV opposite day's net ΔIV): n={leak_n}")
            lines.append(f"      live vannaflow corr vs fwd = {r_l:+.4f}  (EXPECT POSITIVE if the −0.088 was daily-shadow leak)")
            lines.append(f"      md@n={leak_n} = {md_l:.4f} → "
                         f"{'SUPPORTED (leak hypothesis CONFIRMED — mechanism alive)' if r_l > 0 and r_l >= md_l else 'BOUNDED/negative'}")
        else:
            lines.append(f"\n  (d) SHADOW-LEAK SUB-SAMPLE: only {leak_n} buckets — insufficient")

        lines.append(f"\n  R2-5 note: md computed at effective-n ({n_eff_idx} clusters), not pooled n.")
        lines.append(f"  R2-7 note: firing clusters = {n_eff_idx} (20260803 is an expiry, not a day anchor).")
    else:
        lines.append("  insufficient firing buckets — BOUNDED")

    sg_cl = [(sf, resp) for (tk, d), (sf, div, resp, b, vf, ndiv) in clusters.items()
             if tk not in ("SPY", "QQQ")]
    sg_n = sum(len(s) for s, _ in sg_cl)
    lines.append(f"\n### SINGLES screen (n={sg_n})")
    if sg_n >= 4:
        xall, yall = [], []
        for x, y in sg_cl:
            xall.extend(_de_mean(x)); yall.extend(_de_mean(y))
        lines.append(f"  singles gamma-burst corr = {_corr(xall, yall):+.4f}")

    lines.append(f"\n[t3b] total {time.time()-t0:.1f}s")
    out = os.path.join(CACHE, "flow_from_breach_result.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
