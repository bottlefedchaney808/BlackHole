# Dealer Positioning V5 — Improvement Recommendations Debate — Final Pre-Plan SPEC (2026-08-11)

> **Status: JUDGED SPEC (pre-plan).** Output of a 6-panelist CARL debate
> (R1: 5 lens advocates + empirical referee → R2: cross-examiner → R3: judge)
> on `docs/DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md`
> (21 recommendation headers, file sha256 `6d716e1e…` — a FILE hash, not a git ref).
> User's convergence standard: **HIGH win threshold** — single program needs
> supermajority (#1 honest pick of ≥4/6 AND top-2 by ≥5/6 AND referee-verified
> AND R2-surviving); else a referee-endorsed hybrid (≥3/6 top-2 core + R2
> verification); else NO_WINNER → escalate to Jason.
> Nothing here is implemented. This is the pre-plan for the successor to
> `debate-final-spec-20260808.md`.

---

## 0. Executive summary

**No single program wins. A staged hybrid wins.** Under the high threshold,
Gate A fails for every program (max #1 picks = 2/6; referee verification is
unclean for all five). Gate B clears: the referee endorses the staged hybrid,
its core (STAT-RIGOR) is top-2 for 5/5 advocates, and R2 independently
verified all referee arithmetic (Holm-Bonferroni exact at every M, weighted
ranking all six cells, 6-cell recount, M2 identity) plus the hybrid's central
factual claims.

**Headline message for Jason:** the *reproducible* evidence base currently
points **away** from the short-gamma premise — every committed-config
reproduction of the recorded decision row flips **positive** (t +3.4…+4.5,
p 0.0005), and the line's only significant pooled relation (M2 net delta-OI,
block p 0.0245) is **inverted**. The highest-value work is **evidence
re-baselining**, not more tuning. Seven items are escalated to Jason as
owner decisions (see §5).

---

## 1. Debate results

### 1.1 Panel

| Role | Seat | Honest best pick | Ranking (1st→6th) |
|---|---|---|---|
| STAT-RIGOR advocate | 1 | **1 STAT-RIGOR** (forced single experiment: V0 arm) | 1, 5, 2, 3, 4, 6 |
| FLOW-INPUT advocate | 2 | **2 FLOW-INPUT** (R-T3-1 same-phase graft) | 2, 1, 4, 3, 5, 6 |
| SURFACE-MECH advocate | 3 | **1 STAT-RIGOR** (differs from own seat) | 1, 3, 2, 5, 4, 6 |
| HYGIENE advocate | 4 | **4 HYGIENE** (concedes R-T3-1 = highest-value item) | 4, 1, 3, 5, 2, 6 |
| MAGNITUDE-ONLY/DA advocate | 5 | **5 MAGNITUDE** (amended: 2-day R-T1-1+R-T1-3+R-T1-4 block) | 5, 1, 6, 4, 2, 3 |
| Empirical referee | 6 | **HYBRID (staged)** | weighted: 1, 2, 3, 4, 5, 6 (4.70/4.25/4.15/4.00/3.25/2.80) |
| R2 cross-examiner | — | **HYBRID (staged, 3 corrections)** | 1, 2, 3, 4, 5, 6 (post-attack) |

### 1.2 Key verified numbers

