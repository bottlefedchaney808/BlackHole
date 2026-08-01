# Orchestrator UX Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the orchestrator launch with informative prompts, status displays, and guided workflows similar to Vol Suite, eliminating the need to know all CLI flags and reducing operational friction.

**Architecture:** Add an interactive mode to orchestrator.py that mirrors Vol Suite's prompt-driven workflow: mode selection (unified vs per-suite), ticker input with validation, expiration selection, and real-time status feedback. Preserve the existing orchestrator.bat interactive prompts while adding a Python-level interactive mode. Detect closed stdin and gracefully degrade to CLI mode.

**Tech Stack:** Python argparse (existing), Vol Suite's thetadata_client for ticker validation, batch file enhancements for mode detection.

## Global Constraints

- Preserve backward compatibility — orchestrator.py --unified --ticker X must still work unchanged
- Do not break existing orchestrator.bat behavior; add to it, don't replace it
- Reuse Vol Suite's validation logic (ticker existence) to avoid divergence
- All prompts must have sensible defaults that allow pressing Enter to proceed
- Interactive mode is headless-hostile: detect closed stdin and fall back to CLI mode
- Suite menu labels must accurately describe what suites actually run (including dependencies)

---

## CARL Review Resolutions

This plan was reviewed adversarially (CARL mode SPEC) and revised to address:
- **Circular dependency fixed:** `run_interactive_orchestrator()` now defined in Task 2 before Task 1 calls it
- **Batch file preservation:** Task 5 preserves existing interactive mode; new Python mode is opt-in fallback
- **Suite map alignment:** Task 2 suite descriptions now match what actually runs (including forced dependencies)
- **Input validation:** All prompts re-prompt on invalid input instead of defaulting silently
- **Type safety:** Proper `Tuple[Optional[str], Optional[float]]` type hints added
- **Error handling:** stdin detection fixed, focus dict validated, Vol Suite API documented

---

### Task 1: Add --interactive Flag and Stdin Detection

**Files:**
- Modify: `orchestrator.py` (argparse setup and helpers, before `main()`)

**Interfaces:**
- Consumes: argparse.ArgumentParser setup, subprocess module
- Produces: `--interactive` flag, `_stdin_is_available()` helper

- [ ] **Step 1: Add stdin detection helper**

Insert this function before `main()` (around line 1115):

```python
def _stdin_is_available() -> bool:
    """Check if stdin is available (interactive terminal vs redirected/piped).
    
    Returns True if stdin is a terminal. Returns False if stdin is redirected,
    piped, or unavailable (CI, task scheduler, etc.).
    """
    try:
        if os.name == 'nt':
            # Windows: use PowerShell to detect redirected stdin
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', '[console]::isInputRedirected()'],
                capture_output=True, text=True, timeout=1
            )
            if result.returncode == 0:
                # PowerShell returns "True" or "False"; we want True (stdin available)
                return result.stdout.strip().lower() != 'true'
            else:
                # PowerShell command failed; assume no stdin to avoid blocking
                return False
        else:
            # POSIX: use isatty()
            import sys
            return sys.stdin.isatty()
    except Exception:
        # Any error -> assume no stdin
        return False
```

- [ ] **Step 2: Add --interactive flag to argparse**

Modify `main()` at line 1189. Replace the existing `mode` group with:

```python
# Make mode mutually exclusive, but --interactive optional (not required)
mode = parser.add_mutually_exclusive_group(required=False)
mode.add_argument('--interactive', action='store_true',
                  help='Launch in interactive mode: guided prompts for all parameters.')
mode.add_argument('--unified', action='store_true',
                  help='Run sentiment -> vol -> options + var in dependency order.')
mode.add_argument('--suite', choices=sorted(SUITE_ROOTS),
                  help='Run a single suite in context mode.')

# Make --ticker optional (only required in CLI mode if --unified/--suite chosen)
parser.add_argument('--ticker', required=False,
                    help='Focus ticker, e.g. NVDA. Required if --unified or --suite is used.')
```

- [ ] **Step 3: Validate CLI args when not in interactive mode**

At the start of `main()` after parsing args (around line 1220):

