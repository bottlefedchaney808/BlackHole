# Fix dashboard.bat Startup Crash (Missing slowapi Dependency) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dashboard.bat` start the FastAPI dashboard successfully on `127.0.0.1:8787` by fixing the missing `slowapi` dependency in the shared venv, and make future dependency drift fail with a clear, actionable message instead of a raw traceback.

**Architecture:** No application code changes are required — `dashboard/app.py` already correctly declares and uses `slowapi` (rate limiting), and `slowapi==0.1.9` is already pinned in `requirements.txt`. The venv at `.venv/` simply doesn't have it installed (confirmed: `.venv/Lib/site-packages` has no `slowapi*` dist-info, while `fastapi`, `uvicorn`, etc. are all present). Fix = reinstall the venv from `requirements.txt`, then add a pre-flight dependency check to `dashboard.bat` so a stale venv fails fast with a fix-it message instead of a Python traceback.

**Tech Stack:** Python 3.12 (Windows `.venv`), pip, FastAPI/uvicorn, Windows batch script.

## Global Constraints

- Do not change `dashboard/app.py`, `dashboard/auth.py`, or any other application code — the import (`from slowapi import ...`) is correct and required (rate limiting, `dashboard/app.py:45-47,80-82`).
- `requirements.txt` already pins `slowapi==0.1.9` (`requirements.txt:71`) — do not add a second/duplicate pin.
- All commands below are Windows `cmd.exe` commands run from the repo root (`C:\Users\bottl\FinancialDevelopment`), since `.venv` is a Windows venv (`.venv\Scripts\python.exe`, not `.venv/bin/python`).
- Do not delete or recreate `.venv` from scratch — reinstalling into the existing venv is sufficient and avoids re-downloading ~30 packages unnecessarily.
- Keep `dashboard.bat`'s existing behavior (checks for `.venv`, refuses to double-start on port 8787, opens the browser tab) — only add a dependency check, don't restructure the script.

---

### Task 1: Install the missing `slowapi` dependency and confirm the dashboard boots

**Files:**
- Modify: none (environment-only fix)
- Verify against: `C:\Users\bottl\FinancialDevelopment\requirements.txt:71`, `C:\Users\bottl\FinancialDevelopment\dashboard\app.py:45-47`

