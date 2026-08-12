# Evidence Kit — Dealer Positioning v2 Debate (2026-08-08)

Generated from source code at commit HEAD (master).
FILE hash computed separately (sha256sum of this file).

## Subject
Two parallel debate tracks:
- **Track A**: How to improve dealer positioning now that the whale sign model (v5/direction) won the Aug-6 debate. The whale-only leg (min_score=1) × NO_CALL gate × classification-only labeling was the judged winner, but the v5 pooled coefficient was −0.0002 (block perm p=0.0825 — suggestive, not significant). What improvements can push the edge further?
- **Track B**: The replication weights from DDKZ Appendix A — do they "drop from use" at deep OTM? An earlier session (Aug 3) documented the mechanism but the claim needs formal verification.

## Locked Decisions
1. Demeterfi vol replication math (DDKZ 1999) is correct and untouched.
2. Headless default stays vol_surface_replication (v2.1) until a release-note flip.
3. All sign models stay selectable; no model is deleted.
4. The whale threshold_bps knob (WHALE_THRESHOLD_BPS, default 3000 bps) exists and is calibrated.

## Prior Debate Result (2026-08-06)
Judged winner (hybrid): whale-only direction (DEALER_DIRECTION_MIN_SCORE=1) × v4's NO_CALL honesty gate × classification-only labeling.
Ranking: v5@1 > v4 > v5@3 > v2.1 > v1/v2.
Pooled test (n=494): v5 is only negative-sign model (coef −0.0002, t −1.13, block perm p=0.0825); M2 net delta-OI is only significant relation (block p=0.0245) but INVERTED positive.

---

## Source Code Excerpts

### 1. Replication Weights — The DDKZ Recursion
**File**: `Vol_Suite/replication_reference.py:82-103`

```python
def _f_payoff(ST: float, Sstar: float, T: float) -> float:
    return (2.0 / T) * ((ST - Sstar) / Sstar - math.log(ST / Sstar))

def _build_weights(strikes: List[float], Sstar: float, T: float, side: str
                    ) -> Tuple[np.ndarray, np.ndarray]:
    """Appendix A discrete recursion. Every weight guaranteed >= 0."""
    nodes = [Sstar] + list(strikes)
    fvals = [_f_payoff(k, Sstar, T) for k in nodes]
    out_strikes, weights = [], []
    cum = 0.0
    for i in range(1, len(nodes)):
        Ki, Kim1 = nodes[i], nodes[i - 1]
        denom = (Ki - Kim1) if side == 'call' else (Kim1 - Ki)
        slope = (fvals[i] - fvals[i - 1]) / denom
        w = slope - cum
        out_strikes.append(Ki)
        weights.append(w)
        cum += w
    return np.array(out_strikes), np.array(weights)
```

**Mathematical property**: f''(K) = 2/(T*K²) → 0 as K → ∞. So for deep-OTM calls (K >> S*), the curvature flattens, slope ≈ cum, and w → 0. This is a CONSEQUENCE of the convex log-payoff having diminishing second-derivative at extreme moneyness.

### 2. The OTM Leg Weight Gate
**File**: `Vol_Suite/replication_reference.py:431-449`

```python
def _otm_leg_weights(chain_iv, spot, T):
    call_strikes = sorted(k for (k, right) in chain_iv if right == 'C' and k > spot)
    put_strikes = sorted((k for (k, right) in chain_iv if right == 'P' and k < spot), reverse=True)
    if len(call_strikes) < 2 or len(put_strikes) < 2:
        return {}
    Kc, Wc = _build_weights(call_strikes, spot, T, 'call')
    Kp, Wp = _build_weights(put_strikes, spot, T, 'put')
    out = {}
    for k, w in zip(Kc, Wc):
        out[(k, 'C')] = float(w)
    for k, w in zip(Kp, Wp):
        out[(k, 'P')] = float(w)
    return out
```

**Key observation**: This returns ALL OTM strikes with their recursion weights. The vol_surface_replication model uses this as a BINARY GATE (in/out) but DISCARDS the weight magnitude. A strike with w≈0.0001 gets identical sign treatment to a strike with w=5.0.

### 3. Sign Resolution — vol_surface_replication
**File**: `Vol_Suite/dealer_positioning.py:384-391`

