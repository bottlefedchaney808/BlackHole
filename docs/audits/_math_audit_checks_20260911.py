"""Executable math checks for the 2026-09-11 CARL math audit.

Synthetic only. No ThetaData. Run from repo root:
  .venv\\Scripts\\python.exe docs/audits/_math_audit_checks_20260911.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
# Vol_Suite MUST be first among suite dirs: Options_Suite also ships
# expiry_selector.py without DEFAULT_A (CLAUDE.md flat-import collision).
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "VaR_Tools_Simulations"))
sys.path.insert(0, str(ROOT / "Vol_Suite"))


def load_hist_sim_garch():
    path = ROOT / "VaR_Tools_Simulations" / "var_engine" / "hist_sim.py"
    spec = importlib.util.spec_from_file_location("hist_sim_standalone", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._garch_fit


def section(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main():
    out = {}

    # ------------------------------------------------------------------
    # T1 jump-diffusion
    # ------------------------------------------------------------------
    section("T1 jump-diffusion")
    from implied_vol import bs_price, implied_vol
    from jump_diffusion.calibration import MAX_CALIB_STRIKES, calibrate
    from jump_diffusion.models import BatesModel, MertonModel
    from jump_diffusion.pricer import lewis_price
    from variance_swap_live import ChainData

    S0, K, T, r, q, sigma = 100.0, 105.0, 0.5, 0.03, 0.0, 0.22
    m0 = MertonModel(sigma=sigma, lam=0.0, mu_j=0.0, sigma_j=0.01)
    lewis = lewis_price(m0, S0, K, T, r, q, "call")
    bs = bs_price(S0, K, T, r, q, sigma, "call")
    print(f"Merton lam=0 Lewis={lewis:.6f} BS={bs:.6f} abs={abs(lewis-bs):.6e}")
    out["t1_lewis_vs_bs_abs"] = abs(lewis - bs)

    true = MertonModel(sigma=0.20, lam=0.8, mu_j=-0.08, sigma_j=0.12)
    strikes_small = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])

    def synth(model, strikes):
        prices = np.array([lewis_price(model, S0, k, T, r, q, "call") for k in strikes])
        ivs = np.array(
            [implied_vol(p, S0, k, T, r, q, "call") or np.nan for p, k in zip(prices, strikes)]
        )
        return ChainData(
            expiry="20270101",
            strikes=strikes,
            call_mid=prices,
            put_mid=prices,
            r=r,
            q=q,
            call_iv=ivs,
            put_iv=ivs,
        )

    chain = synth(true, strikes_small)
    cal = calibrate(MertonModel, chain, S0, T)
    rec = {k: float(cal.params[k]) for k in true.param_names}
    print("Merton recovery vs true", rec, "true", {k: getattr(true, k) for k in true.param_names})
    print("reported rmse_iv", cal.rmse_iv)
    out["t1_merton_params"] = rec
    out["t1_merton_true"] = {k: getattr(true, k) for k in true.param_names}
    out["t1_merton_rmse"] = cal.rmse_iv

    # Wide chain: reported rmse (mask) vs full-smile rmse
    wide = np.linspace(40.0, 180.0, 40)
    chain_w = synth(true, wide)
    cal_w = calibrate(MertonModel, chain_w, S0, T)
    valid = ~np.isnan(chain_w.call_iv)
    mkt = np.asarray(chain_w.call_iv)[valid]
    fit = cal_w.fitted_ivs
    nearest = np.sort(np.argsort(np.abs(wide[valid] - S0))[:MAX_CALIB_STRIKES])
    mask = np.zeros(len(mkt), dtype=bool)
    mask[nearest] = True
    rmse_mask = float(np.sqrt(np.nanmean((fit[mask] - mkt[mask]) ** 2)))
    rmse_full = float(np.sqrt(np.nanmean((fit - mkt) ** 2)))
    print(
        f"wide n={len(wide)} MAX_CALIB={MAX_CALIB_STRIKES} "
        f"reported={cal_w.rmse_iv:.6f} mask={rmse_mask:.6f} full={rmse_full:.6f} "
        f"full/reported={rmse_full / max(cal_w.rmse_iv, 1e-12):.3f}"
    )
    out["t1_rmse_reported"] = cal_w.rmse_iv
    out["t1_rmse_full"] = rmse_full
    out["t1_rmse_ratio"] = rmse_full / max(cal_w.rmse_iv, 1e-12)

    # Bates jump-share approximation vs integrated variance
    b = BatesModel(
        kappa=2.0, theta=0.09, xi=0.5, rho=-0.6, v0=0.04,
        lam=1.0, mu_j=-0.08, sigma_j=0.12,
    )
    Tshare = 1.0
    jump_var = b.lam * Tshare * (b.sigma_j**2 + b.mu_j**2)
    integ = b.theta * Tshare + (b.v0 - b.theta) * (1 - math.exp(-b.kappa * Tshare)) / b.kappa
    approx = b.jump_variance_share(Tshare)
    true_share = jump_var / (jump_var + integ)
    print(
        f"Bates jump_share approx={approx:.4f} true_integrated={true_share:.4f} "
        f"rel_err={(approx - true_share) / true_share:.1%} "
        f"jump_var={jump_var:.4f} v0T={b.v0 * Tshare:.4f} integ={integ:.4f}"
    )
    out["t1_jump_share_approx"] = approx
    out["t1_jump_share_true"] = true_share

    from jump_diffusion.garch_bridge import adjust_garch_forecast
    g = 0.20
    adj_approx = adjust_garch_forecast(g, approx)
    adj_true = adjust_garch_forecast(g, true_share)
    print(f"GARCH forecast 0.20 -> approx-adj={adj_approx:.4f} true-adj={adj_true:.4f}")
    out["t1_garch_adj_approx"] = adj_approx
    out["t1_garch_adj_true"] = adj_true

    # Bates 7-param recovery is too slow for this pass (500 Nelder-Mead
    # iters x 15 Lewis quads). Identifiability is argued from Merton jump
    # params + the jump-share approximation instead.
    out["t1_bates_params"] = "skipped_slow"

    # ------------------------------------------------------------------
    # T2 unlabeled positional correlation / vol
    # ------------------------------------------------------------------
    section("T2 context-store reindex")
    from VaR_Tools_Simulations import module_registry as mr

    stored = [
        [1.0, 0.10, 0.20],
        [0.10, 1.0, 0.30],
        [0.20, 0.30, 1.0],
    ]
    labeled, src_l = mr._resolve_corr(
        {"correlation_matrix": stored, "correlation_tickers": ["A", "B", "C"]},
        ["C", "A", "B"],
    )
    unlabeled, src_u = mr._resolve_corr(
        {"correlation_matrix": stored},  # no labels
        ["C", "A", "B"],
    )
    print("labeled source", src_l, "corr(C,A)", labeled[0, 1], "want 0.20")
    print("unlabeled source", src_u, "corr(C,A)", unlabeled[0, 1], "positional would be 0.10")
    out["t2_labeled_CA"] = float(labeled[0, 1])
    out["t2_unlabeled_CA"] = float(unlabeled[0, 1])
    out["t2_unlabeled_source"] = src_u

    vols_l, vs_l = mr._resolve_vol(
        {"volatilities": [0.12, 0.40], "correlation_tickers": ["QQQ", "SPY"]},
        ["SPY", "QQQ"],
    )
    vols_u, vs_u = mr._resolve_vol(
        {"volatilities": [0.12, 0.40]},  # no labels, length match
        ["SPY", "QQQ"],
    )
    print("vol labeled SPY,QQQ", list(vols_l), vs_l, "want [0.40, 0.12]")
    print("vol unlabeled SPY,QQQ", list(vols_u), vs_u, "positional [0.12, 0.40] WRONG")
    out["t2_vol_labeled"] = list(map(float, vols_l))
    out["t2_vol_unlabeled"] = list(map(float, vols_u))
    out["t2_vol_unlabeled_source"] = vs_u

    # ------------------------------------------------------------------
    # T3 _resolve_* silent fallbacks
    # ------------------------------------------------------------------
    section("T3 resolvers")
    from Vol_Suite import module_registry as volreg

    w_pos = volreg._resolve_weights(
        {"weights": [0.80, 0.20]}, ["SPY", "QQQ"]
    )
    print("Vol weights explicit positional for [SPY,QQQ] stored as QQQ-heavy:", w_pos)
    out["t3_vol_weights_positional"] = w_pos

    # Options _resolve_sigma announces fallback
    # Import the Options adapter by file so we do not put Options_Suite on
    # sys.path (it collides with Vol_Suite.expiry_selector).
    opt_path = ROOT / "Options_Suite" / "module_registry.py"
    spec = importlib.util.spec_from_file_location("opt_reg_standalone", opt_path)
    optreg = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(optreg)
        sig, src = optreg._resolve_sigma(
            {}, "NO_SUCH_TICKER_XYZ", 100.0, 0.25, "call", "LeisenReimer"
        )
    except Exception as exc:
        sig, src = None, f"{type(exc).__name__}: {exc}"
    print("Options sigma no data:", sig, src[:80] if isinstance(src, str) else src)
    out["t3_opt_sigma"] = sig
    out["t3_opt_sigma_source"] = src[:120] if isinstance(src, str) else str(src)

    # GARCH broadcast: real measurement of ONE name applied to whole basket
    vols_b, src_b = mr._resolve_vol({"garch_conditional_vol": 0.12}, ["SPY", "NVDA", "TSLA"])
    print("garch broadcast", list(vols_b), src_b)

    # ------------------------------------------------------------------
    # T4 GARCH on iid normal
    # ------------------------------------------------------------------
    section("T4 hist_sim _garch_fit iid")
    garch_fit = load_hist_sim_garch()
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0, 0.01, size=2000)
    fit = garch_fit(rets)
    print(
        f"iid N(0,0.01^2) omega={fit['omega']:.8f} alpha={fit['alpha']:.4f} "
        f"beta={fit['beta']:.4f} persistence={fit['persistence']:.6f} "
        f"long_run_vol={fit['long_run_vol']:.6f} current={fit['current_vol']:.6f} "
        f"converged={fit['converged']} sample_std={rets.std():.6f} "
        f"annualised_sample={rets.std()*math.sqrt(252):.4f}"
    )
    out["t4"] = {
        "omega": fit["omega"],
        "alpha": fit["alpha"],
        "beta": fit["beta"],
        "persistence": fit["persistence"],
        "long_run_vol": fit["long_run_vol"],
        "current_vol": fit["current_vol"],
        "converged": fit["converged"],
        "sample_std": float(rets.std()),
    }

    # Real GARCH process should recover persistence away from bound
    h = np.empty(2000)
    h[0] = 0.01**2
    omega_t, a_t, b_t = 1e-6, 0.08, 0.90
    syn = np.empty(2000)
    for t in range(2000):
        syn[t] = rng.normal(0, math.sqrt(h[t] if t == 0 else h[t]))
        if t + 1 < 2000:
            h[t + 1] = omega_t + a_t * syn[t] ** 2 + b_t * h[t]
    fit2 = garch_fit(syn)
    print(
        f"true GARCH(0.08,0.90) recovered pers={fit2['persistence']:.4f} "
        f"alpha={fit2['alpha']:.4f} beta={fit2['beta']:.4f} converged={fit2['converged']}"
    )
    out["t4_true_garch_persistence"] = fit2["persistence"]

    # ------------------------------------------------------------------
    # T5 var_agg
    # ------------------------------------------------------------------
    section("T5 var_agg")
    from var_engine.var_agg import VaRAggInputs, ewma_covariance, run as var_agg_run

    rng = np.random.default_rng(99)
    n, Tlen = 6, 300
    names = [f"A{i}" for i in range(n)]
    pos = rng.uniform(1e5, 2e6, n)
    groups = np.array([0, 0, 0, 1, 1, 1])
    R = rng.normal(0, 0.015, (Tlen, n))
    res = var_agg_run(
        VaRAggInputs(
            asset_names=names, positions=pos, group_mask=groups, returns=R,
            var_days=5, confidence=0.95, n_pca_components=2,
        )
    )
    euler_gap = abs(float(np.sum(res.component_var)) - res.total_var)
    pca_gap = abs(res.pca_var_with_resid - res.total_var)
    print(f"total_var={res.total_var:.2f} cvar={res.total_cvar:.2f} cvar/var={res.total_cvar/res.total_var:.4f}")
    print(f"sum(component)={np.sum(res.component_var):.2f} euler_gap={euler_gap:.6e}")
    print(f"pca_with_resid={res.pca_var_with_resid:.2f} pca_gap_vs_total={pca_gap:.6e}")
    print(f"pca_without={res.pca_var_without_resid:.2f} aggregated={res.aggregated_var:.2f}")
    # Gaussian 95% ES/VaR = pdf(z)/(z*(1-Phi(z)))
    z = stats.norm.ppf(0.95)
    want_ratio = stats.norm.pdf(z) / (z * (1 - stats.norm.cdf(z)))
    print(f"cvar/var vs Gaussian ES/VaR want {want_ratio:.4f}")
    out["t5_euler_gap"] = euler_gap
    out["t5_pca_gap"] = pca_gap
    out["t5_cvar_over_var"] = res.total_cvar / res.total_var
    out["t5_gauss_ratio"] = want_ratio

    # Market-neutral group: net notional ~ 0, gross 2e6
    pos_h = np.array([1_000_000.0, -1_000_000.0, 500_000.0])
    R_h = rng.normal(0, 0.01, (400, 3))
    # induce some independent residual
    R_h[:, 0] += rng.normal(0, 0.005, 400)
    res_h = var_agg_run(
        VaRAggInputs(
            asset_names=["L", "S", "FX"],
            positions=pos_h,
            group_mask=np.array([0, 0, 1]),
            returns=R_h,
            var_days=5, confidence=0.95, n_pca_components=2,
        )
    )
    print(
        f"hedged book total_var={res_h.total_var:,.0f} aggregated={res_h.aggregated_var:,.0f} "
        f"sub={res_h.subport_var} ratio_agg/total={res_h.aggregated_var/max(res_h.total_var,1e-12):.3f}"
    )
    out["t5_hedged_total"] = res_h.total_var
    out["t5_hedged_agg"] = res_h.aggregated_var
    out["t5_hedged_sub"] = {k: float(v) for k, v in res_h.subport_var.items()}

    # EWMA: last-day spike should matter more at lam=0.94 than equal-weight
    R2 = rng.normal(0, 0.01, (200, 2))
    R2[-1] = [0.20, 0.20]  # huge last day
    cov_hi = ewma_covariance(R2, 0.94)
    cov_lo = ewma_covariance(R2, 0.50)
    print(
        f"last-day spike var lam0.94={cov_hi[0,0]:.4f} lam0.50={cov_lo[0,0]:.4f} "
        f"ratio={cov_hi[0,0]/cov_lo[0,0]:.3f}"
    )
    out["t5_ewma_spike_hi"] = float(cov_hi[0, 0])
    out["t5_ewma_spike_lo"] = float(cov_lo[0, 0])

    # ------------------------------------------------------------------
    # T6 dealer zero-gamma classified as short
    # ------------------------------------------------------------------
    section("T6 dealer zero-net classified short")
    # Mirrors backtest_stage3.py:654-655
    net_v1 = 0.0
    regime = "long" if net_v1 > 0 else "short"
    print(f"net_gamma_v1=0.0 -> regime_v1={regime} (forced label, not unclassified)")
    out["t6_zero_is_short"] = regime

    print("\nJSON")
    print(json.dumps(out, indent=2, default=float))


if __name__ == "__main__":
    main()