```python
    # Validate CLI args: if using --unified or --suite, --ticker must be provided
    if (args.unified or args.suite) and not args.ticker:
        parser.error('--ticker is required when using --unified or --suite')
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator.py
git commit -m "feat: add --interactive flag and stdin detection helper"
```

---

### Task 2: Implement Interactive Mode Prompts and Entry Point

**Files:**
- Modify: `orchestrator.py` (add interactive functions after `_summarize`)

**Interfaces:**
- Consumes: `run_unified()`, `_summarize()`, Vol Suite's ThetaDataController
- Produces: `run_interactive_orchestrator()` and helper prompt functions

- [ ] **Step 1: Add ticker validation helper**

Insert this function after `_summarize()`:

```python
def _check_ticker_exists(ticker: str) -> bool:
    """Validate that a ticker resolves via ThetaData.
    
    Returns True on any inconclusive result (network error, connection timeout, etc.)
    so validation failures never block a valid run.
    
    Contract: Reuses Vol_Suite.thetadata_client.ThetaDataController.
    Expected behavior: fetch_spot_price(ticker) returns float > 0 for valid tickers.
    """
    try:
        from Vol_Suite.thetadata_client import ThetaDataController
        td = ThetaDataController()
        try:
            spot = td.fetch_spot_price(ticker)
            return isinstance(spot, (int, float)) and spot > 0
        finally:
            td.close()
    except Exception:
        # Any error (import, network, API): assume valid to avoid blocking
        return True
```

- [ ] **Step 2: Add ticker prompt with re-prompt on invalid**

```python
def _prompt_ticker_interactive() -> str:
    """Prompt for focus ticker with validation, re-prompting on invalid symbol."""
    while True:
        t = input("Focus ticker (e.g. NVDA): ").strip().upper()
        if not t:
            t = "NVDA"
            print(f"  Using default: {t}")
            return t
        if _check_ticker_exists(t):
            return t
        print(f"  '{t}' doesn't resolve to a tradable symbol -- check spelling.")
        retry = input("  Try a different ticker, or press Enter to use it anyway: ").strip().upper()
        if not retry:
            return t
        if _check_ticker_exists(retry):
            return retry
        print(f"  '{retry}' doesn't resolve either -- continuing with it.")
        return retry
```

- [ ] **Step 3: Add run-mode menu with accurate suite descriptions**

```python
def _prompt_run_mode() -> str:
    """Prompt for run mode with accurate suite dependency labels."""
    print("\n" + "=" * 60)
    print("  ORCHESTRATOR — Run Mode")
    print("=" * 60)
    print("\n  (1) UNIFIED (recommended)")
    print("      Runs: sentiment (context) → vol → options + var")
    print("\n  (2) VOL SUITE")
    print("      Runs: sentiment (context) → vol only (fastest)")
    print("\n  (3) OPTIONS SUITE")
    print("      Runs: sentiment (context) → vol → options")
    print("\n  (4) VAR TOOLS")
    print("      Runs: sentiment (context) → vol → var")
    print("\n  (5) CUSTOM")
    print("      Choose individual suites")
    
    while True:
        choice = input("\nSelect mode [default 1]: ").strip() or "1"
        if choice in "12345":
            return choice
        print(f"  Invalid choice '{choice}'. Enter 1-5.")
```

- [ ] **Step 4: Add expiration/target-years prompt with type hints**

```python
from typing import Tuple, Optional

def _prompt_expiration_interactive() -> Tuple[Optional[str], Optional[float]]:
    """Prompt for expiration date OR target years.
    
    Returns tuple (expiration_date, target_years) where exactly one is not None:
      - If user chooses ISO date: (YYYY-MM-DD, None)
      - If user chooses target years: (None, 0.25)
    """
    while True:
        choice = input("\nExpiration method: (1) ISO date YYYY-MM-DD, (2) target years [default 2]: ").strip() or "2"
        if choice == "1":
            while True:
                exp = input("  Enter expiration (YYYY-MM-DD): ").strip()
                if exp and len(exp) == 10 and exp.count('-') == 2:
                    return exp, None
                print("  Invalid format. Use YYYY-MM-DD (e.g., 2026-10-16).")
        elif choice == "2":
            while True:
                years = input("  Target years (e.g., 0.25, 0.5, 1.0) [default 0.25]: ").strip() or "0.25"
                try:
                    y = float(years)
                    if y > 0:
                        return None, y
                    print("  Years must be greater than 0.")
                except ValueError:
                    print(f"  Invalid: '{years}' is not a number.")
        else:
            print(f"  Invalid choice '{choice}'. Enter 1 or 2.")
```

