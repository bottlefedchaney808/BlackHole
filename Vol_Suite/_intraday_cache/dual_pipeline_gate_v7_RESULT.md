# ROUND-9 — Symmetric Convention-Distance REPAIR: moneyness-sign-map benchmark, robustness diagnostics, cluster-as-unit gate (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate_v7.py`

**Network-free / zero acquisition:** R9 reuses v6's exactly-derived new-engine rows (-1×BS_vanna(spot,strike,T,IV) on the persisted v5 grid) and the v5 production `applied_sign` (real SVI `deviation_by_strike`). Same 447 per-strike rows / 12 clusters. No ThetaData / .env / new acquisition.

### Corrected benchmark framing (R9-1)

R8 mislabeled delta_new as a "flat −1×BS baseline". It is actually the **new engine's −1×BS_vanna MONEYNESS SIGN MAP** — a near-balanced 50/50 ±1 map. Observed split: **+1: 224 (50.11%) / −1: 223 (49.89%)** over 447 rows (0: 0). The near-balanced split is exactly why a symmetric two-sided convention-distance (not a one-sided −1 screen) is the correct repair.

### Pooled / effective-size ledger (R9-2)

| Metric | Conservative \|sign·OI·vanna\| | Convention-independent \|OI·vanna\| |
|---|---|---|
| Pooled D_conv | **0.7445** | **0.8948** |
| Row n_eff | 35.4 | 73.4 |
| Cluster K_eff | 5.04 | 5.47 |
| Rows | 447 | 447 |

The two weightings differ ONLY on deadband (sign=0) rows: conservative gives them 0 weight (indeterminate, not dropped), convention-independent keeps them at full weight.

### Distance-class ledger (rows AND OI·vanna mass) (R9-2)

| dist | rows | OI·vanna mass | mass share | conservative w | independent w |
|---|---|---|---|---|---|
| 0 | 95 | 6.211e+05 | 25.8% | 6.211e+05 | 6.211e+05 |
| 1 | 297 | 1.414e+06 | 58.8% | 0 | 1.414e+06 |
| 2 | 55 | 3.683e+05 | 15.3% | 3.683e+05 | 3.683e+05 |

Note: dist 1 = zero-vs-nonzero = the **297 deadband rows** (δ_prod=0). Under the conservative weighting they carry 0 weight (treated as indeterminate, NOT dropped). The R8 "78.7% disagreement" headline was inflated by these weight-0 rows; the true confident-disagreement rate (dist 2 rows / confident rows) is 55/150 = 36.7%.

### Family-balanced D (R9-2)

- **SPY-only:** pooled D_conv = **0.6783** (6 clusters, 216 rows, weight 6.675e+05).
- **QQQ-only:** pooled D_conv = **0.8816** (6 clusters, 231 rows, weight 3.219e+05).

Leave-one-family (= the other family alone) = the above; each family clears 0.50 on its own.

### Leave-one-cluster-out (R9-2)

| Omitted cluster | Pooled D_conv (conservative) | flips to <0.50? | remaining rows |
|---|---|---|---|
| QQQ_20260508 | 0.7482 | no | 413 |
| QQQ_20260605 | 0.7462 | no | 407 |
| QQQ_20260617 | 0.7476 | no | 403 |
| QQQ_20260716 | 0.6349 | no | 411 |
| QQQ_20260729 | 0.8132 | no | 400 |
| QQQ_20260731 | 0.7214 | no | 417 |
| SPY_20260413 | 0.9065 | no | 399 |
| SPY_20260430 | 0.7991 | no | 416 |
| SPY_20260605 | 0.7466 | no | 422 |
| SPY_20260617 | 0.7448 | no | 412 |
| SPY_20260728 | 0.4553 | **YES** | 404 |
| SPY_20260729 | 0.7970 | no | 413 |

Full pooled D_conv = **0.7445**. Dropping SPY_20260728 alone flips the pooled D below the 0.50 bar.

### Cluster-as-unit binomial gate (R9-3)

- Per-cluster D_conv >= 0.50 = ALIGNED (each cluster one datum). **6/12 aligned** — bar is >= 8/12 (2/3). Exact one-sided null P(X >= 6 | Binomial(12,0.5)) = **0.6128**.
- **DOES NOT CLEAR the ≥2/3 cluster-as-unit bar.** 6/12 is the chance-median outcome (P=0.6128); the pooled mass metric alone cannot carry the re-admission, because one cluster's weight (SPY-0728) can still dominate it.
- Aligned clusters: QQQ_20260508, QQQ_20260716, QQQ_20260731, SPY_20260605, SPY_20260617, SPY_20260728.

### VERDICT (fragility-qualified descriptive)

**OPEN (not certified)** — R9 shows a positive pooled convention-distance association between production's real SVI deviation map and the new engine's −1×BS_vanna moneyness sign map: pooled D_conv = 0.7445, robust to |OI·vanna| weighting and family balance (SPY 0.678 / QQQ 0.882). BUT it is single-cluster-hinged (SPY-0728), low power (row n_eff, cluster K_eff < 12), driven by a minority of confident opposite-sign rows, and the near-balanced 50.1%/49.9% ±1 sign map is NOT a 'flat −1' baseline. The cluster-as-unit gate clears 6/12 (>= 8 needed, exact null P = 0.6128) — it does NOT clear the ≥2/3 bar. Descriptive distance consistent with production carrying a **distinct SVI deviation pattern** relative to the −1×BS_vanna moneyness sign map, NOT certified re-admission, NOT promotion, NOT a claim of causal/directional independence. Binding promotion blockers remain: sign-arm FAIL (3/9 unique-day heterogeneous-null P=0.8066; 6/12 P=0.6128); correlational BINDING BLOCKER (convention-bound: A6 reflexivity > vanna; b4 exact sign-flip).

### Binding promotion blockers (UNCHANGED, independent of R8/R9)

- **sign-arm:** FAIL (3/9 unique-day heterogeneous-null P=0.8066; 6/12 P=0.6128)
- **correlational:** BINDING BLOCKER (convention-bound: A6 reflexivity > vanna; b4 exact sign-flip)

R9 supports a re-admission-to-**evidence** discussion only. It does NOT certify the mechanism and does NOT support promotion.