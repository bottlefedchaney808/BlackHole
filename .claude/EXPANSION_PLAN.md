# FinancialDevelopment Expansion Plan

**Prepared:** 2026-07-29  
**Based on:** CARL Adversarial Architectural Review  
**Scope:** Roadmap from development system → production-grade infrastructure  

---

## Executive Summary

The FinancialDevelopment project has a **strong modular architecture** (orchestrator-driven suite pipeline with context handoff), but **requires critical fixes** before production or multi-user deployment. The CARL review identified:

- **4 Critical defects** (auth, connection leaks, race conditions, concurrent access)
- **6 Major architectural gaps** (dependency validation, Vol output contract, state audit, migrations, graceful shutdown, source abstraction)
- **5 Expansion blockers** (data source portability, deployment portability, schema extensibility)

This plan sequences fixes into 5 phases, enabling incremental value:
1. **Phase 1 (3 weeks)** → Production-ready for single operator
2. **Phase 2 (4 weeks)** → Production-ready for team collaboration + multi-source prep
3. **Phase 3 (3 weeks)** → Cloud/container ready, Linux/Mac support
4. **Phase 4 (4 weeks)** → Multi-source architecture (CME, OTC, etc.)
5. **Phase 5 (3 weeks)** → High-volume operations, monitoring

---

## Phase 1: Critical Fixes (3 Weeks, 20 Hours)

**Goal:** Fix security and data integrity defects blocking production deployment.

### 1.1: Implement Dashboard Authentication
**Why:** Dashboard is wide-open to the network; anyone can trigger expensive API calls.
- Add API key validation to POST endpoints
- Rate limit: 1 run per 60s, max 10 concurrent
- Log client IP and timestamp
- **Files:** `dashboard/app.py`, `dashboard/templates/index.html`
- **Effort:** 4 hours
- **Acceptance Criteria:**
  - `/runs/trigger` requires `Authorization: Bearer <key>` header
  - Returns 401 if missing/invalid
  - Rejects requests exceeding rate limit with 429 status
  - orchestrator_runs includes client_ip column

### 1.2: Fix Database Connection Leaks
**Why:** Connections not closed on exception; under load, connection pool exhausts, causing "database is locked" errors.
- Audit all `get_connection()` calls in swaps_query.py, dashboard/app.py, db_loader.py
- Convert to try-finally pattern; prioritize context manager syntax
- **Files:** `swaps_query.py`, `dashboard/app.py`, `db_loader.py`, `orchestrator.py`
- **Effort:** 6 hours
- **Acceptance Criteria:**
  - pytest with concurrent read/write load test; no connection leaks
  - psutil test: connection count returns to baseline after exception

### 1.3: Atomize Backfill State Updates
**Why:** State tracking fails if backfill crashes after upsert; resume causes duplicates.
- Move `loader.set_state()` into same transaction as `upsert_trades()`, or into the try block before exception can occur
- Add idempotency check: skip re-processing same date if trades already exist
- **Files:** `backfill.py`, `db_loader.py`
- **Effort:** 4 hours
- **Acceptance Criteria:**
  - Simulated crash (raise exception after upsert, before set_state): resume detects duplicate and skips
  - scrape_log shows no duplicate entries for same date

### 1.4: Serialize Concurrent Database Access
**Why:** Scheduler and orchestrator both write to swaps.db without coordination; data corruption and inconsistent reads.
- Enable WAL mode: `PRAGMA journal_mode=WAL` in setup_db.py
- Add `BEGIN EXCLUSIVE` in scheduler.py before upsert_trades (prevents concurrent writers)
- Add snapshot isolation for orchestrator reads (preserves consistency during scheduler writes)
- **Files:** `setup_db.py`, `db_loader.py`, `scheduled_ingest.py`, `orchestrator.py`
- **Effort:** 6 hours
- **Acceptance Criteria:**
  - WAL mode enabled; -wal, -shm files present after first write
  - Concurrent backfill + orchestrator run: no "database is locked" errors, no data corruption
  - swap_activity consistent across multiple orchestrator runs during scheduler write

**Phase 1 Delivery:** 20 hours → 2-3 weeks wall-clock time

---

## Phase 2: Architectural Robustness (4 Weeks, 38 Hours)

**Goal:** Add reliability, observability, and multi-source extensibility.

### 2.1: Explicit Dependency Validation
**Why:** Vol_Suite failures flow silently downstream; Options_Suite consumes incomplete context.
- Each suite writes required marker file (e.g., `vol_complete.txt`)
- Orchestrator validates marker before passing to downstream suites
- Add `--fail-on-suite-error` flag to abort run if upstream critical suite fails
- **Files:** `orchestrator.py`, `Vol_Suite/volatility_suite.py`, `Options_Suite/main.py`, `VaR_Tools_Simulations/main.py`
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - Vol_Suite crash: orchestrator detects missing marker, warns user, optionally aborts
  - Options_Suite with --fail-on-suite-error and missing vol data: exits with error, not silent calculation