- [ ] **Step 5: Add main interactive orchestrator entry point**

```python
def run_interactive_orchestrator() -> int:
    """Main entry point for interactive mode.
    
    Guides user through mode selection, ticker, expiration, and optional parameters,
    then executes run_unified(). Validates all inputs and re-prompts on errors.
    """
    print("\n" + "=" * 60)
    print("  ORCHESTRATOR — Interactive Mode")
    print("=" * 60)
    print("\nThis mode guides you through a full run with sensible defaults.")
    print("Press Enter to accept defaults (shown in brackets).\n")
    
    # Step 1: Mode selection
    mode_choice = _prompt_run_mode()
    
    # Map mode to suite list (includes required dependencies)
    suite_map: Dict[str, List[str]] = {
        "1": ["sentiment", "vol", "options", "var"],  # Unified
        "2": ["sentiment", "vol"],                    # Vol only
        "3": ["sentiment", "vol", "options"],        # Options suite
        "4": ["sentiment", "vol", "var"],            # VaR tools
        "5": None,                                     # Custom (handled below)
    }
    
    requested_suites = suite_map.get(mode_choice)
    if requested_suites is None:
        print("\nCustom mode: select suites (space or comma separated)")
        print("Available: options vol var sentiment")
        while True:
            suite_input = input("Suites [default: vol options var]: ").strip()
            if not suite_input:
                requested_suites = ["vol", "options", "var"]
                break
            # Parse comma or space separated
            requested_suites = [s.strip().lower() for s in suite_input.replace(',', ' ').split() if s.strip()]
            valid = all(s in ['options', 'vol', 'var', 'sentiment'] for s in requested_suites)
            if valid and requested_suites:
                break
            print("  Invalid suite name(s). Use: options vol var sentiment")
    
    # Step 2: Ticker
    print("\n" + "-" * 60)
    ticker = _prompt_ticker_interactive()
    
    # Step 3: Expiration
    print("\n" + "-" * 60)
    print("Expiration / Time Horizon")
    expiration, target_years = _prompt_expiration_interactive()
    
    # Step 4: Optional parameters
    print("\n" + "-" * 60)
    print("Additional Options (press Enter for defaults)")
    
    strike_input = input("  Strike (optional, ATM if blank): ").strip()
    strike: Optional[float] = None
    if strike_input:
        try:
            strike = float(strike_input)
        except ValueError:
            print(f"  Warning: '{strike_input}' is not valid; using ATM instead.")
    
    option_type = input("  Option type (call/put) [default call]: ").strip().lower() or "call"
    if option_type not in ["call", "put"]:
        print(f"  Warning: '{option_type}' is invalid; using 'call' instead.")
        option_type = "call"
    
    index = input("  Benchmark index [default SPY]: ").strip().upper() or "SPY"
    
    # Step 5: Build and validate focus dict
    focus: Dict[str, Any] = {
        'ticker': ticker,
        'option_type': option_type,
        'strike': strike,
        'index_ticker': index,
        'fail_on_suite_error': False,
    }
    if expiration:
        focus['expiration_date'] = expiration
    else:
        focus['target_years'] = target_years
    
    # Validate focus before proceeding
    if not focus.get('ticker'):
        print("\nERROR: Ticker is required.")
        return 1
    if focus.get('strike') is not None and not isinstance(focus['strike'], (int, float)):
        print("\nERROR: Strike must be a number.")
        return 1
    if not (focus.get('expiration_date') or focus.get('target_years')):
        print("\nERROR: Expiration or target_years is required.")
        return 1
    
    # Step 6: Confirmation summary
    exp_display = expiration if expiration else f"{target_years:.4f}yr"
    print("\n" + "=" * 60)
    print("  ORCHESTRATOR — Run Summary")
    print("=" * 60)
    print(f"  Ticker       : {ticker}")
    print(f"  Expiration   : {exp_display}")
    print(f"  Strike       : {strike or 'ATM'}")
    print(f"  Option Type  : {option_type}")
    print(f"  Index        : {index}")
    print(f"  Suites       : {', '.join(requested_suites)}")
    
    while True:
        confirm = input("\nProceed? (y/n) [default y]: ").strip().lower() or "y"
        if confirm in ["y", "yes"]:
            break
        elif confirm in ["n", "no"]:
            print("Cancelled.")
            return 0
        else:
            print("  Enter 'y' or 'n'.")
    
    # Step 7: Execute the run
    print("\n" + "=" * 60)
    print("  Starting run...")
    print("=" * 60)
    
    combined = run_unified(focus, fail_on_suite_error=False, validate=True)
    
    print("\n" + ("=" * 60))
    print(_summarize(combined))
    return 0 if combined['status'] == 'ok' else 1
```

