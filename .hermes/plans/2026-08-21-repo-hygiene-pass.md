# Repo hygiene pass — FinancialDevelopment (2026-08-21)

Operator: "do some hygiene on this whole repo. Move docs to docs, organize
everything, trash things we dont need. Update notes and update Obsidian.
Full auto, PM dispatches team."

## Audit findings (verified 2026-08-21)

1. **13 root-level .md docs** (all tracked). Several referenced from live
   code/docs: CLAUDE.md, Procfile (DEPLOY.md), quant_bridge.py comment
   (START_HERE/SETUP_GUIDE), k8s/deployment.yaml, system/*.service,
   tests/test_integration_quant_orchestrator.py, cross-doc refs.
   Precedent: `docs/archive/2026-08-10-root-completion-docs/` did the same
   move once; pattern recurred.
2. **docs/Video Summary/** — 541MB, entirely untracked: 492MB broken
   `.mp4.part` download, 34MB mp3, 15MB clips, 150KB transcripts (valuable:
   0DTE Anderson/Lakha content), 7 helper scripts.
3. **trading_journal/** — 138 untracked generated `ALERT_*.md`, untracked
   desk notes/monitor/x_buzz, modified `scripts/rh_monitor.py` (live position
   baselines, 2026-08-19/20 update). 103 tracked.
4. **Root logs** (5, all gitignored, untracked): backfill_rebuild.log,
   backtests_{full,owniv,}_stderr.log, dashboard_debug.log.
5. **Root scripts** — ALL live (refcounted): do not move entry points named
   in CLAUDE.md/START_HERE/SETUP_GUIDE/Procfile. Orphans:
   - root `test_*.py` (6 files) — NOT in pyproject testpaths (pytest never
     collects them). Move to tests/, run, archive if stale.
   - debug_identity.py (4 lines, 1 ref), pool_integration_example.py,
     quick_pool_verification.py, load_test_pool.py — verify, likely archive.
   - _cron_iv_watchdog.py + iv_watchdog_scan.py — tracked, refs only in
     historical handoff docs. Keep but move to scripts/? NO — keep at root,
     they may be wired to Task Scheduler; moving a cron entry point risks
     breaking a scheduled job. Keep.
   - dashboarttest1.bat — tracked typo test double of dashboard.bat → trash.
6. **dealer_positioning_package_20260811.zip** (144KB, tracked) — shipped
   artifact → untrack into artifacts/ (gitignored).
7. **docs/ root**: 5 tracked screenshots + image.png (check refs → docs/img/
   or archive), 1.4MB variance-swap PDF (→ docs/references/),
   GAP_VS_OTHER_BUILD_2026-08-07.md (→ docs/archive/),
   zinko-beta-note-authed-url.md (stale authed-URL note → trash).
8. **gitignore gaps**: `.ruff_cache/` (pytest_cache already there),
   `trading_journal/ALERT_*.md`, `trading_journal/x_buzz/*.json` +
   `_baseline.json`.
9. **`.claude/hooks/trading_mode_auto_guard.py`** — untracked, not wired into
   .claude/settings.json. Small safety guard; commit as-is (review content).
10. **sentiment-scanner latest_manifest.json** — tracked generated data,
    modified → refresh commit per repo convention.

## Rules for workers

- NO git mutation commands (add/commit/mv/reset) in workers — plain
  `mv`/`cp`/`mkdir`/`rm -i`-safe moves only. PM commits centrally afterwards
  (parallel `git mv` in one repo = index contention).
- Trash = move to `_trash/2026-08-21/` (gitignored), never `rm`.
- Verify every reference before moving (rg, exclude .venv/.worktrees/
  orchestrator_output/outputs/node_modules/docs-archive).

## Worker assignments (Wave 1, parallel)

- **W1 docs**: move 13 root .md → `docs/guides/` (keep CLAUDE.md at root),
  GAP doc → docs/archive/, zinko note → _trash, screenshots → docs/img/,
  PDF → docs/references/. Rewrite ALL references (CLAUDE.md, Procfile,
  quant_bridge.py, k8s/, system/, cross-docs, tests). Verify zero dangling
  refs. agent-kb-lessons.md → docs/guides/.
- **W2 journal+gitignore+hooks+manifest**: trading_journal triage (commit
  candidates listed, ALERT ignore, x_buzz json ignore, rh_monitor diff
  reviewed), .ruff_cache ignore, _trash/ created + ignored, hook file
  reviewed, manifest noted for refresh commit.
- **W3 video**: media (.part/.mp3/clips/image.png) → ~/Videos/oi-0dte-
  anderson/ (outside repo), .part also to _trash; transcripts+scripts →
  docs/research/oi-0dte-anderson/ with README.
- **W4 tests+artifacts**: root test_*.py → tests/, run them via
  .venv/Scripts/python.exe -m pytest, report pass/fail per file; zip →
  artifacts/; logs → _trash; dashboarttest1.bat → _trash; verify pool
  examples/debug_identity refs → report disposition.

Wave 2 (parallel): **W5 Obsidian** vault updates + PM commits + full pytest.