### 2.2: Formalize Vol_Suite Output Contract
**Why:** Stdin scripting is brittle; if Vol_Suite prompts change, orchestrator crashes blindly.
- Add `--context` and `--context-out` arguments to Vol_Suite (reuse from Options_Suite pattern)
- Document expected vol_result.json schema
- Replace _vol_stdin_script() with direct context mode
- **Files:** `Vol_Suite/volatility_suite.py`, `orchestrator.py`
- **Effort:** 12 hours
- **Acceptance Criteria:**
  - `python Vol_Suite/volatility_suite.py --context <path> --context-out <path>` works
  - vol_result.json matches defined schema (e.g., contains gamma_surface, dealer_positioning keys)
  - Reordering prompts in Vol_Suite doesn't break orchestrator

### 2.3: Implement Context Mutation Audit Trail
**Why:** sentiment-scanner mutates context; no visibility into what changed or ability to rollback.
- Validate context before and after sentiment fold-back
- Log before/after JSON diff to orchestrator logs
- Implement rollback: save baseline context; on mutation failure, restore and fail run
- **Files:** `orchestrator.py`, `shared/schemas.py`
- **Effort:** 6 hours
- **Acceptance Criteria:**
  - Orchestrator logs "context mutation" entry with diff
  - Simulated sentiment corruption (e.g., ranked_tickers becomes string): orchestrator detects, logs, fails run
  - Audit trail in orchestrator_runs shows what sentiment context changed

### 2.4: Implement Schema Migration Framework
**Why:** Adding columns or constraints to swap_trades requires manual ALTER TABLE; blocks scaling.
- Create `migrations/` directory with numbered SQL files (001_initial.sql, 002_add_data_quality_flag.sql)
- Add `schema_version` table to track applied migrations
- Rewrite setup_db.py to run all pending migrations idempotently
- Orchestrator checks schema_version on startup; warns if outdated
- **Files:** `setup_db.py`, `migrations/*.sql`, `orchestrator.py`
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - New empty database: setup_db.py runs 001_initial.sql and logs "schema v1 initialized"
  - Running setup_db.py twice: no error, logs "schema already at v1"
  - Adding new migration (002_*.sql) and running setup_db.py: new migration applied, schema_version updated

### 2.5: Add Graceful Shutdown for Long-Running Processes
**Why:** Ctrl+C kills scheduler/backfill mid-operation; state is corrupted, process kills mid-transaction.
- Register SIGINT/SIGTERM handlers in scheduled_ingest.py and backfill.py
- On signal: set `shutdown_requested = True`, finish current batch, exit cleanly
- Log "graceful shutdown initiated" and "shutdown complete"
- APScheduler: call scheduler.shutdown(wait=True) to finish in-flight jobs
- **Files:** `scheduled_ingest.py`, `backfill.py`
- **Effort:** 4 hours
- **Acceptance Criteria:**
  - Ctrl+C during backfill: logs "graceful shutdown", current batch finishes, process exits normally
  - Scheduler: APScheduler stops accepting new jobs, waits for current job to finish, then exits
  - State (last_cumulative_date, last_live_slice_id) is updated before shutdown

**Phase 2 Delivery:** 38 hours → 3-4 weeks cumulative (7 weeks from start)

---

## Phase 3: Portability & Deployment (3 Weeks, 22 Hours)

**Goal:** Enable Docker deployment and cross-platform support.

### 3.1: Add Docker Support
**Why:** Single venv is Windows-only; team members need Linux/Mac support.
- Write `Dockerfile.scheduler` and `Dockerfile.dashboard`
- Create `docker-compose.yml` for local development
- Document build and run instructions
- **Files:** New `Dockerfile.*`, `docker-compose.yml`
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - `docker-compose up` starts scheduler + dashboard locally
  - Dashboard accessible at http://localhost:8787
  - Volumes bind to local .env and swaps.db for local persistence

### 3.2: Add Cross-Platform Shell Scripts
**Why:** .bat files don't work on Linux/Mac.
- Write shell equivalents (orchestrator.sh, run_scheduler.sh, dashboard.sh)
- Use environment variables ($PYTHON, $VENV) instead of hardcoded paths
- Test on macOS and Ubuntu
- **Files:** New `*.sh` scripts, update Python code to use pathlib for paths
- **Effort:** 6 hours
- **Acceptance Criteria:**
  - `bash orchestrator.sh --unified --ticker NVDA --target-years 0.25` works on Linux/Mac
  - PATH resolution works on Windows, Mac, Linux
  - Relative paths converted to absolute via pathlib.Path

