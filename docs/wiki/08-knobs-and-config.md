# Knobs and Config

Configuration gates mechanics. This page lists the knobs, what each gates, and where to set it. It does not lead with YAML.

## Environment

Root `.env` is the single source of truth.

```text
THETADATA_CF_ACCESS_CLIENT_ID=...
THETADATA_CF_ACCESS_CLIENT_SECRET=...
```

Every suite that touches market data reads these. No suite needs its own `.env` for ThetaData anymore.

## Orchestrator CLI flags

| Flag | Default | What it gates |
|---|---|---|
| `--ticker` | required | Focus ticker for the run |
| `--expiry` | none | Explicit expiration in `YYYY-MM-DD` |
| `--target-years` | `0.25` | Horizon when no explicit expiry is given |
| `--strike` | ATM | Optional strike for the options pricer |
| `--option-type` | `call` | `call` or `put` |
| `--index` | `SPY` | Basket benchmark index |
| `--timeout` | `1800` | Per-suite timeout in seconds |
| `--fail-on-suite-error` | off | Abort the chain at the first failing suite |
| `--no-validate` | off | Skip explicit output validation (debug only) |

## Environment-level knobs

| Variable | Default | What it gates |
|---|---|---|
| `SUITE_VALIDATION_STRICT` | `1` | When `0`, missing side-artifact checks downgrade from FAIL to WARN |
| `VS_RUN_CHAIN_SCANNER` | `1` in orchestrator runs | Ensures `chain_strategies.json` is produced by Vol_Suite |
| `SUITE_CHILD_TIMEOUT_SEC` | `1800` | Per-suite child timeout |
| `SWAPS_DB_PATH` | `swaps.db` in repo root | Override for a mounted/volume DB |

## Dashboard module selection

The dashboard exposes registry checkboxes. Each maps to a `ModuleSpec` from `shared/module_registry.all_modules()`. The checkboxes gate which modules run, not the order. Order is still enforced by the orchestrator.

## Suite-specific knobs

### Vol_Suite

| Knob | What it gates |
|---|---|
| Sign model | `oi_heuristic`, `replication`, `vol_surface_replication` — how dealer position is signed |
| Basket index | Which peers are pulled for correlation |
| Target horizon / expiry | Which expiry chain is fetched |

### Options_Suite

| Knob | What it gates |
|---|---|
| `--strike` | Which strike is priced (default ATM) |
| `--option-type` | Call or put |
| Pricing model | Leisen-Reimer default; CRR, SABR, Vanna-Volga, etc. available |

### VaR_Tools_Simulations

| Knob | What it gates |
|---|---|
| `--module` | Only module 1 (`corr_sim`) runs in context mode |
| Correlation matrix | Falls back to identity if Vol_Suite did not produce one |
| Vol input | Falls back to flat 0.25 annual if Vol_Suite did not produce one |

## What this page is not

It is not a config dump. Each knob above is tied to a mechanic described elsewhere in the wiki. If a knob does not gate a mechanic, it does not belong here.

Next: what to do when the run is not clean.
