# Tree Status — where the work actually stands

*Written 2026-09-13. Ground truth for the uncommitted working tree (`master`, ahead 9 of origin).*

The status bar shows **+5351 −553, 19 untracked** and looks like dirt. It is not dirt. It is
three workstreams that never got committed together, plus a handful of genuine cut candidates.
This page says which is which, so the next session doesn't re-derive it.

---

## What the 5351 is made of

| Workstream | Files | Content | Verdict |
|---|---|---|---|
| **Math-audit fixes** (F1–F15, CARL pass 2026-09-11) | ~20 | Integrated variance in `jump_variance_share`, unlabeled corr/vol refusal, GARCH boundary-pin honesty, aggregated-var from group P&L, no net-notional divide, zero-net → unclassified regime, `_resolve_weights` label enforcement | **KEEP — this is the payload** |
| **Pickability + requires contract** (2026-09-12) | ~12 | `ModuleSpec.is_pickable()`, `superseded_by`, 409-on-unpickable, `requires` auto-pull + skip-when-satisfied, dashboard seed of Context Store | **KEEP — one coherent change** |
| **Desk book / console book** | ~8 | `dashboard/console_book.py`, `shared/desk_settings.py`, dealer-book panel, widget jobs | **KEEP** |
| **ThetaData stub-quote guard** | 2 | `_usable_quote_price` in `shared/thetadata.py` — off-hours NBBO stub bid is not a spot price | **KEEP — this killed the NOK false edge** |
| **`rh_tech_read.py`** | 1 | MCP token path `AppData/Local/hermes` → `.hermes` | **KEEP — 1-line real fix** |
| **Formatting debris** | ~8 | black reformat inside `dealer_positioning.py` etc.; 1-line backtick ghosts in wiki 04/07 | **KEEP (harmless) — don't cherry-pick them out** |

Untracked, by disposition:

| Path | Lines | What it is | Verdict |
|---|---|---|---|
| `tests/test_module_pickability.py` | 91 | Pins the picker contract | **KEEP (commit)** |
| `tests/test_module_requires_expansion.py` | 77 | Pins dependency expansion/skip | **KEEP (commit)** |
| `tests/test_thetadata_eod_gaps.py` | 95 | Pins loud EOD gaps | **KEEP (commit)** |
| `tests/test_desk_book_fixes.py` | 159 | Pins desk-book fixes | **KEEP (commit)** |
| `Vol_Suite/tests/test_output_dir_isolation.py` | 77 | Pins output-dir isolation | **KEEP (commit)** |
| `dashboard/console_book.py` | 239 | The desk's second book | **KEEP (commit)** |
| `shared/desk_settings.py` | 62 | Operator-preference persistence | **KEEP (commit)** |
| `tui/findev_tui_mcp.py` + `mcp_config_example.json` | 113 | FastMCP server for the TUI | **KEEP (commit)** |
| `docs/audits/2026-09-11-*` + `_math_audit_checks_*` | — | Audit findings, repo audit, executable checker | **KEEP (commit)** — evidence |
| `Vol_Suite/docs/PLAN_dealer_flows_book_20260825.md` / `THEORY_*` | — | Dealer-flows book design docs, DRAFT status | **KEEP (commit)** — method docs |
| `docs/superpowers/{plans,specs}/2026-08-31-party-taxonomy-*` | — | Party-taxonomy classifier plan/spec | **KEEP (commit)** |
| `artifact-boards/` | — | Interactive boards root | **KEEP (commit)** |
| `.cursor/` | — | Cursor editor skill | **CUT from git — add `.cursor/` to `.gitignore`** |
| `NOK_20261218_chain_scan_*.{csv,png}` | — | Output of the 09-09 stub-quote-bug run | **CUT — delete** (the bug that produced the false "EDGE DETECTED" is now guarded) |
| `chain_strategies.json` | — | Same run's verdict file, `vol_regime: CHEAP` derived from a stub bid | **CUT — delete** (same reason) |

Grok EventTrading worktree (`~/.grok/worktrees/bottl-eventtrading/feat-expansion`, `main` @ `2c374d6`):
effectively clean — only scratch `data/` (1.4 MB) + `.superpowers/`, both throwaway. **CUT from
any future commit — gitignore them there.** The desk commits themselves are already in `main`.

---

## Math audit — did it auto-fix?

Yes. The 2026-09-11 CARL pass (R1 grok-4.5 → R2 grok-4.6, verdict SHIP_WITH_CAVEATS) applied the
patches in this same working tree; that is why the audit fixes and the 5351 are the same diff.
Full disposition table: [2026-09-13-math-audit-verification.md](../audits/2026-09-13-math-audit-verification.md).
Short version: **F1, F2, F4, F5, F6, F8, F9, F12, F15 fixed and pinned by tests; F7, F10, F13,
F14, F16 verified correct, no change needed.** Still open: F3 (Merton jump-param recovery needs a
calibration runner to finish) and R2-F6 (unlabeled `position_vals` still applied positionally).

---

## Verification status

- Pinned new-test block: `tests/test_module_pickability.py`, `test_module_requires_expansion.py`,
  `test_thetadata_eod_gaps.py`, `test_desk_book_fixes.py`, `test_module_registry.py` →
  **41 passed, 1 skipped** on the working tree.
- Full suite (3014 collected): first `-x` run stopped at
  `Vol_Suite/tests/test_dealer_position_book.py::test_sparse_gap_strike_skipped` — **a stale test
  at HEAD, not a working-tree regression.** Commit `1018a70` changed new-strike handling from
  skip to last-observed-OI carry-forward (documented in-code as NEW-STRIKE RESOLUTION) but left
  the old test asserting the skip behavior — while its own `n_new_strikes == 1` assertion
  already contradicted that. Test renamed `test_sparse_gap_strike_solved` and re-pinned to the
  documented behavior; file passes 14/14. Full-suite rerun in flight.

## Next action

One commit (or three logical ones: audit fixes / picker contract / desk book) of everything marked
KEEP above, then the two CUT deletions and two `.gitignore` lines (`.cursor/`, `data/` in the grok
worktree). Nothing in the KEEP set needs further review before it lands.
