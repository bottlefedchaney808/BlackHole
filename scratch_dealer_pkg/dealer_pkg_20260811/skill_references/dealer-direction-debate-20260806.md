# Worked Example — Dealer-Direction Method Debate (2026-08-06)

First full run of the multi-agent-debate protocol. Jason: "perform a multi agent
deep debate … Which method is the best with the given information. Perform a full
pre plan spec … a weaker boundary than the all must submit standard."

## Setup

- Subject: dealer-positioning direction methods v1 (oi_heuristic), v2 (replication),
  v2.1 (vol_surface_replication), v4 (oi_flow), v5 (Direction 5-signal @min_score=3),
  v5@1 (whale-only) — evidence kit `CARL_reference_dealer_direction.md`
  (sha256 prefix 2640c9b2).
- Locked decisions: (1) Demeterfi vol replication untouched; (2) headless default
  v2.1 until release-note flip; (3) no re-running the daily pooled panel; (4) all
  models stay selectable.
- Reviewers: 5-agent R1 panel (parallel) → 1 cross-examiner (R2) → judge (R3).
  Diversity: reduced (subagents only, no cross-family CLI on PATH).

## R1 Panel Ledger (condensed)

| Panelist | Verdict | #1 pick | Key move |
|---|---|---|---|
| v4 OI-Flow defender | SHIP_WITH_CAVEATS | v4 | v5's pooled evidence invalid (dead v5 column in @3 pooled run; whale-only pooled never printed verdict) — AAPL fits the documented MU-v1 false-positive profile |
| v5@3 defender | DEFENDED | v5@3 (gate 2 live) | AAPL perm p 0.0015 = first hypothesis-consistent significant result in the line; v4 all-null (best p 0.15) |
| v5@1 whale-only | UPHELD | v5@1 | Whale-only reproduces every @3 outcome; @3 live-silent 0/6; concedes $25K threshold = price-level artifact |
| v2.1 defender | default stays | v2.1 | "Referee cannot distinguish any model — null is neutral"; AAPL live divergence (surface DAMPEN vs flow AMPLIFY) is information; continuity pick |
| Empirical referee | — | HYBRID | Reported "4 significant cells in ~30 tests split 2 right/2 wrong = noise"; R2 cross-examiner CORRECTED this to **6 cells, 2 right / 4 wrong** (the referee's own statistic was arithmetically wrong — see corrections below). AAPL signal-probability 0.40 (MU-v1 profile); AMD doesn't kill family but falsifies uniform sign |

## Referee's decisive findings (with R2 corrections)

- No method is a demonstrated daily-horizon predictor (0.90 confidence).
- v5 fixes the classifier (non-degenerate splits) but the sign is name-dependent:
  AAPL coef<0 both gates (0.0015/0.0030), AMD coef>0 both gates (0.0020/0.0055).
- **R2 CORRECTION 1 (cell count):** the referee's "4 significant cells in ~30,
  2 right/2 wrong" is wrong. True matrix: 6 significant cells, 2 right (AAPL@3,
  AAPL@1) / 4 wrong (v1-NVDA, v2-AAPL, AMD@3, AMD@1). Worse for the family —
  but sign-consistency per name across two independent gate configs says
  NAME-DEPENDENCE, not noise.
- **R2 CORRECTION 2 (Bonferroni):** family-wise over the 10 v5 tests (5 names ×
  2 gates) keeps BOTH AAPL cells (0.0015×10=0.015, 0.0030×10=0.03); the
  ×30 cross-model correction (whale-only 0.09 fails) wrongly includes degenerate
  v1/v2 cells that shouldn't count toward the family.
- **R2 CORRECTION 3 (premium math):** whale premium = volume × OPTION close ×
  100 (`whale_scanner.py`), NOT spot — one ATM SPY contract ≈ $1–1.5K, not
  $76,977. Both the whale-only defender's concession AND the v4 attacker's
  critique relied on the misread. The $25K threshold is still easier to clear on
  rich names (~30–115 bps of notional across the basket) but ~20–30× less broken.
- The @3 backtested gate is silent in production (0/6 live) — shipped gate must
  match the backtested gate (min_score=1).

## Judged winner (hybrid)

**Whale-only direction (DEALER_DIRECTION_MIN_SCORE=1) × v4's NO_CALL honesty gate
× classification-only labeling** ("book-side classification read, NOT a vol
forecast"), headless default stays v2.1 per locked decision (2). Ranking:
v5@1 > v4 > v5@3 > v2.1 > v1/v2.

Implementation conditions from the referee: NO_CALL gate on; $25K whale threshold
is calibration debt (normalize to premium-relative before trusting expensive
underlyings); AMD/SPY wrong-sign is a family-wide identification caveat (level-OI
magnitude under the flow sign), not a v5 defect; do NOT claim per-name
amplification semantics where the backtest sign is inconsistent.

## Escalated → RESOLVED by Jason (same day)

The decisive experiment — a properly wired pooled-panel run of v5 — collided
with locked decision (3) "do not re-run the daily pooled panel" (both earlier
pooled runs invalid: dead v5 column; truncated mid-fetch). Jason PERMITTED one
re-run (E1). Outcome (n=494, SPY/AAPL/NVDA/TSLA/AMD, ticker-FE, 2000 perms,
`pooled_20260806_011706/`):

- **v5 is the ONLY model with a negative aggregate coef** (−0.0002, t −1.13;
  block perm p 0.0825 — best of any sign model, suggestive but NOT <0.05).
- AAPL survived pooling (family sign holds — did NOT collapse like MU v1).
- AMD's wrong sign is a name effect; it does not flip the aggregate.
- **M2 (net delta-OI) is the only significant pooled relation (block p 0.0245)
  and it is INVERTED (positive)** — the strongest significant dealer-flow signal
  in the line points against the short-gamma→amplify premise.
- Consequence: classification-only labeling is mandatory; no model validates the
  channel's core premise in aggregate.

E4 (threshold calibration) also APPROVED + IMPLEMENTED: `WHALE_THRESHOLD_BPS`
env + `threshold_bps` param in `Direction/whale_scanner.py` — bar = bps/10_000 ×
spot × 100 (bps of one-contract ATM notional); 0/unset = legacy $25K unchanged.
Units lesson: legacy $25K ≈ 3,250 bps of SPY notional — the knob takes values in
the hundreds-to-thousands bps range, not tens.

The judged winner (whale-only × NO_CALL gate × classification-only labeling,
v2.1 default) STANDS, now with pooled support it lacked at ruling time.

## Reusable packet structure

Each panelist got: paths (evidence kit + backtest summary files + spec), the key
perm-p tables inline, locked decisions, position brief with known attacks, and a
YAML response schema (artifact_quote_back / access_proof / verdict / case /
attacks / ranking / best_method / confidence). Panelist full outputs were saved
to `~/.hermes/cache/delegation/subagent-summary-{0..4}-*.txt` and read in full
before composing R2. NOTE for future runs: tell panelists the artifact identity
is a FILE sha256, not a git ref — four of five spent effort verifying it via
`git cat-file`.