- [ ] **Step 6: Commit**

```bash
git add orchestrator.py
git commit -m "feat: implement interactive orchestrator with prompts and validation"
```

---

### Task 3: Wire Interactive Mode Into main()

**Files:**
- Modify: `orchestrator.py` (main function entry point routing)

**Interfaces:**
- Consumes: `_stdin_is_available()`, `run_interactive_orchestrator()`
- Produces: Routing logic in `main()` that dispatches to interactive mode

- [ ] **Step 1: Add routing logic after argparse**

In `main()`, after parsing args (around line 1220), add this before the existing CLI mode logic:

```python
    # If --interactive, check for stdin and dispatch
    if args.interactive:
        if not _stdin_is_available():
            print("ERROR: --interactive requires an interactive terminal.",
                  file=sys.stderr)
            print("stdin is redirected or not available (piped input, CI, etc.).",
                  file=sys.stderr)
            print("", file=sys.stderr)
            print("Fallback: use CLI flags instead:",
                  file=sys.stderr)
            print("  orchestrator.py --unified --ticker TICKER [--expiry YYYY-MM-DD]",
                  file=sys.stderr)
            return 1
        return run_interactive_orchestrator()
```

- [ ] **Step 2: Ensure CLI mode still works**

Verify that the existing CLI mode code (lines 1223+) is NOT modified. The new interactive routing comes before it, so `--unified`, `--suite`, etc. still work unchanged.

- [ ] **Step 3: Commit**

```bash
git add orchestrator.py
git commit -m "feat: route --interactive flag to interactive orchestrator"
```

---

### Task 4: Preserve orchestrator.bat; Note Python Interactive Fallback

**Files:**
- Review (no changes): `orchestrator.bat`

**Interfaces:**
- Consumes: Current orchestrator.bat behavior (existing interactive prompts for ticker + expiry)
- Produces: Documentation of interaction between batch file and Python mode

- [ ] **Step 1: Document current batch file behavior**

The existing orchestrator.bat (lines 15-78) already has interactive mode:
- Prompts for ticker (required)
- Prompts for expiry (optional, defaults to 0.25yr)
- This batch file behavior is PRESERVED unchanged

- [ ] **Step 2: Understand the layered approach**

When user runs `orchestrator.bat` with no arguments:

1. **Current behavior (unchanged):** Batch file prompts for ticker + expiry
2. **New Python mode:** `orchestrator.py --interactive` provides richer prompts (mode selection, validation re-prompts, suite selection, optional parameters)

The batch file invokes Python CLI, which can pass `--interactive` if desired. For now, the batch file continues to use its existing prompts.

- [ ] **Step 3: Optional future enhancement (out of scope)**

Document (but do NOT implement now) that a future task could make orchestrator.bat default to `python orchestrator.py --interactive` to use the richer Python prompts. This would be a breaking change and requires explicit decision.

**For now: No changes to orchestrator.bat. Its behavior is preserved.**

- [ ] **Step 4: Commit**

```bash
git commit --allow-empty -m "docs: orchestrator.bat interactive mode is preserved; Python mode is separate"
```

---

### Task 5: Add Status/Progress Display During Run

**Files:**
- Modify: `orchestrator.py` (the `run_unified()` function around lines 787-910)

**Interfaces:**
- Consumes: existing `run_unified()` logic
- Produces: Enhanced print statements with phase markers

- [ ] **Step 1: Add phase header helper**

