# Dealer Positioning v2 Deep Debate — Final Spec (2026-08-08)

## Debate Summary

8 agents across 3 rounds (R1 panel + R2 cross-examination + R3 judge) debated two tracks:
- Track A: Improving dealer positioning post-whale-sign-change
- Track B: Replication weight math verification ("do weights drop from use?")

**Evidence kit**: `docs/superpowers/specs/debate-evidence-kit-20260808.md`
**Preflight record**: `docs/superpowers/specs/debate-preflight-20260808.md`
**Prior debate**: `~/.hermes/skills/autonomous-ai-agents/multi-agent-debate/references/dealer-direction-debate-20260806.md`

---

## Verdicts

### Track A: Positioning Improvements → Status Quo Upheld

**Winner: R1-A2 (Conservative Defender)**

The R2 emergent finding was decisive: binary-sign aggregation reproduces DDKZ-weighted aggregation within 6% magnitude and 100% directional agreement across 4 tested scenarios. Every Track A proposal that touches the sign/aggregation layer is a no-op.

| Proposal | Disposition |
|----------|-------------|
| Weighted sign | NO-OP (<6% delta) |
| Continuous sign | NO-OP (same mechanism) |
| Multi-day accumulation | DEFERRED — different product, not tested |
| Per-expiry direction bias | DEFERRED — untested, needs production data |

Confidence: 0.87

### Track B: Replication Weight Verification → Weight Decay Is Expected and Correct

**Winner: R1-B2 (Weight-Drop Disproof)**

Three independent pillars:
1. Anti-correlation is structural (r=-0.4177 corrected, persists on dense chains)
2. 3,018x scaling mismatch is a straw-man (binary vs weighted agree within 6%)
3. Design contract at dealer_positioning.py:339-343 explicitly documents intentional discarding

B1's valid residual: information loss IS real, but it's a trade-off not a bug, and anti-correlation makes magnitude-weighting actively harmful.

Confidence: 0.87

---

## Key Mathematical Findings

### DDKZ Weight Profile Is Asymmetric

The earlier agent's claim that "replication weights drop from use" was **partially correct but incomplete**:

| Side | Behavior at 30% OTM | Mathematical Reason |
|------|---------------------|---------------------|
| Calls | Decay to 68% of peak | f''(K) = 2/(TK²) → 0 as K → ∞ |
| Puts | **Grow 14.7×** | f''(K) → ∞ as K → 0 |

On realistic chains (±30% OTM), call-side weights only vary by 1.46× — not a dramatic collapse. The 3,018x raw weight range is real but irrelevant once multiplied by realistic OI (which decays rapidly at deep OTM).

### R1-REF Correlation Error

R1-REF claimed call-wing r(weight, gamma) = +0.766. Actual: **-0.4177**. This is a sign error, not rounding. R2 caught it — same pattern as the Aug-6 debate where the referee miscounted cells.

**Process fix**: Future referee agents must report correlation type, conditioning variables, sample selection criteria, and sign convention.

---

## Improvement Spec

### DO NOT CHANGE (Protected Code Paths)

| Code Path | Current Behavior | Rationale |
|-----------|-----------------|-----------|
| `_resolve_sign()` L322-401 | Binary ±1 with OTM gate | R2: <6% delta vs weighted. Minimax-optimal under p=0.0825 |
| Aggregation L839: `sign * gamma * OI` | Magnitude from gamma*OI | Anti-correlation r=-0.4177 makes |w| harmful |
| `_otm_leg_weights()` replication L431-449 | Weights ≥0, keys reused as gate | Math verified. Keys-only reuse is intentional |
| `resolve_direction_call()` L302-317 | NO_CALL when |net| < floor_frac × gross | Honesty gate working as designed |

### SHADOW TESTS (Diagnostics Only, No Production Impact)

