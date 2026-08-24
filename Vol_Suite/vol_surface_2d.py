#!/usr/bin/env python3
"""vol_surface_2d.py

A full 2D implied volatility surface module that interpolates across BOTH
strike and tenor.  Builds on the per-expiry smile-fitter pattern from
vol_surface_reference.py (quadratic in log-moneyness) and extends it across
the time dimension via linear interpolation in total-variance space.

Workflow
--------
1. Fetch every available expiry for a ticker via ThetaDataController.
2. Per expiry: fit a quadratic smile ``iv = a*x² + b*x + c``, where
   ``x = ln(K / spot)``, anchored on near-ATM strikes (same approach as
   vol_surface_reference's quadratic fallback).
3. Store the fitted (a, b, c) triplet for each distinct tenor.
4. ``VolSurface.iv(strike, tenor)`` returns the interpolated IV at any
   (strike, tenor) pair by:
   - evaluating the two nearest-expiry smiles at the requested strike,
   - linearly interpolating the resulting IVs in total-variance space
     (``total_var = iv² × T``), and
   - converting back to an annualised IV.

   Extrapolation beyond the observed tenor range uses nearest-neighbour
   clamping so that the surface never produces negative or absurd values.

Design notes
------------
- Deliberately lightweight: no SABR across tenors, no stochastic vol
  backbone.  The quadratic-per-expiry + linear-total-variance model is
  the simplest 2D surface that doesn't produce the kind of
  extrapolation blow-up a bare 2D polynomial would.
- All IV values are expressed as decimals (e.g. 0.25 for 25 %).
- Tenors are always in years (calendar days / 365).
"""

import math
import os
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

# ---------------------------------------------------------------------------
# I/O data structures
# ---------------------------------------------------------------------------

EXPIRY_DATE_FMT = "%Y%m%d"
DAYS_PER_YEAR = 365.0
NEAR_ATM_BAND = 0.15  # log-moneyness band for quadratic fit (±15 %)
MIN_FIT_POINTS = 5  # min near-ATM points to trust a per-expiry fit
MIN_TENORS = 2  # need at least 2 tenors for any interp
MAX_EXPIRIES = 48  # safety cap on how many expiries we fetch


@dataclass
class VolSurfacePoint:
    """A single point on the 2D vol surface."""

    strike: float
    tenor: float  # years
    iv: float  # annualised implied volatility (decimal)


@dataclass
class VolSurface:
    """Interpolated 2D implied volatility surface.

    Parameters
    ----------
    ticker : str
        Underlying symbol.
    timestamp : datetime
        Snapshot time of the data used to build the surface.
    points : List[VolSurfacePoint]
        Raw (strike, tenor, iv) triples the surface was built from.
    fitted_params : dict
        Calibrated parameters used by ``iv()``.  Shape::

            {
                'spot': float,          # underlying price
                'tenors': [float, ...],  # sorted unique tenors (years)
                'coeffs': [[a,b,c], ...],   # per-tenor quadratic coeffs
                'fitter': str,
            }

    Methods
    -------
    iv(strike, tenor) -> float
        Interpolate / extrapolate the surface at any (strike, tenor).
    """

    ticker: str
    timestamp: datetime
    points: list[VolSurfacePoint] = field(default_factory=list)
    fitted_params: dict = field(default_factory=dict)

    # ---- cached interpolation helpers (populated lazily) ----
    _tenor_array: np.ndarray | None = field(default=None, repr=False)
    _coeff_array: np.ndarray | None = field(default=None, repr=False)  # shape (N, 3)

    def _ensure_cache(self) -> None:
        if self._tenor_array is not None:
            return
        tenors = self.fitted_params.get("tenors", [])
        coeffs = self.fitted_params.get("coeffs", [])
        if len(tenors) != len(coeffs) or len(tenors) == 0:
            self._tenor_array = np.array([])
            self._coeff_array = np.empty((0, 3))
            return
        self._tenor_array = np.array(tenors, dtype=float)
        self._coeff_array = np.array(coeffs, dtype=float)

    def _quadratic_iv(self, coeffs: np.ndarray, log_m: float) -> float:
        """Evaluate a*x² + b*x + c at log-moneyness *x*."""
        a, b, c = coeffs
        return float(a * log_m * log_m + b * log_m + c)

    def iv(self, strike: float, tenor: float) -> float:
        """Interpolate implied volatility at the requested (strike, tenor).

        Interpolation logic
        -------------------
        1. If *tenor* falls within the observed tenor range, find the two
           bracketing tenors (lower, upper).
        2. Evaluate each bracket's fitted smile at *strike* → two IVs.
        3. Linearly interpolate in TOTAL-VARIANCE space
           (``total_var = iv² × T``) between the two bracketing IVs.
        4. Convert back to annualised IV: ``sqrt(total_var / T)``.
        5. If *tenor* is outside the observed range, clamp to the nearest
           observed tenor (nearest-neighbour extrapolation).

        Returns
        -------
        float
            Annualised implied volatility (decimal).  Guaranteed >= 0.0.
        """
        spot = self.fitted_params.get("spot")
        if not spot or spot <= 0:
            return 0.0
        self._ensure_cache()
        if self._tenor_array is None or len(self._tenor_array) < MIN_TENORS:
            return 0.0

        log_m = math.log(strike / spot)
        t_arr = self._tenor_array
        c_arr = self._coeff_array

        # --- nearest-neighbour extrapolation ---
        if tenor <= t_arr[0]:
            return max(self._quadratic_iv(c_arr[0], log_m), 0.0)
        if tenor >= t_arr[-1]:
            return max(self._quadratic_iv(c_arr[-1], log_m), 0.0)

        # --- bracketed interpolation in total-variance space ---
        idx = int(np.searchsorted(t_arr, tenor, side="right") - 1)
        T_lo, T_hi = float(t_arr[idx]), float(t_arr[idx + 1])
        iv_lo = self._quadratic_iv(c_arr[idx], log_m)
        iv_hi = self._quadratic_iv(c_arr[idx + 1], log_m)

        # Total variance at each bracket tenor
        tv_lo = iv_lo * iv_lo * T_lo
        tv_hi = iv_hi * iv_hi * T_hi

        # Linearly interpolate total variance at target tenor
        if T_hi - T_lo < 1e-12:
            iv_at_target = 0.5 * (iv_lo + iv_hi)
        else:
            lam = (tenor - T_lo) / (T_hi - T_lo)
            tv_t = tv_lo + lam * (tv_hi - tv_lo)
            iv_at_target = math.sqrt(max(tv_t / tenor, 0.0))

        return max(iv_at_target, 0.0)


