# Backtests Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transfer the migrated standalone `Backtests/` package into `FinancialDevelopment`, wire it to the current repo conventions, preserve current suite-specific backtests, and leave the current repo with a tested, reproducible backtest harness rather than only migrated artifacts.

**Architecture:** Keep `FinancialDevelopment` as the canonical repo and import the migrated `Backtests/` package as a separate root-level package, not a replacement for `Vol_Suite/backtest_stage3.py` or other suite-local backtests. The work proceeds in small slices: first stage the package and tests, then adapt imports and paths to the current repo, then connect repo-facing documentation and tooling, and finally verify the narrowest affected test/runtime paths.

**Tech Stack:** Python 3.12+, pytest, existing root `shared/` utilities, ThetaData-backed current repo conventions, existing `Tools/` registry and docs tree.

## Global Constraints

- Keep `FinancialDevelopment` as the only canonical working repo.
- Transfer code and tests from migrated `Backtests/`; do not treat `Backtests/outputs/` as source.
- Preserve existing `Vol_Suite` backtests and current `Tools/tools/backtesting_tool.py`.
- Prefer adapting imports and wrappers over rewriting the harness logic from scratch.
- Do not port WSL-only launch assumptions unchanged; align with current Windows/root repo conventions.
- Only use existing test/build tooling already present in the repo.
- Any new or moved code must be covered by the narrowest relevant existing pytest targets.

---

### Task 1: Stage the Backtests package in the current repo

**Files:**
- Create: `Backtests/__init__.py`
- Create: `Backtests/main.py`
- Create: `Backtests/core.py`
- Create: `Backtests/data.py`
- Create: `Backtests/models.py`
- Create: `Backtests/backtest_pricing.py`
- Create: `Backtests/backtest_greeks.py`
- Create: `Backtests/backtest_signals.py`
- Create: `Backtests/tests/__init__.py`
- Create: `Backtests/tests/conftest.py`
- Create: `Backtests/tests/test_core.py`
- Create: `Backtests/tests/test_greeks.py`
- Create: `Backtests/tests/test_models.py`
- Create: `Backtests/tests/test_pricing.py`
- Create: `Backtests/tests/test_signals.py`
- Modify: `.gitignore`
- Test: `Backtests/tests/test_core.py`

**Interfaces:**
- Consumes: migrated `C:\Users\bottl\Financial_Development_MIGRATED\Backtests\*`
- Produces: importable package `Backtests` with test package `Backtests.tests`

- [ ] **Step 1: Write the failing package-presence test**

```python
from pathlib import Path


def test_backtests_package_files_exist() -> None:
    root = Path("Backtests")
    assert (root / "main.py").exists()
    assert (root / "core.py").exists()
    assert (root / "tests" / "test_core.py").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Backtests/tests/test_core.py::test_backtests_package_files_exist -v`
Expected: FAIL with missing `Backtests/` files

- [ ] **Step 3: Copy in the minimal package skeleton and migrated test files**

```python
# Backtests/__init__.py
"""Standalone backtest tournament package imported from the migrated tree."""
```

```python
# Backtests/tests/__init__.py
```

