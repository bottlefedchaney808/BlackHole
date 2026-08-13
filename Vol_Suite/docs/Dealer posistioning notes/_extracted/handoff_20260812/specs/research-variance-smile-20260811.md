# Research: Variance-Swap Fair Vol, Smile Recovery, and an RP-Native Reference Curve

> **Date:** 2026-08-11
> **Purpose:** inform the vanna-seed battery's next design decision (per-strike
> "RP-native" reference vol for rich/cheap marking).
> **Status:** pure research, no code. Companion to `battery-consolidated-20260811.md`.
> **Directly relevant repo artifacts:** `Vol_Suite/replication_reference.py`
> (`_build_weights`, `_otm_leg_weights`, Appendix-A recursion), `Vol_Suite/rp_smile_plot.py`
> (the failed Option-A/vega-scaled attempt), `Options_Suite/VannaVolga.py`, `Options_Suite/vol_manager.py`.

---

## Executive recommendation (read this first)

The per-strike smile is **NOT recovered from the variance-swap strip** — it is recovered
from the **OTM option IV ladder itself**, and the RP strip supplies one **model-free
level (integral) constraint** that anchors the fit. The `1/K²` weight strip is pure
strike/forward geometry and carries **zero** vol information, so it can only ever yield a
**single aggregate variance number**, never `N` per-strike vols. Any attempt to derive a
smile "from the strip alone" is mathematically under-determined.

**Recommended construction — a variance-swap-consistent SVI reference curve:**

1. Anchor at the forward `F₀ = S₀ e^{(r−q)T}`; keep the existing discrete weight recursion `w_K`.
2. Compute the RP-native fair variance (model-free aggregate):
   `K_var = (2/T) Σ_K w_K · Price_OTM(K)`, then `σ_swap = √max(K_var, 0)`.
3. Fit the **SVI** total-implied-variance smile
   `w(k) = a + b( ρ(k−m) + √((k−m)² + σ²) )`, `k = ln(K/F₀)`, to the **OTM chain IVs**,
   vega-weighted, in **variance space**.
4. **Enforce the RP consistency constraint** on the level: require
   `(2/T) Σ_K w_K · BS(K, √(w(k_K)/T)) = K_var` (hard constraint, or soft penalty; SVI's
   `a`/`b` control level/slope so this is a low-dimension fix-up).
5. Reference vol per strike: `σ_ref(K) = √(w(k_K)/T)`. Mark **rich/SHORT** if
   `market_IV(K) > σ_ref(K)`, cheap/LONG if below — in **IV space**, never vega-normalized.

This has the smile's shape (wings stay positive, so far-OTM compare sensibly — fixing the
A-variant tail-collapse) **and** is exactly variance-swap-consistent in aggregate. SVI is
chosen over SABR/VV for our single-chain use case: 5 params, fit to a single OTM ladder,
linear-in-(a,b) least squares, documented no-arbitrage conditions, and a clean variance
integral.

---

## A. Fair-variance construction from an OTM strip

**Derivation.** The variance swap pays realized quadratic variation vs a fixed strike
`K_var`. In a continuous, no-jump diffusion the replicating instrument is the **log
contract** `ln(S_T/F₀)` (Carr & Madan 1998). A static strip of OTM options with weights
`1/K²` replicates the log payoff because `∂²/∂K² [log payoff] = −1/K²`; the delta-neutral
cash component is handled by forward trading. The standard fair-value formula
(**Demeterfi, Derman, Kamal, Zou 1999, "A Guide to Volatility and Variance Swaps"**):

```
E[σ_R²] = (2/T)[ rT − (S₀/S*)e^{rT} − 1 − ln(S*/S₀)
              + e^{rT}∫₀^{S*} (1/K²)P(K)dK + e^{rT}∫_{S*}^∞ (1/K²)C(K)dK ]
```

