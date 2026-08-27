# SDD Progress Ledger — 2026-08-13 Hermes settings remediation

Plan: docs/superpowers/plans/2026-08-13-hermes-settings-remediation.md

## Decisions (Jason, 2026-08-13)
- OpenRouter: **disable as route, NEVER delete the key value** (comment out the line; key preserved).
- TradingView CDP port: **keep 19222** (no change).
- Session: **already reset by Jason** -> web-search fix + all edits live.

## Task status
- Task 1 (OpenRouter decision): complete — decision recorded (disable route, keep key).
- Task 2 (TV port decision): complete — keep 19222.
- Task 3 (disable OpenRouter key): complete — commented out, key value intact (73 chars), no active route, provider=nous. Backup: .env.bak_20260813_openrouter.
- Task 4 (TV CDP port): complete — kept 19222 (no change needed), server.js exists.
- Task 5 (verify powerhour-prep cron): complete — FIXED-CONFIRMED. Wrapper runs clean end-to-end (exit 0, full prep report written). Stale WSL error in jobs.json from the prior 08-12 .sh run; next scheduled 13:00 CT run should flip last_status to ok — post-run confirm worthwhile.
- Task 6 (session reset): complete — Jason reset; web_search verified live (CPI query returned results); watchdog script exits 0.

## Verification notes
- web_search live after reset (confirmed 2026-08-13): returned real CPI results, core 2.5%.
- web_search_watchdog_cron.py: exit 0 (silent/healthy) before reset; web-search-watchdog cron (00b13c000c47) scheduled 9-18 M-F.

## Authorization Hardening ledger (Dealer-Exposure-Dev branch)
Plan: staged acquisition authorization hardening
- Authorization Task 1: complete (16c6fb8..de8ae55, review clean)
- Authorization Task 2: complete (c9cdb52..c5aa129, review clean)
- Authorization Task 3: complete (e4660ea..7e51936, review clean)
- OpEx Calendar Stages 1-3: complete (review clean)
- Authorization Task 4: complete (74e7462..7721d4c, review clean; executor identity + post-validation probe receipts; 3 Minor non-blocking)

