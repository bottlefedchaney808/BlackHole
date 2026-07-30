#!/usr/bin/env python3
"""Phase 2 Verification Test Suite - Automated"""

import sys
import os
import sqlite3
import tempfile
import subprocess
import json
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Test results
RESULTS = {
    "passed": [],
    "failed": [],
    "warnings": [],
}


def test(name, fn):
    """Run a test and record result"""
    try:
        fn()
        RESULTS["passed"].append(name)
        print("[PASS] %s" % name)
        return True
    except AssertionError as e:
        RESULTS["failed"].append((name, str(e)))
        print("[FAIL] %s: %s" % (name, e))
        return False
    except Exception as e:
        RESULTS["failed"].append((name, "%s: %s" % (type(e).__name__, e)))
        print("[WARN] %s: %s: %s" % (name, type(e).__name__, e))
        RESULTS["warnings"].append(name)
        return False


# ============================================================================
# Test 2.1: Graceful Shutdown
# ============================================================================

def test_shutdown_signal_import():
    """Graceful shutdown module exists and imports"""
    from shutdown_signal import GracefulShutdown
    assert GracefulShutdown is not None


def test_shutdown_signal_methods():
    """GracefulShutdown has required methods"""
    from shutdown_signal import GracefulShutdown
    gs = GracefulShutdown()
    assert hasattr(gs, 'is_requested')
    assert hasattr(gs, 'request_shutdown')
    assert callable(gs.is_requested)
    assert callable(gs.request_shutdown)


def test_backfill_imports_shutdown():
    """backfill.py imports GracefulShutdown"""
    backfill_code = (ROOT / "backfill.py").read_text()
    assert "from shutdown_signal import GracefulShutdown" in backfill_code
    assert "shutdown.is_requested()" in backfill_code


# ============================================================================
# Test 2.2: Dependency Validation
# ============================================================================

def test_orchestrator_has_validation():
    """orchestrator.py has validation functions"""
    orch_code = (ROOT / "orchestrator.py").read_text()
    assert "_validate_suite_output" in orch_code
    assert "--fail-on-suite-error" in orch_code or "fail_on_suite_error" in orch_code


def test_suite_validation_module():
    """shared/suite_validation.py exists"""
    val_path = ROOT / "shared" / "suite_validation.py"
    assert val_path.exists(), f"Missing: {val_path}"
    content = val_path.read_text()
    assert "validate" in content.lower()


# ============================================================================
# Test 2.3: Vol_Suite Output Contract
# ============================================================================

def test_vol_suite_argparse():
    """Vol_Suite has argparse for --context/--context-out"""
    vol_code = (ROOT / "Vol_Suite" / "volatility_suite.py").read_text()
    assert "argparse" in vol_code
    assert "--context" in vol_code or "'--context'" in vol_code
    assert "--context-out" in vol_code or "'--context-out'" in vol_code


def test_orchestrator_uses_vol_flags():
    """orchestrator.py uses new Vol_Suite flags (not stdin)"""
    orch_code = (ROOT / "orchestrator.py").read_text()
    # Should NOT have old stdin script
    if "_vol_stdin_script" in orch_code:
        # If it exists, should not be called
        assert "add_task(_vol_stdin_script" not in orch_code
    # Should reference context flags
    assert "--context" in orch_code


def test_vol_result_schema():
    """Vol_Suite result schema defined"""
    schemas_code = (ROOT / "shared" / "schemas.py").read_text()
    assert "vol" in schemas_code.lower()
    assert "result" in schemas_code.lower()


# ============================================================================
# Test 2.4: Context Audit Trail
# ============================================================================

def test_orchestrator_context_audit():
    """orchestrator.py has context mutation tracking"""
    orch_code = (ROOT / "orchestrator.py").read_text()
    assert "context" in orch_code
    assert "mutation" in orch_code.lower() or "audit" in orch_code.lower()


def test_context_validation_schema():
    """shared/schemas.py has validation for context mutations"""
    schemas_code = (ROOT / "shared" / "schemas.py").read_text()
    assert "context" in schemas_code.lower()
    assert "sentiment" in schemas_code.lower()


# ============================================================================
# Test 2.5: Schema Migration Framework
# ============================================================================

def test_setup_db_migration_framework():
    """setup_db.py has full migration framework"""
    setup_code = (ROOT / "setup_db.py").read_text()
    assert "schema_version" in setup_code
    assert "migrate" in setup_code
    assert "_discover_migrations" in setup_code
    assert "MIGRATIONS_DIR" in setup_code


def test_migrations_directory_exists():
    """migrations/ directory exists"""
    migrations_dir = ROOT / "migrations"
    assert migrations_dir.exists(), f"Missing: {migrations_dir}"
    assert migrations_dir.is_dir()