# ---------------------------------------------------------------------------
# Fitting helpers
# ---------------------------------------------------------------------------


def _log_moneyness(strike: float, spot: float) -> float:
    return math.log(strike / spot)


def _expiry_to_tenor(exp_str: str, ref_date: datetime | None = None) -> float:
    """Convert a YYYYMMDD expiry string to years from *ref_date* (default now)."""
    exp_date = datetime.strptime(exp_str, EXPIRY_DATE_FMT)
    ref = ref_date if ref_date is not None else datetime.now()
    delta_days = (exp_date - ref).days
    return max(delta_days, 1) / DAYS_PER_YEAR


def _fit_quadratic_smile(
    strikes: list[float],
    ivs: list[float],
    spot: float,
) -> tuple[float, float, float] | None:
    """Fit ``iv = a*x² + b*x + c``, ``x = ln(K/spot)``, using only near-ATM
    points (within ±NEAR_ATM_BAND in log-moneyness).

    Returns ``(a, b, c)`` or *None* if too few near-ATM points exist.
    """
    xs, ys = [], []
    for k, iv in zip(strikes, ivs):
        if iv <= 0:
            continue
        x = _log_moneyness(k, spot)
        if abs(x) <= NEAR_ATM_BAND:
            xs.append(x)
            ys.append(iv)
    if len(xs) < MIN_FIT_POINTS:
        return None
    coeffs = np.polyfit(xs, ys, deg=2)
    return (float(coeffs[0]), float(coeffs[1]), float(coeffs[2]))


# ---------------------------------------------------------------------------
# Surface builder
# ---------------------------------------------------------------------------


