# Phase 1 Verification Checklist

**Objective:** Verify that all Phase 1 critical fixes are working correctly.

**Estimated Time:** 2-3 hours

---

## Pre-Flight Checks

- [ ] Backup current swaps.db (in case rollback needed)
- [ ] Verify Python version >= 3.11
- [ ] Install new dependencies: `pip install -r requirements.txt`
- [ ] .env file has THETADATA_CF_* credentials set
- [ ] .env file has DASHBOARD_API_KEY set (or uses default)

---

## Test Suite 1: Dashboard Authentication

### 1.1 API Key Validation

**Setup:**
```bash
# Start dashboard with debug logging
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000 --reload
```

**Test Cases:**

| # | Test | Command | Expected | Status |
|---|------|---------|----------|--------|
| 1.1.1 | No API key | `curl -X POST http://localhost:8000/run/unified -d '{"ticker":"NVDA"}'` | 401 Unauthorized | [ ] |
| 1.1.2 | Wrong API key | `curl -X POST -H "Authorization: Bearer wrong-key" http://localhost:8000/run/unified -d '{"ticker":"NVDA"}'` | 401 Unauthorized | [ ] |
| 1.1.3 | Valid API key (dev) | `curl -X POST -H "Authorization: Bearer dev-key-change-in-production" http://localhost:8000/run/unified -d '{"ticker":"NVDA", "target_years": 0.25}'` | 202 Accepted, run_id returned | [ ] |
| 1.1.4 | Bearer format | `curl -X POST -H "Authorization: Bearer dev-key-change-in-production" ...` | 202 Accepted | [ ] |
| 1.1.5 | Bare key format | `curl -X POST -H "Authorization: dev-key-change-in-production" ...` | 202 Accepted | [ ] |

**Pass Criteria:** All 5 tests pass

### 1.2 Rate Limiting

| # | Test | Command | Expected | Status |
|---|------|---------|----------|--------|
| 1.2.1 | First run in 60s | `curl -X POST -H "Authorization: Bearer dev-key-change-in-production" ...` (1st) | 202 Accepted | [ ] |
| 1.2.2 | Second run <60s | `curl -X POST -H "Authorization: Bearer dev-key-change-in-production" ...` (2nd, same IP) | 429 Too Many Requests | [ ] |
| 1.2.3 | After 60s elapsed | `curl -X POST ...` (after wait) | 202 Accepted | [ ] |

**Pass Criteria:** Rate limiting active, resets after 60s

---

## Test Suite 2: Database Connection Leaks

### 2.1 Normal Query Path

**Setup:**
```bash
# Terminal 1: Start dashboard
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000

# Terminal 2: Monitor open connections
watch 'lsof | grep swaps.db | wc -l'
```

**Test Cases:**

| # | Test | Action | Expected | Status |
|---|------|--------|----------|--------|
| 2.1.1 | Query /swaps | Visit http://localhost:8000/swaps | Page loads, no errors | [ ] |
| 2.1.2 | Query /health | `curl http://localhost:8000/health` | 200 OK, connection count stable | [ ] |
| 2.1.3 | Rapid queries | `for i in {1..10}; do curl http://localhost:8000/health; done` | All succeed, connection count returns to baseline | [ ] |

**Pass Criteria:** Connection count returns to baseline after each request

### 2.2 Exception Path (Simulated)

```python
# In Python REPL:
from swaps_query import SwapsQuery
q = SwapsQuery("/path/to/swaps.db")

# This should raise an exception but NOT leak connection
try:
    q.query_by_upi("INVALID", days_back=-1)  # Invalid days_back
except Exception as e:
    print(f"Exception caught: {e}")
    # Verify connection is closed

# Check: No lingering connections
import os
import sqlite3
conn = sqlite3.connect("/path/to/swaps.db")
cur = conn.cursor()
cur.execute("PRAGMA database_list;")
print(cur.fetchall())
conn.close()
```

**Pass Criteria:** Exception path closes connections properly

---

## Test Suite 3: Upsert Race Condition & State Atomicity

### 3.1 Upsert Atomicity

**Setup:**
```bash
# Terminal 1: Run backfill
python backfill.py

# Terminal 2: Simultaneously query swap_trades
watch 'sqlite3 swaps.db "SELECT COUNT(*) FROM swap_trades;"'

# Terminal 3: Monitor for "database is locked" errors
tail -f backfill.log | grep -i "database is locked"
```

**Test Cases:**

| # | Test | Action | Expected | Status |
|---|------|--------|----------|--------|
| 3.1.1 | Backfill + query | Run backfill while querying | No "database is locked" errors | [ ] |
| 3.1.2 | Duplicate check | Backfill twice same date | Same record count (no duplicates) | [ ] |
| 3.1.3 | Transaction rollback | Simulate crash mid-upsert (Ctrl+C) | Resume handles correctly | [ ] |

**Pass Criteria:** No race conditions, atomicity verified

### 3.2 State Resumption

