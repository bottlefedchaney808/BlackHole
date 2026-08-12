# Migrated Adoption Follow-Through Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transfer the remaining high-value migrated assets into `FinancialDevelopment` without touching sentiment-scanner logic, dashboard code, or `Tools/`, then remove migrated-only clutter that was explicitly not adopted.

**Architecture:** Keep `FinancialDevelopment` as the canonical repo and treat `C:\Users\bottl\Financial_Development_MIGRATED` as a read-only source. The work is split into three independent slices: documentary capture into curated archive folders, low-risk workflow script adoption into `scripts\`, and targeted `Options_Suite` regression-test recovery. Cleanup is folded into each task so the repo ends with only adopted artifacts and an explicit archived record of what was kept.

**Tech Stack:** Python 3.11+, pytest, PowerShell on Windows, existing repo `docs\archive\`, git hook conventions, existing `Options_Suite` and `VaR_Tools_Simulations` test harnesses.

## Global Constraints

- Keep `FinancialDevelopment` as the only canonical working repo.
- Do **not** port or modify `sentiment-scanner\scanner\theta_integration.py`; sentiment is explicitly out of scope.
- Do **not** port or modify dashboard code or `Tools\`; the current repo is ahead there.
- Prefer copying historical docs into curated archive locations over overwriting any live operational docs.
- Prefer adapting scripts to current Windows and repo conventions over preserving WSL-only assumptions unchanged.
- Port only the recommended `Options_Suite` regression tests; do not do broad suite overwrites.
- Do not treat generated outputs, logs, snapshots, or ZIP payloads as source to transfer.
- Only use existing tooling already present in the repo; validation must use the narrowest relevant pytest or script command.

---

### Task 1: Curate and archive migrated research docs

**Files:**
- Create: `docs\archive\2026-08-12-migrated-research\README.md`
- Create: `docs\archive\2026-08-12-migrated-research\DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md`
- Create: `docs\archive\2026-08-12-migrated-research\debate-20260811-v5-recommendations-spec.md`
- Create: `docs\archive\2026-08-12-migrated-research\debate-20260811-v5-recommendations-verified-facts.md`
- Create: `docs\archive\2026-08-12-migrated-research\debate-final-spec-20260808.md`
- Create: `docs\archive\2026-08-12-migrated-research\per-expiry-direction-backtest-20260808.md`
- Create: `docs\archive\2026-08-12-migrated-research\battery-consolidated-20260811.md`
- Create: `docs\archive\2026-08-12-migrated-research\battery-researcher-20260811.md`
- Create: `docs\archive\2026-08-12-migrated-research\research-variance-smile-20260811.md`
- Modify: `docs\superpowers\specs\2026-08-12-adaptation-recommendations.md`
- Test: `docs\archive\2026-08-12-migrated-research\README.md`

**Interfaces:**
- Consumes: migrated docs from `C:\Users\bottl\Financial_Development_MIGRATED\docs\` and `C:\Users\bottl\Financial_Development_MIGRATED\docs\superpowers\specs\`
- Produces: curated archive folder with copied docs plus an index that records provenance and why each doc was preserved

- [ ] **Step 1: Write the failing archive-index assertion as a smoke check**

```python
from pathlib import Path


def test_migrated_research_archive_exists() -> None:
    root = Path("docs/archive/2026-08-12-migrated-research")
    assert (root / "README.md").exists()
    assert (root / "debate-20260811-v5-recommendations-spec.md").exists()
    assert (root / "DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md").exists()
```

- [ ] **Step 2: Run the smoke check so it fails before copying**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -m pytest docs\archive\2026-08-12-migrated-research\README.md`
Expected: FAIL because the archive folder does not exist yet. If pytest cannot target Markdown, run the assertion via `python -c` with the exact snippet above and expect `AssertionError`.

- [ ] **Step 3: Copy the selected migrated research docs into a dated archive folder**

