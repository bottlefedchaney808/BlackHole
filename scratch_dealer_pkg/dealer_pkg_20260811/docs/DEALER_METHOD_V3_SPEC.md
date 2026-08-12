# Dealer-Positioning Method v3 — Accuracy-Focused Spec

> **Purpose:** How to make the `vol_surface_replication` dealer-positioning method
> *scientifically* more accurate (theory + retrievable ThetaData metrics — **not**
> vendor GEX). Written after a CARL R1 adversarial review of the current method +
> Stage-3 backtest. Status: **M1 + M2 IMPLEMENTED & committed** (`472e3ae`, `d04788e`);
> backtest-statistics fixes committed (`6b7ab44`). M3–M6 remain actionable.
> Panel replication of the null recorded 8/4 (see below); tooling:
> `pooled_panel_backtest.py` + `tests/test_pooled_panel_backtest.py` (suite green).
>
> **Live result (SPY 20261030, 90d, 8/4):** v3 (OI-flow) gives a **balanced, non-
> degenerate 30L/31S split** (v1 60/1, v2 5/56 were untestable) — the OI-change
> flow signal fixes the degeneracy as a classifier. But ALL models (v1/v2/v3/
> delta-OI) show a **non-significant** regression effect (best perm p = delta-OI
> 0.31; v3 perm p 0.90; v2's direction wobbles run-to-run). Conclusion: the
> "short-gamma → higher forward vol" effect is **not detectable at daily/5d
> horizon in a single expiry** — likely low power (n=61) and/or a horizon
> mismatch (dealer tape-amplification is an intraday effect). Next lever = **M5**
> (multi-expiry/ticker pooling) and **re-examining the target/horizon**.
>
> **Pooled panel replication (AMD/NVDA/TSLA/MU, 20261120, 90d, n=428, 8/4):**
> M5-style pooling *was* run — ticker-FE panel regression of fwd realized vol on
> continuous net-gamma (controls ATM IV + TTE, 2000 perms, both full-shuffle and
> within-ticker block-shuffle nulls; `Vol_Suite/pooled_panel_backtest.py`). All
> four models null: v1 coef +0.0000 (perm 0.987/0.978), v2 −0.0000 (0.961/0.931),
> v3 +0.0005 (0.798/0.704), M2 0.0000 (0.565/0.662). The one standalone hit that
> ever appeared — MU v1 (perm p 0.019, on a **degenerate 1/112 short-gamma day
> split**) — collapsed to +0.0000 (perm p ~0.98) once pooled: a false positive,
> not signal. **Conclusion: pooling does NOT rescue the proxies.** The problem is
> identification (OI×IV-residual×gamma measures are endogenous to the vol level /
> too noisy at daily OI granularity), not power. Do not re-run this panel; the
> remaining honest levers are trade-level flow data and an intraday horizon, not
> more regression on OI-derived signals.
>
> **Context:** The current v2 method infers dealer-short/long per OTM strike from
> whether the strike's IV trades *rich* or *cheap* vs a SABR reference
> (`vol_surface_reference.resolve_vol_surface_sign`), gated by the Layer-1b OTM
> replication set. Its central weakness (CARL R1-F1) is **endogeneity**: "rich IV →
> dealer short" is mechanically tied to the IV/vol regime, so any test of
> "short-gamma → higher forward vol" is confounded by vol clustering unless
> controlled.

## Fixed already (backtest statistics, commit `6b7ab44`)
- DayRecord now stores `atm_iv` and `T` (time-to-expiry) as controls.
- New **primary test**: OLS of forward realized vol on the **continuous** net-gamma,
  controlling for ATM IV + TTE, with a **permutation null** (`_summarize_regression`).
- `_weighted_dealer_sign` now applies the IV dead-band first (F3) and uses the
  Layer-1b `-1` fallback for missing deviations (F6); export labels corrected (F8).

## Method-accuracy improvements (actionable)

### M1 — Replace the IV-level flow proxy with an OI-change flow signal (highest value)
**Why:** "rich IV → buying" confuses a *level* with *flow*. OI **change** (ΔOI) is a
real flow measure, independent of the vol level — so it removes the endogeneity.
**How:** ThetaData `option_bulk_hist_oi` gives per-strike OI history. For each day,
compute net ΔOI per (k, right). Rising OI at a strike = customers adding that leg →
dealer nets the other side. Use `sign = −sign(ΔOI·direction)` as the method-accuracy
flow signal, replacing (or augmenting) the IV-richness read. **Validation:**
the same regression-with-controls backtest, but with net-ΔOI-driven net-gamma as a
THIRD sign model; if a ΔOI-based v3 beats v2 *after* controls, the method is reading
flow, not vol.
**Test angle (network-free):** unit-test the ΔOI → sign mapping on synthetic
OI-change series.

### M2 — Add delta‑OI as a pure-data reference model
**Why:** net delta‑OI (Σ sign·delta·OI across the chain) is a market-wide measure
that needs no IV-fitted sign at all — a principled, data-only "which side is the
book on." Comparing v1/v2's day labels against net-delta‑OI tells you which sign
convention best matches the most mechanical dealer-neutral reference.
**How:** compute `net_delta_oi = Σ _dealer_sign(right)·|delta|·OI` per day (we already
have delta in the greeks rows). Run it through the same backtest as model v3.
**Test angle:** synthetic chain where delta-OI has a known sign; assert recovery.

### M3 — Ground the magnitude-weighting scale (remove the arbitrary knob)
**Why (F3):** `V2_WEIGHT_SCALE=0.05` is unanchored; the day-net-gamma (and any split)
depends on it.
**How:** set the scale to a data-derived dispersion of that day's deviations
(e.g. `σ = median(|dev|) + ε` over the OTM legs), or sweep a grid and report
coef/t/perm_p as a robustness band. Require the conclusion to be stable across σ.
**Whiteproof:** the continuous regression (primary test) is far less σ-sensitive than
the binary split, so this is belt-and-suspenders, not the main lever.

### M4 — Dollar gamma (not per-share gamma) + replication-strip magnitude
**Why (F7/Layer-1b note):** the "amplification dealers cause" scales with *dollar*
gamma (γ·S·OI), and a true "short variance" dealer is short the **weighted**
replicating strip, not every OTM leg equally.
**How:** (a) scale each leg by spot → dollar gamma, so tail (deep-OTM) legs don't
dominate; (b) weight each leg by its Demeterfi **replication weight** (already
computed by `replication_reference._build_weights`) instead of uniform `±1·OI`, so
v2's magnitude reflects actual variance-replication notional. Both use data we
already fetch.
**Test angle:** one deep-OTM high-OI leg must not out-weight a near-ATM leg (dollar-
gamma scaling); strip-weighted vs. uniform net-gamma agree in sign on a symmetric
chain.

### M5 — Term-structure / multi-expiry dealer book
**Why:** a real dealer book spans expiries; v2 uses a single near-dated expiry, whose
gamma is TTE-sensitive (F4).
**How:** aggregate net-gamma across the front 2–3 expiries (weighted by inverse TTE),
so the dealer-position read is a book, not one expiry. Note the single-expiry
backtest stays as the controlled unit; multi-expiry can run as a sensitivity.

### M6 — De-confound the IV reference itself
**Why:** measuring today's rich/cheap against today's own fitted surface bakes in
the level.
**How (optional):** build the reference from the **prior day's** IV (or a
term-structure-consistent reference) so today's flow is read against yesterday's
level, not today's. Higher effort; only pursue if M1 doesn't resolve the micro-
structure test.

---
### Recommendation / sequencing
1. **M1 + M2 together** give the biggest scientific upgrade (real flow proxies) and
   are directly testable against the new regression + permutation machinery
   (**backtest availability: yes, via a third/fourth `_net_gamma_v*` + the existing
   `_summarize_regression`**).
2. **M4** makes the magnitudes economically right (dollar gamma + strip weights).
3. **M3/M5** harden the estimate (ground σ, multi-expiry); **M6** is optional.
4. Gate each on: does the primary regression's coef sign (short→higher vol) survive
   the controls AND the permutation null, and is it stable across σ / expiries?

**Cross-check vs retrievable metrics (not vendors):** realized forward vol (the
outcome), OI change (M1), and net delta-OI (M2) are all retrievable from
ThetaData/our pipeline today — no paid external data required.