### 3.3: Add Cloud Deployment Configuration
**Why:** Team needs to deploy to staging/production cloud environments.
- Write `Procfile` (Heroku), `*.service` files (systemd), `skaffold.yaml` (Kubernetes)
- Document AWS Lambda / GCP Cloud Run considerations
- **Files:** New `Procfile`, `*.service`, cloud deployment docs
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - `heroku create` and `git push heroku main` deploys dashboard
  - systemd service file enables `systemctl start scheduler`
  - Cloud deployment guide covers environment setup (.env injection)

**Phase 3 Delivery:** 22 hours → 2-3 weeks cumulative (9-10 weeks from start)

---

## Phase 4: Multi-Source Architecture (4 Weeks, 32 Hours)

**Goal:** Enable adding CME, OTC, and other data sources without refactoring.

### 4.1: Abstract Data Source Layer
**Why:** Current DTCC-only architecture makes adding CME/OTC require schema changes throughout.
- Create `DataSourceAdapter` interface: `list_files(regulator, asset)`, `download(url)`, `parse(bytes)` → `list[SwapRecord]`
- Implement `DTCCAdapter` as reference
- Refactor backfill.py to iterate over adapters
- Add `data_source` column to schema (migration 003_add_data_source.sql)
- **Files:** New `data_sources/adapter.py`, `data_sources/dtcc.py`; `backfill.py`, `setup_db.py`, `db_loader.py`
- **Effort:** 16 hours
- **Acceptance Criteria:**
  - New `CMEAdapter` can be added without modifying backfill.py
  - backfill.py: `for adapter in adapters: load_via_adapter(adapter)`
  - swap_trades table has data_source column; queries filter by it
  - Existing backfill continues to work (DTCC adapter handles SEC/CFTC)

### 4.2: Generalize Instrument Identifier Resolution
**Why:** DTCC uses UPI; CME uses product codes; need unified instrument resolver.
- Extend `upi_decoder.py` to instrument resolver interface
- Implement DTCC UPI resolver as reference
- Framework for CME/OTC identifier resolvers
- **Files:** `upi_decoder.py`, new `instrument_resolvers/`
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - `resolve_instrument(upi, source='DTCC')` → company_name, underlying, type
  - `resolve_instrument(cme_code, source='CME')` → similar response
  - Dashboard shows normalized instrument names across sources

### 4.3: Update Orchestrator & Queries for Multi-Source
**Why:** swaps_query and orchestrator assume DTCC data; multi-source queries need filtering.
- swaps_query.top_notional_products() accepts optional data_source filter
- orchestrator.get_recent_swap_activity() includes data_source in context
- Dashboard /swaps route shows data_source tags
- **Files:** `orchestrator.py`, `swaps_query.py`, `dashboard/app.py`, `dashboard/templates/*.html`
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - orchestrator context includes data_source in swap_activity
  - `swaps_query.top_notional_products(data_source='CME')` filters correctly
  - Dashboard swap table shows source column

**Phase 4 Delivery:** 32 hours → 3-4 weeks cumulative (13-14 weeks from start)

---

## Phase 5: Observability & Scaling (3 Weeks, 24 Hours)

**Goal:** Enable production monitoring and high-volume operations.

### 5.1: Add Structured Logging & Metrics
**Why:** Current logs are human-readable; difficult to parse for alerting or aggregation.
- Replace print/logger.info with structured JSON logging
- Add OpenTelemetry instrumentation (optional)
- Metrics: rows_inserted, upsert_duration, errors_count per batch
- Forward all logs to stdout (container-ready)
- **Files:** All modules (add logging calls), new `shared/logging.py`
- **Effort:** 10 hours
- **Acceptance Criteria:**
  - Backfill logs: `{"timestamp": "2026-07-29T12:00:00Z", "level": "INFO", "event": "batch_complete", "rows_inserted": 5000, "duration_ms": 1200}`
  - All logs to stderr (JSON format)
  - Dashboa​rd parse logs for error count trends

### 5.2: Add Database Query Performance Monitoring
**Why:** As data grows, queries slow down; need visibility into slow queries.
- Log query execution time
- Alert on queries > 1s
- EXPLAIN QUERY PLAN for slow queries
- **Files:** `swaps_query.py`, `orchestrator.py`
- **Effort:** 6 hours
- **Acceptance Criteria:**
  - Queries logged: `{"sql": "SELECT...", "duration_ms": 1200, "rows_returned": 5000}`
  - Identify slow queries (> 1s) in backfill and orchestrator
  - QUERY PLAN logged for > 1s queries

