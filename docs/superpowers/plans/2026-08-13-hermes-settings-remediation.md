# Hermes Local-Settings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the non-blocking misconfigurations the 2026-08-13 settings audit found, harden the gateway-URL scheme bug class, and activate the already-applied web-search fix on the next session.

**Architecture:** This is a settings/credential remediation plan, not a code feature. It edits three files in `C:\Users\bottl\AppData\Local\hermes` (`.env`, `config.yaml`) plus verification of two cron jobs and a session reset. Every edit is a targeted, reversible change with a `.bak` backup first. Two items are decision-gated by Jason (Task 1 and Task 2) and must be resolved before those tasks execute.

**Tech Stack:** Hermes local config (`.env` env vars, `config.yaml`), the `hermes` CLI (`hermes config get/set`), Windows-native paths. The repo venv python is at `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe`; the Hermes agent venv python is at `C:\Users\bottl\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`.

## Global Constraints

- **Redact all secrets** (API keys, tokens, passwords) in every command and output — never print token values, only length/prefix/expiry.
- **Backup before every edit**: `cp .env .env.bak_YYYYMMDD_topic` / same for `config.yaml` before mutating.
- **Never run `hermes gateway restart` mid-session from inside the gateway process** — apply config now, it takes effect on the next session (Jason resets the session after the test results are in).
- **`.env` is for secrets; `config.yaml` is for settings.** Gateway URL vars live in `.env`. `web.use_gateway: true` in `config.yaml` is correct and must NOT change.
- The active provider is `nous` (OAuth). `fallback_model` in `config.yaml` is commented out. Do not introduce OpenRouter as a route unless Jason explicitly asks.
- Use the `hermes-agent` skill for the exact documented `hermes config` / `hermes status` / `hermes mcp test` command syntax — do not invent flags.

---

### Task 1: Resolve the OpenRouter exhausted-key decision (DECISION-GATED — needs Jason)

**Files:**
- Decision, no edit yet: `.env` (line ~497 `OPENROUTER_API_KEY=...`), `auth.json` credential_pool.openrouter[0] (last_status=exhausted, 402)

**Interfaces:**
- Produces: a decision (KEEP_TOPUP vs DELETE) that Task 3 and Task 6 branch on.

**Context:** The audit found the OpenRouter key is out of credits (`402 requires more credits`). It is non-blocking today because `nous` is primary and `fallback_model` is commented out. But it is a live landmine: any future config that selects OpenRouter as a fallback will silently 402.

- [ ] **Step 1: Ask Jason which option he wants**

Present exactly two choices (recommend the first):
1. **DELETE the exhausted OpenRouter key** (recommended) — remove `OPENROUTER_API_KEY` from `.env` so it can never be selected as a fallback. Clean, no ongoing billing. If he later wants OpenRouter, he re-adds a funded key.
2. **Top-up OpenRouter credits** — he tops up at openrouter.ai, keep the key. Keeps OpenRouter available as a future fallback.

- [ ] **Step 2: Record the decision**

Note the choice in the plan execution log / a short line at the top of `trading_journal/hermes_settings_audit_20260813.md` so it isn't relitigated.

**Verification:** Jason has made an explicit choice; the decision is recorded.

---

### Task 2: Resolve the TradingView CDP-port decision (DECISION-GATED — needs Jason)

**Files:**
- Decision, no edit yet: `config.yaml` → `mcp_servers.tradingview` env `TV_CDP_PORT` (currently `19222`)

**Interfaces:**
- Produces: a decision (9222 vs 19222) that Task 4 branches on.

**Context:** `19222` was the WSL bridge port (`tvc_proxy.py` forwards `19222 → 9222`). On Windows-native, TradingView launches directly on `9222` (`launch_windows_tv_cdp.ps1` default; `connection.js` default) with no proxy. The audit could not verify which Jason uses without TradingView running.

- [ ] **Step 1: Ask Jason which TradingView setup he runs**

1. **Windows-native, no proxy → use `9222`** (recommended for the post-WSL migration)
2. **Still run the `tvc_proxy` bridge → keep `19222`**

- [ ] **Step 2: Record the decision**

Note it alongside the Task 1 decision.

**Verification:** Jason has chosen a port; the decision is recorded.

---

### Task 3: Remove/disable the exhausted OpenRouter key (if Task 1 = DELETE)

**Files:**
- Modify: `C:\Users\bottl\AppData\Local\hermes\.env` (line ~497 `OPENROUTER_API_KEY`)
- Test (manual): confirm no live route references openrouter

**Interfaces:**
- Consumes: Task 1 decision (DELETE)
- Produces: a `.env` with no exhausted key; a verification that nothing routes via OpenRouter

- [ ] **Step 1: Backup `.env`**

```bash
cd /c/Users/bottl/AppData/Local/hermes && cp .env .env.bak_20260813_openrouter
```