**Interfaces:**
- Consumes: existing `.venv\Scripts\python.exe` and `.venv\Scripts\pip.exe` (already present per `dashboard.bat:8`'s existence check).
- Produces: a venv where `pip show slowapi` succeeds and `dashboard.bat` binds to `127.0.0.1:8787` without raising `ModuleNotFoundError`.

- [ ] **Step 1: Confirm the diagnosis — check what's actually missing**

Open a Windows terminal (Command Prompt or PowerShell) in the repo root and run:

```bat
cd /d C:\Users\bottl\FinancialDevelopment
.venv\Scripts\python.exe -m pip show slowapi
```

Expected: `WARNING: Package(s) not found: slowapi` (this confirms the package is absent from the venv even though it's pinned in `requirements.txt`).

- [ ] **Step 2: Reinstall the full pinned dependency set into the existing venv**

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: pip resolves and installs `slowapi==0.1.9` (plus its transitive dependency `limits`) along with any other already-installed packages it re-verifies; existing packages that already satisfy their pin are left untouched (fast). Watch for the line `Successfully installed slowapi-0.1.9 limits-...` in the output.

- [ ] **Step 3: Confirm the install took**

```bat
.venv\Scripts\python.exe -m pip show slowapi
```

Expected: prints `Name: slowapi`, `Version: 0.1.9`, `Location: ...\.venv\Lib\site-packages` (no more warning).

- [ ] **Step 4: Start the dashboard and confirm it binds**

```bat
dashboard.bat
```

Expected: no traceback; the console shows uvicorn's startup log ending in a line like:

```
INFO:     Uvicorn running on http://127.0.0.1:8787 (Press CTRL+C to quit)
```

and the browser tab that `dashboard.bat` opens after a 2-second delay (`dashboard.bat:22`) loads the dashboard UI instead of a "site can't be reached" error.

- [ ] **Step 5: Verify the port is actually listening (in case the browser cache masks a failure)**

In a second terminal, while `dashboard.bat` is still running in the first:

```bat
netstat -ano | findstr /C:"127.0.0.1:8787" | findstr "LISTENING"
```

Expected: one line showing `TCP    127.0.0.1:8787   ...   LISTENING   <pid>`.

- [ ] **Step 6: Stop the dashboard cleanly**

In the terminal running `dashboard.bat`, press `CTRL+C` once and confirm the process exits (no orphaned `python.exe` still holding port 8787 — re-run the `netstat` check from Step 5 and confirm it now returns nothing).

- [ ] **Step 7: Commit** (only if `requirements.txt` changed — this task is expected to be a pure environment fix with no diff, since `slowapi==0.1.9` was already pinned; skip the commit if `requirements.txt` itself is clean. Scope the check to that one file rather than a bare `git status`, since unrelated WIP files elsewhere in the tree are common in this repo and would otherwise be misread as a signal about this task.)

```bash
git status --short -- requirements.txt
```

If empty, no commit needed for this task — the fix lived entirely in the untracked `.venv/` directory. If it shows a change to `requirements.txt`, then:

```bash
git add requirements.txt
git commit -m "fix(dashboard): re-pin slowapi dependency"
```

---

### Task 2: Make dashboard.bat fail fast with a clear message on future dependency drift

**Why:** Today, a stale venv produces a raw Python traceback ending in a generic "port already in use or access forbidden" hint that doesn't mention missing packages at all — actively misleading for this exact failure mode. Add a pre-flight check so the next time a required package is missing, the user sees one clear line instead of a traceback + wrong troubleshooting advice.

**Files:**
- Modify: `C:\Users\bottl\FinancialDevelopment\dashboard.bat:1-30` (full current contents already read; insert the check between the existing `.venv` existence check and the "already running" check)

**Interfaces:**
- Consumes: `.venv\Scripts\python.exe` (already validated to exist earlier in the script).
- Produces: an early `exit /b 1` with a fix-it message if any package importable by `dashboard/app.py` (currently just the one bug class: `fastapi`, `uvicorn`, `jinja2`, `slowapi`) is missing, before uvicorn ever runs.

- [ ] **Step 1: Write the check — add it to `dashboard.bat` right after the existing venv-exists check**

Current `dashboard.bat` (full contents, for reference):

```bat
@echo off
REM Launches the live local dashboard (swap data + orchestrator control panel)
REM and opens it in your default browser. Binds to localhost only.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Refuse to start a second dashboard on the same port -- besides just
REM failing to bind, a duplicate process also doubles up writes against
REM swaps.db and can cause "database is locked" errors for the scheduler.
netstat -ano | findstr /C:"127.0.0.1:8787" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo A dashboard already appears to be running on port 8787.
    echo Only run ONE instance of dashboard.bat at a time.
    echo Opening your existing dashboard in the browser instead...
    start "" http://127.0.0.1:8787
    pause
    exit /b 0
)
REM Give uvicorn a couple seconds to bind before opening the browser tab,
REM so it doesn't load before anything is listening.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8787"
cd dashboard
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8787
if %ERRORLEVEL% neq 0 (
    echo.
    echo Dashboard exited with an error ^(see above^). If it says the port is
    echo already in use or "access forbidden", another process or an unrelated
    echo Windows service already owns port 8787 — edit dashboard.bat and pick
    echo a different --port number ^(and update the URL above it^).
    pause
)
```

New version — insert a dependency check block right after the `.venv` existence check (i.e. after the closing `)` on the line following `pause` / `exit /b 1` for that block, before the `REM Refuse to start a second dashboard...` comment):

```bat
@echo off
REM Launches the live local dashboard (swap data + orchestrator control panel)
REM and opens it in your default browser. Binds to localhost only.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Catch a stale/incomplete venv (packages missing vs. requirements.txt)
REM *before* uvicorn starts, so the failure is one clear line instead of a
REM Python traceback plus misleading "port already in use" advice.
.venv\Scripts\python.exe -c "import fastapi, uvicorn, jinja2, slowapi" 2>nul
if errorlevel 1 (
    echo Your .venv is missing one or more required packages
    echo ^(fastapi / uvicorn / jinja2 / slowapi^). This usually means
    echo requirements.txt was updated after your venv was created.
    echo.
    echo Fix: .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Refuse to start a second dashboard on the same port -- besides just
REM failing to bind, a duplicate process also doubles up writes against
REM swaps.db and can cause "database is locked" errors for the scheduler.
netstat -ano | findstr /C:"127.0.0.1:8787" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo A dashboard already appears to be running on port 8787.
    echo Only run ONE instance of dashboard.bat at a time.
    echo Opening your existing dashboard in the browser instead...
    start "" http://127.0.0.1:8787
    pause
    exit /b 0
)
REM Give uvicorn a couple seconds to bind before opening the browser tab,
REM so it doesn't load before anything is listening.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8787"
cd dashboard
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8787
if %ERRORLEVEL% neq 0 (
    echo.
    echo Dashboard exited with an error ^(see above^). If it says the port is
    echo already in use or "access forbidden", another process or an unrelated
    echo Windows service already owns port 8787 — edit dashboard.bat and pick
    echo a different --port number ^(and update the URL above it^).
    pause
)
```

- [ ] **Step 2: Apply the edit**

Open `dashboard.bat` in a text editor and insert the new `REM Catch a stale/incomplete venv...` block (the 12 lines from `REM Catch a stale/incomplete venv` through the matching `exit /b 1` / `)`) immediately after the existing `.venv\Scripts\python.exe` existence-check block, and before the `REM Refuse to start a second dashboard...` block. Save.

- [ ] **Step 3: Verify the check fires correctly on a broken venv**

Temporarily simulate the original bug to prove the new message appears instead of a traceback:

```bat
.venv\Scripts\python.exe -m pip uninstall -y slowapi
dashboard.bat
```

Expected output (no traceback):

```
Your .venv is missing one or more required packages
(fastapi / uvicorn / jinja2 / slowapi). This usually means
requirements.txt was updated after your venv was created.

Fix: .venv\Scripts\python.exe -m pip install -r requirements.txt

Press any key to continue . . .
```

- [ ] **Step 4: Restore the dependency and verify normal startup still works**

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
dashboard.bat
```

Expected: same successful startup as Task 1 Step 4 (uvicorn binds to `127.0.0.1:8787`, browser tab loads). Stop with `CTRL+C` when confirmed.

- [ ] **Step 5: Verify the "already running" and "missing venv" branches still work (regression check)**

With the dashboard running from Step 4 in one terminal, open a second terminal and run `dashboard.bat` again — expected: "A dashboard already appears to be running on port 8787." message and it opens the existing tab (unchanged behavior, confirming the new check didn't break this branch since a healthy venv passes the import check and falls through to it).

- [ ] **Step 6: Commit**

```bash
git add dashboard.bat
git commit -m "fix(dashboard): fail fast with clear message when venv is missing required packages"
```

---

## Self-Review Notes

- **Spec coverage:** The reported symptom (`ModuleNotFoundError: No module named 'slowapi'` and nothing listening on 8787) is fully addressed by Task 1 — the crash prevented uvicorn from ever binding, which is why nothing loaded in the browser; no separate networking/binding bug exists. Task 2 addresses recurrence/diagnosability, which isn't strictly required to fix today's crash but directly follows from the fact that the existing error message actively misdirects the user toward a port conflict that isn't the real cause.
- **Not in scope:** The "artifact not visible in `C:\Users\bottl\Claude\Artifacts`" issue is unrelated to this codebase (no code in this repo references that path) — it's a question about the Claude desktop app's Artifacts feature vs. this Cowork session's workspace folder, and is addressed separately outside this plan, not as an implementation task.