### 5.3: Implement Connection Pooling
**Why:** db_loader.py creates new connection per batch; backfill with 1.5M rows is slow.
- SQLitePool or simple connection pool (max 5 connections)
- Measure backfill performance improvement
- Document pool sizing tuning
- **Files:** `db_loader.py`, `swaps_query.py`
- **Effort:** 8 hours
- **Acceptance Criteria:**
  - Backfill 1.5M rows: < 30 minutes (vs. current ~2 hours)
  - Connection pool metrics: avg 2-3 active connections, reuse rate > 90%
  - No "too many connections" errors

**Phase 5 Delivery:** 24 hours → 2-3 weeks cumulative (16-17 weeks from start)

---

## Critical Path (MVP Production, 3 Weeks)

If you need production-ready code in 3 weeks, skip Phases 3-5 and deploy:

**Phase 1 (all 4 items)** + **Phase 2.1 only (dependency validation)**

This covers:
- ✅ Dashboard auth (stops DoS)
- ✅ DB connection leaks (stops crashes)
- ✅ State atomicity (stops duplicates)
- ✅ Write serialization (prevents corruption)
- ✅ Dependency validation (prevents silent failures)

**After 3 weeks:** System is production-ready for a **single operator or small team** (no multi-user public access yet). Remaining phases enable scaling, team collab, and multi-source support.

---

## Recommended Timeline

| Week | Phase | Items | Milestones |
|------|-------|-------|-----------|
| 1-3 | **1** | Auth, DB leaks, state atomicity, serialization | Production-ready (single op) |
| 4-7 | **2** | Dependency validation, Vol contract, audit, migrations, shutdown | Team-ready, multi-source prep |
| 8-10 | **3** | Docker, cross-platform, cloud deployment | Multi-environment ready |
| 11-14 | **4** | Data source abstraction, multi-source orchestration | CME/OTC/alternatives ready |
| 15-17 | **5** | Logging, metrics, pooling | High-volume ops ready |

**Total:** 17 weeks (4 months) to full production-grade system

---

## Success Metrics per Phase

### Phase 1
- [ ] Dashboard requires API key; rejects unauthorized requests with 401
- [ ] No "database is locked" errors under concurrent load
- [ ] Backfill resume detects duplicates; skips re-processing
- [ ] Concurrent scheduler + orchestrator: no data corruption

### Phase 2
- [ ] Vol_Suite failure: orchestrator warns, halts downstream execution
- [ ] Vol_Suite output validated against schema; orchestrator fails if schema mismatch
- [ ] Context mutations logged with before/after diff
- [ ] Schema version tracked; new migrations apply idempotently
- [ ] Graceful shutdown: Ctrl+C finishes current batch, exits cleanly

### Phase 3
- [ ] `docker-compose up` starts full system on any platform
- [ ] `bash orchestrator.sh` works on macOS/Linux/Windows
- [ ] Heroku deployment: `git push heroku main` deploys dashboard

### Phase 4
- [ ] New `CMEAdapter` added; backfill loads CME futures without refactoring backfill.py
- [ ] Swaps from multiple sources queryable; data_source column present
- [ ] Dashboard shows source tags; queries filter by source

### Phase 5
- [ ] All logs JSON-formatted; parseable for alerting
- [ ] Queries logged; slow queries (> 1s) identified automatically
- [ ] Backfill performance: 1.5M rows in < 30 min (vs. ~2 hours currently)

---

## Dependencies & Blockers

**Phase 1 ↔ Phase 2:** Phase 1 must complete before Phase 2 begins (critical fixes must be in place before adding complexity)

**Phase 2 ↔ Phase 4:** Phase 2 migrations framework must exist before Phase 4's schema changes

**Phase 3 parallel:** Can start after Phase 1 (doesn't depend on Phase 2)

**Phase 5 parallel:** Can start after Phase 2 (logging and metrics are independent)

---

## Cost Estimate

| Phase | Effort | Team | Duration | Capacity |
|-------|--------|------|----------|----------|
| 1 | 20h | 1-2 devs | 2-3 weeks | 60% of one dev |
| 2 | 38h | 1-2 devs | 3-4 weeks | 60% of one dev |
| 3 | 22h | 1 dev | 2-3 weeks | 40% of one dev |
| 4 | 32h | 1 dev | 3-4 weeks | 50% of one dev |
| 5 | 24h | 1 dev | 2-3 weeks | 40% of one dev |
| **Total** | **136h** | **1 dev (full-time)** | **16-17 weeks** | |

**Recommendation:** Dedicate one full-time developer for phases 1-3 (9-10 weeks), then rotate or parallelize phases 4-5 with other work.