def build_surface(
    ticker: str,
    td: "ThetaDataController",  # noqa: F821  (quoted for forward compat)
    ref_date: datetime | None = None,
    spot_override: float | None = None,
) -> VolSurface | None:
    """Build a 2D implied-vol surface for *ticker* by fetching every available
    expiry from ThetaData, fitting a quadratic smile per expiry, and storing
    the calibrated parameters for strike × tenor interpolation.

    Parameters
    ----------
    ticker : str
        Underlying ticker symbol.
    td : ThetaDataController
        Authenticated ThetaData client.
    ref_date : datetime or None
        Reference date for tenor calculation (default: ``datetime.now()``).
    spot_override : float or None
        If given, use this as the spot price instead of fetching it from
        ThetaData.

    Returns
    -------
    VolSurface or None
        *None* if fewer than *MIN_TENORS* tenors could be fitted (no
        surface possible).
    """
    ref = ref_date if ref_date is not None else datetime.now()

    # --- spot ---
    if spot_override is not None and spot_override > 0:
        spot = spot_override
    else:
        spot = td.fetch_spot_price(ticker)
    if not spot or spot <= 0:
        return None

    # --- list available expiries ---
    all_exps = td.list_expirations(ticker)
    if not all_exps:
        return None
    # Drop expired/past dates BEFORE sorting + truncating, so a stale
    # expiry never occupies one of the MAX_EXPIRIES slots ahead of a
    # valid future one.
    all_exps = [e for e in all_exps if _expiry_to_tenor(e, ref) > 0]
    if not all_exps:
        return None
    # Keep the nearest N (cap to avoid excessive network calls)
    all_exps = sorted(all_exps)[:MAX_EXPIRIES]

    # --- collect per-expiry smiles ---
    all_points: list[VolSurfacePoint] = []
    tenors: list[float] = []
    coeffs_list: list[tuple[float, float, float]] = []

    for exp_str in all_exps:
        tenor = _expiry_to_tenor(exp_str, ref)
        if tenor <= 0:
            continue

        # Fetch bulk greeks for this expiry
        try:
            greeks = td.option_bulk_greeks(ticker, exp_str)
        except Exception:
            continue  # skip expiries that fail

        strikes: list[float] = []
        ivs: list[float] = []
        for row in greeks:
            try:
                k = float(row.get("strike", 0)) / 1000.0  # theta int → dollar
                iv = float(row.get("implied_vol", 0.0))
            except (ValueError, TypeError):
                continue
            if k <= 0 or iv <= 0:
                continue
            strikes.append(k)
            ivs.append(iv)
            all_points.append(VolSurfacePoint(strike=k, tenor=tenor, iv=iv))

        if len(strikes) < MIN_FIT_POINTS:
            continue

        coeffs = _fit_quadratic_smile(strikes, ivs, spot)
        if coeffs is None:
            continue

        tenors.append(tenor)
        coeffs_list.append(coeffs)

    # --- finalise ---
    if len(tenors) < MIN_TENORS:
        return None

    # Sort by tenor
    combined = sorted(zip(tenors, coeffs_list), key=lambda x: x[0])
    tenors_sorted = [c[0] for c in combined]
    coeffs_sorted = [c[1] for c in combined]

    fitted_params = {
        "spot": spot,
        "tenors": tenors_sorted,
        "coeffs": coeffs_sorted,
        "fitter": "quadratic_per_expiry",
    }

    surface = VolSurface(
        ticker=ticker,
        timestamp=ref,
        points=all_points,
        fitted_params=fitted_params,
    )
    return surface


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def plot(surface: VolSurface, path: str) -> str:
    """Generate a 3D surface plot of implied volatility across strike × tenor.

    A dense grid is evaluated via ``surface.iv(strike, tenor)`` so the plot
    reflects the interpolation/extrapolation logic, not just the raw points.

    Parameters
    ----------
    surface : VolSurface
        The calibrated surface to visualise.
    path : str
        Absolute or relative file path for the saved image (PNG suggested).

    Returns
    -------
    str
        The *path* the plot was saved to (same as input, for call-chaining).
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 (registers 3D projection)

    # --- determine grid bounds ---
    spot = surface.fitted_params.get("spot", 100.0)
    tenors_arr = surface.fitted_params.get("tenors", [])
    if not tenors_arr:
        # No calibration — nothing to plot
        return path

    min_ten = max(0.0, min(tenors_arr))
    max_ten = max(tenors_arr) * 1.15 + 1e-6

    # Strike range: ±40 % of spot (generous)
    min_strike = spot * 0.60
    max_strike = spot * 1.40

    # --- build dense evaluation grid ---
    n_strikes = 60
    n_tenors = 40

    K_grid = np.linspace(min_strike, max_strike, n_strikes)
    T_grid = np.linspace(min_ten, max_ten, n_tenors)
    KK, TT = np.meshgrid(K_grid, T_grid)
    IVV = np.zeros_like(KK)

    for i in range(n_tenors):
        for j in range(n_strikes):
            IVV[i, j] = surface.iv(float(KK[i, j]), float(TT[i, j]))

    # --- plot ---
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection="3d")

    surf_plot = ax.plot_surface(
        KK,
        TT,
        IVV,
        cmap="viridis",
        edgecolor="none",
        alpha=0.92,
    )

    # Overlay raw data points
    raw_ks = [p.strike for p in surface.points]
    raw_ts = [p.tenor for p in surface.points]
    raw_iv = [p.iv for p in surface.points]
    ax.scatter(raw_ks, raw_ts, raw_iv, color="red", s=8, alpha=0.4, label="Raw points")

    ax.set_xlabel("Strike ($)")
    ax.set_ylabel("Tenor (years)")
    ax.set_zlabel("Implied Vol")
    ax.set_title(f"{surface.ticker} 2D Vol Surface  ({surface.timestamp.date()})")
    fig.colorbar(surf_plot, ax=ax, shrink=0.6, aspect=20, label="IV")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return path