```bash
# Terminal 1: Start backfill
python backfill.py

# Wait ~30 seconds, then Ctrl+C to interrupt

# Terminal 2: Check last_cumulative_date
sqlite3 swaps.db "SELECT last_cumulative_date, last_live_slice_id FROM ingestion_state LIMIT 1;"

# Terminal 3: Restart backfill
python backfill.py

# Verify: Should resume from last_cumulative_date (no re-processing)
```

**Pass Criteria:** Resume skips already-processed dates

---

## Test Suite 4: WAL Mode

### 4.1 File Creation

```bash
# After first write to database:
ls -la swaps.db*

# Should see:
# swaps.db (main file)
# swaps.db-wal (write-ahead log, ~100KB)
# swaps.db-shm (shared memory, ~32KB)
```

**Test Cases:**

| # | Test | Check | Expected | Status |
|---|------|-------|----------|--------|
| 4.1.1 | WAL files present | `ls -la swaps.db*` | .db-wal and .db-shm exist | [ ] |
| 4.1.2 | WAL file size | `ls -lh swaps.db-wal` | < 100MB (reasonable) | [ ] |

**Pass Criteria:** WAL files created and appropriately sized

### 4.2 Concurrent Read/Write

```bash
# Terminal 1: Continuous backfill (1000+ records)
python backfill.py

# Terminal 2: Continuous reads during backfill (in loop)
while true; do
  sqlite3 swaps.db "SELECT COUNT(*) FROM swap_trades;"
  sqlite3 swaps.db "SELECT * FROM orchestrator_runs LIMIT 5;"
  sleep 1
done

# Terminal 3: Monitor for locking errors
tail -f backfill.log | grep -i "locked"
```

**Test Cases:**

| # | Test | Duration | Expected | Status |
|---|------|----------|----------|--------|
| 4.2.1 | Concurrent I/O | 60 seconds | No "database is locked" | [ ] |
| 4.2.2 | Query responsiveness | Measure query latency | < 100ms (no blocking) | [ ] |
| 4.2.3 | Backfill throughput | Measure rows/sec | No degradation vs. before | [ ] |

**Pass Criteria:** Concurrent operations don't block each other

---

## Test Suite 5: Integration Test (Full Pipeline)

### 5.1 End-to-End Scenario

```bash
# 1. Start scheduler in background
nohup python run_scheduler.bat > scheduler.log 2>&1 &

# 2. Start dashboard
python -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8000

# 3. Trigger orchestrator run via dashboard (in new terminal)
curl -X POST http://localhost:8000/run/unified \
  -H "Authorization: Bearer dev-key-change-in-production" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "target_years": 0.25}'

# 4. Monitor dashboard and logs for errors
```

**Expected Sequence:**
1. Run queued and assigned run_id
2. Sentiment-scanner runs (or skipped)
3. Vol_Suite runs
4. Options_Suite and VaR_Tools run in parallel
5. All suites complete without "database is locked" errors
6. Results logged to orchestrator_runs table

**Pass Criteria:** Full orchestrator run succeeds without locking issues

---

## Test Suite 6: Stress Test (Optional but Recommended)

### 6.1 High Concurrency

```bash
# Simulate 10 concurrent orchestrator triggers
for i in {1..10}; do
  curl -X POST http://localhost:8000/run/unified \
    -H "Authorization: Bearer dev-key-change-in-production" \
    -H "Content-Type: application/json" \
    -d '{"ticker": "NVDA", "target_years": 0.25}' &
done

# Monitor database for locking errors
tail -f scheduler.log | grep -i "locked"
```

**Expected:** At most 1 run succeeds (due to 1/60s rate limit), others get 429

**Pass Criteria:** Rate limiting works; no database corruption

---

## Summary Checklist

### Critical (All Must Pass)
- [ ] Test Suite 1: Dashboard Authentication (5 tests)
- [ ] Test Suite 2: Database Connections (3 tests)
- [ ] Test Suite 3: Upsert Atomicity (3 tests)
- [ ] Test Suite 5: Integration Test (1 test)

### Important (Should Pass)
- [ ] Test Suite 4: WAL Mode (4 tests)
- [ ] Test Suite 6: Stress Test (1 test)

### Overall Status
- [ ] All critical tests pass
- [ ] No "database is locked" errors observed
- [ ] No duplicate records found
- [ ] API authentication working
- [ ] Rate limiting working
- [ ] WAL files created and stable

---

## Rollback Instructions

If any test fails, rollback is safe:

```bash
# 1. Stop all running processes
pkill -f orchestrator
pkill -f dashboard
pkill -f scheduler

# 2. Restore from backup
cp swaps.db.backup swaps.db
rm swaps.db-wal swaps.db-shm 2>/dev/null

# 3. Revert code changes (git)
git revert <Phase1-commit-hash>

# 4. Restart
python setup_db.py
```

---

## Success Criteria

**Phase 1 is verified when:**
✅ All critical tests pass  
✅ No "database is locked" errors  
✅ Dashboard authentication required  
✅ Concurrent operations succeed  
✅ Backfill is idempotent (no duplicates)  
✅ WAL mode enabled  

**Status:** Ready for Phase 2 (Architectural Robustness)