- [ ] **Step 2: Comment out the exhausted key (keep it recoverable, don't hard-delete)**

Change line ~497 from:
```
OPENROUTER_API_KEY=sk-or-...c11c
```
to:
```
# OPENROUTER_API_KEY=sk-or-...c11c   # EXHAUSTED (402, 2026-08-13). Commented out so it can't be a silent fallback.
# Add a funded key or delete this line to fully remove OpenRouter.
```

> Keep the token value intact (commented) so it is recoverable; the redaction in this plan is only for the report, not the file.

- [ ] **Step 3: Confirm no active route selects OpenRouter**

```bash
cd /c/Users/bottl/AppData/Local/hermes && grep -niE "openrouter" config.yaml
```

Expected: only the commented-out `fallback_model` reference, no active `provider: openrouter` / `model.provider: openrouter`.

- [ ] **Step 4: Verify the primary provider still resolves**

```bash
cd /c/Users/bottl/AppData/Local/hermes && hermes config get model.provider
```

Expected: `nous`

- [ ] **Step 5: Commit / log the change**

Append one line to the audit report: `OPENROUTER_API_KEY commented out (exhausted) — <date>`.

**Verification:** grep shows no active openrouter route; `hermes config get model.provider` returns `nous`; `.env.bak_20260813_openrouter` exists.

---

### Task 4: Set the TradingView CDP port (per Task 2 decision)

**Files:**
- Modify: `C:\Users\bottl\AppData\Local\hermes\config.yaml` → `mcp_servers.tradingview.env.TV_CDP_PORT`

**Interfaces:**
- Consumes: Task 2 decision (9222 vs 19222)
- Produces: correct CDP port for the Windows-native setup

- [ ] **Step 1: Backup `config.yaml`**

```bash
cd /c/Users/bottl/AppData/Local/hermes && cp config.yaml config.yaml.bak_20260813_tvport
```

- [ ] **Step 2: Set the port via the documented `hermes config set` syntax**

If decision = `9222`:
```bash
cd /c/Users/bottl/AppData/Local/hermes && hermes config set mcp_servers.tradingview.env.TV_CDP_PORT 9222
```

If decision = keep `19222`: no change; record that the bridge proxy is still in use.

> NOTE (from memory): `hermes config set mcp_servers.X.args '[...]'` stores a STRING for list args. `TV_CDP_PORT` is a scalar string/int, so a plain `hermes config set` is correct. Verify the write landed with Step 3.

- [ ] **Step 3: Verify the write**

```bash
cd /c/Users/bottl/AppData/Local/hermes && hermes config get mcp_servers.tradingview.env.TV_CDP_PORT
```

Expected: `9222` (or `19222` if kept)

- [ ] **Step 4: Confirm the tradingview server path is still correct**

```bash
ls "C:\Users\bottl\FinancialDevelopment\tradingview-mcp\src\server.js"
```

Expected: file exists (relocation already verified by audit; re-confirm after edit).

- [ ] **Step 5: Log the change**

Append to the audit report: `TV_CDP_PORT set to <port> — <date>`.

**Verification:** `hermes config get` returns the chosen port; `server.js` exists; `config.yaml.bak_20260813_tvport` exists. (Actual TV MCP connectivity needs a live TradingView launch — deferred.)

---

### Task 5: Verify the `powerhour-prep` cron Windows wrapper end-to-end

**Files:**
- Verify: `C:\Users\bottl\AppData\Local\hermes\cron\jobs.json` job `0866dbb60d0e` (powerhour-prep)
- Verify: `C:\Users\bottl\AppData\Local\hermes\scripts\powerhour_prep_cron.py`

**Interfaces:**
- Consumes: nothing
- Produces: confirmation that the Windows-native `.py` wrapper runs clean (or a caught failure)

**Context:** The audit flagged that `powerhour-prep` had a WSL failure on 08-12 (`Windows Subsystem for Linux has no installed distributions`) and now points at the `.py` wrapper — but hasn't run successfully since. It runs at 13:00 CDT today. Rather than wait for the cron, run it manually once.

- [ ] **Step 1: Confirm the job points at the `.py` wrapper**

```bash
cd /c/Users/bottl/AppData/Local/hermes && python -c "import json; d=json.load(open('cron/jobs.json')); jobs={j.get('id') or j.get('job_id'): j for j in (d if isinstance(d,list) else d.get('jobs',[]))}; j=jobs.get('0866dbb60d0e'); print('script=', j.get('script'), '| no_agent=', j.get('no_agent'))"
```

Expected: `script= powerhour_prep_cron.py` and `no_agent= True` (or the correct key names the store uses — inspect the JSON shape first).

- [ ] **Step 2: Run the wrapper manually once (foreground, generous timeout)**

```bash
cd /c/Users/bottl/AppData/Local/hermes && env -u PYTHONPATH -u PYTHONHOME ./hermes-agent/venv/Scripts/python scripts/powerhour_prep_cron.py
```

> This is the same pattern the other Windows cron wrappers use. If it needs the project venv python, run it with `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe` and `PYTHONPATH="Options_Suite;Vol_Suite;VaR_Tools_Simulations;."` per the memory of the Windows-native cron gotcha (forward-slash paths, clear PYTHONPATH/PYTHONHOME).

- [ ] **Step 3: Interpret the result**

- Exit 0 + expected output → FIXED-CONFIRMED; append to audit report.
- Error → diagnose (likely path/venv), fix, re-run. Do not mark the cron verified until a manual run succeeds.

- [ ] **Step 4: Update the audit report status**

Change `§2.6` from `FIXED-in-config / UNVERIFIED` to `FIXED-CONFIRMED <date>` (or leave UNVERIFIED + log the new error if it failed).

**Verification:** a manual run of `powerhour_prep_cron.py` exits 0 with expected output; audit report status updated.

---

### Task 6: Session reset to activate the web-search fix + confirm gateway health

**Files:**
- No edit. Action: Jason resets this Hermes session after the test results are in.

**Interfaces:**
- Consumes: the already-applied `.env` fixes (FIRECRAWL scheme, TOOL_GATEWAY_DOMAIN bare suffix) + Task 3/4 edits
- Produces: a fresh session with working web_search and the stale WSL cwd cleared

**Context:** `.env` is read at session startup. The FIRECRAWL fix (applied + verified earlier) and any Task 3/4 edits only take effect on a NEW session. The stale WSL cwd (`/home/bottl/Financial_Development`) is session-scoped and clears on a fresh session.

- [ ] **Step 1: Confirm the gateway is healthy before the reset**

```bash
cd /c/Users/bottl/AppData/Local/hermes && hermes gateway status
```

Expected: gateway up; Photon sidecar listening on `127.0.0.1:8789`.

- [ ] **Step 2: (Jason) reset the session**

Jason starts a fresh Hermes session (do not resume the stale `20260813_025029` session).

- [ ] **Step 3: Verify web_search works on the fresh session**

Run one real `web_search` in the new session. Expected: results returned, no `'NoneType' object has no attribute 'status_code'` and no `No scheme supplied` in `logs/errors.log`.

- [ ] **Step 4: Confirm the watchdog is live**

```bash
cd /c/Users/bottl/AppData/Local/hermes && ./hermes-agent/venv/Scripts/python scripts/web_search_watchdog_cron.py; echo "exit=$?"
```

Expected: `exit=0` (silent = healthy).

**Verification:** fresh-session `web_search` returns results; watchdog exits 0; `logs/errors.log` has no new scheme errors; gateway healthy.

---

## Self-Review

**1. Spec coverage (the audit findings):**
- OpenRouter 402 → Task 1 (decision) + Task 3 (remove)
- TV_CDP_PORT stale → Task 2 (decision) + Task 4 (set)
- powerhour-prep cron unverified → Task 5 (verify)
- stale WSL cwd → Task 6 (session reset) ✓
- web-search fix activation → Task 6 ✓
- scheme discipline → already applied by the audit (comments in `.env`, verified); no plan task needed — documented in Global Constraints ✓
- proposed hygiene (redundant `sk-` key, `REDDIT_PASSWORD` plaintext) → intentionally NOT in this plan (non-actionable, harmless, Jason didn't ask to change them). Called out in the handoff note.

**2. Placeholder scan:** No TBD/TODO; every task has exact paths, exact commands, expected output. Decisions gated by Jason are explicit choice lists, not placeholders.

**3. Type/name consistency:** `TV_CDP_PORT`, `powerhour_prep_cron.py`, `0866dbb60d0e`, `web_search_watchdog_cron.py` used consistently. Note: Task 5 Step 1 reads the cron store with a best-effort JSON shape and instructs the implementer to inspect the actual shape first (honest about the store's key names).

**Gap noted:** `tests/test_identifiers.py` CME/OTC adapter failures (pre-existing, unrelated to this plan) are deliberately out of scope — they are not local-settings issues.

---

## Handoff note (not a task — context for the executor)

- **Non-actionable audit items (left alone):** redundant static `sk-…` key in `config.yaml` alongside OAuth (harmless, OAuth wins) and `REDDIT_PASSWORD` plaintext in `config.yaml` (hygiene smell, no functional impact). Jason didn't ask to change these; doing so risks breaking the reddit MCP for zero benefit. Flag if he wants them cleaned.
- **Pre-existing, out of scope:** the 14 `test_identifiers.py` CME/OTC adapter constructor-mismatch failures. They are unrelated to local settings.
- **Reminder to the executor:** the two DECISION-GATED tasks (1, 2) must resolve with Jason BEFORE Tasks 3 and 4 run. Do not guess his choice.
