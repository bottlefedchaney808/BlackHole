#!/usr/bin/env python3
# garch_analysis.py
# GARCH(1,1) Volatility Analysis Module
# Fits GARCH(1,1) with t-distribution to any ticker.
# Generates: standardized residuals diagnostics, conditional volatility, 30-day forecast.

import numpy as np
import pandas as pd
import warnings
import os
from datetime import datetime, timedelta, timezone

from correlation_engine import fetch_price_history
from vs_utils import timestamped_output_dir

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from arch import arch_model
from statsmodels.tsa.stattools import adfuller
from statsmodels.stats.diagnostic import het_arch
from scipy.stats import norm, t as student_t

warnings.filterwarnings("ignore")

ANNUALIZE = 252
FORECAST = 30

# 3 years of daily data (~750 observations). Previously this was pinned to a
# fixed "2015-01-01", which meant the lookback silently grew every year -- by
# mid-2026 it was requesting 11.5 years and generating the single largest data
# request in the suite. 750 observations is comfortably sufficient to fit
# GARCH(1,1) with a t-distribution (5 parameters), and a rolling window keeps
# the fit weighted toward the current volatility regime rather than averaging
# across a decade of structurally different ones.
DEFAULT_YEARS = 3
DEFAULT_START = (datetime.now(timezone.utc).date()
                 - timedelta(days=int(DEFAULT_YEARS * 365.25))).strftime("%Y-%m-%d")

plt.rcParams.update({
    "font.size": 10,
    "figure.facecolor": "white",
    "axes.facecolor": "#f8f9fa",
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.edgecolor": "#cccccc",
})


def _to_float(val):
    """Safely extract a float from a scalar, Series, or DataFrame."""
    if isinstance(val, (pd.Series, pd.DataFrame)):
        if not val.empty:
            try:
                if isinstance(val, pd.DataFrame):
                    return float(val.iloc[0, 0])
                return float(val.iloc[0])
            except Exception:
                pass
        return np.nan
    try:
        return float(val)
    except (TypeError, ValueError):
        return np.nan


