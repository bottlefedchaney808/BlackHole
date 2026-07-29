"""price_dist.py — Price Distribution Analysis + Probability Calculator
Replicates VaRtools sheets 'Price Distribution', 'Spot at any time', 'MC Sim'.

Three tools in one:
  1. price_distribution() — skewness/kurtosis/normality tests on a price series
  2. prob_at_expiry()     — lognormal probability of hitting a price AT expiry
  3. prob_any_time()      — probability of touching a barrier AT ANY TIME during T
                            (reflection principle / first-passage-time formula)
  4. mc_probabilities()   — full MC prob engine (above/below/touching/between targets)
  5. lognormal_dist()     — analytic lognormal price distribution table

All these run on prices you give them — no network calls.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Tuple, List
from scipy.stats import norm, skew, kurtosis, t as student_t


# ══════════════════════════════════════════════════════════════════════════════
# 1. Price Distribution Analysis
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PriceDistResults:
    n:                  int
    mean_return:        float
    std_return:         float     # daily
    annual_vol:         float
    skewness:           float
    excess_kurtosis:    float
    se_skewness:        float
    se_kurtosis:        float
    t_ratio_skewness:   float
    t_ratio_kurtosis:   float
    skew_normal:        bool      # True if NOT significantly different from normal
    kurt_normal:        bool


def price_distribution(prices: np.ndarray, trading_days: float = 252.0) -> PriceDistResults:
    """Compute skewness/kurtosis statistics from a price series."""
    lr  = np.diff(np.log(prices))
    n   = len(lr)
    mu  = float(lr.mean())
    s   = float(lr.std(ddof=1))

    sk  = float(skew(lr))
    ek  = float(kurtosis(lr, fisher=True))   # excess kurtosis (normal=0)

    se_sk = float(np.sqrt(6*n*(n-1) / ((n-2)*(n+1)*(n+3))))
    se_ku = float(2 * se_sk * np.sqrt((n**2-1) / ((n-3)*(n+5))))

    t_sk = sk / se_sk if se_sk > 0 else 0.0
    t_ku = ek / se_ku if se_ku > 0 else 0.0

    # significant at 5% (2-tail) if |t| > 1.96
    return PriceDistResults(
        n              = n,
        mean_return    = mu,
        std_return     = s,
        annual_vol     = s * np.sqrt(trading_days),
        skewness       = sk,
        excess_kurtosis= ek,
        se_skewness    = se_sk,
        se_kurtosis    = se_ku,
        t_ratio_skewness = t_sk,
        t_ratio_kurtosis = t_ku,
        skew_normal    = abs(t_sk) < 1.96,
        kurt_normal    = abs(t_ku) < 1.96,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Probability at expiry (lognormal)
# ══════════════════════════════════════════════════════════════════════════════

def prob_at_expiry(S: float, target: float, days: float,
                   vol: float, mu: float = 0.0,
                   trading_days: float = 252.0) -> Tuple[float, float]:
    """P(S_T > target) and P(S_T < target) at expiry using lognormal.
    mu = expected annual return (drift); vol = annual volatility.
    Returns (prob_above, prob_below).
    """
    T   = days / trading_days
    if T <= 0 or vol <= 0 or S <= 0 or target <= 0:
        above = 1.0 if S > target else 0.0
        return above, 1.0 - above
    # log-normal: ln(S_T) ~ N(ln(S) + (mu-0.5σ²)T, σ²T)
    d = (np.log(S/target) + (mu - 0.5*vol**2)*T) / (vol*np.sqrt(T))
    prob_above = float(norm.cdf(d))
    prob_below = 1.0 - prob_above
    return prob_above, prob_below


# ══════════════════════════════════════════════════════════════════════════════
# 3. Probability of touching a barrier AT ANY TIME (reflection principle)
# ══════════════════════════════════════════════════════════════════════════════

def prob_touch_any_time(S: float, barrier: float, days: float,
                        vol: float, mu: float = 0.0,
                        trading_days: float = 252.0,
                        obs_per_day: float = 1.0) -> float:
    """P(S hits barrier at any point during [0,T]) using reflection principle.

    For continuous monitoring:
        P(touch above H | H > S) = N(-d1) + exp(2μT/σ²·ln(H/S)) · N(-d2)
    where d1 = [ln(S/H) + (μ+σ²/2)T] / (σ√T)
          d2 = [ln(S/H) - (μ-σ²/2)T] / (σ√T)

    obs_per_day > 1 for intraday monitoring (discrete barrier approx).
    """
    T   = days / trading_days
    if T <= 0 or vol <= 0:
        return 1.0 if S >= barrier else 0.0

    # discrete-monitoring correction (Broadie, Glasserman, Kou 1997)
    beta = 0.5826  # magic constant
    if obs_per_day > 0:
        m = obs_per_day * days
        if barrier > S:
            barrier_adj = barrier * np.exp(beta * vol * np.sqrt(T / m))
        else:
            barrier_adj = barrier * np.exp(-beta * vol * np.sqrt(T / m))
    else:
        barrier_adj = barrier

    lnSH = np.log(S / barrier_adj)
    sqT  = vol * np.sqrt(T)
    d1   = (lnSH + (mu + 0.5*vol**2)*T) / sqT
    d2   = (lnSH - (mu - 0.5*vol**2)*T) / sqT  # note sign convention

    drift_adj = np.exp(2 * mu * np.log(barrier_adj/S) / (vol**2)) if vol > 0 else 0.0

    if barrier > S:
        # prob of touching upper barrier
        p = norm.cdf(-d1) + drift_adj * norm.cdf(-d2)
    else:
        # prob of touching lower barrier
        p = norm.cdf(d1) + drift_adj * norm.cdf(d2)

    return float(np.clip(p, 0.0, 1.0))


def spot_at_probability(S: float, prob: float, days: float,
                        vol: float, mu: float = 0.0,
                        trading_days: float = 252.0,
                        prob_type: str = "E") -> float:
    """Inverse: what spot price has probability `prob` of being reached?
    prob_type = 'E' (at expiry) or 'A' (at any time during T).
    """
    T = days / trading_days
    if T <= 0 or vol <= 0:
        return S

    if prob_type.upper() == "E":
        # P(S_T > H) = prob  →  H = S * exp((mu - 0.5σ²)T + σ√T · z_p)
        z = norm.ppf(1.0 - prob)
        return float(S * np.exp((mu - 0.5*vol**2)*T + vol*np.sqrt(T)*z))

    elif prob_type.upper() == "A":
        # Numerically invert prob_touch_any_time
        from scipy.optimize import brentq
        def _obj(H):
            return prob_touch_any_time(S, H, days, vol, mu, trading_days) - prob
        try:
            lo, hi = S * 0.001, S * 100.0
            # need opposite signs
            if _obj(lo) * _obj(hi) > 0:
                # both same sign — prob too high or too low
                if _obj(hi) > 0:
                    return hi
                return lo
            return float(brentq(_obj, lo, hi, xtol=1e-4))
        except Exception:
            return float(S * np.exp(vol * np.sqrt(T) * norm.ppf(1 - prob)))

    raise ValueError(f"prob_type must be 'E' or 'A', got {prob_type!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Full MC probability engine
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MCProbInputs:
    spot:        float
    upper:       float          # upper target price
    lower:       float          # lower target price
    days:        float          # calendar days
    vol:         float          # annual vol
    mu:          float  = 0.07  # expected annual return
    div_yield:   float  = 0.0
    n_sims:      int    = 50_000
    prices_per_day: int = 1
    seed:        Optional[int] = 42
    trading_days: float = 252.0


@dataclass
class MCProbResults:
    above_upper_at_expiry:   float
    above_upper_any_time:    float
    below_lower_at_expiry:   float
    below_lower_any_time:    float
    touching_either:         float
    touching_neither:        float
    touching_both:           float
    between_at_any_time:     float   # always 1.0 if lower/upper bracket start
    avg_end_price:           float


def mc_probabilities(inp: MCProbInputs) -> MCProbResults:
    rng   = np.random.default_rng(inp.seed)
    T     = inp.days / inp.trading_days
    dt    = 1.0 / (inp.trading_days * inp.prices_per_day)
    steps = int(inp.days * inp.prices_per_day)
    drift = (inp.mu - inp.div_yield - 0.5*inp.vol**2) * dt
    diff  = inp.vol * np.sqrt(dt)
    N     = inp.n_sims

    # simulate paths
    Z   = rng.standard_normal((N, steps))
    lr  = drift + diff * Z              # log returns per step
    log_paths = np.cumsum(lr, axis=1)   # (N, steps)
    paths     = inp.spot * np.exp(log_paths)  # (N, steps) price paths

    end_prices = paths[:, -1]
    path_max   = paths.max(axis=1)
    path_min   = paths.min(axis=1)

    above_exp  = float((end_prices > inp.upper).mean())
    below_exp  = float((end_prices < inp.lower).mean())
    above_any  = float((path_max > inp.upper).mean())
    below_any  = float((path_min < inp.lower).mean())

    touch_upper = path_max > inp.upper
    touch_lower = path_min < inp.lower
    touching_either  = float((touch_upper | touch_lower).mean())
    touching_neither = float((~touch_upper & ~touch_lower).mean())
    touching_both    = float((touch_upper & touch_lower).mean())
    between_any      = 1.0   # by definition if spot is between targets

    return MCProbResults(
        above_upper_at_expiry = above_exp,
        above_upper_any_time  = above_any,
        below_lower_at_expiry = below_exp,
        below_lower_any_time  = below_any,
        touching_either       = touching_either,
        touching_neither      = touching_neither,
        touching_both         = touching_both,
        between_at_any_time   = between_any,
        avg_end_price         = float(end_prices.mean()),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 5. Lognormal distribution table
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LognormalDistEntry:
    price:      float
    prob_at:    float     # probability density at this price bucket
    prob_below: float     # cumulative P(S_T < price)
    prob_above: float     # 1 - prob_below


def lognormal_dist(S: float, days: float, vol: float,
                   mu: float = 0.0,
                   trading_days: float = 252.0,
                   n_points: int = 100,
                   price_range: Tuple[float,float] = None) -> List[LognormalDistEntry]:
    """Analytic lognormal distribution table over a price grid."""
    T    = days / trading_days
    if T <= 0 or vol <= 0:
        return []
    ln_mu  = np.log(S) + (mu - 0.5*vol**2)*T
    ln_sig = vol * np.sqrt(T)

    if price_range is None:
        lo = S * np.exp(-4*ln_sig)
        hi = S * np.exp(+4*ln_sig)
    else:
        lo, hi = price_range

    prices = np.linspace(lo, hi, n_points)
    out    = []
    for p in prices:
        if p <= 0:
            continue
        z   = (np.log(p) - ln_mu) / ln_sig
        pdf = norm.pdf(z) / (p * ln_sig)
        cdf = float(norm.cdf(z))
        out.append(LognormalDistEntry(
            price     = float(p),
            prob_at   = float(pdf),
            prob_below= cdf,
            prob_above= 1.0 - cdf,
        ))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Demo
# ══════════════════════════════════════════════════════════════════════════════

def demo():
    import xlrd
    from pathlib import Path

    print("=" * 60)
    print("1. Price Distribution (AAPL ~1yr of data from Excel)")
    xl = Path(__file__).parent.parent / "VaRtools Samples.xls"
    if xl.exists():
        wb   = xlrd.open_workbook(str(xl))
        sh   = wb.sheet_by_name("Price Distriubtion")
        px   = []
        for r in range(12, sh.nrows):
            v = sh.cell_value(r, 1)
            if v and isinstance(v, float) and v > 0:
                px.append(v)
        px = np.array(px)
        d  = price_distribution(px)
        print(f"  n={d.n}  annual vol={d.annual_vol:.4f}")
        print(f"  Skewness={d.skewness:.4f}  (t={d.t_ratio_skewness:.2f}, normal={d.skew_normal})")
        print(f"  Ex.Kurtosis={d.excess_kurtosis:.4f}  (t={d.t_ratio_kurtosis:.2f}, normal={d.kurt_normal})")

    print("\n2. Probability calculations (S=100, vol=0.25, T=252d)")
    S, H_up, H_dn, days, vol, mu = 100.0, 130.0, 80.0, 252.0, 0.25, 0.08
    pa, pb = prob_at_expiry(S, H_up, days, vol, mu)
    print(f"  P(above 130 at expiry)  = {pa:.4f}")
    pt_up  = prob_touch_any_time(S, H_up, days, vol, mu)
    pt_dn  = prob_touch_any_time(S, H_dn, days, vol, mu)
    print(f"  P(touch 130 any time)   = {pt_up:.4f}")
    print(f"  P(touch  80 any time)   = {pt_dn:.4f}")

    H_target = spot_at_probability(S, 0.35, days, vol, mu, prob_type="A")
    print(f"  Spot with 35% any-time prob: {H_target:.4f}")

    print("\n3. MC Probabilities (S=90, upper=222, lower=24, vol=1.83)")
    inp = MCProbInputs(spot=90, upper=222, lower=24, days=365,
                       vol=1.83, mu=0.07, n_sims=50_000)
    r = mc_probabilities(inp)
    print(f"  Above upper at expiry  : {r.above_upper_at_expiry:.4f}")
    print(f"  Above upper any time   : {r.above_upper_any_time:.4f}")
    print(f"  Below lower at expiry  : {r.below_lower_at_expiry:.4f}")
    print(f"  Below lower any time   : {r.below_lower_any_time:.4f}")
    print(f"  Touching either target : {r.touching_either:.4f}")
    print(f"  Touching neither       : {r.touching_neither:.4f}")
    print(f"  Touching both          : {r.touching_both:.4f}")
    print(f"  Avg end price          : {r.avg_end_price:.4f}")

    print("\n4. Lognormal distribution table (first 5 rows)")
    tbl = lognormal_dist(S=90, days=365, vol=1.09, mu=0.07, n_points=50)
    print(f"  {'Price':>8}  {'P(at)':>10}  {'P(below)':>10}  {'P(above)':>10}")
    for row in tbl[:5]:
        print(f"  {row.price:8.2f}  {row.prob_at:10.6f}  {row.prob_below:10.6f}  {row.prob_above:10.6f}")


if __name__ == "__main__":
    demo()
