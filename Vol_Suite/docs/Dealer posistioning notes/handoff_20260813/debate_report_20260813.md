# Dealer Positioning — CARL Audit + 5-Person Debate (2026-08-13)

**Mode:** CODE audit → SPEC debate (hybrid, per explicit request)
**Subject:** `Vol_Suite/dealer_positioning.py` + accumulation/sign-model wiring, seed-vs-flow question
**Artifact identity:** git HEAD `a4a6f0e` on `master`, verified against source before the panel ran
**Panel:** 5 sequential agents (Quant → MM#1 → MM#2 → Broker-Dealer PM → Risk Manager), each given the full audit dossier + prior transcript

## The headline finding — supersedes the seed-vs-flow question

**The "V5 Direction / sabr_deviation / 150d accumulation" model the repo owner believed was live had never been merged into this repo.** Source-verified as of the audit:

- `dealer_positioning.py:251` — `VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication')`. No `'direction'` model existed. `grep -rn "CANONICAL_SIGN_MODEL"` across `Vol_Suite/*.py` → zero matches.
- `compute_dealer_positioning()` defaulted `sign_model='oi_heuristic'` — same-day snapshot, no moneyness gating. Context-mode/orchestrator path defaulted to `vol_surface_replication` via `VS_SIGN_MODEL` env var — also a same-day snapshot.
- **The accumulation engine had zero live callers.** `replication_reference.compute_accumulated_position` / `_accumulate_from_history` were called only by their own test file and their own `__main__` CLI.
- `sabr_deviation` as a per-expiry *sign source* with a NO_CALL/fallback_bias gate did not exist in this tree.
- The two-vanna bug (scanner computes its own vanna independently from the 4-panel chart) was confirmed still live.
- Screenshot evidence (`Screenshot 2026-08-11 231303/231529/231720.png`) showed two sign models rendered for the same ticker (TGB), same timestamp, with **opposite conclusions** — "V5 Direction... Gamma: DAMPENING" vs "Vol-Surface + Replication (v2.1)... Gamma: AMPLIFYING." Neither was reproducible from current source — both were artifacts from a separate WSL/Ubuntu environment whose work never landed in this Windows git tree (the 2026-08-12 git-flatten/"baseline snapshot" event did not carry it over).
- The WSL-side backtest that "won" and got locked in as canonical was run from an uncommitted tree; a later reproduction attempt flipped the coefficient's sign until a gate was restored.

## Panel verdict on seed vs. flow: NOT resolved — real disagreement survived all 5 rounds

- **Quant**: "Seed is dead" rests on one leg of a two-legged pre-registered test. Near-zero seed_share at 150 trading days is close to mechanically inevitable for any bounded mean-reverting accumulation — that's the null hypothesis, not a discovery. The decisive tests (M2 joint, lead-lag, OOS falsifier vs. locked V5) came back GREY or never ran. Position: don't kill the seed, run the falsifier.
- **MM#1**: Seed value is *not symmetric across products* — single-name OI decays to staleness fast (flow wins near-definitionally there); index/ETF OI has genuinely slow structural components that may retain seed value for months.
- **MM#2 (adversarial)**: Directly challenged MM#1 — index/SPX OI is *not* cleanly structural either; a large share is inter-dealer/MM-to-MM churn that nets to ~zero customer-driven inventory, so sign-flipping it as customer flow may be *more* wrong on index names, not less. **This specific disagreement was not resolved by the panel.**
- **PM**: Regardless of who's right, don't fund the decoration (index/single-name splits, customer-vs-MM filters) before funding the one load-bearing test (OOS falsifier + lead-lag).
- **Risk manager**: Reframed the choice as risk-asymmetry — shipping flow-only when seed was actually informative degrades gracefully; shipping a stale/wrong seed produces a quiet, non-averaging, hard-to-detect systematic bias. That asymmetry argues for defaulting to flow-only pending the falsifier, independent of the p=0.068 coin-flip in the WSL numbers.

## Findings ledger

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | Critical | Live repo contained none of the V5/sabr_deviation/150d-accumulation/whale-gate/SVI/vannaflow work | CONFIRMED, source-verified |
| F2 | Critical | Two sign models rendered side-by-side, opposite conclusions, screenshot-evidenced | CONFIRMED — top near-term operational risk |
| F3 | Critical | Accumulation engine had zero live callers — dead code, not a live bug | CONFIRMED — resolved the "not accumulating live" suspicion |
| F4 | Major | "Seed is dead" conclusion rested on an incomplete test battery | CONFIRMED; panel split on inference |
| F5 | Major | Decision-grade backtest run from an uncommitted tree once already; sign flipped on reproduction | CONFIRMED |
| F6 | Minor | Vanna sign convention flipped mid-investigation (3rd sign-convention flip in this codebase's documented history) | CONFIRMED — pattern, not incident |
| F7 | Major | No independent, non-circular validation source exists — validating against spot price action is partially circular | Escalated |
| F8 | Critical | No control stopped a mislabeled/wrong-model chart from being actionable in the UI | Escalated — biggest cross-panel blind spot |

## What the panel converged on (unanimous, acted on this session)

1. Visible model-identity label on every chart, sourced from the running code.
2. Kill dual-render — one sign model on screen at a time, traceable to `VALID_SIGN_MODELS`.
3. Unify vanna computation (scanner + dealer chart share one solver call).
4. Fund the OOS falsifier + lead-lag test against in-tree code, gate any seed-vs-flow claim on it.
5. Stop letting WSL/uncommitted-environment results get called "the winner" in any doc without in-tree reproduction.
6. Render-time + CI-time assertion that the documented canonical sign model matches what's dispatched.

## Human decision (2026-08-13, post-debate)

Repo owner: **keep the seed for now**, run the pre-registered falsifier/lead-lag testing, concentrate engineering on (1) wiring accumulation into the live path for real, (2) fixing the vanna discrepancy, (3) starting the falsifier testing. The customer-vs-inter-dealer OI fraction question (MM#1 vs MM#2's unresolved disagreement) stays a parallel research thread using the existing rich/cheap SABR-deviation marking as the starting method — not a blocker.

**Convergence: partial.** The wiring/governance findings (F1–F3, F8) were unanimous and were acted on this session (see `HANDOFF.md` for what shipped). The seed-vs-flow empirical question (F4) remains open, now with a real (not GREY-by-omission) falsifier result — see `HANDOFF.md` for the current state and why it's still not fully decisive.