**SHADOW-1: Weighted-sign deviation monitor**
- Path: New logging hook after L839 aggregation
- Log: `weighted_sign = Σ(gamma_i × oi_i × sign_i × w_i) / Σ(gamma_i × oi_i)` vs binary sign
- Alert: If |Δ| > 0.15 on any primary ticker for 3 consecutive days → reopen debate
- Duration: 30 trading days minimum
- Env flag: `DEALER_SHADOW_WEIGHTED_SIGN=true`

**SHADOW-2: 0DTE contamination diagnostic**
- Path: Telemetry in `_resolve_sign` logging
- Log: Fraction of total |gamma×OI| from 0DTE expiries; sign-flip rate when 0DTE excluded
- Alert: If sign-flip rate > 15% → escalate to P0 gate
- Duration: 30 trading days

**SHADOW-3: Illiquid chain density monitor**
- Path: Warning log when strikes per expiry < 15
- Log: Strike count, SABR fit stability, sign-flip rate
- Alert: If sign-flip rate > 25% on chains < 15 strikes → add minimum-strike gate
- Env flag: `SABR_STABILITY_WARN=true`

### DO NOT SHIP

| Proposal | Proponent | Reason Rejected |
|----------|-----------|-----------------|
| Weighted sign | R1-A1 | No-op (<6% delta). Complexity for zero gain. |
| Continuous sign | R1-A1 | Same mechanism — no-op. |
| Magnitude-weighted aggregation (|w|) | R1-B1 | Anti-correlation r=-0.4177 suppresses gamma-rich strikes 57-88%. Actively harmful. |
| Multi-day accumulation | R1-A1 | Untested, different product. |

---

## Escalations

### ESC-1: 0DTE Contamination — OPEN, BLOCKING for new tickers

| Field | Value |
|-------|-------|
| Severity | UNKNOWN — needs production data |
| Plausibility | HIGH — 0DTE gamma is orders of magnitude larger |
| Action | Production data pull: fraction of total OI from 0DTE on each tracked ticker |
| Timeline | 30 trading days of telemetry |

### ESC-2: Illiquid Chain SABR Noise — MONITOR

| Field | Value |
|-------|-------|
| Severity | LOW for SPY/QQQ (200+ strikes), UNKNOWN for illiquid |
| Action | Add minimum-strike warning log. Do not implement sign-smoothing until measured. |
| Timeline | Before next illiquid-ticker deployment |

### ESC-3: REF Verification Protocol — PROCESS FIX

| Field | Value |
|-------|-------|
| Severity | PROCESS — did not affect final outcome (R2 corrected) |
| Issue | R1-REF reported r=+0.766, actual r=-0.4177. No methodology documented. |
| Action | Require second-reviewer for empirical claims supporting ship/don't-ship decisions. |

### ESC-4: p-value 0.0825 Stability — QUARTERLY CHECK

| Field | Value |
|-------|-------|
| Severity | LOW but structural — A2's defense rests on this |
| Action | Re-run evidence-base test quarterly. Document trend. |

---

## Debate Ledger

### Track A Claims

| # | Claim | Agent | Disposition | Key Evidence |
|---|-------|-------|-------------|-------------|
| A1 | Weighted sign improves signal | R1-A1 | REFUTED | R2: 100% directional agreement, <6% magnitude Δ |
| A2 | Continuous sign captures more info | R1-A1 | REFUTED | Same mechanism as weighted — no-op |
| A3 | Multi-day accumulation | R1-A1 | DEFERRED | Different product, not tested |
| A4 | Per-expiry direction bias | R1-A1 | DEFERRED | Contradicts design contract; untested |
| A5 | Binary sign + deadband is minimax-optimal | R1-A2 | UPHELD | p=0.0825 + R2 emergent finding |
| A6 | FM-1 (weighted sign) is a no-op | R1-A3 | VERIFIED | R2 confirmed <6% delta |
| A7 | FM-2 (SABR noise) is dangerous | R1-A3 | PARTIALLY VERIFIED | True for illiquid, not production for SPY/QQQ |

### Track B Claims

