# Failure and What to Ignore

This repo is designed to fail loud. A missing input is a named gap. A quiet zero is a bug.

## Loud failure

| Symptom | Likely cause | What to do |
|---|---|---|
| `options_result.json`: "Could not fetch spot for ..." | Invalid ticker, bad ThetaData credentials, or network/API issue | Check ticker spelling; verify `.env` credentials; check ThetaData connectivity |
| `var_result.json` notes say "correlation matrix missing; used identity matrix" and/or "volatilities missing; used default annual volatility 0.25" | Vol_Suite failed or basket resolution returned no peers | Check Vol_Suite logs; verify ticker has option-chain data |
| `vol_result.json`: validation FAIL on `required_file[correlation_matrix_*.csv]` | Basket was single-name or correlation step failed | Set `SUITE_VALIDATION_STRICT=0` if single-name, or investigate the vol failure |
| Orchestrator summary says `options=FAILED: ... validation=FAIL` | Options_Suite exited 0 but wrote a marker that failed schema validation | Check validation errors in orchestrator log |
| Suite timeout (returncode -1) | Slow network, heavy chain pull, or the suite is genuinely hanging | Increase `--timeout`; check ThetaData rate limits |
| `sentiment_result.json` status `error` or `partial` | One or more market-signals scanners failed | Check the `errors` list inside `sentiment_result.json` |
| `swaps_result.json` status `no_data` | `swaps.db` is cold or DTCC has no recent activity | Run `python backfill.py` and/or leave `run_scheduler.bat` running |
| Orchestrator exits with "Shared interpreter not found" | Root `.venv` is missing or corrupt | Re-run `python -m venv .venv` and `pip install -r requirements.txt` |

## What to ignore

These are not signals. They are traps.

| Trap | Why it is not a signal |
|---|---|
| **GEX print = the book** | GEX is an imported aggregate. The book is a priced, signed position. |
| **Blank pane = no positioning** | Blank is usually a bug (missing data, bad ticker, wrong expiry). |
| **`auto` expiry on 1-DTE** | The chain looks empty because the horizon is too short. |
| **VIX spike = regime flip** | VIX is a level. Flow is signed by ΔIV. |
| **A path from −500k to +92k means the book disarmed** | It could be a sign flip, a roll, or a data gap. Read the two estimates together. |
| **High IV rank = buy vol** | IV rank is level. It does not tell you the direction of flow. |
| **Buy-and-hold as default** | FinDev is a positioning read, not a strategy. |

## Vendor gaps and parity fill

When the vendor leaves `implied_vol=0` for a leg, Vol_Suite now mirrors the opposite-right leg's solved IV before dropping the row. This is parity fill. It is not the same as inventing data. If both sides fail, the row is dropped and the run continues with a named gap.

## Stale server / stale data

Only one `dashboard.bat`/`tools.bat` and one `run_scheduler.bat` should run at a time. A second instance races the first against `swaps.db` and produces "database is locked" errors. Never stop those processes while an orchestrator run is in flight.

## What this page is not

It is not a list of every possible traceback. It is a list of the failures that are loud by design and the traps that look like signals but are not. For the full failure-mode table, see the [Analysis Pipeline Runbook](../guides/FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md).

Next: how to extend the repo without teaching a new method.
