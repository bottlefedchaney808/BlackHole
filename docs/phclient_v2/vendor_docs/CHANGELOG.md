# Changelog

## 2.0.1 — 2026-08-15

**Packaging fix.** Declare `pyyaml>=6.0,<7.0` as a runtime dependency: the v2
client import chain (`client_v2` → `_generated.descriptors` → `catalog`) imports
`yaml`, but 2.0.0's wheel did not declare it — a clean-environment install could
not import the client. Caught by the release pipeline's offline smoke gate
(row 31) during the staging rehearsal (run 31861302053). No API changes.

## 2.0.0 — 2026-08-07

**v1 deletion gate executed.** The legacy flat-method clients
(`potatohedge.client.PHClient`, `potatohedge.async_client.AsyncPHClient`)
and their lazy package exports are removed; the v2 namespaced,
capability-scoped generated client (`potatohedge.client_v2.PHClient`,
constructed from `potatohedge.config.ClientConfig`) is the only client.

- Canonical generation from the 140/140 conformance run against PH_API pin
`cfa5ae71` (certified isolated instance, 12/12 preflight); external theta
evidence registered from slice-run-016 (46/46 arms, validator v3, RTH
machine-checked, td-stage proxy).
- Caller attribution: every request carries `PH-Caller-Id` +
`PH-Client-Version`; `ClientConfig.caller_id` is mandatory per the
14-caller manifest (`inventory/caller-universe.yaml`).
- Typed error hierarchy (`potatohedge.errors.PHClientError`) replaces
`requests.exceptions.*`; responses are `ResponseEnvelope`s (`.data`).
- Legacy v1-only tests and examples removed; the demo notebook is
v2-only. `dealer_positioning` root-param parameterization and the CTB
family are excluded from 2.0 certification (`certification_exclusions`).
- Release evidence: `migration/release-evidence.yaml`.

All notable changes to PHClient will be documented in this file.

## [1.6.0] - 2025-11-04

### Added - API Parity Implementation
- **CTB (Cost-to-Borrow) Analysis - 8 new endpoints:**
- `get_ctb_timeseries()` - Historical borrow rate time series with forward returns
- `get_ctb_correlation_ftd()` - CTB/FTD correlation analysis
- `get_ctb_distribution()` - Distribution by sector and market cap
- `get_ctb_drop_events()` - Covering signal detection
- `get_ctb_spike_events()` - Short squeeze detection
- `get_ctb_term_structure()` - Contango/backwardation analysis
- `get_ctb_velocity()` - Rate of change detection
- `get_ctb_forward_returns_matrix()` - Heatmap visualization data

- **RegSho/FTD Analysis - 11 new endpoints:**
- `get_ftd_history()` - FTD data for specific symbol
- `get_bulk_ftd_data()` - Bulk FTD across all symbols
- `get_threshold_history()` - Threshold securities history
- `get_bulk_threshold_data()` - Bulk threshold data
- `get_ftd_correlation()` - FTD-price correlation analysis
- `get_ftd_cycles()` - T+35 cycle detection
- `get_ftd_options_correlation()` - Options activity during FTD
- `get_ftd_screener_current()` - Current FTD screener results
- `get_ftd_screener_alerts()` - FTD alert system
- `get_ftd_watchlist()` - FTD watchlist management
- `get_regsho_health()` - RegSho system health

- **Flow Analysis - 5 new endpoints:**
- `get_flow_momentum()` - Multi-timeframe momentum analysis
- `get_flow_sweeps()` - Multi-exchange sweep detection
- `get_flow_sweeps_intensity()` - Sweep intensity metrics
- `get_flow_unusual()` - Unusual activity detection (⚠️ known schema issues)
- `get_flow_unusual_summary()` - Unusual activity aggregations (⚠️ known schema issues)

### Coverage Improvements
- **API Parity:** 60.5% → 94.5% (+34 percentage points)
- **Total New Endpoints:** 24 endpoints added
- **RegSho/FTD:** 0% → 100% coverage
- **CTB Analysis:** 0% → 100% coverage
- **Flow Router:** 70% → 100% coverage

### Documentation
- All methods include comprehensive docstrings with usage examples
- Known schema issues clearly documented with warnings
- References to `FLOW_ENDPOINT_REFACTOR_PROPOSAL.md` for problematic endpoints

### Notes
- All endpoints implemented in both sync (`PHClient`) and async (`AsyncPHClient`) clients
- CTB and RegSho endpoints use `/ctb/` and `/regsho/` prefixes (NOT `/api/`)
- Flow unusual/sweeps endpoints have known schema mismatches (see docstring warnings)

## [1.5.0] - 2025-11-04

### Added - Async Signals Methods
- `get_signals_daily()` - Get signals for specific date (async)
- `get_signals_latest()` - Get latest signals (async)
- `get_signals_history()` - Get signal history (async)
- `get_signals_statistics()` - Get signal statistics (async)
- `get_signals_config()` - Get signal configuration (async)

### Coverage
- Added async signals methods for feature parity with sync client
- All endpoint paths verified correct against PH_API

## [1.4.0] - Previous Release

(Prior changelog entries preserved from original CHANGELOG.md)
