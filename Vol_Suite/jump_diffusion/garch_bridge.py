"""Two ties between the jump-diffusion model zoo and Vol_Suite's existing
GARCH(1,1) fit (garch_analysis.py):

1. jump_filtered_returns -- flag/winsorize jump-attributable days in the
   historical return series *before* GARCH sees them, using the current
   Merton-implied diffusive vol as a measure-consistent threshold scale (a
   naive rolling-std threshold is contaminated by the jumps it's trying to
   detect).
2. adjust_garch_forecast -- post-hoc scale the GARCH conditional-vol
   forecast by the Bates jump-variance share. NOTE: this is a forecast
   adjustment, not a re-estimated GARCH-X historical fit -- a true
   GARCH-X exogenous-regressor fit would need a *daily historical* series
   of jump-variance-share, which would require calibrating Bates against a
   full option chain for every historical day (not available in this
   pipeline, which only has today's chain). This is a deliberate scope
   narrowing from the design spec's literal "GARCH-X variance equation"
   language -- see design spec's Architecture section.
"""

import numpy as np


def jump_filtered_returns(
    log_returns: np.ndarray, merton_sigma: float, k: float = 4.0
) -> tuple:
    """Flag days where |return| exceeds k * daily-sigma (implied by
    merton_sigma, an annualized vol) as jump days, and replace them with the
    threshold value (winsorize, sign-preserved) so GARCH's fit reflects
    diffusive clustering only. Returns (filtered_returns, jump_day_mask).
    """
    log_returns = np.asarray(log_returns, dtype=float)
    daily_sigma = merton_sigma / np.sqrt(252)
    threshold = k * daily_sigma
    mask = np.abs(log_returns) > threshold
    filtered = log_returns.copy()
    filtered[mask] = np.sign(log_returns[mask]) * threshold
    return filtered, mask


def adjust_garch_forecast(
    garch_conditional_vol: float, jump_variance_share: float, gamma: float = 0.25
) -> float:
    """Scale a GARCH conditional-vol forecast upward by the option-implied
    jump-variance share. gamma is a tunable sensitivity (default 0.25 means
    a jump_variance_share of 1.0 -- all variance is jump-attributable --
    scales the forecast up 25%); this is a heuristic starting point, not a
    fitted coefficient, and is expected to be tuned once live data exists.
    """
    return garch_conditional_vol * (1.0 + gamma * jump_variance_share)
