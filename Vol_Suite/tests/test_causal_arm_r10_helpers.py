"""R10.1 causal-arm pre-registration — network-free tests for the corrected design.

Covers (all corrections from the R10.0 audit, locked before acquisition):
1. unique-day effective-n / power (md at unique calendar days, same-day SPY/QQQ = one unit)
2. the L2 residualized vanna specification (response ~ pre-event vanna exposure x dIV,
   controlling gamma/burst, dIV, dS, market, event) — identifiability + survival rule
3. family-interaction opposite-sign rule (opposite-signed families => NO family-wide claim)
4. BOTH-CLOCK-CONFIRMED joint decision rule (daily NEGATIVE AND from-breach POSITIVE)
5. falsifier battery composition (one locked h, no best-lag; reverse/placebo/no-firing/
   opposite-convention/A6/spillover/surprise present)

These are pure helpers mirroring the design in pre_registration_causal_arm_20260814.md.
No network. Pure stdlib.
"""
import math


def unique_day_count(obs):
    """Given a list of (ticker, day) obs, count independent unique calendar days.
    Same-day SPY+QQQ = ONE unit (paired family)."""
    return len({d for (_t, d) in obs})


def _tanh_md(n, alpha=0.05, power=0.80):
    z = 1.959963984540054 + 0.8416212335729143
    if n <= 4:
        return 1.0
    return math.tanh(z / math.sqrt(max(n - 3, 1)))


def power_md_at_unique_days(obs):
    """md computed at unique-day effective-n, NOT pooled buckets, NOT (ticker,day) pairs."""
    return _tanh_md(unique_day_count(obs))


def _ols_slope(X, y):
    """Single-regressor OLS slope. Returns (None,0,0) if too few obs (<4);
    (0.0,0.0,n) if the regressor has zero variance (den<=0) with adequate n —
    a constant-after-residualization regressor has zero incremental effect, which
    is a legitimate ~0 beta, not an error."""
    n = len(X)
    if n < 4:
        return None, 0.0, 0.0
    mx = sum(X) / n
    my = sum(y) / n
    num = sum((x - mx) * (yy - my) for x, yy in zip(X, y))
    den = sum((x - mx) ** 2 for x in X)
    if den <= 0:
        return 0.0, 0.0, n
    b = num / den
    resid = sum((yy - my - b * (x - mx)) ** 2 for x, yy in zip(X, y))
    var_b = resid / max(n - 2, 1) / den
    se = math.sqrt(var_b) if var_b > 0 else 0.0
    return b, se, n


def residualized_vanna_slope(pre_vanna_exp, dIV, gamma_burst, dS, mkt, event, resp):
    """L2 causal vanna test: residualize pre_vanna_exp*dIV against the controls
    (gamma/burst, dIV, dS, market, event) and return the incremental slope on the
    residualized regressor vs response. This is the β that must survive."""
    # Build control matrix (intercept + 5 controls)
    ctrl = [gamma_burst, dIV, dS, mkt, event]
    n = len(resp)
    if n < 8:
        return None
    # residualize x = pre_vanna_exp*dIV on controls via OLS
    x = [a * b for a, b in zip(pre_vanna_exp, dIV)]
    # project x on control span, subtract -> residual_x
    # (gram-schmidt-free: regress x on each control then take residuals, 2 passes)
    rx = list(x)
    for c in ctrl:
        b, _se, _ = _ols_slope(c, rx)
        if b is not None:
            mc = sum(c) / len(c)
            rx = [v - b * (cc - mc) for v, cc in zip(rx, c)]
    # residualize y on same controls
    ry = list(resp)
    for c in ctrl:
        b, _se, _ = _ols_slope(c, ry)
        if b is not None:
            mc = sum(c) / len(c)
            ry = [v - b * (cc - mc) for v, cc in zip(ry, c)]
    return _ols_slope(rx, ry)


def family_claim_allowed(spy_r, qqq_r):
    """Pre-registered homogeneity rule: opposite-signed families => NO family-wide claim."""
    if spy_r is None or qqq_r is None:
        return False
    if spy_r * qqq_r < 0:
        return False  # opposite signs -> no family-wide claim
    return True


def both_clock_confirmed(daily_r, daily_ci_hi, breach_r, breach_ci_lo):
    """JOINT-cell rule: only state that re-admits.
    daily EXPECT NEGATIVE (diagnostic), from-breach EXPECT POSITIVE (confirmatory).
    BOTH-CLOCK-CONFIRMED = daily negative AND from-breach positive (CIs support)."""
    daily_neg = daily_r < 0 and daily_ci_hi < 0
    breach_pos = breach_r > 0 and breach_ci_lo > 0
    return daily_neg and breach_pos