```python
# Backtests/tests/conftest.py
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

Also copy the migrated `main.py`, `core.py`, `data.py`, `models.py`,
`backtest_pricing.py`, `backtest_greeks.py`, `backtest_signals.py`, and the
five migrated test modules without semantic changes yet.

- [ ] **Step 4: Ignore package outputs, not source**

```gitignore
Backtests/outputs/
```

- [ ] **Step 5: Run test to verify package presence passes**

Run: `pytest Backtests/tests/test_core.py::test_backtests_package_files_exist -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .gitignore Backtests
git commit -m "feat(backtests): stage migrated tournament package"
```

### Task 2: Adapt Backtests imports and path assumptions to the current repo

**Files:**
- Modify: `Backtests/main.py`
- Modify: `Backtests/core.py`
- Modify: `Backtests/data.py`
- Modify: `Backtests/models.py`
- Modify: `Backtests/backtest_pricing.py`
- Modify: `Backtests/backtest_greeks.py`
- Modify: `Backtests/backtest_signals.py`
- Modify: `Backtests/tests/conftest.py`
- Test: `Backtests/tests/test_core.py`
- Test: `Backtests/tests/test_models.py`

**Interfaces:**
- Consumes: current repo `shared.thetadata`, current root import layout, current sentiment pack/output conventions
- Produces: Backtests modules importable from the current repo without WSL-specific path hacks

- [ ] **Step 1: Write the failing import-path test**

```python
def test_backtests_main_imports_without_wsl_path_hacks() -> None:
    import Backtests.main  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Backtests/tests/test_core.py::test_backtests_main_imports_without_wsl_path_hacks -v`
Expected: FAIL with import errors or bad module-path assumptions

- [ ] **Step 3: Replace sibling-file imports with package-safe imports**

```python
# Backtests/main.py
from Backtests.backtest_pricing import render_pricing_report, run_pricing
from Backtests.backtest_greeks import render_greeks_report, run_greeks
from Backtests.backtest_signals import render_signals_report, run_signal_tournament
from Backtests.core import write_json, write_txt
from Backtests.data import fetch_chain_days, pick_expiry, recent_calendar_dates
```

```python
# Backtests/tests modules
from Backtests.core import ...
```

Update any `sys.path.insert(...)` or flat-import assumptions so the package works
from the current repo root via normal pytest/import behavior.

- [ ] **Step 4: Align default paths with current repo conventions**

```python
# Backtests/main.py
def run_tournament(
    ...,
    out_dir: str = "Backtests/outputs",
    packs_dir: str = "sentiment-scanner/data/exports/highlighted_ticker_packs",
    ...
) -> Dict[str, Any]:
    ...
```

Keep those defaults only if they already match the current repo; otherwise
update them to the actual current-repo locations and make tests assert the
chosen paths explicitly.

- [ ] **Step 5: Run narrow import tests**

Run: `pytest Backtests/tests/test_core.py Backtests/tests/test_models.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Backtests
git commit -m "fix(backtests): adapt tournament imports to current repo"
```

### Task 3: Reconcile Backtests behavior with current data contracts

**Files:**
- Modify: `Backtests/core.py`
- Modify: `Backtests/data.py`
- Modify: `Backtests/models.py`
- Modify: `Backtests/backtest_pricing.py`
- Modify: `Backtests/backtest_greeks.py`
- Modify: `Backtests/backtest_signals.py`
- Modify: `Backtests/tests/test_core.py`
- Modify: `Backtests/tests/test_pricing.py`
- Modify: `Backtests/tests/test_greeks.py`
- Modify: `Backtests/tests/test_signals.py`

**Interfaces:**
- Consumes: current ThetaData row normalization contract, current sentiment pack format, current options row field names
- Produces: Backtests harnesses that operate against the current repo’s data shapes

- [ ] **Step 1: Write one failing normalization/contract test per harness**

```python
def test_normalize_row_preserves_current_theta_fields():
    row = {"strike": 727000, "right": "CALL", "implied_vol": "0.2", "date": "20260720"}
    normalized = normalize_row(row)
    assert normalized["strike"] == 727.0
    assert normalized["right"] == "C"
    assert normalized["date"] == "20260720"
```

```python
def test_pricing_harness_accepts_current_repo_chain_rows():
    ...
```

```python
def test_signal_harness_uses_current_pack_layout():
    ...
```

- [ ] **Step 2: Run targeted tests to verify contract gaps fail**

Run: `pytest Backtests/tests/test_core.py Backtests/tests/test_pricing.py Backtests/tests/test_signals.py -q`
Expected: FAIL in any place where migrated assumptions differ from current repo contracts

- [ ] **Step 3: Implement the minimal contract adaptations**

```python
def normalize_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rec = dict(row)
    ...
    return rec