- V5 per-expiry headline: t = −1.919, block perm p = **0.0680** (NOT significant); full-perm p = 0.0135 (fails Holm at M≥4 by 0.001 — retire from headline use).
- Regime counts identical across all 4 per-expiry variants: SPY 39/46 (85% one-sided), AAPL **46/46 (100% — degenerate, zero classification variance)**.
- v1 (oi_heuristic): "block p < 0.02" on the same test, unreconciled.
- Pooled n=494: v5 only negative-sign model (coef −0.0002, t −1.13, block p 0.0825); **M2 net delta-OI the only significant pooled relation (block p 0.0245), INVERTED (positive)**.
- **Recorded decision row (coef −0.0003, t −1.919, SPY 39/46, AAPL 46/46) came from an UNCOMMITTED working tree** (decision runs 08-08 19:58–20:10 predate the first commit of `_compute_sabr_deviation_for_expiry` at 08-09 02:16). Every committed-config reproduction is POSITIVE: ungated +0.0010/t+4.503/p 0.0005 (SPY 0/46, AAPL 4/46); gated +0.0009/t+3.472 (SPY 24/46, AAPL 19/46). Closest reproduction (−0.0002/t−1.547/SPY 35/AAPL 43) requires NON-default knobs (uniform + MIN_SCORE=1 + GATE=0 + 30d) — not the shipped default.
- Holm-Bonferroni (M≥4 defensible): **zero results survive family-wise correction in the premise-supporting direction**.
- Structural gap (R-T3-1): backtest v5 leg consumes per-day LEVEL OI (`backtest_stage3.py:407,426,806,831`); live model consumes 150d accumulated book (`dealer_positioning.py:862`).
- Gate history: `5f9833c` removed the SABR gate AND flipped the gate regression test's assertion in the same commit, shipped green ("378 passed"); `89d2b28` restored gate + tests. Gate test exists in-tree (`test_dealer_positioning_direction.py:209,219`) but is **env-fragile**: fails under `DEALER_DIRECTION_GATE=0` (reproduced live by driver and R2).
- `_CALL_PUT_RATIO` symbol does **not exist** (grep zero hits); 1.2 is an inline literal (`whale_scanner.py:114-116`). `WHALE_THRESHOLD_BPS` code default is "0" (legacy $25K), 3000 bps is the calibrated recommendation, never shipped as default.
- SHADOW-1/2/3 env flags: **absent from code** (grep-verified) — specified 08-08, never shipped.
- Whale `scan()` picks the single nearest expiry (`whale_scanner.py:80`) — 0DTE-biased on SPY/QQQ; whale is the **only** direction source (other 4 Direction signals are boolean conviction gates only).

### 1.3 Referee corrections adopted (C1–C10)

| # | Claim in the recommendations doc | Verdict |
|---|---|---|
| C1 | R-T2-5: "no existing permanent regression guard" | **CONTRADICTED in-tree** — gate tests exist (`test_dealer_positioning_direction.py:209,219`, added `89d2b28`). Survives only as verify-and-strengthen. |
| C2 | `_CALL_PUT_RATIO = 1.2` (hardcoded constant) | **Symbol fabricated** — inline literal; extraction is the first step. |
| C3 | §0 "any live run silently runs ungated vol_surface_replication" | **Mis-framed for this tree** — canonical WSL tree is V5 single-model (`dealer_positioning.py:294-295`); applies only to the stale `/mnt/c/Users/bottl/FinancialDevelopment` snapshot (not a target). |
| C4 | §5.2/§8 master-spec citations | Numbering unverifiable (master spec has no §5.2/§8); quoted content verified via skill reference docs instead. |
| C5 | MU "1-of-112" degenerate split | Exact count unverifiable in readable docs (denominator matches logs). |
| C6 | ESC-2/ESC-3 attribution | Minor slip — ESC-3 is the REF verification protocol, not a sign-flip item. |
| C7 | 0.0680 vs 0.0710 | Different models' cells (v5 vs v4) — not an inconsistency. |
| C8 | "17 recommendations" | Miscount — the doc has **21** headers (5+6+7+3). |
| C9 | WHALE_THRESHOLD_BPS "calibrated 3000" | Code default is "0"; 3000 is the calibration, not shipped default. |
| C10 | R-T3-1 "independently proposed by both, unprompted" | Unverifiable externally; documented only in the doc. |

---

## 2. Judged winner with explicit criteria