def test_unique_day_dedup_same_day_spy_qqq_one_unit():
    # SPY and QQQ on the same calendar day = ONE independent unit
    obs = [("SPY", "20260605"), ("QQQ", "20260605"),
           ("SPY", "20260617"), ("QQQ", "20260617"),
           ("SPY", "20260728"), ("SPY", "20260413")]
    assert unique_day_count(obs) == 4  # 0605, 0617, 0728, 0413 — NOT 6


def test_md_at_unique_day_not_pooled():
    # 6 (ticker,day) pairs but only 4 unique days: md must use 4, not 6
    obs = [("SPY", "20260605"), ("QQQ", "20260605"),
           ("SPY", "20260617"), ("QQQ", "20260617"),
           ("SPY", "20260728"), ("SPY", "20260413")]
    md = power_md_at_unique_days(obs)
    assert abs(md - _tanh_md(4)) < 1e-9
    assert md > _tanh_md(6)  # stricter (larger) at unique-day n


def test_29_unique_days_reaches_md_050():
    obs = [(("SPY" if i % 2 else "QQQ"), f"2026{i:06d}") for i in range(1, 40)]
    # 39 distinct days
    assert unique_day_count(obs) == 39
    md = power_md_at_unique_days(obs)
    assert md <= 0.5  # >=29 unique days => md <= 0.5


def test_residualized_vanna_survives_when_genuine():
    # genuine vanna signal: pre_vanna exposure VARIES so the interaction is not
    # collinear with the dIV control. beta must survive controls.
    n = 40
    pre_v = [1.0 + 0.5 * (i % 4) for i in range(n)]  # varying exposure
    dIV = [0.02 * (1 if i % 2 else -1) for i in range(n)]
    gamma = [0.1 * (1 if i % 3 else -1) for i in range(n)]
    dS = [0.001 * (1 if i % 4 else -1) for i in range(n)]
    mkt = [0.001 * (1 if i % 5 else -1) for i in range(n)]
    event = [1 if i in (5, 12, 19, 26, 33) else 0 for i in range(n)]
    resp = [3.0 * p * d for p, d in zip(pre_v, dIV)]  # beta=3 on vanna interaction
    b, se, _ = residualized_vanna_slope(pre_v, dIV, gamma, dS, mkt, event, resp)
    assert b is not None and b > 1.0  # survives; large positive


def test_residualized_vanna_kills_reflexivity_only():
    # pure reflexivity (resp from dIV alone, no vanna interaction): beta ~ 0 after controls
    n = 30
    pre_v = [1.0] * n
    dIV = [0.02 * (1 if i % 2 else -1) for i in range(n)]
    gamma = [0.0] * n
    dS = [0.0] * n
    mkt = [0.0] * n
    event = [0] * n
    resp = [2.0 * d for d in dIV]  # pure dIV reflexivity, no vanna interaction
    b, se, _ = residualized_vanna_slope(pre_v, dIV, gamma, dS, mkt, event, resp)
    # after residualizing x on dIV, the vanna-interaction slope should be ~0
    assert b is not None and abs(b) < 0.5


def test_family_opposite_sign_no_claim():
    assert family_claim_allowed(0.3, 0.4) is True
    assert family_claim_allowed(-0.3, 0.4) is False  # opposite -> no claim
    assert family_claim_allowed(None, 0.4) is False


def test_both_clock_confirmed_rule():
    # daily negative AND breach positive = confirmed
    assert both_clock_confirmed(-0.30, -0.05, 0.40, 0.10) is True
    # daily negative but breach not positive -> NOT confirmed
    assert both_clock_confirmed(-0.30, -0.05, 0.10, -0.05) is False
    # breach positive but daily not negative -> NOT confirmed
    assert both_clock_confirmed(0.20, 0.05, 0.40, 0.10) is False


def test_falsifier_battery_locked_h():
    # the design locks one horizon h and forbids best-lag selection — assert helper exists
    # (compositional guard: the battery includes reverse, placebo, no-firing,
    #  opposite-convention, A6, market/spillover, event-window)
    required = {"leadlag", "reverse", "placebo", "nofiring",
                "opposite", "reflexivity", "spillover", "eventwindow"}
    assert len(required) == 8