Choosing the cutoff `S*` at the forward collapses the cash/linear terms, leaving the
**continuum weight `(2/T)·(1/K²)·dK`**. On a discrete ladder the exact weights come from
the tangent/recursion construction (the repo's Appendix-A `_build_weights`), which
converges to `(2/T)(ΔK/K²)` on a fine grid, and

```
K_var = (2/T) Σ_K w_K · Price_OTM(K)
```

This is precisely what `rp_smile_plot.py` already computes (lines 69-73).

**Subtleties.**
- **Discretization & truncation:** a finite ladder truncates the tails. Far-OTM options
  carry real weight under `1/K²`; standard practice extrapolates IV beyond the last listed
  strike (flat, or constant slope in implied-variance vs log-moneyness) and bounds the
  tail contribution. On a sparse chain this is the dominant error term.
- **Dividend/carry:** moneyness must use the **forward**, not spot. The repo uses spot as
  `S*`, which is acceptable but the forward is the cleaner anchor and removes the
  carry-dependent cash terms.
- **Log-vs-linear & jumps:** the log-contract replication is exact only in continuous
  diffusion. Jumps + discretization create a real gap between the option-strip price (log
  contract) and realized quadratic variation (Broadie & Jain 2008). The strip prices the
  **log contract**, not realized variance per se.
- **Volatility swap ≠ variance swap:** `σ_swap = √K_var` is the variance-swap implied vol,
  NOT the volatility-swap fair strike (which needs a convexity correction ~
  `√E[σ²] − VolVar/(8·σ̂)`). Keep them distinct in reporting.
- **Bid/ask:** `K_var` is **linear** in option prices, so illiquid wings inject noise
  linearly. Down-weight/mark-to-mid consistently.

---

## B. Can a per-strike SMILE be recovered from a variance-swap static replication? (The crux)

**No — and here is the mathematical obstacle.**

1. **The variance swap is one scalar.** Its price gives exactly **one constraint** on the
   smile:
   `(2/T) Σ_K w_K · BS(K, σ_impl(K)) = K_var`.
   That is one equation for `N` unknown strike-vols → the smile is **under-determined**.
   Infinitely many distinct smiles share the identical fair variance. A single variance
   number cannot pin down a function.
2. **The weights carry zero vol information.** `w_K` is pure strike/forward geometry
   (the Appendix-A recursion has no vol input — see the module docstring). It is a fixed
   kernel; it does not "imply" per-strike vols any more than an integration quadrature
   implies the integrand's shape.
3. **The variance→smile map is model-dependent.** `E[∫(dS/S)²]` as a functional of the
   smile differs under local vol vs stochastic vol (Gatheral Ch. 11; Bergomi 2016). So even
   "recovering the smile from variance-swap prices" across maturities is ill-posed and
   model-conditional.

**The practical resolution (what actually works):** the smile's **shape is already
available directly from each OTM option's own IV** — the chain *is* the per-strike smile.
The RP strip's legitimate role is the **model-free LEVEL constraint**: it defines the
aggregate `K_var` that a reference smile must respect. So:

- **Shape** ← OTM option IV ladder (fit a parametric smile).
- **Level / consistency** ← the RP fair-variance integral.
- The variance swap does **not generate** the smile; it **anchors** a smile fit to the chain.

**Literature note:** there is no body of work "recovering the vol surface from variance
swap prices" in the sense of generating a strike smile — the direction of dependence runs
the other way (CBOE VIX *builds* a 30-day variance-swap fair strike from the `1/K²` OTM
strip). The **forward-variance smile** (Bergomi) is about **maturity** structure from a
strip of variance swaps across tenors, **not** strike structure within a tenor — and so
candidate (iii) is rejected for our single-expiry use case (details in §D).

---

## C. Standard smile-construction methods and their fit requirements

| Method | Inputs needed | Fit to a single chain's OTM IVs? | Per-strike curve robust for cheap/rich? |
|---|---|---|---|
| **SABR** (Hagan et al. 2002) | a handful of IV points across moneyness/tenor; 4 params `(α,β,ρ,ν)`, `β` often fixed (0.5 / 1) | Yes, but **over-parametrized** vs one OTM chain → multiple fits, needs regularization | Moderate: good ATM, **unstable far-OTM / low-strike** (negative density risk) |
| **Vanna-Volga** (Castagna & Mercurio 2007) | ATM + 25Δ RR/BF (±10Δ), clean wing deltas | FX-native quoting; awkward on full equity ladders | Good near anchor deltas, **poor extrapolation** beyond |
| **SVI** (Gatheral 2006; Gatheral & Jacquier 2014) | OTM IVs across the chain; 5 params `(a,b,ρ,m,σ)` | **Yes** — linear in `(a,b)`, convex LSQ, robust on one chain | Good; documented no-arbitrage (butterfly) conditions; integrates to clean variance |
| Bachelier/normal vol | same as lognormal | Yes | Only when BS-IV breaks (low strike / very short tenor) — not our case |
| Cubic spline in implied variance vs log-moneyness | full OTM IV ladder | Yes (interpolant) | Data-driven, **no-arbitrage not guaranteed**, poor extrapolation — fallback only |

**For our use case, SVI is the best fit** (single chain, needs a shape + a clean variance
integral + robust far-OTM). Vanna-Volga already exists in the repo
(`Options_Suite/VannaVolga.py`) and is a legitimate alternative if Jason prefers an
existing, smile-consistent object — but it is not RP-consistent by construction and
extrapolates worse in the wings.

---

## D. Recommendation for the per-strike RP-native reference curve

### Selected: variance-swap-consistent SVI reference

**Concrete algorithm (exact formulas):**

1. **Anchor & weights.** Forward `F₀ = S₀ e^{(r−q)T}`. Reuse the existing `_otm_leg_weights`
   recursion for `w_K` (already validated against DDKZ Fig. 3).
2. **RP-native aggregate.** `K_var = (2/T) Σ_K w_K · Price_OTM(K)`; `σ_swap = √max(K_var, 0)`.
3. **SVI fit.** Define log-moneyness `k = ln(K/F₀)`. Fit total implied variance
   `w(k) = a + b( ρ(k−m) + √((k−m)² + σ²) )` to the OTM chain by minimizing
   `Σ_K vega_K · ( σ_mkt(K) − √(w(k_K)/T) )²`
   subject to `w(k) ≥ 0` and butterfly non-negativity (`∂²C/∂K² ≥ 0`, Gatheral-Jacquier
   conditions). Fit in **variance space** (SVI is affine there; mitigates ATM-wing
   imbalance).
4. **RP consistency constraint (the load-bearing step).** Require the fitted smile to
   reproduce the fair variance:
   `(2/T) Σ_K w_K · BS(K, √(w(k_K)/T)) = K_var`.
   Because SVI's `a` (level) and `b` (overall slope) control the average variance level,
   enforce this as a hard constraint on `(a,b)` or a soft penalty `λ·(∫ − K_var)²`. This is
   what makes the curve genuinely **RP-native** rather than merely "an IV fit."
5. **Mark.** `σ_ref(K) = √(w(k_K)/T)`. Per strike: **rich/SHORT** if `market_IV(K) > σ_ref(K)`,
   **cheap/LONG** if below. Use the IV-space residual — see §E.

**Why this fixes the two failures.** (a) `σ_ref` now has the **smile's shape**: the SVI
wings stay positive and realistic far-OTM, so a far-OTM strike is compared against a real
smile vol, not against `0` — eliminating the A-variant "every far-OTM strike is
rich/SHORT" tail collapse. (b) It is **variance-swap-consistent**: the fitted curve
aggregates to the same `K_var` as the RP strip, so "marking cheap/rich vs the RP" has an
exact, defensible meaning. (c) It remains robust on **thin chains** because SVI is a
regularized 5-param fit, not a per-strike identity that inverts to degeneracy (Idea A).

### Candidates rejected (with reasons)

- **(i) Variance-swap-consistent SABR fit:** viable but strictly worse for our purposes —
  over-parametrized vs one OTM chain, unstable far-OTM, and no cleaner integral than SVI.
- **(ii) "Log-strip implied vol" per strike:** ill-defined. Each OTM option's IV *already is*
  its per-strike vol; there is no additional "log-strip vol" independent of the chain's own
  IVs. As a *reference*, this degenerates back to the raw chain.
- **(iii) Forward-variance smile:** gives **tenor** structure from a strip of variance swaps
  across maturities, not strike structure within one tenor. Requires multiple expiries and
  does not answer "what is the reference vol at strike K today." Rejected.
- **(iv) Per-strike vol "from the 1/K² weights via a model assumption":** the weights alone
  are vol-free; the *model assumption* (SVI/SABR) is precisely what injects the shape, and
  the weights only anchor the integral. This collapses to the recommended construction
  once made precise.

---

## E. Pitfalls

- **Unit consistency.** Keep `T` in years everywhere: `w` is **total variance** (`vol²·T`),
  `σ = √(w/T)` is vol, `K_var` is `vol²/year`. Do not mix IV and variance space in the fit.
- **Negative variance.** Guard `K_var < 0` (wide bid/ask or degenerate strip) — clamp to
  `max(K_var, ATM_IV²)` before `√`, never feed `NaN` to the fit.
- **Non-convex / no-arbitrage regimes.** Enforce `∂²C/∂K² ≥ 0` (butterfly) and `w ≥ 0`
  during the fit (Gatheral-Jacquier conditions). If the market smile genuinely violates
  them, **flag** rather than force — an over-constrained SVI fit on a broken chain is noise.
- **Far-OTM illiquidity.** Down-weight or exclude zero-OI / extreme-moneyness strikes from
  the fit; the SVI wing beyond the listed range is **extrapolation** — cap the confidence
  band and treat far-wing marks as low-trust.
- **Marking residual — never vega-normalized.** Use the **IV-space residual** `σ_mkt − σ_ref`,
  or the **vega-weighted price residual** `vega·Δσ` (≈ Δprice, gives P&L-scaled richness).
  Do **not divide by vega** (that is the A-variant's collapse — `vega→0` far-OTM blows the
  residual to ±∞). Optionally divide by `σ_swap` for a dimensionless z-score.
- **Circularity.** Fit on a cleaned/robust subset of strikes; mark on all. If the same
  strikes drive both the fit and the rich/cheap label, the marking is circular.
- **Seed signing.** rich=SHORT is fine, but verify the SVI fit itself isn't dominated by the
  strikes being signed (thin chains especially) — cross-check against Vanna-Volga
  (`Options_Suite`) as a two-desk microscope when in doubt (battery Gate-0 / TEST-8 spirit).

---

## References

- **Demeterfi, Derman, Kamal, Zou (1999),** "A Guide to Volatility and Variance Swaps,"
  *Journal of Derivatives* 6(4) — the fair-variance `(2/T)Σw_K·Price` formula + Appendix-A recursion.
- **Carr, P. & Madan, D. (1998),** "Towards a Theory of Volatility Trading," in *Volatility* (Risk Books) — static replication of the log contract.
- **Gatheral, J. (2006),** *The Volatility Surface: A Practitioner's Guide*, Wiley — Ch. 1-3 (SVI), Ch. 11 (variance swaps, model-dependent fair variance).
- **Gatheral, J. & Jacquier, A. (2014),** "Arbitrage-Free SVI Volatility Surfaces," *Quantitative Finance* 14(1) — SVI no-arbitrage conditions.
- **Broadie, M. & Jain, A. (2008),** "The Effect of Jumps and Discretization on the Pricing of Variance Swaps and Options," *Journal of Derivatives* — log-vs-linear gap.
- **Hagan, Kumar, Lesniewski, Woodward (2002),** "Managing Smile Risk," *Wilmott* — SABR.
- **Castagna, A. & Mercurio, F. (2007),** "The Vanna-Volga Method for Implied Volatilities," *Risk* — VV construction.
- **Bergomi, L. (2016),** *Stochastic Volatility Modeling*, CRC — forward variance / variance-swap smile (maturity structure).
- **Carr, P. & Wu, L. (2009),** "Variance Risk Premiums," *Review of Financial Studies* — VS fair strikes vs realized.
- **CBOE (2003-2026),** VIX White Paper — model-free variance construction from the `1/K²` OTM strip (the reverse direction).
