# CARL Adversarial Review — Findings Ledger

**Project:** FinancialDevelopment  
**Date:** 2026-07-29  
**Artifact Identity:** master@17de36c baseline + live working tree  
**Review Mode:** SPEC + CODE (Architecture + Implementation)  
**Reviewers:** R1 (Code Audit), R2 (Architectural Risk)  
**Convergence:** YES (3 rounds conducted; critical findings verified)  
**Verdict:** **NEEDS_REVISION** before production deployment

---

## CRITICAL FINDINGS (Block Production Deployment)

### C1: Dashboard Has No Authentication [R1-F1, R2-A6]
- **Severity:** CRITICAL
- **Evidence:** dashboard/app.py has no auth middleware; anyone on network can trigger API calls that cost money
- **Consequence:** DoS risk, unauthorized API consumption, unaudited operations
- **Fix:** Add HTTPBasic/OAuth2, rate limiting, API key validation to /runs/* endpoints
- **Effort:** 4 hours
- **Status:** VERIFIED (both R1 and R2)

### C2: Database Connection Leaks [R1-F2]
- **Severity:** CRITICAL
- **Evidence:** swaps_query.py, dashboard/app.py, db_loader.py call get_connection() without try-finally; exceptions bypass conn.close()
- **Consequence:** Connection pool exhaustion under high load; "database is locked" errors; scheduler stalls
- **Fix:** Wrap all queries in try-finally or context manager
- **Effort:** 6 hours
- **Status:** VERIFIED (code-level)

### C3: Database Concurrent Access Not Serialized [R2-A4, R2-A8]
- **Severity:** CRITICAL
- **Evidence:** scheduler writes to swap_trades, orchestrator reads/writes orchestrator_runs, no explicit locking between them; scheduler's mkdir lock is not respected by other components
- **Consequence:** Dirty reads, partial updates visible to readers; inconsistent swap_activity; data corruption under concurrent load
- **Fix:** Enable SQLite WAL mode, add BEGIN EXCLUSIVE in scheduler, snapshot isolation for orchestrator
- **Effort:** 6 hours
- **Status:** VERIFIED (architectural)

### C4: Upsert Race Condition + State Rollback [R1-F3, R1-F4, R2-A5]
- **Severity:** CRITICAL
- **Evidence:** db_loader.py line 68-72 SELECT before INSERT (race window); backfill.py line 101 set_state called AFTER try block (rollback failure)
- **Consequence:** Duplicate records on resume; inaccurate inserted/updated counts; state tracking loss
- **Fix:** Remove pre-check SELECT, rely on ON CONFLICT; wrap upsert+set_state in transaction
- **Effort:** 4 hours
- **Status:** VERIFIED (both code and architectural)

---

## MAJOR FINDINGS (Block Reliability / Multi-Source Expansion)

### M1: No Explicit Dependency Validation [R2-A1]
- **Severity:** MAJOR
- **Evidence:** orchestrator accepts failed Vol_Suite output (missing files); downstream suites consume incomplete context silently
- **Consequence:** Silent failures; invalid analysis results; no visibility into which stage failed
- **Fix:** Validate output schema before passing to children; fail-fast option
- **Effort:** 8 hours

### M2: Vol_Suite Output Contract is Opaque [R2-A2]
- **Severity:** MAJOR
- **Evidence:** Vol_Suite has no --context-out; orchestrator reconstructs payload from filesystem artifacts; no schema validation
- **Consequence:** Fragile coupling; output format changes break orchestrator silently
- **Fix:** Add --context/--context-out to Vol_Suite; formalize output schema
- **Effort:** 12 hours

### M3: Context Mutation is Unaudited [R2-A3]
- **Severity:** MAJOR
- **Evidence:** sentiment-scanner mutates context; orchestrator folds it back without before/after validation; no rollback on failure
- **Consequence:** Silent data corruption; mutations not tracked for audit
- **Fix:** Validate before/after mutations; add audit trail; implement rollback
- **Effort:** 6 hours

### M4: No Schema Migration Framework [R2-A9]
- **Severity:** MAJOR
- **Evidence:** setup_db.py runs once with CREATE TABLE IF NOT EXISTS; no version tracking; adding columns requires manual ALTER TABLE
- **Consequence:** Schema changes are manual and error-prone; blocks adding new data sources
- **Fix:** Implement lightweight migration runner with version tracking
- **Effort:** 8 hours

### M5: Vol_Suite Stdin Scripting is Brittle [R2-A10]
- **Severity:** MAJOR
- **Evidence:** orchestrator hardcodes prompt answers; if prompts reorder, child crashes with EOFError
- **Consequence:** Coupling to Vol_Suite's prompt order; fragile deployment
- **Fix:** Add --context support to Vol_Suite instead of stdin scripting
- **Effort:** 12 hours (included in M2)

### M6: No Graceful Shutdown [R2-A12]
- **Severity:** MAJOR
- **Evidence:** scheduler and backfill have no SIGINT/SIGTERM handlers; Ctrl+C kills mid-operation
- **Consequence:** Partial data writes; corrupted state; no shutdown log
- **Fix:** Add signal handlers, finish current batch, clean exit
- **Effort:** 4 hours

---

## MINOR FINDINGS (Code Quality, Performance)

### N1: Error Handling Gaps
- R1-F5: datetime.strptime() not wrapped (4 hours)
- R1-F7: Empty table handling in swaps_query (2 hours)
- R1-F8: DTCC API response.json() not validated (2 hours)
- R1-F9: Asymmetric error handling in scheduled_ingest (2 hours)

### N2: Validation Gaps
- R1-F13: swap_activity_limit not validated (1 hour)
- R1-F6: JSON encoding inconsistency (utf-8 vs utf-8-sig) (2 hours)

### N3: Performance & Resource Management
- R1-F14: glob.glob() iterates all files without limit (2 hours)
- Driver-F4: DB connection pooling missing; slow backfill (8 hours)
- Driver-F3: Temp files created without cleanup guarantee (2 hours)

### N4: Compatibility & Code Quality
- R1-F10: CSV read off-by-one bug (1 hour)
- R1-F11: Mutable default argument in load_env_once (1 hour)
- R1-F12: Type hint `list[dict]` incompatible with Python 3.8 (1 hour)
- Driver-F1: Hardcoded Windows paths (SHARED_PYTHON) (2 hours)

---

## EXPANSION CONSTRAINTS (Block Multi-Source / Cloud Deployment)

### E1: DTCC-Only Architecture [R2-A11]
- **Blocker for:** Adding CME, OTC, or alternative data sources
- **Fix:** Abstract DataSourceAdapter interface; refactor backfill to use adapters
- **Effort:** 16 hours

### E2: Windows-Only Deployment [R2-A13]
- **Blocker for:** Linux/Mac support, Docker, cloud deployment
- **Fix:** Add Docker support, cross-platform shell scripts, cloud configs
- **Effort:** 22 hours

### E3: Extra Fields Bypass Validation [R2-A14]
- **Blocker for:** Schema versioning, future extensibility
- **Fix:** Formalize extra fields in schema v2; child suites must preserve them
- **Effort:** 4 hours

---

## PHASED REMEDIATION ROADMAP

### Phase 1: Critical Fixes (2-3 weeks, 20 hours)
→ Unblocks: Production deployment, team access
1. Dashboard auth (4h)
2. DB connection leaks (6h)
3. Atomize backfill state (4h)
4. Write serialization (6h)

### Phase 2: Architectural Robustness (3-4 weeks, 38 hours)
→ Unblocks: Reliability, multi-source prep
1. Dependency validation (8h)
2. Vol_Suite output contract (12h)
3. Context mutation audit (6h)
4. Schema migrations (8h)
5. Graceful shutdown (4h)

### Phase 3: Portability & Deployment (2-3 weeks, 22 hours)
→ Unblocks: Multi-environment, team collaboration
1. Docker support (8h)
2. Cross-platform scripts (6h)
3. Cloud deployment config (8h)

### Phase 4: Multi-Source Architecture (3-4 weeks, 32 hours)
→ Unblocks: Adding CME, OTC, alternatives
1. DataSourceAdapter abstraction (16h)
2. UPI generalization (8h)
3. Multi-source orchestration (8h)

### Phase 5: Observability & Scaling (2-3 weeks, 24 hours)
→ Unblocks: Production monitoring, high-volume ops
1. Structured logging (10h)
2. Query performance monitoring (6h)
3. Connection pooling (8h)

**Total Effort:** ~136 hours (10-11 weeks)  
**MVP Production (Phase 1 + 2.1):** ~28 hours (3 weeks)

---

## VERDICT & RECOMMENDATION

**Current Status:** ✗ NOT PRODUCTION-READY
- Strong modular architecture (context-driven handoff, DAG-like stages)
- Robust for single-operator development (current use case)
- Critical security and data integrity issues must be fixed before:
  - Multi-user or team access
  - High-volume concurrent operations
  - Cloud deployment

**Recommendation:** Deploy Phase 1 immediately (critical fixes). Phase 2 before team collaboration. Phases 3-5 enable future scaling.

**Sign-Off:** 
- R1 Audit: VERIFIED (14 findings, high confidence)
- R2 Architecture: VERIFIED (14 findings, high confidence)
- Driver Assessment: SYNTHESIZED (5 expansion constraints, 5 architectural notes)
- Convergence: YES (no conflicting verdicts; overlapping findings reinforce severity)
