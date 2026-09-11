#!/usr/bin/env python3
# vrp_term_structure.py
# Variance Risk Premium term structure across multiple tenors (1mo~12mo)
# for a single ticker, showing fair vol, ATM IV, VRP, and trailing RV at
# each tenor.

import math
import os
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime

import matplotlib
import numpy as np

matplotlib.use("Agg")
import expiry_selector
import matplotlib.pyplot as plt
from correlation_engine import fetch_price_history
from thetadata_client import ThetaDataController

# Reuse core functions from the existing live module -- these are the same
# arithmetic (static replication, forward price, half-width weights) used by
# every other component in Vol_Suite.
from variance_swap_live import (
    compute_fair_variance_strike,
    compute_realized_vol,
    fetch_chain_thetadata,
)
from vs_utils import timestamped_output_dir

warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

# ---------------------------------------------------------------------------
# Tenors to evaluate -- expressed as (label, target_years) pairs.
# The target_years feeds into expiry_selector.nearest_expiry, which uses a
# DEFAULT_A=365 calendar-day convention to find the closest listed option
# expiration for each target.
# ---------------------------------------------------------------------------
TARGET_TENORS: list[tuple[str, float]] = [
    ("1mo", 1.0 / 12.0),
    ("3mo", 0.25),
    ("6mo", 0.5),
    ("12mo", 1.0),
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VrpTermPoint:
    """A single point on the VRP term structure for one expiry/tenor."""

    expiry_label: str  # e.g. "1mo", "3mo"
    expiry_date: str  # actual expiry date resolved, e.g. "20260717"
    T_years: float  # actual time-to-maturity in years
    fair_vol_pct: float  # fair variance swap strike vol (%)
    atm_iv_pct: float  # ATM implied volatility (%)
    # fair_vol_pct - atm_iv_pct. BOTH SIDES IMPLIED, so this is the CONVEXITY
    # premium, not the volatility risk premium.
    #
    # This field was called `vrp_pct` until 2026-09-11, which collided with a
    # genuinely different quantity of the same name elsewhere in this repo:
    #
    #   here (was vrp_pct):   fair_vol_pct - atm_iv_pct   (both implied)
    #   screener / live:      fair_vol_pct - realized     (the textbook VRP)
    #
    # variance_swap_screener.py and variance_swap_live.py publish exactly this
    # quantity under `convexity_premium_vol_pct` / `convexity_pct` and reserve
    # "VRP" for fair-minus-realized -- so this module's "VRP" was the siblings'
    # "convexity", and the two are routinely read side by side. Renamed to match
    # the rest of the repo. See PROJECT_AUDIT_AND_SPEC.md finding #4.
    convexity_pct: float
    rv_30d_pct: float  # trailing 30-day realized vol (%), annualized
    # The textbook VRP: fair_vol_pct - rv_30d_pct, which is what the rest of
    # this repo means by VRP and what this module never actually computed.
    # NaN when realized vol is unavailable -- never 0.0, which would read as a
    # measured "fair vol equals realized vol".
    vrp_vs_realized_pct: float = float("nan")
    model_implied_vrp_pct: float | None = (
        None  # cross-check against fair_vol_pct via a jump-diffusion model
    )


@dataclass
class VrpTermStructureResult:
    """Full VRP term structure for one ticker at one timestamp."""

    ticker: str
    timestamp: str  # ISO-format timestamp of computation
    points: list[VrpTermPoint]  # one per tenor, ordered short→long
    shape: str  # "flat" | "upward" | "downward" | "humped"
    chart_path: str | None = None  # path to saved plot, if generated


# ---------------------------------------------------------------------------
# Shape classification
# ---------------------------------------------------------------------------


def _classify_term_structure(points: list[VrpTermPoint]) -> str:
    """Classify the VRP term structure shape based on VRP values across tenors.

    Rules:
      - If fewer than 3 valid points → 'flat' (insufficient data to classify)
      - If max VRP - min VRP < 2.0 pct pts → 'flat'
      - If strictly increasing (each next VRP >= previous) → 'upward'
      - If strictly decreasing (each next VRP <= previous) → 'downward'
      - Otherwise → 'humped'
    """
    valid = [
        p for p in points
        if not (math.isnan(p.convexity_pct) or math.isinf(p.convexity_pct))
    ]
    if len(valid) < 3:
        return "flat"

    vrps = [p.convexity_pct for p in valid]

    if max(vrps) - min(vrps) < 2.0:
        return "flat"

    # Check monotonicity
    increasing = all(vrps[i] <= vrps[i + 1] for i in range(len(vrps) - 1))
    decreasing = all(vrps[i] >= vrps[i + 1] for i in range(len(vrps) - 1))

    if increasing:
        return "upward"
    elif decreasing:
        return "downward"
    else:
        return "humped"


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def compute_vrp_term_structure(
    ticker: str,
    td: ThetaDataController,
    spot: float,
    r: float,
    q: float,
    jump_model_cls=None,
) -> VrpTermStructureResult:
    """Compute the VRP term structure for *ticker* across multiple tenors.

    Parameters
    ----------
    ticker : str
        Equity ticker symbol (e.g. "SPY").
    td : ThetaDataController
        Connected ThetaData client used to list expirations and fetch chains.
    spot : float
        Current spot price.
    r : float
        Risk-free rate (decimal, e.g. 0.05).
    q : float
        Dividend yield (decimal, e.g. 0.0).

    Returns
    -------
    VrpTermStructureResult
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch price history once for RV computations across all tenors
    prices: np.ndarray = np.array([])
    try:
        hist_df = fetch_price_history([ticker], period="2y")
        prices = hist_df[ticker].values.flatten()
    except Exception:
        pass

    points: list[VrpTermPoint] = []

    for label, target_years in TARGET_TENORS:
        try:
            # 1) Resolve the nearest listed expiry for this target tenor
            expiry_str, actual_T = expiry_selector.nearest_expiry(
                td, ticker, target_years
            )

            # 2) Fetch the option chain
            chain = fetch_chain_thetadata(td, ticker, expiry_str, r, q)

            # 3) Compute fair vol and ATM IV via static replication
            result = compute_fair_variance_strike(chain, spot, actual_T)

            fair_vol_pct = result["fair_variance_swap_strike_vol_pct"]
            atm_iv_pct = result["atm_implied_vol_pct"]
            # Fair-minus-ATM-implied. This is the CONVEXITY premium; the rest
            # of the repo reserves "VRP" for fair-minus-REALIZED (computed
            # below as vrp_vs_realized). The old comment here read
            # "convexity premium = VRP at this tenor", equating two things
            # the sibling modules deliberately keep apart.
            vrp = fair_vol_pct - atm_iv_pct

            model_implied_vrp_pct = None
            if jump_model_cls is not None:
                try:
                    from jump_diffusion.calibration import calibrate

                    jd_result = calibrate(jump_model_cls, chain, spot, actual_T)
                    # ATM fitted IV is the model's own read on the ATM point --
                    # closest strike to spot in the calibrated chain.
                    atm_idx = int(np.argmin(np.abs(jd_result.strikes - spot)))
                    model_iv_pct = float(jd_result.fitted_ivs[atm_idx]) * 100
                    model_implied_vrp_pct = model_iv_pct - atm_iv_pct
                except Exception as exc:
                    print(
                        f"  [jump_diffusion] VRP tie calibration failed for {label}: {exc}"
                    )

            # 4) Trailing 30-day realized vol
            rv_val = (
                compute_realized_vol(prices, min(30, len(prices)))
                if len(prices) >= 5
                else float("nan")
            )
            rv_val_pct = rv_val * 100.0 if not math.isnan(rv_val) else float("nan")

            points.append(
                VrpTermPoint(
                    expiry_label=label,
                    expiry_date=expiry_str,
                    T_years=actual_T,
                    fair_vol_pct=fair_vol_pct,
                    atm_iv_pct=atm_iv_pct,
                    convexity_pct=vrp,
                    rv_30d_pct=rv_val_pct,
                    vrp_vs_realized_pct=(
                        float("nan")
                        if math.isnan(rv_val_pct)
                        else fair_vol_pct - rv_val_pct
                    ),
                    model_implied_vrp_pct=model_implied_vrp_pct,
                )
            )
        except Exception:
            # If a tenor fails (no expiry, no chain, or computation error),
            # record an NaN point so the caller can still see the gap in the
            # term structure.
            points.append(
                VrpTermPoint(
                    expiry_label=label,
                    expiry_date="",
                    T_years=float("nan"),
                    fair_vol_pct=float("nan"),
                    atm_iv_pct=float("nan"),
                    convexity_pct=float("nan"),
                    rv_30d_pct=float("nan"),
                )
            )

    shape = _classify_term_structure(points)

    return VrpTermStructureResult(
        ticker=ticker,
        timestamp=timestamp,
        points=points,
        shape=shape,
    )


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def plot_vrp_term_structure(result: VrpTermStructureResult, path: str) -> str:
    """Generate a matplotlib chart showing VRP across tenors, saved to *path*.

    Returns *path* on success.
    """
    labels = [p.expiry_label for p in result.points]
    fair_vols = [p.fair_vol_pct for p in result.points]
    atm_ivs = [p.atm_iv_pct for p in result.points]
    vrps = [p.convexity_pct for p in result.points]
    rvs = [p.rv_30d_pct for p in result.points]

    x = np.arange(len(labels))
    width = 0.20

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 10), gridspec_kw={"height_ratios": [2, 1]}
    )
    fig.suptitle(
        f"{result.ticker} VRP Term Structure — {result.timestamp[:10]}  (shape: {result.shape})",
        fontsize=14,
        fontweight="bold",
    )

    # --- Top panel: vol levels ---
    ax1.bar(
        x - 1.5 * width, fair_vols, width, label="Fair Vol", color="#2196F3", alpha=0.85
    )
    ax1.bar(
        x - 0.5 * width, atm_ivs, width, label="ATM IV", color="#FF9800", alpha=0.85
    )
    ax1.bar(x + 0.5 * width, rvs, width, label="RV (30d)", color="#4CAF50", alpha=0.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("Vol (%)")
    ax1.set_title("Volatility Levels by Tenor")
    ax1.legend(fontsize=9)
    ax1.grid(axis="y", alpha=0.3)

    # Annotate values on bars (fair vol only, keeps the chart readable)
    for i, (fv, ai, rv) in enumerate(zip(fair_vols, atm_ivs, rvs)):
        if not math.isnan(fv):
            ax1.text(
                i - 1.5 * width,
                fv + 0.3,
                f"{fv:.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
                color="#1565C0",
            )
        if not math.isnan(ai):
            ax1.text(
                i - 0.5 * width,
                ai + 0.3,
                f"{ai:.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
                color="#E65100",
            )
        if not math.isnan(rv):
            ax1.text(
                i + 0.5 * width,
                rv + 0.3,
                f"{rv:.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
                color="#1B5E20",
            )

    # --- Bottom panel: convexity-premium bar chart ---
    # Labelled "Convexity", not "VRP". The series is fair_vol - ATM IV, both
    # implied; the rest of the repo calls fair-minus-REALIZED the VRP. Axis
    # and title used to read "VRP"/"Variance Risk Premium", which put the
    # wrong name on the number a reader takes off the chart.
    colors = []
    for v in vrps:
        if math.isnan(v):
            colors.append("#CCCCCC")
        elif v > 0:
            colors.append("#e74c3c")  # fair > IV = short vol pays
        else:
            colors.append("#2ecc71")  # fair < IV = long vol pays

    bars = ax2.bar(
        x, vrps, width * 2.5, color=colors, alpha=0.8, edgecolor="#333", linewidth=0.5
    )
    ax2.axhline(y=0, color="gray", linestyle="-", linewidth=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels)
    ax2.set_ylabel("Convexity (vol pts)")
    ax2.set_title("Convexity Premium (Fair Vol − ATM IV)")
    ax2.grid(axis="y", alpha=0.3)

    # Annotate convexity values
    for i, (bar, v) in enumerate(zip(bars, vrps)):
        if not math.isnan(v):
            y_pos = bar.get_height() + (0.3 if v >= 0 else -0.3)
            va = "bottom" if v >= 0 else "top"
            ax2.text(
                i, y_pos, f"{v:+.1f}", ha="center", va=va, fontsize=9, fontweight="bold"
            )

    # Shape annotation in bottom-right of bottom panel
    ax2.text(
        0.95,
        0.95,
        f"Shape: {result.shape}",
        transform=ax2.transAxes,
        ha="right",
        va="top",
        fontsize=11,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#FFF9C4", alpha=0.9),
    )

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return path


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------


def main() -> None:
    """Interactive entry point for term structure analysis."""
    print("=" * 60)
    print("VRP TERM STRUCTURE ANALYZER")
    print("=" * 60)

    ticker = input("Enter ticker: ").strip().upper() or "SPY"

    print("\nConnecting to ThetaData...")
    td = ThetaDataController()
    try:
        q = td.fetch_dividend_yield(ticker)
        r_live = td.fetch_risk_free_rate(0.25)
        r = r_live if r_live is not None else 0.05
        spot = td.fetch_spot_price(ticker)
        print(f"  {ticker}: Spot={spot:.2f}, r={r:.4f}, q={q:.4f}")

        print("\nComputing VRP term structure...")
        result = compute_vrp_term_structure(ticker, td, spot, r, q)
    finally:
        td.close()

    print(
        f"\n{'Tenor':<6} {'Expiry':<10} {'T(yr)':<8} {'FairVol%':<10} {'ATM IV%':<10} {'Conv%':<10} {'RV30%':<10} {'VRPvRV%':<10}"
    )
    print("-" * 76)
    for p in result.points:
        fv = f"{p.fair_vol_pct:.2f}" if not math.isnan(p.fair_vol_pct) else "N/A"
        av = f"{p.atm_iv_pct:.2f}" if not math.isnan(p.atm_iv_pct) else "N/A"
        vp = f"{p.convexity_pct:+.2f}" if not math.isnan(p.convexity_pct) else "N/A"
        rv = f"{p.rv_30d_pct:.2f}" if not math.isnan(p.rv_30d_pct) else "N/A"
        ty = f"{p.T_years:.4f}" if not math.isnan(p.T_years) else "N/A"
        # Both premia side by side, each under its own name: Conv% is
        # fair-minus-ATM-implied, VRPvRV% is the textbook fair-minus-realized
        # this module previously reported for neither.
        vr = (
            f"{p.vrp_vs_realized_pct:+.2f}"
            if not math.isnan(p.vrp_vs_realized_pct)
            else "N/A"
        )
        print(
            f"{p.expiry_label:<6} {p.expiry_date:<10} {ty:<8} {fv:<10} "
            f"{av:<10} {vp:<10} {rv:<10} {vr:<10}"
        )
    print(f"\nTerm Structure Shape: {result.shape}")

    out_dir = os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    chart_path = os.path.join(out_dir, f"{ticker}_vrp_term_structure_{ts}.png")

    print("\nGenerating chart...")
    chart_path = plot_vrp_term_structure(result, chart_path)
    result.chart_path = chart_path
    print(f"  Saved: {chart_path}")
    print("Done.")


if __name__ == "__main__":
    main()