```

Adapt only the places where migrated harness assumptions differ from the
current repo’s real shapes. Preserve the harness structure rather than
rewriting it into suite-specific code.

- [ ] **Step 4: Re-run targeted harness tests**

Run: `pytest Backtests/tests/test_core.py Backtests/tests/test_pricing.py Backtests/tests/test_greeks.py Backtests/tests/test_signals.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Backtests
git commit -m "fix(backtests): align tournament harnesses with current contracts"
```

### Task 4: Document and expose the transferred Backtests package

**Files:**
- Modify: `docs/guides/START_HERE.md`
- Modify: `CLAUDE.md`
- Modify: `docs\superpowers\specs\2026-08-12-folder-reconciliation-design.md`
- Optional Modify: `Tools/tools/backtesting_tool.py`
- Optional Test: existing tool/backtest tests only if tool wiring is changed

**Interfaces:**
- Consumes: finished `Backtests` package and current repo operator docs
- Produces: documented backtest workflow and, if chosen, a clear hook from the existing tools surface

- [ ] **Step 1: Write a failing documentation expectation test (or doc checklist note if no doc tests exist)**

```python
# If no doc test framework exists, use a review checklist instead:
EXPECTED_START_HERE_LINE = "Backtests\\main.py --harness all"
```

- [ ] **Step 2: Update operator docs with concrete commands**

```markdown
| Backtest tournament | `python Backtests\main.py --harness all --ticker SPY,QQQ --lookback-days 1` | Runs pricing, greeks, and signal harnesses and writes text/JSON artifacts to `Backtests\outputs\`. |
```

```markdown
- `Backtests/` — standalone tournament harness for pricing, greeks, and signal evaluation; separate from `Vol_Suite/backtest_stage3.py`.
```

If `Tools/tools/backtesting_tool.py` can call this package cleanly without
confusing existing behavior, add a narrow integration path; otherwise document
the package without tool wiring in this task.

- [ ] **Step 3: Run only the affected tests if tool wiring changed**

Run: `pytest Tools/tests/test_registry.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs/guides/START_HERE.md CLAUDE.md docs/superpowers/specs/2026-08-12-folder-reconciliation-design.md Tools/tools/backtesting_tool.py
git commit -m "docs(backtests): document transferred tournament harness"
```

### Task 5: Verify the transferred package end-to-end

**Files:**
- Test: `Backtests/tests/test_core.py`
- Test: `Backtests/tests/test_models.py`
- Test: `Backtests/tests/test_pricing.py`
- Test: `Backtests/tests/test_greeks.py`
- Test: `Backtests/tests/test_signals.py`
- Test: `Vol_Suite/tests/test_backtest_stage3.py`
- Optional Test: `Tools/tests/test_registry.py` if tool wiring changed

**Interfaces:**
- Consumes: finished transferred package and preserved current backtests
- Produces: a verified state where migrated `Backtests/` coexists with current suite-specific backtests

- [ ] **Step 1: Run the transferred package tests**

Run: `pytest Backtests/tests -q`
Expected: PASS

- [ ] **Step 2: Run a preservation test for the current suite-specific backtest**

Run: `pytest Vol_Suite/tests/test_backtest_stage3.py -q`
Expected: PASS

- [ ] **Step 3: Run a smoke CLI invocation without network-heavy scope**

Run: `python Backtests/main.py --harness pricing --ticker SPY --lookback-days 1 --json`
Expected: Valid JSON on stdout or a narrowly diagnosable provider/runtime failure that confirms the CLI wiring path is correct

- [ ] **Step 4: Commit**

```bash
git add Backtests docs/guides/START_HERE.md CLAUDE.md docs/superpowers/specs/2026-08-12-folder-reconciliation-design.md
git commit -m "test(backtests): verify transferred tournament package"
```