```text
Source -> Destination
docs\DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md
  -> docs\archive\2026-08-12-migrated-research\DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md

docs\superpowers\specs\debate-20260811-v5-recommendations-spec.md
  -> docs\archive\2026-08-12-migrated-research\debate-20260811-v5-recommendations-spec.md

docs\superpowers\specs\debate-20260811-v5-recommendations-verified-facts.md
  -> docs\archive\2026-08-12-migrated-research\debate-20260811-v5-recommendations-verified-facts.md

docs\superpowers\specs\debate-final-spec-20260808.md
  -> docs\archive\2026-08-12-migrated-research\debate-final-spec-20260808.md

docs\superpowers\specs\per-expiry-direction-backtest-20260808.md
  -> docs\archive\2026-08-12-migrated-research\per-expiry-direction-backtest-20260808.md

docs\superpowers\specs\battery-consolidated-20260811.md
  -> docs\archive\2026-08-12-migrated-research\battery-consolidated-20260811.md

docs\superpowers\specs\battery-researcher-20260811.md
  -> docs\archive\2026-08-12-migrated-research\battery-researcher-20260811.md

docs\superpowers\specs\research-variance-smile-20260811.md
  -> docs\archive\2026-08-12-migrated-research\research-variance-smile-20260811.md
```

- [ ] **Step 4: Write the archive README with provenance and non-live status**

```markdown
# Migrated Research Archive (2026-08-12)

These files were copied from `C:\Users\bottl\Financial_Development_MIGRATED` into the canonical
`FinancialDevelopment` repo because they preserve useful reasoning and evidence.

They are **archival research**, not live operational specs. Do not treat them as the current source
of truth without checking the active repo code and newer docs first.

## Included documents

| File | Source | Why preserved |
| --- | --- | --- |
| DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md | migrated docs root | dealer-positioning recommendation set |
| debate-20260811-v5-recommendations-spec.md | migrated docs/superpowers/specs | detailed debate/spec reasoning |
| debate-20260811-v5-recommendations-verified-facts.md | migrated docs/superpowers/specs | factual ledger for recommendation claims |
| debate-final-spec-20260808.md | migrated docs/superpowers/specs | earlier debate baseline |
| per-expiry-direction-backtest-20260808.md | migrated docs/superpowers/specs | backtest design/evidence worth retaining |
| battery-consolidated-20260811.md | migrated docs/superpowers/specs | battery research consolidation |
| battery-researcher-20260811.md | migrated docs/superpowers/specs | supporting battery research |
| research-variance-smile-20260811.md | migrated docs/superpowers/specs | volatility/smile research evidence |
```

- [ ] **Step 5: Update the adaptation recommendations doc to point at the archived folder**

```markdown
**Status:** archived in `docs/archive/2026-08-12-migrated-research/`
```

Add that line under the research-doc recommendation so later operators can find the transferred material immediately.

- [ ] **Step 6: Re-run the archive smoke check**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -c "from pathlib import Path; root = Path(r'docs/archive/2026-08-12-migrated-research'); assert (root / 'README.md').exists(); assert (root / 'debate-20260811-v5-recommendations-spec.md').exists(); assert (root / 'DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md').exists(); print('archive-ok')"`
Expected: `archive-ok`

- [ ] **Step 7: Commit**

```bash
git add docs/archive/2026-08-12-migrated-research docs/superpowers/specs/2026-08-12-adaptation-recommendations.md
git commit -m "docs: archive migrated research corpus"
```

### Task 2: Adopt the workflow scripts that still fit the current repo

**Files:**
- Create: `scripts\burst_checkpoint.sh`
- Create: `scripts\hooks\commit-msg`
- Create: `scripts\verify_tradingview_submodule.sh`
- Modify: `START_HERE.md`
- Modify: `CLAUDE.md`
- Test: `scripts\burst_checkpoint.sh`
- Test: `scripts\verify_tradingview_submodule.sh`

**Interfaces:**
- Consumes: migrated scripts from `C:\Users\bottl\Financial_Development_MIGRATED\scripts\`
- Produces: current-repo script surfaces that work from the repo root and document how to use them

- [ ] **Step 1: Write failing presence assertions for the adopted scripts**

```python
from pathlib import Path


