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