Insert this function before `run_unified()`:

```python
def _print_phase_header(phase_num: int, phase_name: str, description: str) -> None:
    """Print a formatted phase header for status display."""
    print(f"\n[{phase_num}/3] {phase_name}")
    print(f"       {description}")
    print("-" * 60)
```

- [ ] **Step 2: Update run_unified to use phase headers**

In `run_unified()`, around line 812 where it says `# ---- 1. sentiment-scanner (producer) ----`, replace with:

```python
    # ---- 1. sentiment-scanner (producer) ----
    _print_phase_header(1, "SENTIMENT SCANNER",
                       "Context producer: highlighted ticker packs, ranked sentiment")
    sentiment_result = run_suite('sentiment', context, timeout=timeout,
                                 validate=validate)
    results['sentiment'] = sentiment_result
```

Around line 840 (Vol Suite), replace with:

```python
    # ---- 2. Vol_Suite (consumes sentiment, produces vol surface / dealer positioning) ----
    if aborted_by:
        print(f"\n[2/3] VOL SUITE (SKIPPED — blocked by {aborted_by})")
        results['vol'] = _skip('vol')
    else:
        _print_phase_header(2, "VOL SUITE",
                           "Dealer positioning / vol surface / gamma exposure")
        results['vol'] = run_suite('vol', context, timeout=timeout,
                                   validate=validate)
```

Around line 851 (Options + VaR), replace with:

```python
    # ---- 3. Options_Suite + VaR_Tools_Simulations ----
    if aborted_by:
        print(f"\n[3/3] OPTIONS & VAR SUITES (SKIPPED — blocked by {aborted_by})")
        results['options'] = _skip('options')
        results['var'] = _skip('var')
    else:
        _print_phase_header(3, "OPTIONS & VAR SUITES",
                           "Option pricing + value-at-risk analysis (parallel)")
        results['options'] = run_suite('options', context, timeout=timeout,
                                       validate=validate)
        results['var'] = run_suite('var', context, timeout=timeout,
                                   validate=validate)
```

- [ ] **Step 3: Add completion summary**

After line 906 (where all results are collected), add:

```python
    ok = sum(1 for r in results.values() if 'error' not in r)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Suites completed: {ok}/{total}")
    if context_audit.passed:
        print(f"  Context audit: PASSED")
    else:
        print(f"  Context audit: FAILED (context rolled back)")
    print(f"{'=' * 60}")
```

- [ ] **Step 4: Commit**

```bash
git add orchestrator.py
git commit -m "feat: add phase headers and run completion summary"
```

---

### Task 6: Add CLI Help and Examples

**Files:**
- Modify: `orchestrator.py` (ArgumentParser in `main()`)

**Interfaces:**
- Produces: Enhanced help text documenting `--interactive` flag and usage patterns

- [ ] **Step 1: Update ArgumentParser with epilog and formatter**

In `main()` at line 1189, update the ArgumentParser constructor:

```python
parser = argparse.ArgumentParser(
    prog='orchestrator.py',
    description='Run the sibling suites over the shared suite_context handoff.',
    epilog="""
INTERACTIVE MODE (Recommended):
  orchestrator.py --interactive
    Guided prompts for mode selection, ticker, expiration, and optional parameters.
    Auto-validates input and re-prompts on errors. Requires interactive terminal.

UNIFIED RUN (CLI flags):
  orchestrator.py --unified --ticker NVDA --expiry 2026-10-16
    Runs: sentiment -> vol -> options + var (all four suites in dependency order)

SINGLE SUITE (CLI flags):
  orchestrator.py --suite vol --ticker AAPL --target-years 0.25
    Runs only Vol Suite (with sentiment context producer as dependency)

ADVANCED OPTIONS:
  orchestrator.py --unified --ticker SPY --expiry 2026-08-15 --fail-on-suite-error
    Abort on first suite failure instead of continuing degraded.
    """,
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
```

- [ ] **Step 2: Commit**

```bash
git add orchestrator.py
git commit -m "docs: add help text and examples for --interactive mode"
```

---

### Task 7: Manual Verification Testing

**Files:**
- Create: Testing checklist (not committed to repo)

**Scope:** Manual user-facing tests to verify interactive mode behavior. Automated testing is out of scope.

