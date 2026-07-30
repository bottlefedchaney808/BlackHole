# Cross-platform launchers

This repo ships two parallel sets of launcher scripts that do the same
thing: `.bat` for Windows, `.sh` for Linux/Mac. Use whichever matches your
OS -- both call the same underlying Python entrypoints (`orchestrator.py`,
`scheduled_ingest.py`, `dashboard/app.py`) with the same arguments.

| Task | Windows | Linux/Mac |
| --- | --- | --- |
| Run a suite / unified pipeline | `orchestrator.bat` | `orchestrator.sh` |
| Start the DTCC live poller | `run_scheduler.bat` | `run_scheduler.sh` |
| Start the local dashboard | `dashboard.bat` | `dashboard.sh` |

## Linux/Mac: using the `.sh` scripts

One-time setup, from the repo root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
chmod +x orchestrator.sh run_scheduler.sh dashboard.sh
```

Then:

```bash
# Run the orchestrator (unified pipeline or a single suite)
bash orchestrator.sh --unified --ticker NVDA --target-years 0.25
bash orchestrator.sh --suite options --ticker AAPL --target-years 0.25

# Start the DTCC live poller (leave the terminal open; Ctrl+C to stop)
bash run_scheduler.sh

# Start the dashboard and open it in your browser
bash dashboard.sh
```

Or, once `chmod +x` has been run, invoke them directly: `./orchestrator.sh ...`.

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
orchestrator.bat --unified --ticker NVDA --target-years 0.25
orchestrator.bat --suite options --ticker AAPL --target-years 0.25
run_scheduler.bat
dashboard.bat
```

## Path resolution: how it works on both platforms

Two Python entrypoints used to hardcode a Windows-only venv path
(`.venv\Scripts\python.exe`). Both now resolve the interpreter with
`pathlib.Path`, checking the real filesystem instead of assuming a layout:

- **`orchestrator.py`** (`_find_shared_python()`, near the top of the file)
  resolves `SHARED_PYTHON` -- the interpreter every child suite
  (`Options_Suite`, `Vol_Suite`, `VaR_Tools_Simulations`, `sentiment-scanner`)
  is launched with, instead of `sys.executable`. It checks, in order:
  1. `<repo_root>/.venv/Scripts/python.exe` (Windows)
  2. `<repo_root>/.venv/bin/python3` (Linux/Mac)
  3. `<repo_root>/.venv/bin/python` (Linux/Mac fallback)

  If none exist, it still returns the platform-appropriate default path (via
  `os.name`) so the existing `os.path.exists(SHARED_PYTHON)` guard fails with
  its normal "Shared interpreter not found" error rather than crashing on a
  `None`.

- **`sentiment-scanner/main.py`** (`_find_vol_suite_python()`) resolves the
  *separate* Vol_Suite-local venv the same way, for the same reason:
  Vol_Suite has its own dependencies (`arch`, `statsmodels`, ...) that aren't
  necessarily installed in sentiment-scanner's own venv.

Both resolvers use `Path.exists()` checks rather than branching purely on
`os.name`, so a venv created under an unusual environment (e.g. Git Bash or
WSL on a Windows machine) still resolves to the interpreter that's actually
present on disk.

`ROOT` itself (`orchestrator.py`) is still computed with
`os.path.dirname(os.path.abspath(__file__))` and used with `os.path.join`
throughout the rest of the file -- that was already platform-agnostic (both
functions handle `/` and `\` correctly on their respective OSes), so it was
left as-is rather than rewritten to `Path` wholesale, keeping the change
focused on the actual hardcoded-path bug.

## `.gitattributes`

`*.sh` files are pinned to `eol=lf` and `*.bat` files to `eol=crlf` in
`.gitattributes`, regardless of a contributor's `core.autocrlf` setting. This
matters specifically for the `.sh` scripts: a CRLF-terminated shebang line
(`#!/usr/bin/env bash\r`) fails on Linux/Mac with a cryptic
"`bad interpreter`" error, so the repo enforces LF for those files at
checkout time.
