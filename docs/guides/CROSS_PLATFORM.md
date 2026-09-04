# Cross-platform launchers

This repo ships two parallel sets of launcher scripts that do the same
thing: `.bat` for Windows, `.sh` for Linux/Mac. Use whichever matches your
OS -- both call the same underlying Python entrypoints (`orchestrator.py`,
`scheduled_ingest.py`, `dashboard/app.py`) with the same arguments. (The
old cross-suite `orchestrator.bat`/`orchestrator.sh` subprocess launchers were
removed 2026-09-04 -- modules now run in-process; `dashboard.bat`/`.sh` is the
primary way to run registered modules as widgets.)

| Task | Windows | Linux/Mac |
| --- | --- | --- |
| Run registered modules as widgets | `dashboard.bat` + browse `:8787` (or `curl` `POST /api/widgets/{slug}/run`) | `dashboard.sh` |
| Run modules in-process (scripted) | `.venv\Scripts\python.exe` + `shared.module_execution.run_selected_modules` | `.venv/bin/python` + same |
| Start the DTCC live poller | `run_scheduler.bat` | `run_scheduler.sh` |
| Start the local dashboard | `dashboard.bat` | `dashboard.sh` |

## Linux/Mac: using the `.sh` scripts

One-time setup, from the repo root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x run_scheduler.sh dashboard.sh
```

Then:

```bash
# Start the dashboard and open it in your browser (run modules from there)
bash dashboard.sh

# Or run registered modules in-process, without the web UI:
.venv/bin/python -c "import shared.module_execution as me; print(me.run_selected_modules(['dealer_exposure'], {'ticker':'AAPL'})['status'])"

# Start the DTCC live poller (leave the terminal open; Ctrl+C to stop)
bash run_scheduler.sh
```

Or, once `chmod +x` has been run, invoke them directly: `./dashboard.sh ...`.

Each `.sh` script:

- `cd`s to its own directory first (via `${BASH_SOURCE[0]}`), so it works
  no matter what directory you invoke it from.
- Clears `PYTHONPATH`/`PYTHONHOME` before launching, exactly like the `.bat`
  scripts, so a stray environment variable from another Python install can't
  leak in.
- Auto-detects the venv interpreter (`.venv/bin/python3`, falling back to
  `.venv/bin/python`) instead of hardcoding a path, and exits with a clear
  "run `python3 -m venv .venv ...`" message if neither exists.
- Preserves the safety checks from the `.bat` originals: `run_scheduler.sh`
  takes the same atomic `mkdir .scheduler.lock` single-instance lock (with a
  `trap ... EXIT` so the lock is released even on Ctrl+C or an error, which
  is slightly more robust than the batch version); `dashboard.sh` refuses to
  start a second instance on port 8787, probing the port with Bash's
  `/dev/tcp` pseudo-device so it doesn't depend on `netstat`/`lsof`/`nc`
  being installed.
- Opens the dashboard URL automatically via `open` (Mac) or `xdg-open`
  (Linux), whichever is present; if neither is found it just prints the URL.
- Propagates the child Python process's exit code, so `$?` / CI checks see
  real failures instead of always exiting 0.

## Windows: using the `.bat` scripts

No setup beyond the standard `python -m venv .venv && .venv\Scripts\python.exe
-m pip install -r requirements.txt`. Then, from a `cmd.exe` prompt at the repo
root:

```bat
dashboard.bat        :: start the dashboard, run modules as widgets from :8787
run_scheduler.bat
```

Modules run in-process (no child interpreter needed), so the Windows example
for a scripted module run is:

```bat
.venv\Scripts\python.exe -c "import shared.module_execution as me; print(me.run_selected_modules(['chain_scanner'], {'ticker':'SPY'})['status'])"
```

## Path resolution: how it works on both platforms

**Widget-native run path (no child interpreter).** Since Phase 7 (2026-09-04)
registered modules run in-process, the run path no longer shells out to a child
Python interpreter — so platform venv-path differences no longer affect *running
a module*. You just invoke your own `.venv` interpreter
(`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/Mac) and it
imports `shared/module_execution.py` / the suites directly.

The remaining platform-specific interpreter resolution is in
`sentiment-scanner/main.py` (`_find_vol_suite_python()`), which still resolves
the *separate* Vol_Suite-local venv the same way: Vol_Suite has its own
dependencies (`arch`, `statsmodels`, ...) that aren't necessarily installed in
sentiment-scanner's own venv. That resolver uses `Path.exists()` checks rather
than branching purely on `os.name`, so a venv created under an unusual
environment (e.g. Git Bash or WSL on a Windows machine) still resolves to the
interpreter that's actually present on disk.

`orchestrator.py` still computes `ROOT` itself with
`os.path.dirname(os.path.abspath(__file__))` and uses it with `os.path.join`
throughout -- that was already platform-agnostic (both functions handle `/` and
`\` correctly on their respective OSes).

## `.gitattributes`

`*.sh` files are pinned to `eol=lf` and `*.bat` files to `eol=crlf` in
`.gitattributes`, regardless of a contributor's `core.autocrlf` setting. This
matters specifically for the `.sh` scripts: a CRLF-terminated shebang line
(`#!/usr/bin/env bash\r`) fails on Linux/Mac with a cryptic
"`bad interpreter`" error, so the repo enforces LF for those files at
checkout time.