def test_recommended_scripts_exist() -> None:
    assert Path("scripts/burst_checkpoint.sh").exists()
    assert Path("scripts/hooks/commit-msg").exists()
    assert Path("scripts/verify_tradingview_submodule.sh").exists()
```

- [ ] **Step 2: Run the assertions so they fail before copy/adaptation**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -c "from pathlib import Path; assert Path(r'scripts/burst_checkpoint.sh').exists(); assert Path(r'scripts/hooks/commit-msg').exists(); assert Path(r'scripts/verify_tradingview_submodule.sh').exists()"`
Expected: `AssertionError`

- [ ] **Step 3: Copy the three scripts and adapt them to the current repo**

```bash
scripts/burst_checkpoint.sh
scripts/hooks/commit-msg
scripts/verify_tradingview_submodule.sh
```

Required adjustments:

1. In `scripts/burst_checkpoint.sh`, replace the migrated virtualenv path:

```bash
PY="Financial_Dev_Env/bin/python3"
```

with the current Windows-compatible repo venv launcher:

```bash
PY=".venv/Scripts/python.exe"
```

2. In `scripts/burst_checkpoint.sh`, update the test selection so it only references tests that actually exist in the current repo.

3. In `scripts/verify_tradingview_submodule.sh`, keep the gitlink/source checks, but tolerate the absence of `hermes` by treating the live-CDP portion as informational rather than a hard failure on this Windows repo.

4. In `scripts/hooks/commit-msg`, preserve the migrated subject policy exactly unless the current repo already documents a stricter one.

- [ ] **Step 4: Document the new scripts in the operator docs**

Add a short row or bullet to `START_HERE.md` and `CLAUDE.md` covering:

- what `scripts/burst_checkpoint.sh` is for,
- how to install/use `scripts/hooks/commit-msg`,
- what `scripts/verify_tradingview_submodule.sh` validates.

Use concrete commands such as:

```bash
bash scripts/burst_checkpoint.sh vol
git config core.hooksPath scripts/hooks
bash scripts/verify_tradingview_submodule.sh
```

- [ ] **Step 5: Run narrow validation for the adapted scripts**

Run: `bash scripts/burst_checkpoint.sh vol`
Expected: exit 0 and a narrow Vol_Suite checkpoint summary

Run: `bash scripts/verify_tradingview_submodule.sh`
Expected: exit 0 when gitlink/source checks pass, even if live-CDP tooling is unavailable

- [ ] **Step 6: Re-run the presence assertions**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -c "from pathlib import Path; assert Path(r'scripts/burst_checkpoint.sh').exists(); assert Path(r'scripts/hooks/commit-msg').exists(); assert Path(r'scripts/verify_tradingview_submodule.sh').exists(); print('scripts-ok')"`
Expected: `scripts-ok`

- [ ] **Step 7: Commit**

```bash
git add scripts START_HERE.md CLAUDE.md
git commit -m "chore: adopt migrated workflow scripts"
```

### Task 3: Port the recommended Options_Suite regression tests and clean out non-adopted transfer clutter

**Files:**
- Create: `Options_Suite\tests\test_active_imports.py`
- Create: `Options_Suite\tests\test_gpu_parity.py`
- Create: `Options_Suite\tests\test_heston_lsm_discount.py`
- Modify: `Options_Suite\tests\conftest.py`
- Modify: `docs\superpowers\specs\2026-08-12-adaptation-recommendations.md`
- Test: `Options_Suite\tests\test_active_imports.py`
- Test: `Options_Suite\tests\test_gpu_parity.py`
- Test: `Options_Suite\tests\test_heston_lsm_discount.py`

**Interfaces:**
- Consumes: migrated test files from `C:\Users\bottl\Financial_Development_MIGRATED\Options_Suite\tests\`
- Produces: current-repo regression tests aligned to the active `Options_Suite` import layout and GPU/CPU guard conventions

- [ ] **Step 1: Write failing imports for the three new regression tests**

```python
def test_ported_option_suite_regressions_present() -> None:
    import Options_Suite.tests.test_active_imports  # noqa: F401
    import Options_Suite.tests.test_gpu_parity  # noqa: F401
    import Options_Suite.tests.test_heston_lsm_discount  # noqa: F401
