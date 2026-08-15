# ROUND-8 — Symmetric Two-Sided Magnitude-Weighted Convention-Distance (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate_v6.py`

**Cem's boundary (R3, deleg_54ae4bc3):** the ONLY door to reconsider APPROVAL is a symmetric, two-sided, magnitude-weighted convention-distance rerun: persist new-engine per-strike rows alongside production, compute D = Σ_s w_s|δ_prod,s − δ_new,s| / Σ_s w_s on the real SVI deviation path. A positive result would still NOT establish causality — it licenses at most re-admission to evidence, never automatic promotion.

### Network provenance

- **new_engine_data_status = `network-free`** — the new-engine per-strike `dealer_frame_vanna` is a pure analytic function `-1×BS_vanna(spot,strike,T,IV)` of inputs ALL persisted in the v5 production per-strike arrays (strike/right/iv) + the cluster `close_spot` + day→expiry (T). Derived EXACTLY with the same engine formula (`ebe.dealer_frame_vanna(ebe.bs_vanna(...))`). ZERO network calls, ZERO new-day acquisition, ZERO approximation. **No rerun was required.**

- Production δ side: the v5 persisted `applied_sign` was produced by the fallback0 patch calling `resolve_vol_surface_sign` → the REAL SVI `deviation_by_strike` path (rich +1 / cheap −1 / |dev|≤0.01 deadband → 0) — **NOT** the v3 IV-vs-chain-median proxy. δ_new is a MEASURED nonzero value (−1×BS), **not** an assumed 0.

### Per-cluster symmetric D_conv (12 resolvable clusters)

| Ticker | Day | Src | n_strikes | D_conv | weight | disagree/strike |
|---|---|---|---|---|---|---|
| QQQ | 20260508 | seed-v3 | 34 | 0.5001 | 1.491e+04 | 27/34 |
| QQQ | 20260605 | seed-v3 | 40 | 0.3017 | 3825 | 27/40 |
| QQQ | 20260716 | seed-v3 | 36 | 1.3161 | 1.592e+05 | 27/36 |
| QQQ | 20260731 | seed-v3 | 30 | 1.4593 | 3.094e+04 | 26/30 |
| SPY | 20260605 | seed-v3 | 25 | 0.5285 | 9702 | 20/25 |
| QQQ | 20260617 | v4 | 44 | 0.0074 | 4215 | 39/44 |
| QQQ | 20260729 | v4 | 47 | 0.1885 | 1.088e+05 | 34/47 |
| SPY | 20260413 | v4 | 48 | 0.0069 | 1.782e+05 | 40/48 |
| SPY | 20260430 | v4 | 31 | 0.0623 | 7.331e+04 | 24/31 |
| SPY | 20260617 | v4 | 35 | 0.6390 | 2641 | 32/35 |
| SPY | 20260728 | v4 | 43 | 1.3009 | 3.384e+05 | 32/43 |
| SPY | 20260729 | v4 | 34 | 0.0000 | 6.526e+04 | 24/34 |

- **Mean D_conv across clusters (unweighted, each cluster one datum): `0.5259`**
- D_conv distribution across 12 clusters: [0.5001, 0.3017, 1.3161, 1.4593, 0.5285, 0.0074, 0.1885, 0.0069, 0.0623, 0.6390, 1.3009, 0.0000]
- **Pooled mass-weighted D_conv (Cem's raw formula over all per-strike rows): `0.7445`**
- Per-strike disagreement fraction: **352/447 = 78.7%** (unweighted rows); pooled weighted disagreement mass = 7.366e+05 / 9.894e+05 total weight.
- (δ_prod, δ_new) pair-count histogram (pooled, all clusters): (-1,-1)=35, (-1,1)=21, (0,-1)=154, (0,1)=143, (1,-1)=34, (1,1)=60 — distance 0 = same sign, 1 = zero-vs-nonzero, 2 = opposite signs.

### VERDICT

**OPEN / RE-ADMIT TO EVIDENCE** — pooled mass-weighted D_conv = 0.7445 >= 0.50 ⇒ production's real SVI deviation map is materially INDEPENDENT of the flat −1×BS benchmark (mean cluster D_conv = 0.5259). Mechanism OPENS — re-admitted to evidence. NOT a promotion: this measures deviation-magnitude independence, NOT causality; mechanism stays descriptive/conditional until Cem APPROVES.

### What this can and cannot establish

- **CAN:** R8 measures how far production's REAL SVI deviation map is from a flat −1×BS benchmark, on BOTH sides, magnitude-weighted per strike. Material D_conv ⇒ production carries real independent SVI deviation (mechanism OPEN → re-admitted to evidence). D_conv ≈ 0 ⇒ production hugs the −1 baseline (stays demoted).
- **CANNOT:** R8 measures deviation-**magnitude independence**, NOT causality. Even a material D_conv does **not** establish that the deviation is mechanically causal, does **not** license promotion, and does **not** overturn the sign-arm / correlational evidence. Per Cem's boundary, a positive result re-admits to evidence only; the mechanism stays descriptive/conditional until Cem APPROVES.