```python
if sign_model == 'vol_surface_replication':
    if otm_strikes is None or (strike, right) not in otm_strikes:
        return 0.0           # <-- gated OUT by OTM set
    if vol_surface_ref is not None:
        vs_sign = vol_surface_reference.resolve_vol_surface_sign(vol_surface_ref, strike, right)
        if vs_sign != 0.0:
            return vs_sign   # <-- per-strike rich/cheap sign from SABR deviation
    return -1.0              # <-- fallback: Layer 1b's flat default
```

**Critical**: sign = rich(+1)/cheap(-1) OR flat −1 fallback. Weight magnitude is NEVER used. A deep-OTM strike at the edge of the chain with near-zero replication weight gets the same −1 sign as the first OTM strike.

### 4. SABR Reference Curve
**File**: `Vol_Suite/vol_surface_reference.py:240-313`

```python
def fit_sabr_reference(chain_iv, forward, T, beta=SABR_BETA):
    """ATM-pinned SABR calibration. UNWEIGHTED (equal weight per strike)."""
    # ...alpha solved via bisection, (rho,nu) via multi-start L-BFGS-B
    # 3×3 grid of starts: rho ∈ {-0.5, 0, 0.5}, nu ∈ {0.3, 0.6, 0.9}
```

The SABR reference is fit UNWEIGHTED across the whole OTM strip. Deviation = IV_market - IV_SABR. Per-strike sign: dev > 0.01 → −1.0 (rich/dealer short), dev < −0.01 → +1.0 (cheap/dealer long), |dev| ≤ 0.01 → 0.0 (deadband, no read).

### 5. The Five Sign Models
**File**: `Vol_Suite/dealer_positioning.py:287`

```python
VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication', 'oi_flow', 'direction')
```

- v1 `oi_heuristic`: flat call=+1/put=−1, every strike
- v2 `replication`: OTM only, flat −1 everywhere in the set
- v2.1 `vol_surface_replication` (default): OTM gate + per-strike SABR deviation sign
- v4 `oi_flow`: sign from ΔOI (day-over-day OI change)
- v5 `direction`: whale-sign from Direction 5-signal package, run-level bias applied uniformly to all OTM legs

### 6. Direction Sign (v5)
**File**: `Vol_Suite/dealer_positioning.py:402-419`

```python
if sign_model == 'direction':
    # Run-level bias (+1 bullish / -1 bearish / 0 no-read) from Direction package
    if otm_strikes is not None and (strike, right) not in otm_strikes:
        return 0.0
    if not direction_bias:
        return 0.0
    direction = 1.0 if right == 'C' else -1.0
    return -direction_bias * direction
```

The direction bias is applied UNIFORMLY across all OTM legs. No per-strike granularity. No weight magnitude.

### 7. Aggregation
**File**: `Vol_Suite/dealer_positioning.py:489-490` (approximate)

```python
gamma_by_strike[k] += sign * γ * OI
dollar_gamma_by_strike[k] += sign * γ * S * 100 * OI
```

All OTM strikes contribute equally. No replication weight scaling.

---

## The Two Core Questions for the Debate

### Track A: Improving Dealer Positioning Post-Whale-Sign-Change
The v5 direction model won but with weak pooled evidence (p=0.0825). The sign is applied uniformly across all OTM legs. Potential improvements:
1. **Weighted sign**: use replication weight magnitude to scale contributions (deep-OTM gets less weight)
2. **Continuous sign**: instead of binary ±1, use the SABR deviation magnitude as a continuous weight
3. **Multi-day accumulation**: `replication_reference.compute_accumulated_position()` exists but is not integrated into live dealer positioning
4. **Better threshold calibration**: the 3000 bps threshold was calibrated on 5 names; wider validation needed
5. **Per-expiry vs. run-level**: v5 applies one bias to ALL expiries; should it vary per-expiry?

### Track B: Replication Weight Verification
Claim: "replication weights just drop from use at some point."
This is mathematically EXPECTED for the DDKZ recursion (f'' → 0 at extreme moneyness). But the CRITICAL question is whether this is a PROBLEM for the vol_surface_replication model, which discards weight magnitude entirely and uses only the binary OTM gate. The weights determine WHO is in/out of the set, but deep-OTM strikes with near-zero weights are still "in" and still get full ±1 sign treatment.