```

- [ ] **Step 2: Run the imports so they fail before copying**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -m pytest Options_Suite\tests\test_active_imports.py -q`
Expected: FAIL because the test file does not exist yet

- [ ] **Step 3: Copy the three migrated tests and adapt imports for package-relative current-repo use**

Required changes:

1. In `test_active_imports.py`, import the active modules through `Options_Suite.<module>` rather than flat top-level names if the current repo no longer relies on cwd-only imports.
2. Keep the legacy-module leak assertions only if those legacy names still exist or are intentionally absent in the current repo; if not, trim the list to the current quarantine contract instead of inventing a new one.
3. In `test_gpu_parity.py`, keep the subprocess-based backend split and skip-on-no-CUDA behavior, but make the child add the repo root to `sys.path` and import `Options_Suite.MCHestonLSM` rather than assuming the test directory is the package root.
4. In `test_heston_lsm_discount.py`, import from `Options_Suite.MCHestonLSM`.
5. In `Options_Suite\tests\conftest.py`, add only the minimal path or environment support needed for these tests to run under the current repo’s pytest configuration.

- [ ] **Step 4: Run the narrow test targets**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -m pytest Options_Suite\tests\test_active_imports.py Options_Suite\tests\test_heston_lsm_discount.py -q`
Expected: PASS

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -m pytest Options_Suite\tests\test_gpu_parity.py -q`
Expected: PASS or SKIP when no CUDA device is available

- [ ] **Step 5: Record cleanup of explicitly non-adopted transfer items**

Update `docs\superpowers\specs\2026-08-12-adaptation-recommendations.md` with a short status note that:

- research docs were archived,
- scripts were adopted,
- the three `Options_Suite` tests were ported,
- sentiment scanner was intentionally left alone,
- dashboard and `Tools` were intentionally not touched.

This is the cleanup record so there is no ambiguity about what was not transferred.

- [ ] **Step 6: Run the full recommended Options_Suite regression slice**

Run: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe -m pytest Options_Suite\tests\test_active_imports.py Options_Suite\tests\test_gpu_parity.py Options_Suite\tests\test_heston_lsm_discount.py Options_Suite\tests\test_iv_solvers.py Options_Suite\tests\test_pricing_models.py -q`
Expected: PASS, with `test_gpu_parity.py` allowed to SKIP on non-CUDA hosts

- [ ] **Step 7: Commit**

```bash
git add Options_Suite/tests/conftest.py Options_Suite/tests/test_active_imports.py Options_Suite/tests/test_gpu_parity.py Options_Suite/tests/test_heston_lsm_discount.py docs/superpowers/specs/2026-08-12-adaptation-recommendations.md
git commit -m "test(options-suite): recover migrated regression coverage"
```

## Self-Review

- Spec coverage: this plan covers the recommended research-doc transfer, script adoption, and `Options_Suite` regression-test adoption. It intentionally excludes sentiment-scanner, dashboard, and `Tools` per the user’s instruction.
- Placeholder scan: every task names exact files, commands, and expected outcomes.
- Type consistency: every produced path and command uses current repo locations and current `.venv\Scripts\python.exe`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-migrated-adoption-followthrough.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
