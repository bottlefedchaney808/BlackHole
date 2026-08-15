# Causal-Arm v2 — PRE_WINDOW Re-Acquisition Decision Table (2026-08-15)

Model stays **DESCRIPTIVE/CONDITIONAL** throughout. A positive β' is re-admission evidence only, never auto-promotion. No day's ΔIV was imputed.

## Provenance census (7 genuinely-new unique days, SPY+QQQ = 1 unit)

- intended causal units = 7  (the genuine Apr 13–23 2026 gap; every other day is already held in the 62-day corpus, v4/v5 list, or the SPY/QQQ seed corpus Apr 24–Aug 14)
- **PRE_WINDOW n/N = 1/7**  (coverage 0.1429)
- associational exclusions = 6; hard gaps = 0
- FAIL-LOUD fired: coverage < 100% of intended causal units. Causal corpus reduced to the passing unit(s); every failed day stays associational-only and is excluded from causal claims.

| day | families | habitat | dte | firing | pre_vanna | ΔIV_pre_window | prov | daily_ret | breach_ret | daily_neg | breach_pos |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 20260414 | QQQ,SPY | NONE | 3 | 0 | -489261 | 0.0053 | PRE_WINDOW | 0.0107 | -0.0001 | False | False |
| 20260415 | QQQ,SPY | NONE | 2 | 0 | 200869 | — | ASSOCIATIONA | 0.0098 | — | False | None |
| 20260416 | QQQ,SPY | NONE | 1 | 0 | -47860 | — | ASSOCIATIONA | 0.0022 | — | False | None |
| 20260420 | QQQ,SPY | NONE | 4 | 0 | 492895 | — | ASSOCIATIONA | -0.0012 | — | True | None |
| 20260421 | QQQ,SPY | NONE | 3 | 0 | -87155 | — | ASSOCIATIONA | -0.0063 | -0.0003 | True | False |
| 20260422 | QQQ,SPY | NONE | 2 | 0 | -260186 | — | ASSOCIATIONA | 0.0057 | — | False | None |
| 20260423 | QQQ,SPY | NONE | 1 | 0 | -78710 | — | ASSOCIATIONA | -0.0021 | -0.0001 | True | False |

## Pre-event vanna exposure summary (source: 7 genuinely-new units, control days)

- n=7 control days; mean=-38,487; min=-489,261; max=492,895; spread (max-min)=982,157
- all units are control (NONE habitat); mixed DTE 1–4 within the locked 1–10 band; both SPY and QQQ per unit; same-day = one independent unit.

## Orthogonalized driver — CAUSAL-ELIGIBLE PRE_WINDOW-only corpus

- effective unique-day n = **1**
- ΔIV provenance = `PRE_WINDOW` → **CAUSAL-ELIGIBLE**
- rank = 1/11; cond = —; cond_std = —; max_VIF = — (flag=None)
- β' (vanna_orth) = —  se=—  t=—  status=NOT-IDENTIFIABLE  [unavailable: rank-deficient design (rank 1 < p 11)]
- honest β'-power (one-sided) = —  (available=False, n_for_80=None, reach_80=False)
- family rule = False  (missing or non-identifiable family beta)
- BOTH-CLOCK = NOT-CONFIRMED  (blocked by: 0/1 days both-clock; family rule; identifiable β)  [confirmed 0/1 eligible]

## Orthogonalized driver — ASSOCIATIONAL fallback corpus (all new; excluded from causal claims)

- effective unique-day n = **7**
- ΔIV provenance = `DAY_LEVEL-UNVERIFIED` → **ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS**
- rank = 7/11; cond = —; cond_std = —; max_VIF = — (flag=None)
- β' (vanna_orth) = —  se=—  t=—  status=NOT-IDENTIFIABLE  [unavailable: rank-deficient design (rank 7 < p 11)]
- honest β'-power (one-sided) = —  (available=False, n_for_80=None, reach_80=False)
- family rule = False  (missing or non-identifiable family beta)
- BOTH-CLOCK = NOT-CONFIRMED  (blocked by: 0/3 days both-clock; family rule; identifiable β)  [confirmed 0/3 eligible]

## Consolidated decision table

| corpus | n_units | PRE_WINDOW n/N | rank | cond_std | max_VIF | β' | se | power | n_for_80 | family rule | BOTH-CLOCK | causal claim |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CAUSAL-ELIGIBLE (PRE_WINDOW-only) | 1 | 1/7 | 1/11 | — | — | — | — | — | None | False | NOT-CONFIRMED | NONE — <1 unit / NOT powered |
| ASSOCIATIONAL fallback (all new) | 7 | 1/7 | 7/11 | — | — | — | — | — | None | False | NOT-CONFIRMED | **excluded from causal claims** |

## BLUNT POWER READ (do not overclaim)

**The PRE_WINDOW causal corpus is NOT powered for a fresh R1→R2→R3 Cem decision.**

- Causal-eligible PRE_WINDOW units = 1 of the 257-unit β-power target. The genuine new-day pool available for re-acquisition was **only 7 days** (Apr 13–23 2026); every later trading day is already held in the SPY/QQQ seed corpus. There are **no genuinely-new days left to acquire toward 257** without reacquiring held days (forbidden) or expanding the universe beyond SPY/QQQ.
- Of those 7, only 1 carries valid PRE_WINDOW provenance on BOTH SPY+QQQ (20260414). The other 6 are associational-only (no firing bucket on one or both families → no pre-breach ΔIV anchor).
- With n_eff=1, the β' fit is not identifiable/powered: n_eff=1 cannot even estimate the L2 interaction (p≈11) — rank < p. Power is undefined, n_for_80 unreachable in-sample, reach_80=False.
- The associational fallback (n_eff=7) is equally underpowered and is excluded from any causal claim by the fail-closed gate.

**What a positive β' would mean (conditional, not observed here):** if the PRE_WINDOW causal corpus ever reaches n_eff ≥ ~257 with 100% provenance and power ≥ 0.80, a positive, identifiable, opposite-sign-family β' would be **re-admission evidence only** — it re-admits the dealer-frame vanna hypothesis to evidence and triggers a fresh R1→R2→R3 Cem review. It is **never auto-promotion**; the model stays descriptive/conditional until Cem rules.

**Recommendation:** the 7-day re-acquisition is complete but does NOT reach the causal target. Do NOT proceed to R3 Cem on the PRE_WINDOW causal arm with n_eff=1. To honestly reach ~257 effective PRE_WINDOW days would require a new universe expansion (non-SPY/QQQ tickers, or a fresh pre-breach IV capture on re-designated held days), which is outside this task's locked scope.