## SDD Ledger — 2026-08-18 Direction chart overlay (plan 2026-08-18-direction-chart-overlay.md)
- Pre-flight: Claude's verified fix wave committed as f5cf319 (branch fix/adversarial-audit-20260817). latest_manifest.json data churn intentionally left unstaged.
- Branch: fix/adversarial-audit-20260817 (HEAD f5cf319). Not master.
- Task 1: complete (commits f5cf319..141e0a6, review clean; 4 Minor hygiene, no Critical/Important; ⚠️ closed by controller live-verify: bulk_hist OI returns open_interest key — alias correct)
- Task 2: complete (commits 465bd78 + fix 17b2d39; review approved; I-1 expiry-filter normalization fixed + regression proven to fail pre-fix; 66 passed; ⚠️-3 (replay uses today's expiry list) flagged for Task 3 review)
- Task 3: complete (commit a45a819, review PASS/Approved, 4 Minor, no Crit/Imp; I-1 normalization applied to _pick_expiry controller-endorsed; ⚠️-3 CLOSED: today's-expiry-universe limitation to be documented in Task 6 replay.py docstring + plan Task 6 section)
- Task 3 Minor (record for final review): M-1 no regression test for _pick_expiry dash-as_of normalization; M-2 seen["close"] never asserted in new test; M-3/M-4 hygiene.
- Task 4: complete (commit 081c791, review PASS/Approved, 4 Minor, no Crit/Imp)
- Task 4 Minor (final review triage): M-1/M-2 brief verbatims (@pytest.mark.unit missing, redundant re-imports); M-3 report undercounted mock updates (actual 7, not 6); M-4 venv ruff UP045 on Optional[str] (brief-mandated style, no gate wired).
- Task 5: complete (commit ca89828, review PASS/Approved; backward compat verified at 6 call sites; 71 passed)
- Task 6: complete (commit 87090f5, review PASS/Approved, 4 Minor informational; ⚠️-3 limitation documented in replay.py docstring — CLOSED)
- Task 7: complete (commit 466fbd4, review PASS/Approved; placement verified at lines 366-388 after subplots_adjust before savefig; ⚠️ pixel-level + end-to-end verification deferred to Task 8's real render)
- Task 8: complete (commit aad047d, review PASS/Approved, 3 Minor; PNG pixel-verified by controller: 52 bars, markers + live stamp confirmed)
- ALL 8 TASKS COMPLETE. Branch commits: f5cf319 (pre-flight fix wave) + 141e0a6, 465bd78, 17b2d39, a45a819, 081c791, ca89828, 87090f5, 466fbd4, aad047d
- FINAL REVIEW: VERDICT SHIP (no Critical/Important, no pre-merge fixes; 170 passed re-verified; cheap follow-ups: _pick_expiry dash-as_of regression test, @pytest.mark.unit on replay tests, MEDIUM marker renders filled cyan ▲ vs documented △, replay refetches OHLCV per bar ~4x documented)
- ⏸️ ON HOLD (Jason, 2026-08-18): direction-chart overlay feature iteration STOPPED. Committed state: master @ fedd92f (aad047d CLI + a643979 ≥3/5 gate + fedd92f score-based markers 0=▼sell/3=●hold/4=▲buy/5=◆add). Skills updated to score-based convention. User: "still needs a ton of work" — do NOT resume iteration without explicit go-ahead.
## v2 indicator plan (feat/direction-indicator-v2) — resumed full-auto 2026-08-18
- Task 1: complete (commit fb3150a, review PASS/Approved, no Crit/Imp; RTH 09:30<=t<16:00 exclusive + unparseable-ts->[] adjudicated correct; ⚠️ carry: ThetaData may stamp 15:45-16:00 bar at 16:00 -> RTH filter could drop final bar — verify live in T2+)
- Task 2: complete (commit 1495fb8, review PASS/Approved, no Crit/Imp; disclosures adjudicated ACCEPTED: count_waves->wave_type=="impulse_wave_3", adx>25 strict per entry point, trend is single-TF (Task 5 must not expect multi-TF); ⚠️ carry: 16:00 close-bar RTH drop -> one-bar lag on last bar, confirm live; RED-phase report-only)
- Task 3: complete (commit 53f3116, review PASS/Approved, no Crit/Imp; row-key handling adjudicated defensible, whale_scanner byte-identical; ⚠️ carry to T8: live scanner row-key (premium vs premium_paid) + use_csv string-coercion unverified live; ⚠️ carry to T4: ~5s SDK transport timeout -> stage per-bar flow calls)
- Task 4: complete (commit 6f8badb, review PASS/Approved, no Crit/Imp; rule deviation adjudicated: v1 max-pain-within-2% governs per brief's match-v1-if-readable clause; ⚠️ carry to T8: live dealer.weighted_greeks wire format (header placement) unverified — one-off live smoke before production wiring)
- Task 5: complete (commit 880186d, review PASS/Approved, no Crit/Imp; mirror contract verified side-by-side IDENTICAL to signal_generator.generate; signal_generator.py byte-identical; notes: bar_eval default interval 15m, NONE entries carry {} signals)
- Task 6: complete (commit 9cc2fda, review PASS/Approved, no Crit/Imp; lazy resolution adjudicated; 3 Minor informational; ⚠️ carry to T8: injectable generate_fn contract divergence (kw vs positional) — recheck when T8 wires a real generate_fn; CLI breakage expected until T8)
- Task 7: complete (commit 66dd373, review PASS/Approved, no Crit/Imp; 3 Minor informational: linear scan perf, dup-ts first-wins, ts:None edge; ⚠️ carry: naive-tz assumption on obs.timestamp — production bars must be naive local datetimes)
- Task 8: complete (commit 8bf3f9f, review PASS/Approved, no Crit/Imp; live run 78 bars scores VARY [0]x50->[1]x14->[2]x8->[3]x6 — v2 point PROVEN; controller pixel-verified PNG (78 candles, ▼+● markers, no lime/gold); ⚠️ carry: flow premium key unverified 3/3 live timeouts — whale leg degrades off, needs re-probe; ▲/◆ paths unit-tested only, not live)
- Task 9: complete (commit ca62dfd; full regression 226 passed on the brief's exact suite, 228 passed incl. tests/test_render_direction_chart.py; root tests/ 683 passed / 44 skipped / 5 failed — all 5 pre-existing in tests/test_suite_validation.py (Vol_Suite output-marker validation: required_file NVDA_gamma_records check, strict-env default, json-serializable checks), file + shared/schemas.py + shared/suite_validation.py untouched by branch; scope-boundary diff EMPTY for the six Direction module files + signal_generator.py; indicator.py docstring honest-data contract appended; WIKI.md v2-indicator section appended). Calibration: v2 closed-bar per-bar semantics = data as of bar_ts INCLUSIVE via intraday_bars_as_of; whale flow intraday via PH v2 flow.scanner_trades_in_time_range (bar's own 15-min window, not v1 EOD wall); dealer gamma SAMPLED coarse-grid every N bars, last regime HELD neutral between grid points — never interpolated-as-fact, never fabricated; OI max-pain EOD (OI settles daily by nature); unfetchable per-bar legs degrade to neutral (NONE/0/False) — documented in both indicator.py docstring and WIKI.md.
- Task 9: complete (commit ca62dfd, review PASS/Approved, no Crit/Imp; scope boundary held — 6 modules + signal_generator.py 0-byte diff vs master; 226/228 passed; 5 root failures pre-existing test_suite_validation.py; docstring honest-data contract + wiki v2 section landed)
- ALL 9 TASKS COMPLETE (v2 indicator). Commits: fb3150a, 1495fb8, 53f3116, 6f8badb, 880186d, 9cc2fda, 66dd373, 8bf3f9f, ca62dfd

## position-gated markers (feat/position-gated-markers) — started 2026-08-19
- Task 1: complete (commit ea16315, review PASS/Approved, no Crit/Imp; helper unused by draw loop as required)
- Task 2: complete (commit 492270f, review PASS/Approved, no Crit/Imp; 2 Minor informational; controller re-ran test_candlestick_chart.py)
- Task 3: complete (commit 5334216, review PASS/Approved, no Crit/Imp; live markers all none — plan-correct, no 4/5; 54 passed)
- ALL 3 TASKS COMPLETE (position-gated markers). Commits: ea16315, 492270f, 5334216

## native chart app phase 1 (feat/native-chart-app) — started 2026-08-19
- Task 1: complete (commit 6668a63, review PASS/Approved; ⚠️ carry to T8: add chart_app/tests to pyproject testpaths)
- Task 2: complete (commit 22447b1, review PASS/Approved; warmup <50 plan-mandated)
- Task 3: complete (commit 9bfff5d, review PASS/Approved; live HIGH/MEDIUM honest-off until whale sampled)
- Task 4: complete (commit 897a80a, review PASS/Approved)
- Task 5: complete (commit 3009528, review PASS/Approved; 2 Minor: zoom reset on poll, sell=rotated triangle)
- Task 6: complete (commit be5413f, review PASS/Approved; controller re-ran 16 passed; Direction/dashboard empty)
- Task 7: complete (commit 968a24a, review PASS/Approved; /api/order 404; skill outside git)
- Task 8: complete (commit 75fa9f2, review PASS/Approved; 22 bars 07-20→08-18; testpaths fixed)
- ALL 8 TASKS COMPLETE (native chart app). Commits: 6668a63 22447b1 9bfff5d 897a80a 3009528 be5413f 968a24a 75fa9f2

## Swaps Dashboard Split + Chart Tab (worktree-swaps-dashboard-split) — started 2026-08-27
Plan: docs/superpowers/plans/2026-08-27-swaps-dashboard-split.md (CARL-hardened, 3 review rounds, 15 findings fixed, converged SHIP)
Worktree: .claude/worktrees/swaps-dashboard-split, branch worktree-swaps-dashboard-split, base master@18cbf47
Baseline: dashboard/tests/ 215 passed, 0 failures.
- Task 1: complete (commit 18cbf47..d126ac2, review Approved; 1 Minor self-resolving lint note re unused imports until Task 2 uses them)
- Task 2: complete (commit d126ac2..95b90c2). Note: resumed from an interrupted prior attempt -- swaps_dashboard/app.py had been reverted to the Task-1 stub but a full Step-3 implementation survived as a stray root-level .app_backup.py; verified its content matched the plan's Step 3 code exactly, restored it as swaps_dashboard/app.py, deleted the backup file, then staged/committed per the plan's file list. swaps_dashboard/tests/ 6 passed; dashboard/tests/ 211 passed (215 baseline - 4 moved into test_swaps_options_cache.py's new home = 211, no regression). grep-verified dashboard/app.py still needs SwapsQuery/SwapsLoader/_db per plan's non-deletion list.
- Task 3: complete (commit 95b90c2..5ba126c, general-purpose subagent, controller-verified). swaps_dashboard/tests/ 8 passed; dashboard/tests/ 211 passed, no regression. Controller re-verified: dashboard/app.py's /health (2148) and _db() (125) untouched, SwapsQuery/SwapsLoader imports retained (still used by _database_stats/_ingestion_state/_scrape_log), CrossSourceQueryBuilder import removed as required.
- Task 4: complete (commit 5ba126c..25f6780, general-purpose subagent, controller-verified). swaps_dashboard/tests/ 12 passed (8+4 new); dashboard/tests/ 211 passed, no regression. Controller re-verified CARL R2-F3 isolation (every test patches both swaps_app.DB_PATH and swaps_app.orchestrator.get_recent_swap_activity) and CARL R1-F1 'ingestion' key present in snapshot schema.
- Task 5: complete (commit 25f6780..1e21c7b, general-purpose subagent, controller-verified). New swaps_dashboard.bat/.sh (port 8788, mirrors dashboard.bat's venv/dep/port-collision-guard/browser-launch pattern). No test changes; swaps_dashboard/tests/ 12 passed, dashboard/tests/ 211 passed.
- Task 6: complete (commit 1e21c7b..00861b5, general-purpose subagent, controller-verified). dashboard/app.py's Overview+Tools pages now read the snapshot via _swaps_snapshot() + new _swap_card.html partial; _database_stats/_ingestion_state/_scrape_log/_top_notional removed from dashboard/app.py (grep-confirmed gone). Controller re-verified _db()/_orchestrator_runs()/_lookup_run() untouched and "Orchestrator run history" widget preserved in index.html. dashboard/tests/ 217 passed (211+6 new test_swap_card.py); swaps_dashboard/tests/ 12 passed.
- Task 7: complete (commit 00861b5..0da45c9, controller-authored directly -- test-only task, no implementation, per brief's own note it was already satisfied by Tasks 2/3/6). dashboard/tests/test_no_swaps_imports.py added; passed.
- Task 8: complete (commit 0da45c9..b6319f8, general-purpose subagent, controller-verified). New /chart route + chart.html (iframes chart_app:8791), nav "Swap trades"->"Chart" retargeted. Controller-confirmed no lingering /swaps route in dashboard/app.py (grep empty). dashboard/tests/ 220 passed (218+2 new); swaps_dashboard/tests/ 12 passed.
- Task 9: complete (commit b6319f8..9ed63a8, general-purpose subagent, controller-verified). dashboard.bat/.sh now headlessly auto-launch chart_app (uvicorn :8791, /min, no browser tab of its own) right after the existing port-8787 guard block. dashboard/tests/ 220 passed, swaps_dashboard/tests/ 12 passed, no regression. ALL 9 CODE TASKS COMPLETE -- only Task 10 (manual end-to-end verification) remains.
- Task 10: PARTIAL (no commit -- verification-only task, per plan). Step 6 automated checks done by controller: dashboard/tests/ 220 passed + swaps_dashboard/tests/ 12 passed (run as SEPARATE invocations -- plan's exact Step 6 combined command `pytest dashboard/tests/ swaps_dashboard/tests/ -q` hits a real pytest module-name collision, both dirs resolve to bare module `tests` since dashboard/ and swaps_dashboard/ are intentionally __init__.py-less namespace packages; not an implementation bug, a plan-text oversight -- flag for whoever owns this plan doc). Full default `pytest -q` collection: 2428 passed, 19 failed + 7 errored, ALL confirmed pre-existing/unrelated via `git diff --name-only master...HEAD` (zero overlap with Vol_Suite dual-pipeline-gate, decode_upis, phase2, market_signals, suite_validation, integration_quant_orchestrator, backtesting_tool -- none touched by this branch). Steps 1-5 (live dashboard.bat/swaps_dashboard.bat boot + browser verification) NOT performed -- worktree has no .venv of its own, .bat scripts can't launch from here; needs the main repo tree's shared .venv, most naturally done after merge.