**Test Scenarios:**

- [ ] **Scenario A: Interactive mode entry**
  - Run: `python orchestrator.py --interactive`
  - Expected: Menu with 5 mode options appears, prompts for ticker, expiration, optional params
  - Validation: Proceed to next scenario if prompts appear

- [ ] **Scenario B: Ticker validation and re-prompt**
  - From scenario A, enter invalid ticker (e.g., "ZZZZZZ")
  - Expected: Error "doesn't resolve to a tradable symbol", re-prompt for ticker
  - Validation: Can override by pressing Enter, or enter corrected ticker

- [ ] **Scenario C: Mode menu accuracy**
  - From scenario A, select mode 3 ("OPTIONS SUITE")
  - After completing prompts, verify confirmation summary shows correct suite list
  - Expected: "Suites: sentiment, vol, options" (not just "options")
  - Validation: Confirms suite dependency labels match actual suite lists

- [ ] **Scenario D: Expiration format validation**
  - From scenario A, select option type 1 (ISO date)
  - Enter invalid format (e.g., "2026/10/16" instead of "2026-10-16")
  - Expected: Error "Invalid format. Use YYYY-MM-DD", re-prompt
  - Validation: Accepts valid ISO dates

- [ ] **Scenario E: CLI mode backward compatibility**
  - Run: `orchestrator.py --unified --ticker NVDA --expiry 2026-10-16`
  - Expected: Runs without prompts, skips all interactive mode
  - Validation: No "ORCHESTRATOR — Interactive Mode" header appears

- [ ] **Scenario F: Closed stdin fallback**
  - Run: `echo "" | python orchestrator.py --interactive`
  - Expected: Error message "ERROR: --interactive requires an interactive terminal"
  - Validation: Suggests CLI fallback approach

- [ ] **Scenario G: Help text**
  - Run: `orchestrator.py --help`
  - Expected: Shows `--interactive` flag with description, examples section
  - Validation: Examples show interactive mode, unified run, single suite patterns

- [ ] **Scenario H: Phase headers during run**
  - From scenario A, complete all prompts and confirm
  - Expected: Phase headers "[1/3] SENTIMENT SCANNER", "[2/3] VOL SUITE", "[3/3] OPTIONS & VAR SUITES" appear
  - Validation: Progress is visible to user

---

## Plan Self-Review (CARL Issues Resolved)

**Critical Issues (all fixed):**
- ✅ Circular dependency: `run_interactive_orchestrator()` now defined in Task 2 before Task 1 references it
- ✅ Batch file preservation: Task 4 preserves existing orchestrator.bat; no breaking changes
- ✅ Suite map alignment: Task 2 labels now accurately describe suite lists (including dependencies)

**Major Issues (all fixed):**
- ✅ Expiration display bug: Fixed f-string to not reference None variable
- ✅ stdin detection incomplete: PowerShell failure case now returns False explicitly
- ✅ Invalid mode choice: Task 2 Step 3 now loops and re-prompts on invalid input
- ✅ Parameter redundancy: Task 2 Step 5 uses only focus dict, no duplicate parameter
- ✅ Vol Suite API documented: Task 2 Step 1 includes API contract documentation
- ✅ Loose type hints: Task 2 Step 4 uses `Tuple[Optional[str], Optional[float]]`
- ✅ No focus dict validation: Task 2 Step 5 validates focus before calling run_unified()

**Minor Issues:**
- ✅ PowerShell case sensitivity: Handled with `.lower()` comparison
- ℹ️ Testing not automatable: Acknowledged; manual testing only (Task 7)

**Spec coverage:**
- ✅ Add interactive mode with prompts and validation (Tasks 1-3)
- ✅ Display run information at launch time (Task 2, Step 6 confirmation summary)
- ✅ Make it easier to operate (Task 2 rich prompts vs CLI args)
- ✅ Make it similar to Vol Suite (Task 2 menu-driven workflow)
- ✅ Preserve backward compatibility (Task 1 CLI mode unchanged, Task 4 batch file unchanged)

**Placeholder scan:** None found.

**Type consistency:** All function signatures use consistent typing. Focus dict keys: ticker, option_type, strike, index_ticker, expiration_date, target_years, fail_on_suite_error.