**WINNER: STAGED HYBRID** (referee-endorsed, with R2's three corrections folded in).

### Gate application (high threshold)

**Gate A (single program) — FAILS.** #1 honest picks: STAT-RIGOR 2/6, FLOW-INPUT 1/6, HYGIENE 1/6, MAGNITUDE 1/6, SURFACE-MECH 0/6, REFEREE 0/6 (picks hybrid). Needs ≥4/6 — max is 2/6. Additionally, referee verification is unclean for every program: R-T2-5 falsified in-tree (C1), `_CALL_PUT_RATIO` fabricated (C2), R-T3-1's evidence row unreproducible from committed code, R-T2-4 conflicts with ESC-2 measure-first (`debate-final-spec:127`), R-T4-3 is scoping-only.

**Gate B (hybrid) — CLEARS.**
1. Referee ranks the staged hybrid as best answer — YES (`best_program: HYBRID`).
2. Core top-2 support ≥3/6 — hybrid core = STAT-RIGOR, top-2 for **5/5 advocates** (STAT #1, FLOW #2, SURFACE #1, HYG #2, DA #2) + referee's own ranking #1.
3. R2 verification — R2 independently re-derived all referee arithmetic (Holm boundaries exact at every M 2…34; weighted ranking all six cells; 6-cell recount 2 right/4 wrong; M2 = net delta-OI) and source-verified the hybrid's central claims (R-T3-1 seam gap, gate env-fragility reproduced live, 5f9833c test-flip reproduced).

**R2's three corrections folded in:**
1. **R-T1-1 V0 arm runs fully in Stage 1** on the corrected harness — Stage-0 V0 dropped or relabeled a harness smoke test (non-decisive).
2. **R-T2-5 recast as verify-and-strengthen** (replay 5f9833c diff + realistic-surface variant), not new work.
3. **R-T4-1 gated** on a Stage-1 reproducing config.

**NO_WINNER NOT declared** — the hybrid is a coherent, referee-endorsed, R2-verified answer, not a forced pick. If Jason rejects the hybrid framing and demands a single-program commitment, the honest fallback is **NO_WINNER → escalate with the ranked landscape** (see §5).

### Dissent recorded

- **STAT-RIGOR adv** — overruled in part: his program wins the hybrid's CORE, but the standalone bundle (falsified R-T2-5, mis-sequenced R-T4-1) fails Gate A. His P&L caveat is carried: "if the true objective were move P&L this quarter, R-T2-2 is most likely to change outcomes."
- **FLOW-INPUT adv** — overruled: R-T2-2 demoted to Stage 2 conditional (fabricated symbol C2, unmeasured causal claim, self-test SHADOW-2 is vaporware until R-T3-4 ships). The graft demand is honored: R-T3-1 lands in Stage 1 ahead of any input tuning.
- **SURFACE-MECH adv** — overruled on his own seat: mechanism fixes deferred to Stage 2 conditional; R-SM-A/B/E pins adopted as riders.
- **HYGIENE adv** — overruled: flagship claim contradicted in-tree (C1); rails absorbed into Stage 0, not funded as THE program.
- **DA/MAGNITUDE adv** — overruled on ordering: V0 arm accepted as the architecture-level test but moved fully to Stage 1; R-DA-1 go/no-go and R-DA-2 M2 note adopted.
- **REFEREE** — ENDORSED: the judged winner is the referee's proposal with R2's amendments.

### Ranking (final)

1. **STAT-RIGOR** (hybrid core) — R-T3-1 seam gap source-verified; Holm confirmed; degenerate-leg charge confirmed.
2. **MAGNITUDE-ONLY/DA** — only architecture-level test (V0 arm); M2 inversion verified; cheapest decisive experiment.
3. **HYGIENE** — strongest incident evidence (5f9833c test-flip + env-fragility, both reproduced); cheapest total; rides along with Stage 0.
4. **FLOW-INPUT** — real defect (single-expiry 0DTE read; whale = sole sign source) but damaged by C2, unmeasured causal claim, self-test gated.
5. **SURFACE-MECH** — code-verified pathologies but refuted-in-scope flip premise, R-T2-4 no-op history, R-T3-3 costliest.
6. **REFEREE** — not a program; verification discipline verified clean; no evidence of its own.

---

## 3. Implementation phases (pre-plan)

### PHASE 0 — cheap audit + rails (no new data, ~2–3 days)

| Item | File(s) | Test | Acceptance |
|---|---|---|---|
| R-T2-5 verify-and-strengthen | `Vol_Suite/tests/test_dealer_positioning_direction.py` | Replay `5f9833c` diff on a worktree: existing gate tests (:209, :219) must fail red on the removed-gate tree; add realistic-surface variant beyond `_FakeSurface` mocks | pytest green WITH `DEALER_DIRECTION_GATE=0` exported (today: red); replay documented red |
| H-2 hermetic gate test | same file | monkeypatch `DEALER_DIRECTION_GATE` explicitly | file green under backtest env |
| H-3 live-entry startup assertion | `Vol_Suite/dealer_positioning.py` | `run_dealer_positioning` raises if `CANONICAL_SIGN_MODEL != 'direction'` (complements :354 ValueError) | live-entry test raises |
| H-4 BACKTEST_MODE marker | `Vol_Suite/dealer_positioning.py` | explicit marker required to honor `DEALER_DIRECTION_GATE=0`; live entry with var set, no marker → raises | raises/passes as specified |
| H-1 anti-flip pre-commit hook | repo CI config (~30 lines) | blocks any commit touching sign-resolution that flips a gate-test assertion (replay full `5f9833c` diff) | hook blocks it |
| R-T1-2/3 honest re-report | `docs/superpowers/specs/per-expiry-direction-backtest-20260808.md` + new spec | SPY-only recompute reporting **effective variance** (SPY 39/46 = 7 flips/46, not n=46); harness check flags >75% one-sided legs | recompute table with effective-variance column |
| R-T1-4 family pre-specification | spec doc | family M≥4 committed BEFORE any sweep, INCLUDING future R-T2-1 cells (STAT-RIGOR rec #4, R-DA-3); retire p=0.0135 from headline framing | family statement committed |
| REPRODUCIBILITY LEDGER | CI check | every recorded decision row in docs/ carries env block (min_score, bps, gate state, per_expiry mode, tickers, lookback, working-tree commit) | decision row without env block fails CI |
| R-T4-3 intraday scoping note (parallel, ungated) | new note | intraday pooled-panel permutation result + R-DA-4 pre-committed closure rule (keep/close with evidence) | note contains both |
| V0 arm | — | **NOT in Phase 0** (R2 correction #1); at most a labeled non-decisive smoke test | labeled |

### PHASE 1 — pivotal code (~1–2 weeks)

| Item | File(s) | Test | Acceptance |
|---|---|---|---|
| R-T3-1 accumulation seam | `Vol_Suite/backtest_stage3.py` v5 leg (:400-432) | wire causal 150d accumulated book (`dealer_positioning.py:862`; accumulation from history up to day d is no-lookahead) into the v5 leg; keep level-OI leg for delta reporting. Numeric known-answer fixture (locked #3); reproducibility contract test (STAT-RIGOR rec #1: recorded row coef ±0.0001, short-counts ±2/46 from shipped defaults — **fails red when the seam is wired in**) | fixture green; level-vs-accumulated delta documented |
| Corrected R-T2-1 sweep | `Vol_Suite/backtest_stage3.py`, `Vol_Suite/pooled_panel_backtest.py` | grid = `DEALER_DIRECTION_GATE {0,1}` × min_score × bps, **gate state recorded per cell** (referee rec #1); closure ONLY on shipped default reproducing the recorded row (non-default = documented override needing Jason sign-off); conditional-invalidation clause (R2 #4): results void if the seam changes outcomes; each report states its harness | sweep table with per-cell env block; closure status decided |
| V0 null arm (R-T1-1) | new magnitude-only arm in `backtest_stage3.py` | run on BOTH level-OI and accumulated harnesses, delta reported (referee rec #3 / R2 #3); **R-DA-1 go/no-go pre-registered BEFORE running**: sign arm must beat magnitude block-perm p by ≥0.02 AND t ≥0.3 more negative; else freeze daily-horizon tuning → move budget to R-T4-3 | decision rule committed before execution; referee (ESC-3 protocol) confirms tolerances unadjusted |
| R-T4-1 pre-registration | — | **gated** on a Stage-1 reproducing config (R2 correction #3) | no ThetaData spend before |

### PHASE 2 — conditional (only if Stage-1 measurement demands)

| Item | File(s) | Gate |
|---|---|---|
| R-T3-4 SHADOW-2/3 | `dealer_positioning.py` + `Direction/whale_scanner.py` env flags (SHADOW-3 spec at `debate-final-spec:94-98`) | Stage-1 measurement shows the monitoring gap matters |
| R-T2-2 whale multi-expiry | `Direction/whale_scanner.py:77-80` rewrite + FLOW-1 blended per-bucket bias + per-day sign-disagreement log (test = SHADOW-2; 30-day shadow, >15% disagreement escalates) | SHADOW-2 shipped first (R2: FLOW cannot self-verify until R-T3-4/FLOW-1 lands) |
| R-T3-2/3 deadband/jackknife | `dealer_positioning.py:541` (+`vol_surface_reference.py:323-350` baseline) — R-SM-A measure-then-smooth (30-day log-only deadband-crossing monitor; flip rate >15% justifies R-T2-4 else no-op), R-SM-B tanh at expiry-bias level, R-SM-D expiry-level jackknife gated to <20-strike chains, R-SM-E RMSE_REFERENCE pinned | measurement shows wing instability; known-answer fixture for deadband |
| R-T2-3 ratio extraction | `whale_scanner.py:114-119` inline literal → env/param (FLOW-2) with E4 calibration record | only after Phase-1 evidence re-baseline |

---

## 4. Expansion / function suggestions (debated and narrowed)

- **DEFERRED:** R-T3-5 (aggressor side; FLOW-3 de-risk = confidence weight, not hard reclassification), R-T3-6 (ΔOI; FLOW-5 de-risk = 0.5-weight conviction gate) — both for ThetaData rate risk + staged-pairs fetch conflict. R-T4-1 gated. Further knob tuning until R-T3-1 lands.
- **CARRIED AS RIDERS:** R-DA-2 inverted-M2 one-page mechanism note (0.5 day, ≥1 falsifiable implication); FLOW-4 covered-write known-answer fixture (~20 lines, satisfies locked #3 — documents the sold-call-prints-bullish bug); R-T1-5 startup assertion as insurance (H-3); R-T2-6 tripwire (H-4 + BACKTEST_MODE).
- The doc's §3 "run all five Tier-1 items" default is **superseded** by hybrid sequencing.

---

## 5. Open decisions / escalations (7 NEEDS_HUMAN — Jason)

Each with a **recommended default if Jason is silent**:

1. **RECORDED-ROW UNREPRODUCIBILITY** (data-integrity; the most important). Recorded row came from an uncommitted tree; committed reproductions all positive. *Default: formally re-baseline the per-expiry §5 evidence table against committed-config reproductions (positive coef, t +3.5…+4.5, p 0.0005); retract the 39/46 / 46/46 headline from claims; Stage-1 R-T3-1 + corrected sweep establishes the new baseline.*
2. **M2-INVERSION × LABELING** (product decision). The line's only significant pooled relation points against the premise. *Default: ship classification-only labels (§4.2); fund R-T4-2 zero-work labeling; remove "$X net dealer gamma" forecast framing from rendered artifacts.*
3. **WHALE_THRESHOLD_BPS default "0" vs calibrated 3000** (`whale_scanner.py:25`). *Default: keep "0" (legacy $25K) until the Stage-1 calibration sweep lands; document the calibration record before any flip.*
4. **Doc §3 "run all five Tier-1" default.** *Default: adopt hybrid Stage 0/1/2 sequencing; if budget-constrained, the 2-day no-new-data block (R-T1-1+R-T1-3+R-T1-4, DA amendment) is the highest-value cheap unit.*
5. **Gate env-fragility + in-commit test-flip pattern** (process decision). *Default: ratify H-1 (anti-flip pre-commit rule) + H-2 (hermetic gate test) — 2-line test fix + ~30-line hook.*
6. **R-T4-3 intraday scoping** (1–2 days, ungated, 3/6 panelists want it now). *Default: green-light in parallel with Phase 0.*
7. **R-T4-1 pre-registration budget** (ThetaData pulls; 5-ticker/120-day circuit breakers). *Default: hold all spend until a Stage-1 reproducing config exists, then bounded pulls with circuit breakers.*

---

## 6. Protocol record

- Artifact: `DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md` sha256 `6d716e1e…` (FILE hash).
- R1: 6 subagents in one batch (deleg_44a49b83) → R2: 1 cross-examiner (deleg_40bbd06e) → R3: 1 judge (deleg_98e5ae89; first dispatch died on a 503, re-dispatched).
- Panelist full outputs: `~/.hermes/cache/delegation/subagent-summary-{0..5}-20260811_012918_*.txt` (R1), `subagent-summary-0-20260811_013606_345641.txt` (R2), `subagent-summary-0-20260811_013948_163085.txt` (R3).
- Companion files: `debate-20260811-v5-recommendations-preflight.md`, `-verified-facts.md`, `-r1-ledger.md`.
- Protocol lessons applied: file-hash artifact identity (panelists told it's NOT a git ref); R2 dispatched after R1 (never same batch); R2 independently recomputed referee arithmetic (clean this time — the 08-06 count error did not recur); driver spot-verified every load-bearing claim before writing this spec.
