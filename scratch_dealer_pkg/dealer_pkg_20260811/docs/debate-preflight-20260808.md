# Preflight Record — Dealer Positioning v2 Deep Debate (2026-08-08)

## Mode
Multi-agent adversarial debate (CARL-compatible panel). Evidence-driven.

## Subject
Two parallel debate tracks:
- **Track A**: Improving dealer positioning now that the whale sign model (v5/direction) won the Aug-6 debate. What changes push the edge further?
- **Track B**: Replication weight math verification — do DDKZ Appendix A weights "drop from use" at deep OTM, and is this a problem for vol_surface_replication?

## Scope
**IN scope**: sign model improvements, weight magnitude integration, multi-day accumulation, continuous vs binary sign, replication weight decay analysis, numerical precision concerns, SABR reference curve behavior at extremes.

**OUT of scope**: deleting sign models, changing the DDKZ recursion math itself, new data sources, execution infrastructure changes.

## Locked Decisions (do not relitigate)
1. DDKZ 1999 Appendix A recursion is mathematically correct.
2. Headless default stays vol_surface_replication (v2.1) until release-note flip.
3. All 5 sign models stay selectable; no model is deleted.
4. Whale threshold_bps knob (WHALE_THRESHOLD_BPS, default 3000 bps) exists and is calibrated.
5. The NO_CALL gate (DEALER_NO_CALL_FLOOR_FRAC=0.05) stays as the output honesty layer.

## Evidence Kit
File: `docs/superpowers/specs/debate-evidence-kit-20260808.md`
SHA256 prefix: 5444b4f398b769bc (FILE hash, NOT a git ref)

Source files:
- `Vol_Suite/dealer_positioning.py` (1700 lines, 5 sign models)
- `Vol_Suite/vol_surface_reference.py` (485 lines, SABR + quadratic fitters)
- `Vol_Suite/replication_reference.py` (665 lines, DDKZ recursion + accumulation)
- `Vol_Suite/DEALER_POSITIONING_V2_DESIGN.md` (design doc)
- Prior debate reference: `~/.hermes/skills/autonomous-ai-agents/multi-agent-debate/references/dealer-direction-debate-20260806.md`

## Acceptance Gates
1. Every claim must cite path:line or derive from first principles.
2. Mathematical arguments must include equations, not just verbal descriptions.
3. Each argument must state its own weakest point (risk acknowledgment).
4. A point is "won" only if the opposing agent cannot produce a valid refutation.
5. The empirical referee's arithmetic is independently verified by the cross-examiner.
6. The judge picks winners; dissent is recorded. Stalemate → escalated to Jason.

## Reviewer Plan
- R1: 6-agent panel (3 positioning + 2 math + 1 empirical referee), parallel dispatch
- R2: 1 cross-examiner (after R1, attacks every position + verifies referee math)
- R3: 1 judge/synthesizer (receives R1+R2, declares winners, drafts spec)
- Driver verification: spot-check every finding against source before final spec

## High Threshold for Winning Arguments
Per Jason's instruction "make the threshold to win an argument or point high":
- **Mathematical derivation required**: No verbal arguments. Claims about weights or positioning must include the actual formula or algorithm step.
- **Falsifiability required**: Every argument must include a specific condition under which it would be proven wrong.
- **Concrete example required**: Positioning proposals need worked examples with numbers. Weight claims need specific formula references.
- **Counterargument survival**: A point is only "won" if the opposing agent cannot produce a valid refutation.
- **Risk acknowledgment**: Winning arguments must explicitly state their weakest point.
