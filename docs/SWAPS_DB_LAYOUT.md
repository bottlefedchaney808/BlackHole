# Swaps Database Layout

This repo uses a three-file layout for swaps trade data:

| File | Role | Size | Apps |
|---|---|---|---|
| `C:/Users/bottl/OneDrive/Stocks/Swaps/swaps.db` | Live query DB | ~323 GB | `SWAPS_DB_PATH` |
| `FinancialDevelopment/swaps.db` | GitHub-sized recent window | ~4.66 MB | clone default if env unset |
| `OneDrive/.../archive/*.db.gz` | Cold backup of pre-2026-01-01 | ~10 GB gzipped | restore script only |

## Usage

- **Default (fresh clone):** Apps open `swaps.db` in the repo root, which contains trades with `effective_date >= 2026-01-01` (~941 rows). If `SWAPS_DB_PATH` is unset, a warning is logged once per process.
- **Live queries:** Set `SWAPS_DB_PATH=C:\Users\bottl\OneDrive\Stocks\Swaps\swaps.db` to query the full ~71M-row OneDrive database. This is done automatically by the launchers (`dashboard.bat`, `run_scheduler.bat`, etc.).
- **Gzip archives:** Located at `OneDrive/Stocks/Swaps/archive/`; used only by `split_swaps_db.py` for cold backup. They are not queryable without decompression and ATTACH.

## How it works

1. `split_swaps_db.py` (on branch `wt/t_b82fb352`) shards the original 323 GB database:
   - **Primary** (`swaps.db`): trades with `effective_date >= cutoff` (default 2026-01-01).
   - **Archives** (`*.db.gz`): older trades compressed by month.

2. All apps (`db_loader.py`, `swaps_query.py`, `orchestrator.py`, `setup_db.py`, `blackhole_investments/tools/market_tools.py`) respect `SWAPS_DB_PATH`. If unset, they fall back to the repo-root `swaps.db`.

3. The launchers (`dashboard.bat/.sh`, `run_scheduler.bat/.sh`, `swaps_dashboard.bat/.sh`) export `SWAPS_DB_PATH` overridably, so you can point them at a different DB for testing.
