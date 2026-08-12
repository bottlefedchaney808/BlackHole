# Dealer Positioning — Test Results (2026-08-11, fresh)

All runs executed 2026-08-11 with the scrubbed project interpreter:
`env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest`
(Python 3.14.4, pytest 9.1.1, `Financial_Dev_Env` venv, `-p no:cacheprovider`).
Verbatim output in `test_results/`.

## Summary

| Suite | Result | Time |
|---|---|---|
| **Vol_Suite/tests (FULL)** | **378 passed, 5 skipped** | 332.29s (5:32) |
| Direction/tests | 75 passed | 0.17s |
| test_dealer_positioning_sign_model.py | 5 passed | 1.51s |
| test_options_chain_v5_wiring.py (vanna parity) | 4 passed | 44.50s |
| test_run_modes_smoke.py | 2 passed | 0.85s |
| test_dealer_positioning_direction.py | 36 passed (35 fast + 1 slow e2e @ ~103s) | ~5 min total |

Full-suite summary line (verbatim):

```
378 passed, 5 skipped, 1 warning in 332.29s (0:05:32)
```

The 5 skips are `test_implied_vol.py:98` — "price below the floor where vol is
recoverable" (pre-existing, deterministic, unrelated to dealer changes).

## What the counts cover

- **378 = canonical-model contract** — direction sign mapping (bullish/bearish,
  zero-bias no-fallback), SABR deviation per-expiry (positive→short,
  negative→long, deadband→fallback, empty→fallback, zero-fallback→silent),
  per-expiry mode LOCKED to `sabr_deviation`, 150d accumulated book feeding ALL
  four greeks (gamma/delta/vanna/charm), NO_CALL gate, suppression registry +
  read_label, sign-model lock (`VALID_SIGN_MODELS == ('direction',)`),
  **scanner-vanna == dealer-engine-vanna parity**, backtest v5 leg wired to the
  live per-expiry resolver, pooled panel, fair-variance snapshot invariance,
  paper validation (Demeterfi Fig. 3), hedge-requirement units.
- **75 (Direction) = whale scanner** incl. `WHALE_THRESHOLD_BPS` relative-mode
  tests, 5-signal generator, data adapter normalization.

## Historical baselines for comparison

| Date | Result | Context |
|---|---|---|
| 08-06 | 356 passed, 5 skipped | oi_flow shipped (pre-lock, 5 selectable models) |
| 08-09 night | 378 passed, 5 skipped | gate restored + two-vanna wiring; same as today |

## Reproduction

```bash
cd /home/bottl/Financial_Development
env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest Vol_Suite/tests -q -p no:cacheprovider
env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest Direction/tests -q -p no:cacheprovider
```

Note: `test_report_renders_caveat_key_and_read_label` (direction file) takes
~103s — a real `compute_dealer_positioning("AMD")` with a fake ThetaData
controller building the full SABR surface. It is slow, not hung. Do not kill
the suite because of it.