| # | Claim | Agent | Disposition | Key Evidence |
|---|-------|-------|-------------|-------------|
| B1 | f''(K) = 2/(TK²), call weights decay 68% at 30% OTM | R1-B1 | VERIFIED | Math correct, but irrelevant post-OI |
| B2 | Put-side weights grow 14.7× at 30% OTM | R1-B1 | VERIFIED | Offset by OI decay at deep OTM |
| B3 | 3,018x scaling mismatch is "the real issue" | R1-B1 | REFUTED | R2: binary vs weighted agree within 6% |
| B4 | Magnitude-weighted aggregation should replace current | R1-B1 | REFUTED | Anti-correlation r=-0.4177 makes it harmful |
| B5 | BS gamma decays 500×, making weight decay irrelevant | R1-B2 | VERIFIED | Gamma decay dominates |
| B6 | DDKZ weights anti-correlated with gamma | R1-B2 | VERIFIED | Corrected r=-0.4177; persists on dense chains |
| B7 | Adding |w| suppresses gamma-rich strikes 57-88% | R1-B2 | VERIFIED | Follows from B6 |
| B8 | Design contract at L339-343 is intentional | R1-B2 | VERIFIED | R2 Attack 4 confirmed; trade-off not bug |

### Referee Claims

| # | Claim | Agent | Disposition |
|---|-------|-------|-------------|
| R1 | DDKZ weights ≥ 0 | R1-REF | VERIFIED |
| R2 | Asymmetric decay (calls decay, puts grow) | R1-REF | VERIFIED |
| R3 | _resolve_sign discards magnitude | R1-REF | VERIFIED |
| R4 | Aggregation = sign × gamma × OI | R1-REF | VERIFIED |
| R5 | Call-wing r(weight, gamma) = +0.766 | R1-REF | FALSIFIED — actual -0.4177 |

### R2 Cross-Examination

| # | Attack | Disposition |
|---|--------|-------------|
| X1 | Anti-correlation is grid-spacing artifact | PARTIALLY REFUTED — artifact real (97.2%) but correlation persists (r=-0.2496 on 200 strikes) |
| X2 | 3,018× straw-man | VERIFIED — binary vs weighted: 100% directional, <6% magnitude |
| X3 | SABR noise in production | REFUTED for primary tickers (200+ strikes) |
| X4 | Design contract vs info loss | VERIFIED — trade-off, not bug |
| X5 | Put-side weight growth | VERIFIED — 14.7×, partially offset by OI decay |
| X6 | 0DTE contamination | ESCALATED to ESC-1 |

### Emergent Findings (Post-R1)

| # | Finding | Implication |
|---|---------|-------------|
| E1 | Binary sign reproduces DDKZ-weighted within 6% | Debate practically settled without code changes |
| E2 | OTM gate is the dominant feature; strike-weighting is secondary | Validates binary-sign + OTM-gate design |
| E3 | Anti-correlation robust to chain density | B2's case is structural, not artifactual |

---

## Final Confidence: 0.85

| Component | Confidence | Rationale |
|-----------|-----------|-----------|
| Track A verdict (status quo) | 0.88 | Emergent finding empirical and cross-validated |
| Track B verdict (B2 wins) | 0.87 | Three independent confirmations |
| Improvement spec (no changes) | 0.86 | Follows directly from verdicts |
| 0DTE escalation severity | 0.38 | Architecturally plausible but zero production data |
| R1-REF error correction | 0.95 | R2 caught it; corrected value consistent with theory |
| Process integrity | 0.72 | Met the bar, but only because R2 caught a material sign error |

**Key risk**: The R2 emergent finding (<6% delta) was tested on only 4 scenarios. Unusual production OI profiles could breach the 6% bound. The shadow test is the safeguard.

---

**One-line verdict**: Ship status quo. Shadow-log weighted-sign deviation and 0DTE share for 30 days. Do not touch the strike-aggregation design contract. The debate validated the design rather than finding bugs.