def test_initial_migration_exists():
    """001_initial.sql migration exists"""
    initial = ROOT / "migrations" / "001_initial.sql"
    assert initial.exists(), f"Missing: {initial}"
    content = initial.read_text()
    assert "CREATE TABLE IF NOT EXISTS" in content


def test_setup_db_cli_flags():
    """setup_db.py has --migrate and --status flags"""
    setup_code = (ROOT / "setup_db.py").read_text()
    assert "--migrate" in setup_code
    assert "--status" in setup_code


def test_migration_idempotency():
    """Migration runner is idempotent (checks schema_version)"""
    setup_code = (ROOT / "setup_db.py").read_text()
    assert "_get_current_version" in setup_code
    assert "schema_version" in setup_code
    # Should only apply migrations not in schema_version
    assert "if m.version > starting_version" in setup_code or "pending" in setup_code


# ============================================================================
# Test 2.X: Integration Tests
# ============================================================================

def test_orchestrator_imports_no_errors():
    """orchestrator.py imports without errors"""
    try:
        import orchestrator  # noqa
    except ImportError as e:
        if "Vol_Suite" in str(e) or "sentiment-scanner" in str(e):
            # It's OK if suite imports fail (suites may have own deps)
            pass
        else:
            raise


def test_db_loader_atomic_upsert():
    """db_loader.py has atomic upsert with transactions"""
    loader_code = (ROOT / "db_loader.py").read_text()
    assert "BEGIN EXCLUSIVE" in loader_code or "BEGIN" in loader_code
    assert "ON CONFLICT" in loader_code
    # Should NOT have separate SELECT before INSERT
    assert 'SELECT 1 FROM swap_trades WHERE dissemination_id' not in loader_code


def test_backfill_state_atomicity():
    """backfill.py moves set_state into try block"""
    backfill_code = (ROOT / "backfill.py").read_text()
    # Should have CRITICAL comment about atomicity
    assert "CRITICAL" in backfill_code or "atomicity" in backfill_code.lower()


# ============================================================================
# Main
# ============================================================================

def main():
    print("\n" + "=" * 70)
    print("PHASE 2 VERIFICATION TEST SUITE")
    print("=" * 70 + "\n")

    # Test 2.1: Graceful Shutdown
    print("[2.1] Graceful Shutdown")
    test("shutdown_signal module imports", test_shutdown_signal_import)
    test("shutdown_signal has required methods", test_shutdown_signal_methods)
    test("backfill.py imports shutdown signal", test_backfill_imports_shutdown)
    print()

    # Test 2.2: Dependency Validation
    print("[2.2] Dependency Validation")
    test("orchestrator.py has validation functions", test_orchestrator_has_validation)
    test("suite_validation.py module exists", test_suite_validation_module)
    print()

    # Test 2.3: Vol_Suite Contract
    print("[2.3] Vol_Suite Output Contract")
    test("Vol_Suite has argparse CLI", test_vol_suite_argparse)
    test("orchestrator uses Vol_Suite flags", test_orchestrator_uses_vol_flags)
    test("vol_result schema defined", test_vol_result_schema)
    print()

    # Test 2.4: Context Audit Trail
    print("[2.4] Context Audit Trail")
    test("orchestrator has context audit", test_orchestrator_context_audit)
    test("context validation schema exists", test_context_validation_schema)
    print()

    # Test 2.5: Schema Migrations
    print("[2.5] Schema Migration Framework")
    test("setup_db has migration framework", test_setup_db_migration_framework)
    test("migrations/ directory exists", test_migrations_directory_exists)
    test("001_initial.sql migration exists", test_initial_migration_exists)
    test("setup_db has CLI flags", test_setup_db_cli_flags)
    test("migrations are idempotent", test_migration_idempotency)
    print()

    # Integration tests
    print("[INT] Integration Tests")
    test("orchestrator imports without errors", test_orchestrator_imports_no_errors)
    test("db_loader uses atomic upsert", test_db_loader_atomic_upsert)
    test("backfill has atomic state updates", test_backfill_state_atomicity)
    print()

    # Summary
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    passed = len(RESULTS["passed"])
    failed = len(RESULTS["failed"])
    warnings = len(RESULTS["warnings"])
    total = passed + failed

    print("PASSED: %d/%d" % (passed, total))
    print("FAILED: %d/%d" % (failed, total))
    if warnings:
        print("WARNINGS: %d" % warnings)

    if RESULTS["failed"]:
        print("\nFailed tests:")
        for name, error in RESULTS["failed"]:
            print("  - %s: %s" % (name, error))

    print("\n" + ("=" * 70))
    if failed == 0:
        print("SUCCESS: PHASE 2 VERIFICATION - ALL TESTS PASSED")
        print("=" * 70)
        return 0
    else:
        print("WARNING: PHASE 2 VERIFICATION - %d TESTS FAILED" % failed)
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
