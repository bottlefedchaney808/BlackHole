# Dealer Positioning — Work Package (2026-08-11)

Consolidated spec, debates, notes, evidence trails, and fresh test results for
`Vol_Suite/dealer_positioning.py` and all its consumers.

## Start here

| File | What it is |
|---|---|
| `SPEC_dealer_positioning_20260811.md` | **THE updated spec.** Current canonical model (source-verified), full timeline, condensed debate records, all backtest numbers, the two-vanna fix, open items, operational rules |
| `TEST_RESULTS_20260811.md` | **Fresh test results** (2026-08-11): focused dealer battery, full Vol_Suite, Direction package |

## Historical record (`docs/`)

All documents carry their original date; the ones that describe deleted models
carry SUPERSEDED banners in their own headers. Read them as the record of the
work, not as current behavior — the current model is `direction` +
`sabr_deviation` + 150d accumulation (see the spec).

| Date | Document | Content |
|---|---|---|
| 08-04 | `DEALER_METHOD_V3_SPEC.md` | Accuracy spec M1–M6 + empirical referee (all daily proxies null) |
| 08-05 | `dealer_positioning_brief.md` | Architecture, formulas, thresholds, constraints (pre-debate code briefing) |
| 08-05 | `dealer_positioning_debate_notes.md` | Five-voice debate (Carl unavailable) — full transcript of positions |
| 08-05 | `PLAN_dealer_v2_to_v3_migration.md` | v2 → v3 migration plan (surface-backed path, rejected default flip) |
| 08-06 | `dealer_positioning_debate_report.md` | Consolidated 08-06 debate report (SUPERSEDED banner) |
| 08-06 | `dealer_positioning_test_results.md` | oi_flow implementation + 356 tests + before/after metrics (SUPERSEDED) |
| 08-06 | `CARL_reference_dealer_direction.md` | Evidence kit for the 08-06 CARL debate (v5 direction vs the field) |
| 08-06 | `PLAN_dealer_direction_v5_winner.md` | Judged winner (direction @ min_score=1) + pre-plan spec (SUPERSEDED) |
| 08-08 | `dealer-positioning-v3-impl-plan-20260808.md` | SABR all-strikes + 150d accumulation + per-expiry A/B/C plan |
| 08-08 | `debate-preflight-20260808.md` | 8-agent debate preflight (locked decisions, acceptance gates) |
| 08-08 | `debate-evidence-kit-20260808.md` | Source-code excerpts handed to the panel |
| 08-08 | `r1-ref-empirical-report-20260808.md` | R1 referee verification (weight profile, gamma decay) |
| 08-08 | `r2-cross-examination-20260808.md` | R2 attacks + correlation sign-error catch (0.766 vs −0.4177) |
| 08-08 | `debate-final-spec-20260808.md` | Verdicts: Track A status quo, Track B weight-decay correct; shadow tests |
| 08-08 | `per-expiry-direction-backtest-20260808.md` | sabr_deviation wins the per-expiry comparison (t −1.919, block p 0.0680) |
| 08-09 | `2026-08-09-sabr-deviation-default-evidence.md` | ⚠️ OUTDATED intermediate report — still names the deleted `vol_surface_replication` primary default; superseded by the 08-09 lock |
| — | `DEALER_POSITIONING_V2_DESIGN.md` | Original v2 design doc (reference) |

## Evidence trails (`skill_references/`)

| File | Content |
|---|---|
| `dealer-direction-debate-20260806.md` | Full 08-06 CARL debate worked example (R1 ledger, R2 corrections, E1/E4 outcomes) |
| `two-vanna-divergence-20260809.md` | The two-vanna UROY run evidence trail (root cause chain, memory flip-flop lesson, stale artifacts) |
| `stale-model-comparison-uuuu-20260809.md` | UUUU side-by-side: canonical direction vs deleted v2.1 fossil (provenance checklist, 100× hedge-gap reconciliation) |
| `backtest-v5-sabr-reproducibility-20260809.md` | The lost-working-tree backtest saga: uncommitted decision runs, gate removal sign flip, reproduction numbers, operational rule |

## Test results (`test_results/`)

Fresh 2026-08-11 pytest output, scrubbed environment
(`env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest`):

- `vol_suite_full.txt` — full `Vol_Suite/tests` run
- `direction_package.txt` — full `Direction/tests` run (75 passed)
- `focused_dealer.txt` — focused dealer battery (sign-model, v5 wiring, run-modes smoke)

## Verification

```bash
cd /home/bottl/Financial_Development
env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest Vol_Suite/tests -q
env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest Direction/tests -q
```