def run_garch_analysis(ticker: str, start: str = DEFAULT_START, end: str = None):
    """Download data, fit GARCH(1,1) with t-dist, generate plots and print report."""
    if end is None:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"\n{'=' * 70}")
    print(f"  GARCH VOLATILITY ANALYSIS — {ticker}")
    print(f"{'=' * 70}")
    print(f"  Period: {start} to {end}")

    # --- 1. Download (ThetaData first, yfinance fallback only -- see
    # correlation_engine.fetch_price_history) ---
    print(f"\n  Downloading {ticker} data...")
    try:
        start_dt = pd.to_datetime(start)
        end_dt = pd.to_datetime(end) if end else pd.Timestamp.now(tz=timezone.utc).tz_localize(None)
        years_back = max((pd.Timestamp.now(tz=timezone.utc).tz_localize(None) - start_dt).days / 365.25, 0.1)
        hist_df = fetch_price_history([ticker], period=f"{years_back:.3f}y")
        prices = hist_df[ticker].dropna()
        prices = prices[(prices.index >= start_dt) & (prices.index <= end_dt)]
        if prices.empty:
            raise ValueError(f"No data fetched for {ticker}")
        log_returns = np.log(prices / prices.shift(1)).dropna() * 100
        mean_ret = _to_float(log_returns.mean())
        std_ret = _to_float(log_returns.std())
        n = len(log_returns)
        print(f"  Samples: {n}, From: {log_returns.index[0].date()} to {log_returns.index[-1].date()}")
        print(f"  Mean return: {mean_ret:.3f}%, Std: {std_ret:.3f}%")
    except Exception as e:
        raise ValueError(f"Error during data download: {e}") from e

    # --- 2. Pre-tests ---
    print("\n  Performing preliminary tests...")
    arch_lm_p = np.nan
    try:
        adf = adfuller(log_returns.dropna(), maxlag=10)
        print(f"  ADF test: stat={adf[0]:.4f}, p={adf[1]:.4e} -> {'stationary' if adf[1] < 0.05 else 'non-stationary'}")
        arch_test = het_arch(log_returns.dropna().values, nlags=10)
        arch_lm_p = float(arch_test[1])
        print(f"  ARCH LM test: stat={arch_test[0]:.2f}, p={arch_lm_p:.4e} -> {'ARCH effects detected' if arch_lm_p < 0.05 else 'no significant ARCH'}")
        if arch_lm_p >= 0.05:
            print()
            print("  *** WARNING: no significant ARCH effects in this series. ***")
            print("  GARCH models conditional heteroskedasticity -- volatility clustering.")
            print("  This test says there isn't any to model, so the fit below is")
            print("  describing noise and its parameters should not be interpreted.")
            if arch_lm_p > 0.5:
                print()
                print("  A p-value this extreme is unusual for daily equity returns, which")
                print("  almost always cluster. Suspect the DATA before concluding the")
                print("  market is well behaved -- check that the price series is sorted")
                print("  ascending by date, since shuffled returns destroy the clustering")
                print("  this test looks for.")
    except Exception as e:
        print(f"  Warning: Pre-test failed: {e}")

    # --- 3. Fit GARCH(1,1) ---
    print(f"\n  Fitting GARCH(1,1) with t-distribution...")
    try:
        am = arch_model(log_returns, vol="Garch", p=1, q=1, mean="Constant", dist="t", rescale=False)
        res = am.fit(disp="off")
        print(res.summary())
    except Exception as e:
        raise RuntimeError(f"GARCH model fitting failed: {e}") from e

    omega = float(res.params.get("omega", 0))
    alpha = float(res.params.get("alpha[1]", 0))
    beta = float(res.params.get("beta[1]", 0))
    nu = float(res.params.get("nu", 5))
    persistence = alpha + beta
    uncond_vol = np.sqrt((omega / (1 - persistence) * ANNUALIZE)) / 100 if persistence < 1 else np.nan

    print(f"\n{'─' * 50}")
    print(f"  MODEL PARAMETERS")
    print(f"{'─' * 50}")
    print(f"  omega (constant):       {omega:.6f}")
    print(f"  alpha (ARCH):           {alpha:.4f}")
    print(f"  beta  (GARCH):          {beta:.4f}")
    print(f"  nu (t-dist df):         {nu:.2f}")
    print(f"  Persistence (α+β):      {persistence:.4f}")
    print(f"  Unconditional vol (ann): {uncond_vol:.2%}" if not np.isnan(uncond_vol) else "  Unconditional vol (ann): N/A")
    # Some ARCH result objects expose slightly different attribute names
    def _get_result_attr(obj, candidates, default=np.nan):
        for name in candidates:
            val = getattr(obj, name, None)
            if val is not None:
                try:
                    return float(val)
                except Exception:
                    return val
        return default

    loglik = _get_result_attr(res, ["log_likelihood", "loglikelihood", "llf", "loglik"], np.nan)
    aic = _get_result_attr(res, ["aic"], np.nan)
    bic = _get_result_attr(res, ["bic"], np.nan)

    if not np.isnan(loglik):
        try:
            print(f"  Log-Likelihood:         {loglik:.2f}")
        except Exception:
            print(f"  Log-Likelihood:         {loglik}")
    else:
        print("  Log-Likelihood:         N/A")
    print(f"  AIC:                    {aic:.2f}" if not np.isnan(aic) else "  AIC:                    N/A")
    print(f"  BIC:                    {bic:.2f}" if not np.isnan(bic) else "  BIC:                    N/A")

    # --- 4. Standardized Residuals ---
    print(f"\n  Analyzing standardized residuals...")
    std_resid = res.std_resid.dropna()
    # Never write chart output into the bare module directory (Vol_Suite/ root).
    # When invoked through volatility_suite.py, VS_OUTPUT_DIR is always set to a
    # per-run outputs/<timestamp>/ folder. When run standalone (no VS_OUTPUT_DIR),
    # fall back to that same outputs/ convention instead of cwd/module dir.
    out_dir = os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not std_resid.empty:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        fig.suptitle(f"{ticker}  GARCH(1,1) — Standardized Residual Diagnostics", fontsize=14, fontweight="bold")
        axes[0].plot(std_resid.index, std_resid.values, color="#1f77b4", linewidth=0.5, alpha=0.7)
        axes[0].axhline(y=0, color="gray", linestyle="--", linewidth=0.5)
        axes[0].axhline(y=3, color="red", linestyle=":", linewidth=0.8, alpha=0.5, label="+3σ")
        axes[0].axhline(y=-3, color="red", linestyle=":", linewidth=0.8, alpha=0.5, label="-3σ")
        axes[0].set_title("Standardized Residuals")
        axes[0].set_ylabel("Std Residual")
        axes[0].legend(loc="upper right", fontsize=8)
        axes[1].hist(std_resid, bins=60, density=True, alpha=0.7, color="steelblue", edgecolor="white")
        x_range = np.linspace(float(std_resid.min()), float(std_resid.max()), 200)
        axes[1].plot(x_range, norm.pdf(x_range), "r--", linewidth=2, label="N(0,1)")
        axes[1].plot(x_range, student_t.pdf(x_range, df=nu), "g--", linewidth=2, label=f"t(df={nu:.1f})")
        axes[1].set_title("Distribution of Std Residuals")
        axes[1].set_xlabel("Value")
        axes[1].set_ylabel("Density")
        axes[1].legend(fontsize=9)
        plt.tight_layout()
        f_resid = os.path.join(out_dir, f"{ticker}_garch_std_residuals_{ts}.png")
        plt.savefig(f_resid, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Saved: {f_resid}")

    # --- 5. Conditional Volatility ---
    print(f"\n  Analyzing conditional volatility...")
    cond_vol = res.conditional_volatility * np.sqrt(ANNUALIZE) / 100

    if not cond_vol.empty:
        fig, ax = plt.subplots(figsize=(14, 5))
        ax.plot(cond_vol.index, cond_vol.values, color="#d62728", linewidth=0.8, alpha=0.9, label="GARCH Conditional Vol (ann.)")
        ax.fill_between(cond_vol.index, 0, cond_vol.values, color="#d62728", alpha=0.08)
        actual_vol = (log_returns.abs() * np.sqrt(ANNUALIZE) / 100).dropna()
        ax.scatter(actual_vol.index[::5], actual_vol.values[::5], s=8, color="gray", alpha=0.3, label="Actual |Return| (ann.)", zorder=2)
        ax.set_title(f"{ticker}  Conditional Volatility — GARCH(1,1) with t-Distribution", fontsize=13, fontweight="bold")
        ax.set_ylabel("Annualized Volatility")
        ax.set_xlabel("Date")
        ax.legend(fontsize=9)
        plt.tight_layout()
        f_cond = os.path.join(out_dir, f"{ticker}_garch_conditional_vol_{ts}.png")
        plt.savefig(f_cond, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Saved: {f_cond}")

        print(f"\n  Conditional Volatility (annualized):")
        print(f"    Mean: {_to_float(cond_vol.mean()):.2%}")
        print(f"    Current: {_to_float(cond_vol.iloc[-1]):.2%}")
        print(f"    Max: {_to_float(cond_vol.max()):.2%}")
        print(f"    Min: {_to_float(cond_vol.min()):.2%}")

    # --- 6. Forecast ---
    print(f"\n  Generating {FORECAST}-day volatility forecast...")
    try:
        forecasts = res.forecast(horizon=FORECAST, reindex=False)
        fc_var = forecasts.variance.iloc[-1].values
        fc_vol = np.sqrt(fc_var) * np.sqrt(ANNUALIZE) / 100
        fc_dates = pd.bdate_range(start=log_returns.index[-1] + pd.Timedelta(days=1), periods=FORECAST)
        fc_series = pd.Series(fc_vol, index=fc_dates, name="Forecast Volatility")

        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(fc_series.index, fc_series.values, 'o-', color="#2ca02c", linewidth=1.5, markersize=4, label="GARCH Forecast")
        ax.axhline(y=uncond_vol, color="gray", linestyle="--", alpha=0.7, label=f"Unconditional ({uncond_vol:.1%})")
        ax.axhline(y=_to_float(cond_vol.iloc[-1]), color="#d62728", linestyle=":", alpha=0.7, label=f"Current ({_to_float(cond_vol.iloc[-1]):.1%})")
        ax.fill_between(fc_series.index, 0, fc_series.values, color="#2ca02c", alpha=0.15)
        ax.set_title(f"{ticker}  {FORECAST}-Day Annualized Volatility Forecast", fontsize=13, fontweight="bold")
        ax.set_ylabel("Annualized Volatility")
        ax.set_xlabel("Date")
        ax.legend(fontsize=9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        plt.tight_layout()
        f_fc = os.path.join(out_dir, f"{ticker}_garch_forecast_{ts}.png")
        plt.savefig(f_fc, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Saved: {f_fc}")

        print(f"\n{'─' * 50}")
        print(f"  {FORECAST}-DAY VOLATILITY FORECAST")
        print(f"{'─' * 50}")
        for i, (date, vol) in enumerate(fc_series.items()):
            print(f"    Day {i+1:2d}: {date.strftime('%Y-%m-%d')} — {vol:.2%}")
    except Exception as e:
        print(f"  Warning: Forecast generation failed: {e}")

    # --- 7. Summary Interpretation ---
    print(f"\n{'=' * 70}")
    print(f"  INTERPRETATION")
    print(f"{'=' * 70}")

    # Refuse to narrate a model whose parameters failed their own significance
    # tests. Reporting "GARCH effect dominates" off a beta with p=0.23 dresses
    # noise up as a volatility regime -- the reader has no way to tell the
    # difference from the prose alone.
    try:
        pvals = {k: float(v) for k, v in res.pvalues.items()}
        vol_params = {k: pvals[k] for k in ("omega", "alpha[1]", "beta[1]") if k in pvals}
        insignificant = [k for k, p in vol_params.items() if p >= 0.05]
    except Exception:
        vol_params, insignificant = {}, []

    if insignificant or (not np.isnan(arch_lm_p) and arch_lm_p >= 0.05):
        print("  MODEL NOT INTERPRETABLE — findings suppressed.")
        if not np.isnan(arch_lm_p) and arch_lm_p >= 0.05:
            print(f"    - ARCH LM pre-test p={arch_lm_p:.3f}: no volatility clustering to model.")
        if insignificant:
            detail = ", ".join(f"{k} p={vol_params[k]:.3f}" for k in insignificant)
            print(f"    - Volatility parameters not significant at 5%: {detail}")
        print()
        print("  The parameter table above is still shown so you can see the fit, but")
        print("  no directional read (persistence regime, ARCH vs GARCH dominance,")
        print("  mean-reversion direction) can be supported by it. The forecast is")
        print("  effectively a flat line at the unconditional vol and carries no")
        print("  information beyond that level.")
        print(f"\n{'=' * 70}\n")
        return res

    if np.isnan(persistence):
        print("  • Persistence calculation failed.")
    elif persistence > 0.99:
        print(f"  • Volatility persistence is VERY HIGH (α+β = {persistence:.4f}) -> Shocks decay slowly")
    elif persistence > 0.95:
        print(f"  • Volatility persistence is HIGH (α+β = {persistence:.4f}) -> Shocks persist")
    else:
        print(f"  • Volatility persistence is MODERATE (α+β = {persistence:.4f})")
    if alpha > beta:
        print(f"  • ARCH effect dominates (α={alpha:.4f} > β={beta:.4f}) -> vol reacts strongly to new shocks")
    else:
        print(f"  • GARCH effect dominates (β={beta:.4f} > α={alpha:.4f}) -> vol is driven by past vol")
    current_vol_val = _to_float(cond_vol.iloc[-1]) if not cond_vol.empty else np.nan
    if not np.isnan(current_vol_val) and not np.isnan(uncond_vol):
        print(f"  • Current cond. vol: {current_vol_val:.2%} vs Uncond. vol: {uncond_vol:.2%}")
        if current_vol_val > uncond_vol:
            print(f"    -> Current vol is ABOVE unconditional -> expected to mean-revert DOWNWARD")
        else:
            print(f"    -> Current vol is BELOW unconditional -> expected to mean-revert UPWARD")
    print(f"  • t-distribution df = {nu:.2f} -> {'Heavy' if nu < 8 else 'Near-normal'} tails")
    print(f"  • Model fit: AIC={res.aic:.2f}, BIC={res.bic:.2f} (lower is better)")
    print(f"\n{'=' * 70}\n")
    return res


def run_garch_module(ticker: str, start: str = DEFAULT_START, end: str = None, output_dir: str = None) -> tuple:
    """Wrapper that sets VS_OUTPUT_DIR and runs run_garch_analysis.

    Returns ``(output_files, interpretation_text, annualized_conditional_vol)``.

    The third element is the *current* GARCH conditional volatility, annualized
    and expressed as a decimal fraction (0.31 == 31%). It used to exist only as
    a console print inside run_garch_analysis, which meant every downstream
    consumer -- notably the VaR Monte Carlo -- had to re-fit the model or fall
    back to a hardcoded guess. It is ``None`` when the fit failed or the result
    object carried no conditional volatility series.
    """
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    os.environ['VS_OUTPUT_DIR'] = out_dir
    try:
        res = run_garch_analysis(ticker, start=start, end=end)
    except Exception as e:
        print(f"  GARCH module failed: {e}")
        return [], f"Ticker: {ticker} - GARCH analysis failed: {e}", None

    # Collect files generated for this ticker in out_dir
    files = []
    for fn in os.listdir(out_dir):
        if fn.startswith(f"{ticker}_garch_") and (fn.lower().endswith('.png') or fn.lower().endswith('.pdf')):
            files.append(os.path.join(out_dir, fn))

    # res.conditional_volatility is daily and on the *percent* scale, because
    # run_garch_analysis fits the model on log returns scaled by 100.
    garch_conditional_vol = None
    try:
        daily_vol_pct = float(res.conditional_volatility.iloc[-1])
        garch_conditional_vol = daily_vol_pct / 100.0 * np.sqrt(ANNUALIZE)
    except Exception:
        pass

    # Build interpretation text
    try:
        alpha = float(res.params.get('alpha[1]', np.nan))
        beta = float(res.params.get('beta[1]', np.nan))
        nu = float(res.params.get('nu', np.nan))
        persistence = alpha + beta
        interp_lines = [
            f"Ticker: {ticker}",
            f"GARCH(1,1) params: alpha={alpha:.4f}, beta={beta:.4f}, persistence={persistence:.4f}",
            f"t-distribution df: {nu:.2f}",
        ]
        if garch_conditional_vol is not None:
            interp_lines.append(f"Annualized conditional vol: {garch_conditional_vol:.2%}")
    except Exception:
        interp_lines = [f"Ticker: {ticker} - GARCH analysis completed."]
    interp = "\n".join(interp_lines)
    return files, interp, garch_conditional_vol


def main():
    print("=" * 60)
    print("  GARCH VOLATILITY ANALYSIS")
    print("=" * 60)
    ticker = input("\nEnter ticker: ").strip().upper() or "SPY"
    start = input(f"Start date (YYYY-MM-DD, default {DEFAULT_START} = {DEFAULT_YEARS}y): ").strip()
    if not start:
        start = DEFAULT_START
    try:
        run_garch_analysis(ticker, start=start)
    except Exception as e:
        print(f"\n  Error: {e}")
        import traceback
        traceback.print_exc()
    print("\n  Done.\n")


if __name__ == "__main__":
    main()