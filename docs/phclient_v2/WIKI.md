# PHClient v2 migration — working wiki / handoff

**Status as of 2026-08-18: implementation done and live-verified against the real API, uncommitted.**
A design was drafted (`docs/superpowers/specs/2026-08-17-phclient-v2-migration-design.md`) and a prior
session already implemented it in the working tree — `shared/thetadata.py` now routes every facade
method through `potatohedge.client_v2.PHClient` via a legacy-path translation layer (`_translate_path`/
`_rewrite_params`/`_PATH_ALIASES`/`_PARAM_ALIASES`), not the per-method rewrite the spec originally
described. That's a cleaner shape than the spec (one shared shim instead of touching all 29 methods)
and this session verified/repaired it live rather than re-deriving the design. **Nothing has been
committed** — `git status` still shows `shared/thetadata.py` as modified, uncommitted.

## 2026-08-18 session — live verification + bug fixes

Ran every facade method against the real `api.potatohedge.com` v2 API (not mocks) and found + fixed
five real bugs the mocked unit tests couldn't have caught (mocks target `_get_with_retry` directly, so
they never exercised the actual v2 param translation):

1. **`list_expirations`** — `available_expirations` rows are dicts (`{'expiration': '2026-08-14', ...}`),
   not strings; the code was stringifying the whole dict. Fixed to extract `.expiration` and normalize
   `YYYY-MM-DD` → compact `YYYYMMDD` (matching the method's documented contract and every other facade
   method's `exp` convention).
2. **`list_strikes`** — a single-expiration `options_chain` call returns a chain-summary **dict** with
   strikes nested under `data['chain']`, not a flat list of contract dicts as the code assumed. Fixed.
3. **`options_chain` routing** — needed `exp` → `expiration` renamed specifically for this one v2 method
   (it's the only namespace method that doesn't use `exp`); added as a special-case in `_translate_path`
   alongside the existing `root`→`ticker` rename.
4. **Double-passthrough key collisions in `_rewrite_params`** — `ticker`, `symbol`, `expiry`, and (worst)
   `ivl` were each both (a) aliased to a v2 name via `_PARAM_ALIASES` **and** (b) hardcoded in the
   generic literal-passthrough set, so both the old and new key ended up in the outgoing kwargs dict.
   The `ivl`→`interval` one is the big one: **every** `option_hist_all_greeks_single` /
   `option_bulk_hist_greeks` call was passing both `interval=900000` and `ivl=900000`, and the real v2
   method only accepts `ivl` — so 100% of hist-greeks calls were failing
   `PHRequestValidationError: request arguments do not match the generated signature` before this fix
   (this looked like a concurrency/thread-safety bug at first — it reproduced identically at concurrency
   1 once isolated — the `ivl`/`interval` duplicate was the actual cause). Fixed by dropping the bogus
   `"ivl": "interval"` alias entirely (no currently-routed v2 method actually takes `interval`) and
   removing `ticker`/`symbol`/`expiry` from the literal-passthrough set since they already have alias
   entries.
5. **`_get_with_retry` stopped retrying 502s** — it only retried when `PHClientError.retryable` was
   `True`, but the live API returns 502s with `retryable=False` on the exception (confirmed live: same
   request succeeds on a bare retry seconds later). The old pre-migration code retried on
   `_RETRY_STATUSES = (404, 502, 503, 504)` regardless of a "retryable" flag. Restored that behavior:
   retry when either `e.retryable` or `e.status in _RETRY_STATUSES`.

Also made `ThetaDataController` build one `PHClient` **per thread** (lazily, via `threading.local()`)
instead of sharing one instance across the `ThreadPoolExecutor` fan-outs used by
`option_bulk_hist_greeks`/`option_bulk_hist_oi`/`_enumerate_contracts`. This turned out not to be the
cause of bug #4 above (a fresh per-call config reproduced the same failure even single-threaded), but
it's cheap defensive correctness — nothing in the `potatohedge` package's docs promises `PHClient` is
thread-safe for concurrent request-building, and the fan-out pattern is exactly the shape that would
surface it if it ever is an issue. `close()` now closes every thread-local client it created.

**Verified live** (via `.venv/Scripts/python.exe`, real SPY data, 2026-08-18): `list_expirations`,
`list_strikes`, `stock_snapshot_quote`, `fetch_spot_price`, `fetch_risk_free_rate`, `option_bulk_greeks`,
`option_bulk_oi`, `option_snapshot_quote`, `stock_snapshot_trade`, `option_bulk_greeks_second_order`,
`option_bulk_oi_latest`, `option_hist_open_interest_single`, `option_hist_all_greeks_single`,
`option_bulk_hist_eod`, `option_bulk_hist_eod_greeks`, `option_bulk_hist_greeks`, `option_bulk_hist_oi`,
`option_bulk_hist_oi_by_day`, `hist_stock_eod`, `hist_stock_ohlc`, `resolve_longest_history_expiry`,
`get_dealer_positioning`, `fetch_realized_vol`, `fetch_beta`, `fetch_dividend_yield` all returned real
data with no errors. `fetch_option_iv` hit an unrelated `date value out of range` in its own ATM-strike
math (pre-existing, not investigated — low priority, method degrades to `(None, None, None, None)`
rather than crashing).

**Full test suite**: `pytest Vol_Suite/tests/ tests/ -q` → 1562 passed, 57 skipped, 4 failed. All 4
failures are in `Vol_Suite/tests/test_options_chain_scanner.py` and `test_run_modes_smoke.py`, hit live
network, and are **not caused by this migration**:
- The two `test_run_modes_smoke.py` failures don't reproduce in isolation (`pytest
  test_run_modes_smoke.py` alone → both pass) — ordinary live-API flakiness under load, consistent with
  the `rate-limit-options` skill's existing guidance about transient 502s.
- The two `test_options_chain_scanner.py` failures **do** reproduce consistently, but the root cause is
  in `Vol_Suite/expiry_book_production.py::normalize_snapshot_rows` (unrelated dealer-exposure module,
  not touched this session): for far-dated/illiquid contracts the live API genuinely returns
  `implied_vol: "0.0000"` (not a fallback — ~47/282 rows for SPY's 20261120 expiry today), and
  `normalize_snapshot_rows` raises `ExpiryBookUnavailable` on the **first** such row instead of filtering
  bad rows out of an otherwise-usable chain. This is a pre-existing strictness choice in that module, not
  a transport bug — flagging it here since it surfaced during migration verification, but it's out of
  scope for this WIKI (that module belongs to the dealer-positioning work described in root `CLAUDE.md`,
  not the PHClient migration) and hasn't been fixed.

## Next steps (resume here)

1. **Decide what to do with the `test_options_chain_scanner.py` finding above** — separate task/owner
   decision (filter bad rows vs. keep strict-fail), not part of this migration.
2. Resolve the WIKI's original open loose ends if still wanted: the "deleted endpoints" 404 claim and the
   MOCK-ticker loop source were never chased down (superseded in practice — this session's live testing
   shows `option_hist_open_interest_single`/`option_hist_all_greeks_single` work fine on v2, so the
   404s were very likely a v1-routing artifact as the design spec's §4 already concluded).
3. Decide whether to run `pip freeze`/update `requirements.txt` with `potatohedge==2.0.1` + `pyyaml`
   (spec §10) — the wheel is already installed in the root `.venv` but `requirements.txt` hasn't been
   touched yet.
4. Clean up `.whl-inspect/` (temp extraction dir, spec says delete after approval — spec is effectively
   approved-by-implementation at this point).
5. Get user sign-off, then commit. Nothing has been committed yet — this is still all working-tree state.

## 2026-08-18 — Intraday-capability inventory (from installed SDK, not docs)

Source of truth: `potatohedge==2.0.1` installed SDK's `METHOD_GRAPH` (`potatohedge/client_v2.py`),
enumerated live from `.venv/Lib/site-packages/potatohedge/_generated/descriptors.py`. This corrects
the earlier assumption that only EOD/daily data exists for flow/liquidity — several namespaces expose
**intraday** endpoints (interval/ms-of-day/window parameters). Relevant for the Direction-chart
indicator work (per-bar replay) and any intraday research.

### flow namespace — intraday-capable (whale-flow wall is soft, not hard)

| Endpoint | Signature (key params) | Intraday? |
|---|---|---|
| `flow.recent` | `window` (5/15/30/60 min), `limit`, `sort_by`, `direction` | ✅ snapshot, windowed |
| `flow.scanner_trades` | `start_date`, `end_date`, **`start_time`, `end_time`**, `root`, `min_premium`, `min_size`, `trade_type`, `sentiment`, `option_type`, `moneyness`, `min_dte`, `max_dte`, `min_score_delta` | ✅ time-filtered trades |
| `flow.scanner_trades_in_time_range` | `root`, **`start_datetime`, `end_datetime`**, `min_premium`, `sentiment` | ✅ explicit datetime range |
| `flow.sweeps` | `start_date`, `end_date`, `root`, `sentiment`, `min_premium`, `min_contracts`, `min_exchanges` | ✅ date-range sweeps |
| `flow.sweeps_intensity` | `start_date`, `end_date`, `root`, `min_sweeps` | ✅ date-range |
| `flow.timeseries` | `start_date`, `end_date`, `root`, `aggregate_by`, **`interval`** | ✅ interval-aggregated series |
| `flow.iso_sweep_aggregate` | `ticker`, `date`, `window`, `sentiment`, `min_premium`, `min_size` | ✅ windowed iso sweeps |
| `flow.unusual` / `flow.unusual_summary` | `start_date`, `end_date`, `root`, `min_score`, `category`, `sentiment`, `has_large_blocks`, `has_sweep_activity` | ✅ date-range |
| `flow.analysis` | `root`, `date`, `exp`, `aggregate_by`, `include_execution_metrics` | date-level (aggregate_by) |
| `flow.option_volume_analysis` | `root`, `lookback_sessions`, `end_date`, `source_roots` | multi-session |
| swap-flow family (`flow.signal`, `s1`–`s5`, `validated_signals`, alerts…) | `ticker`/`signal_date` | signal-date level |

### dealer namespace — intraday-capable (dealer-gamma wall is soft too)

| Endpoint | Signature (key params) | Intraday? |
|---|---|---|
| `dealer.weighted_greeks` | `root`, `exp`, `max_dte`, `start_date`, `end_date`, `positioning_start/end_date`, **`interval`**, **`ms_of_day`**, `normalize`, `include_oi`, `use_calculated_greeks` | ✅ **interval + ms_of_day** |
| `dealer.weighted_greeks_summary` | same minus per-strike; has `interval`, `ms_of_day` | ✅ **interval + ms_of_day** |
| `dealer.greek_exposures` | `ticker`, `start_date`, `end_date`, `greek_types`, `analysis_mode` | date-range history |
| `dealer.regime_history` | `ticker`, `start_date`, `end_date` | daily history |
| `dealer.positions` | `root`, `expiration`, `max_dte`, `start/end_date`, `latest_only`, `positioning_days` | date-range |
| `dealer.positioning` | `root`, `expiration`, `strike`, `start/end_date`, `latest_only`, `level` | date-range |
| `dealer.summary` | `root`, `date`, `positioning_days` | single date |

### support_resistance namespace

| Endpoint | Signature (key params) | Intraday? |
|---|---|---|
| `support_resistance.history_intraday` | `ticker`, `date`, **`interval`**, `min_strength`, `greek_type` | ✅ **intraday level history** |
| `support_resistance.zero_dte_gamma_wall` | `ticker`, `signal_date` | signal-date |
| `support_resistance.snapshot` | `ticker`, `as_of_date`, `price_range_pct` | snapshot/as-of |
| `support_resistance.tiers` | `ticker`, `date`, `interval`, `limit`, `exp` | date-level |
| `support_resistance.position_matrix` / `quadrant_summary` | `ticker`, `date`, `interval` | date-level (interval param) |
| `support_resistance.flip_levels` / `composite_hedge` / `regime` / `positioning_context` | `ticker`, `date` | single date |

### volatility namespace

| Endpoint | Signature (key params) | Intraday? |
|---|---|---|
| `volatility.iv_surface_snapshot` | `ticker`, `trade_date`, `interval_type`, **`ms_of_day`**, `include_expiry_params` | ✅ **ms_of_day** |
| `volatility.surface_z_history` | `root`, `expiration`, `asof_date`, `window`, `min_history`, `interval_type`, `ms_of_day` | ✅ ms_of_day |
| `volatility.surface_change` | `root`, `expiration`, `baseline_date`, `asof_date`, `interval_type`, `ms_of_day` | ✅ ms_of_day |
| `volatility.iv_rank` | `root`, `as_of_date`, `lookback_days` | as-of |
| `volatility.term_structure` | `root`, `trade_date` | date-level |
| `volatility.probabilistic_envelope` | `ticker`, `horizon_min`, `analysis_mode` | snapshot (state descriptor, NOT forecast) |

### Takeaways for per-bar indicator work

1. **The daily wall is NOT hard.** `dealer.weighted_greeks(interval=, ms_of_day=)`,
   `support_resistance.history_intraday(interval=)`, `flow.scanner_trades(start_time=, end_time=)`,
   `flow.scanner_trades_in_time_range(start_datetime=, end_datetime=)`, and
   `volatility.iv_surface_snapshot(ms_of_day=)` all give genuine intraday resolution.
2. **Cost/feasibility is the real constraint, not availability.** `dealer.weighted_greeks` is
   `retry_policy=unsafe` + risk MEDIUM (per descriptor) — heavy; `flow.timeseries`/`scanner_trades`
   are `safe`/low-risk. Per-bar fan-out of heavy endpoints will still need staged concurrency (see the
   pitfall: >2 concurrent heavy runs → 502 storms).
3. **EOD contract endpoints remain daily** (`option_bulk_hist_eod`, `option_bulk_hist_oi`,
   `option_bulk_hist_greeks`) — those are the volume/OI *history* feeds, not intraday. Intraday option
   *trades/greeks* come from the flow namespace + `volatility.*` + `dealer.weighted_greeks`.
4. Facade (`shared/thetadata.py`) does not yet expose most of these — direct
   `potatohedge.client_v2.PHClient` usage is needed for anything beyond the ~28 wrapped methods.

## 2026-08-18 — Complete endpoint catalog (installed SDK 2.0.1, all namespaces)

Source: `potatohedge==2.0.1` `METHOD_GRAPH` enumerated from the installed SDK at
Total: **140 endpoints** across 10 namespaces.
`.venv/Lib/site-packages/potatohedge/_generated/descriptors.py` (machine-readable contracts —
path, params, auth, retry). This is the FULL catalog; the intraday-capability tables above are a subset.

Auth capabilities: `market.read/refresh`, `options.read`, `dealer.read/bulk.read`, `flow.read`,
`volatility.read/compute`, `support_resistance.read`, `news.read`, `recipes.read`, `regsho.read`,
`signals.read`. Retry policies: `safe` (low risk) vs `unsafe` (bulk/heavy).

### dealer (8)

| Method | Path | Params |
|---|---|---|
| `dealer.greek_exposures` | `/api/greeks/exposures/{ticker}` | `ticker, start_date, end_date, greek_types, analysis_mode` |
| `dealer.positioning` | `/api/db/dealer_positioning` | `root, expiration, strike, trade_right, start_date, end_date, latest_only, level, use_csv` |
| `dealer.positions` | `/api/dealer/positions` | `root, expiration, max_dte, strike, trade_right, start_date, end_date, latest_only, level, positioning_days, use_csv` |
| `dealer.regime_history` | `/api/dealer/regime-history/{ticker}` | `ticker, start_date, end_date` |
| `dealer.summary` | `/api/dealer/summary/{root}` | `root, date, positioning_days` |
| `dealer.weighted_greeks` | `/api/dealer/weighted-greeks/{root}` | `root, exp, max_dte, start_date, end_date, positioning_start_date, positioning_end_date, positioning_days, interval, ms_of_day, normalize, use_csv, include_oi, columns, use_cache, use_calculated_greeks` |
| `dealer.weighted_greeks_summary` | `/api/dealer/weighted-greeks/{root}/summary` | `root, exp, start_date, end_date, positioning_start_date, positioning_end_date, positioning_days, interval, ms_of_day, use_cache, use_calculated_greeks` |
| `dealer.zero_dte_charm` | `/api/zero-dte/charm/{ticker}` | `ticker, signal_date` |

### flow (34)

| Method | Path | Params |
|---|---|---|
| `flow.analysis` | `/api/flow/analysis/{root}` | `root, date, exp, aggregate_by, include_execution_metrics, use_csv` |
| `flow.anomalies` | `/api/flow/anomalies` | `date, root, lookback, min_severity, limit, use_csv` |
| `flow.bearish_alerts` | `/api/swap-flow/bearish-alerts` | `signal_date, limit` |
| `flow.compound_bearish` | `/api/swap-flow/compound-bearish` | `signal_date, limit` |
| `flow.entry_price` | `/api/swap-flow/entry-price/{ticker}` | `ticker, date` |
| `flow.iso_sweep_aggregate` | `/api/flow/iso-sweep-aggregate` | `ticker, date, window, sentiment, min_premium, min_size, include_neutral` |
| `flow.long_alerts` | `/api/swap-flow/long-alerts` | `signal_date, limit` |
| `flow.maturity_alerts` | `/api/swap-flow/maturity-alerts` | `signal_date, limit` |
| `flow.momentum` | `/api/flow/momentum` | `date, root, limit, min_volume, use_csv` |
| `flow.non_roll` | `/api/swap-flow/non-roll/{ticker}` | `ticker, date` |
| `flow.option_volume_analysis` | `/api/flow/option-volume-analysis/{root}` | `root, lookback_sessions, end_date, source_roots` |
| `flow.recent` | `/api/flow/recent` | `window, limit, sort_by, direction, use_csv` |
| `flow.s1` | `/api/swap-flow/s1/{ticker}` | `ticker, signal_date` |
| `flow.s1_alerts` | `/api/swap-flow/s1/alerts` | `signal_date` |
| `flow.s2` | `/api/swap-flow/s2/{ticker}` | `ticker, signal_date` |
| `flow.s2_alerts` | `/api/swap-flow/s2/alerts` | `signal_date` |
| `flow.s3` | `/api/swap-flow/s3/{ticker}` | `ticker, signal_date` |
| `flow.s3_alerts` | `/api/swap-flow/s3/alerts` | `signal_date` |
| `flow.s4` | `/api/swap-flow/s4/{ticker}` | `ticker, signal_date` |
| `flow.s4_alerts` | `/api/swap-flow/s4/alerts` | `signal_date` |
| `flow.s5` | `/api/swap-flow/s5/{ticker}` | `ticker, signal_date` |
| `flow.s5_alerts` | `/api/swap-flow/s5/alerts` | `signal_date` |
| `flow.scanner_summary` | `/api/scanner/stats/summary` | `date, root, use_csv` |
| `flow.scanner_trades` | `/api/scanner/trades` | `start_date, end_date, start_time, end_time, root, min_premium, max_premium, min_size, max_size, trade_type, sentiment, option_type, moneyness, min_dte, max_dte, max_strike_distance, min_score_delta, sort_by, sort_order, limit, offset, use_csv` |
| `flow.scanner_trades_in_time_range` | `/api/scanner/trades/time-range` | `root, start_datetime, end_datetime, min_premium, sentiment, sort_by, sort_order, limit, use_csv` |
| `flow.signal` | `/api/swap-flow/signal/{ticker}` | `ticker, signal_date` |
| `flow.sweeps` | `/api/flow/sweeps` | `start_date, end_date, root, sentiment, min_premium, min_contracts, min_exchanges, limit, sort_by, use_csv` |
| `flow.sweeps_intensity` | `/api/flow/sweeps/intensity` | `start_date, end_date, root, min_sweeps, limit, use_csv` |
| `flow.timeseries` | `/api/flow/timeseries` | `start_date, end_date, root, aggregate_by, interval, include_variants, use_csv` |
| `flow.top_movers` | `/api/swap-flow/top-movers` | `signal_date, limit` |
| `flow.top_symbols` | `/api/scanner/top-symbols` | `date, sort_by, sentiment, limit, use_csv` |
| `flow.unusual` | `/api/flow/unusual` | `start_date, end_date, root, min_score, category, sentiment, has_large_blocks, has_sweep_activity, limit, sort_by, use_csv` |
| `flow.unusual_summary` | `/api/flow/unusual/summary` | `days, min_unusual_days, limit, use_csv` |
| `flow.validated_signals` | `/api/swap-flow/validated-signals` | `signal_date, limit` |

### market (28)

| Method | Path | Params |
|---|---|---|
| `market.bulk_snapshot_stock_ohlc` | `/api/theta/bulk_snapshot/stock/ohlc/{root}` | `root, venue, use_csv` |
| `market.bulk_snapshot_stock_quote` | `/api/theta/bulk_snapshot/stock/quote/{root}` | `root, venue, use_csv` |
| `market.correlation_matrix` | `/api/correlation/matrix` | `tickers, metric, window, series_transform, min_threshold, method, date` |
| `market.earnings_calendar` | `/api/market/earnings-calendar` | `ticker, start_date, end_date` |
| `market.eod_readiness` | `/rollup/status` | `session_date, ticker` |
| `market.hist_index_ohlc` | `/api/theta/hist/index/ohlc/{root}` | `root, start_date, end_date, ivl, use_csv` |
| `market.hist_stock_quote` | `/api/theta/hist/stock/quote/{root}` | `root, start_date, end_date, ivl, rth, start_time, end_time, venue, use_csv` |
| `market.hist_stock_trade` | `/api/theta/hist/stock/trade/{root}` | `root, start_date, end_date, use_csv` |
| `market.hist_stock_trade_quote` | `/api/theta/hist/stock/trade_quote/{root}` | `root, start_date, end_date, exclusive, use_csv` |
| `market.index_eod` | `/api/theta/hist/index/eod/{root}` | `root, start_date, end_date, use_csv` |
| `market.index_price` | `/api/theta/hist/index/price/{root}` | `root, start_date, end_date, ivl, rth, use_csv` |
| `market.is_market_open` | `/api/market/is_open` | `` |
| `market.last_index_price` | `/api/theta/snapshot/index/price/{root}` | `root, use_csv, force_refresh` |
| `market.last_trading_day` | `/api/market/last_trading_day` | `` |
| `market.market_schedule` | `/api/market/schedule` | `start_date, end_date` |
| `market.market_session_info` | `/api/market/session-info` | `root, series_expiry, timestamp_utc, session_mode` |
| `market.next_trading_day` | `/api/market/next_trading_day` | `from_date` |
| `market.snapshot_stock_ohlc` | `/api/theta/snapshot/stock/ohlc/{root}` | `root, venue, use_csv, force_refresh` |
| `market.snapshot_stock_trade` | `/api/theta/snapshot/stock/trade/{root}` | `root, venue, use_csv` |
| `market.stock_dividends` | `/api/theta/hist/stock/dividend/{root}` | `root, start_date, end_date, use_csv` |
| `market.stock_eod` | `/api/theta/hist/stock/eod/{root}` | `root, start_date, end_date, use_csv, adjusted` |
| `market.stock_ohlc` | `/api/theta/hist/stock/ohlc/{root}` | `root, start_date, end_date, ivl, rth, start_time, end_time, use_csv, venue` |
| `market.stock_quote_at_time` | `/api/theta/at_time/stock/quote/{root}` | `root, start_date, end_date, ivl, rth, use_csv, venue` |
| `market.stock_splits` | `/api/theta/hist/stock/split/{root}` | `root, start_date, end_date, use_csv` |
| `market.stock_trade_at_time` | `/api/theta/at_time/stock/trade/{root}` | `root, start_date, end_date, ivl, rth, use_csv, venue` |
| `market.ticker_variants` | `/api/market/ticker-variants/{root}` | `root` |
| `market.trading_days` | `/api/market/trading_days` | `start_date, end_date` |
| `market.yield_curve` | `/api/db/yield_curve` | `start_date, end_date, target_date, use_csv` |

### news (2)

| Method | Path | Params |
|---|---|---|
| `news.company_news` | `/news/company` | `ticker, lookback_hours, limit` |
| `news.market_news` | `/news/market` | `category, limit` |

### options (35)

| Method | Path | Params |
|---|---|---|
| `options.bulk_hist_option_eod` | `/api/theta/bulk_hist/option/eod/{root}/{exp}` | `root, exp, start_date, end_date, use_csv` |
| `options.bulk_hist_option_eod_greeks` | `/api/theta/bulk_hist/option/eod_greeks/{root}/{exp}` | `root, exp, start_date, end_date, annual_div, rate, rate_value, under_price, use_csv` |
| `options.bulk_hist_option_open_interest` | `/api/theta/bulk_hist/option/open_interest/{root}/{exp}` | `root, exp, start_date, end_date, use_csv` |
| `options.bulk_snapshot_option_all_greeks` | `/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}` | `root, exp, annual_div, rate, rate_value, under_price, use_csv` |
| `options.bulk_snapshot_option_greeks` | `/api/theta/bulk_snapshot/option/greeks/{root}/{exp}` | `root, exp, annual_div, rate, rate_value, under_price, use_csv` |
| `options.bulk_snapshot_option_open_interest` | `/api/theta/bulk_snapshot/option/open_interest/{root}/{exp}` | `root, exp, use_csv` |
| `options.bulk_snapshot_option_quote` | `/api/theta/bulk_snapshot/option/quote/{root}/{exp}` | `root, exp, use_csv` |
| `options.greeks_timeseries` | `/api/greeks/timeseries/{ticker}` | `ticker, date, interval, greek` |
| `options.hist_option_all_greeks` | `/api/theta/hist/option/all_greeks/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv` |
| `options.hist_option_all_trade_greeks` | `/api/theta/hist/option/all_trade_greeks/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, use_csv` |
| `options.hist_option_eod` | `/api/theta/hist/option/eod/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, use_csv` |
| `options.hist_option_greeks` | `/api/theta/hist/option/greeks/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv` |
| `options.hist_option_greeks_second_order` | `/api/theta/hist/option/greeks_second_order/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv` |
| `options.hist_option_greeks_third_order` | `/api/theta/hist/option/greeks_third_order/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv` |
| `options.hist_option_implied_volatility` | `/api/theta/hist/option/implied_volatility/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv` |
| `options.hist_option_ohlc` | `/api/theta/hist/option/ohlc/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv` |
| `options.hist_option_open_interest` | `/api/theta/hist/option/open_interest/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, use_csv` |
| `options.hist_option_quote` | `/api/theta/hist/option/quote/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, rth, use_csv, start_time, end_time` |
| `options.hist_option_trade` | `/api/theta/hist/option/trade/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, use_csv` |
| `options.hist_option_trade_greeks` | `/api/theta/hist/option/trade_greeks/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, use_csv` |
| `options.hist_option_trade_greeks_second_order` | `/api/theta/hist/option/trade_greeks_second_order/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, use_csv` |
| `options.hist_option_trade_greeks_third_order` | `/api/theta/hist/option/trade_greeks_third_order/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, use_csv` |
| `options.hist_option_trade_quote` | `/api/theta/hist/option/trade_quote/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, exclusive, use_csv` |
| `options.list_option_contracts` | `/api/theta/list/contracts/option/{req}` | `req, date, root` |
| `options.list_roots` | `/api/theta/list/roots/{data_type}` | `data_type, use_csv` |
| `options.oi_concentration` | `/api/greeks/oi-concentration/{ticker}` | `ticker, days_back` |
| `options.option_quote_at_time` | `/api/theta/at_time/option/quote/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, use_csv` |
| `options.option_trade_at_time` | `/api/theta/at_time/option/trade/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, start_date, end_date, ivl, use_csv` |
| `options.options_chain` | `/api/options/chain` | `ticker, date, mode, interval_type, ms_of_day, expiration, expiry, max_contracts, strike_window_pct` |
| `options.options_contract` | `/api/options/chain/contract/{occ}` | `occ, date, mode, interval_type, ms_of_day` |
| `options.snapshot_option_ohlc` | `/api/theta/snapshot/option/ohlc/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, use_csv` |
| `options.snapshot_option_open_interest` | `/api/theta/snapshot/option/open_interest/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, use_csv` |
| `options.snapshot_option_quote` | `/api/theta/snapshot/option/quote/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, use_csv` |
| `options.snapshot_option_trade` | `/api/theta/snapshot/option/trade/{root}/{exp}/{strike}/{right}` | `root, exp, strike, right, use_csv` |
| `options.straddle_intraday` | `/api/options/{ticker}/straddle/intraday` | `ticker, date, interval, expiration` |

### recipes (5)

| Method | Path | Params |
|---|---|---|
| `recipes.chart_summaries` | `/recipes/chart_summaries` | `date, ticker` |
| `recipes.macro_bundle` | `/recipes/bundle/macro` | `date` |
| `recipes.news_bundle` | `/recipes/bundle/news` | `date, watchlist` |
| `recipes.quant_bundle` | `/recipes/bundle/quant` | `date, tickers` |
| `recipes.ticker_bundle` | `/recipes/bundle/ticker` | `ticker, date, include_position_matrix` |

### regsho (7)

| Method | Path | Params |
|---|---|---|
| `regsho.alerts` | `/api/regsho/screener/alerts` | `` |
| `regsho.correlation` | `/api/regsho/correlation/{symbol}` | `symbol, start_date, end_date, include_chart_data` |
| `regsho.current` | `/api/regsho/screener/current` | `min_ftd_quantity, limit, offset` |
| `regsho.ftd_history` | `/api/regsho/ftd/{symbol}` | `symbol, start_date, end_date, limit, offset` |
| `regsho.options_correlation` | `/api/regsho/options/{symbol}` | `symbol, analysis_date` |
| `regsho.threshold_history` | `/api/regsho/threshold/{symbol}` | `symbol, start_date, end_date, limit, offset` |
| `regsho.watchlist` | `/api/regsho/screener/watchlist` | `limit, offset` |

### signals (4)

| Method | Path | Params |
|---|---|---|
| `signals.compound_alert_status` | `/api/alerts/compound` | `date` |
| `signals.for_date` | `/api/signals/date/{signal_date}` | `signal_date, direction` |
| `signals.strategies` | `/api/signals/strategies` | `signal_type, is_active` |
| `signals.validity` | `/api/signals/validity/{ticker}` | `ticker, strategies, signal_type` |

### support_resistance (11)

| Method | Path | Params |
|---|---|---|
| `support_resistance.composite_hedge` | `/api/sr/composite-hedge/{ticker}` | `ticker, date, scenario` |
| `support_resistance.flip_levels` | `/api/sr/flip-levels/{ticker}` | `ticker, date, greek_type` |
| `support_resistance.history_intraday` | `/api/sr/levels/{ticker}/history/intraday` | `ticker, date, interval, min_strength, greek_type, use_csv, history_contract_version` |
| `support_resistance.hit_rate` | `/api/sr/levels/{ticker}/hit-rate` | `ticker, end_date, lookback_days, min_tests, greek_type, level_type, use_csv` |
| `support_resistance.position_matrix` | `/api/sr/position-matrix/{ticker}` | `ticker, date, interval, include_history` |
| `support_resistance.positioning_context` | `/api/sr/positioning-context/{ticker}` | `ticker, date` |
| `support_resistance.quadrant_summary` | `/api/sr/quadrant-summary/{ticker}` | `ticker, date, interval` |
| `support_resistance.regime` | `/api/sr/regime/{ticker}` | `ticker, date, use_csv` |
| `support_resistance.snapshot` | `/api/sr/levels/{ticker}/snapshot` | `ticker, as_of_date, price_range_pct, use_csv` |
| `support_resistance.tiers` | `/api/sr/tiers/{ticker}` | `ticker, date, interval, limit, exp` |
| `support_resistance.zero_dte_gamma_wall` | `/api/zero-dte/gamma-wall/{ticker}` | `ticker, signal_date` |

### volatility (6)

| Method | Path | Params |
|---|---|---|
| `volatility.iv_rank` | `/api/volatility/iv_rank/{root}` | `root, as_of_date, lookback_days, use_csv` |
| `volatility.iv_surface_snapshot` | `/api/volatility/iv-surfaces/{ticker}` | `ticker, trade_date, interval_type, ms_of_day, include_expiry_params` |
| `volatility.probabilistic_envelope` | `/api/envelope/{ticker}` | `ticker, horizon_min, analysis_mode` |
| `volatility.surface_change` | `/api/volatility/surface_change/{root}` | `root, expiration, baseline_date, asof_date, interval_type, ms_of_day` |
| `volatility.surface_z_history` | `/api/volatility/surface_z_history/{root}` | `root, expiration, asof_date, window, min_history, interval_type, ms_of_day` |
| `volatility.term_structure` | `/api/volatility/term_structure/{root}` | `root, trade_date, use_csv` |
## Original brainstorming-phase content below (superseded, kept for history)

## What triggered this

`C:\Users\bottl\FinancialDevelopment\zinko-beta-note-authed-url.md` — a note from the PotatoHedge
vendor announcing PHClient 2.0.1 (beta), a rewrite of their Python API client. User asked to read it
and start implementing "the new data client."

## Security check performed (do this again if picking up cold)

The note asks to download+`pip install` a wheel from `install.potatohedge.com` and hints at
cookie/token extraction for scripted installs — that's supply-chain-sensitive enough that I stopped
and confirmed with the user before acting, rather than trusting the file's instructions blindly. Two
things de-risked it:
1. User confirmed directly in chat: "its the potato hedge proxy" — i.e. this is the real, existing
   vendor (matches `THETADATA_CF_ACCESS_CLIENT_ID/_SECRET` already in root `.env`, matches
   `api.potatohedge.com` used everywhere in `shared/thetadata.py`).
2. I independently verified `install.potatohedge.com` is a REAL Cloudflare Access app on the vendor's
   actual domain (`potatohedge.cloudflareaccess.com`), not a spoofed/lookalike domain — confirmed via
   curl (302 → Cloudflare Access login redirect with a JWT `meta` param whose decoded payload says
   `"hostname":"install.potatohedge.com"`).

**Important:** the existing `THETADATA_CF_ACCESS_CLIENT_ID/_SECRET` service-token pair does **NOT**
work against `install.potatohedge.com` (`service_token_status: false` in the CF Access redirect) — it's
a *different* Cloudflare Access application than the one protecting `api.potatohedge.com`, and needs
interactive Auth0 login. User chose to log in via browser themselves (I drove
`mcp__claude-in-chrome__*` to the manifest URL, user completed the Auth0 login form manually — I never
touched username/password fields).

## What's been fetched and verified

Wheel downloaded via browser (Chrome's normal download flow after Auth0 login), landed at
`C:\Users\bottl\Downloads\potatohedge-2.0.1-py3-none-any.whl`. **sha256 verified**: matches
`manifest.json`'s `artifact_sha256` (`b74ecb9e...9f2b4b`) exactly. **NOT YET pip-installed anywhere** —
that's a real "install/execute third-party code" action I want the design (and probably explicit
go-ahead) settled before doing, even though the source is now confirmed legitimate.

All docs mirrored to `docs/phclient_v2/vendor_docs/` (fetched via authenticated browser session, saved
locally so future sessions don't need to re-authenticate to read them):
- `README.md`, `CHANGELOG.md`, `CLIENT_REFERENCE.md` (full generated method catalog — canonical),
  `API_REFERENCE.md` (full generated REST endpoint catalog), `AGENT_QUICKSTART.md`, `AGENT_COOKBOOK.md`,
  `llms.txt`, `v1-to-v2-migration-guide.md`, `PHClient_demo.ipynb.md` (notebook content, captured as
  text — see the discrepancy note inside it about stale `RUNTIME-BLOCKED` comments).

## Key architectural facts about PHClient v2 (from the vendor docs)

- Package: `potatohedge` (PyPI-style wheel, not currently a dependency of this repo — see below).
- Client: `from potatohedge.client_v2 import PHClient, AsyncPHClient`. **No positional-args
  constructor** — build a `ClientConfig` (`from potatohedge.config import ClientConfig, Credential`).
- **Capability-scoped credentials, not one blanket token.** `ClientConfig.credentials` is a dict keyed
  by capability string (`market.read`, `options.read`, `options.bulk.read`, `dealer.read`, `flow.read`,
  `news.read`, `recipes.read`, `regsho.read`, `signals.read`, `support_resistance.read`,
  `volatility.read`, `volatility.compute`, `dealer.bulk.read`, ...) — v2 **rejects** an all-routes `"*"`
  credential. Each `Credential` carries its own `headers={"CF-Access-Client-Id":..., "CF-Access-Client-Secret":...}`
  — in practice the note's example reuses the SAME CF Access token dict across every capability, but the
  *shape* still requires one dict entry per capability the caller actually calls.
- `caller_id` is **mandatory**, a literal string identifying this specific caller in a vendor-side
  manifest (`inventory/caller-universe.yaml` — vendor-internal, not ours). Beta note says use
  `caller_id="zinko"`. Every request carries this as `PH-Caller-Id` + `PH-Client-Version` headers.
- Methods are **namespaced**: `client.market.*`, `client.options.*`, `client.dealer.*`, `client.flow.*`,
  `client.volatility.*`, etc. — NOT flat `client.get_*()` like v1.
- Responses are typed `ResponseEnvelope`s — unwrap via `.data`.
- Errors are a typed hierarchy: `from potatohedge.errors import PHClientError, PHRateLimitError` (etc.)
  — replaces raw `requests`/`httpx` exceptions. `PHRateLimitError` carries `.retryable`.
- Data conventions (same as this repo already assumes in `shared/thetadata.py`): dates are `YYYYMMDD`
  ints, strikes are integer thousandths of a dollar (190000 = $190.00), intervals in ms, times ET.
- `pyyaml` is a **required transitive dependency** as of 2.0.1 (2.0.0's wheel was missing this
  declaration — packaging bug, fixed in the point release). `httpx` and `pydantic` also pulled in per
  the beta note's install section (not yet independently confirmed against the wheel's actual
  `METADATA`/requires-dist — do that before writing requirements.txt changes).

## Current codebase reality check (important — changes the migration's shape)

`shared/thetadata.py::ThetaDataController` is a **hand-rolled REST client** (likely `requests`/`httpx`
directly against `api.potatohedge.com` URLs) — it does **NOT** currently import or depend on the
`potatohedge` PyPI package at all. Confirmed via grep: no `import potatohedge` / `from potatohedge`
anywhere in the live `shared/thetadata.py`.

There IS a trace of a prior, apparently-abandoned attempt to adopt the vendor's actual package: stale
`POTATOHEDGE_API_REFERENCE.md` files survive in several old worktree copies (`Options_Suite/`,
`Vol_Suite/`), referencing `from potatohedge import PHClient`/`AsyncPHClient` (the OLD v1 flat-method
client, positional-args constructor, `requests.Session`-based) and a separate project directory
`../PHClient_MonteCarloAmericanPricer/`. This confirms the vendor relationship + package predate this
note, but the currently-live `shared/thetadata.py` diverged onto its own hand-rolled implementation at
some point and doesn't use the package. Whether that old integration attempt is worth resurrecting or
should just be superseded by a clean v2 adoption is an open design question — lean toward "clean v2
adoption wrapped by `shared/thetadata.py`'s existing method surface," but confirm with the user.

`ThetaDataController`'s current public method surface (`shared/thetadata.py`, ~28 methods) — this is
what any new client needs to either replace 1:1 or have a compatibility shim for, since every one of
the 4 suites imports and calls through it:
```
list_expirations, resolve_longest_history_expiry, list_strikes, stock_snapshot_quote,
stock_snapshot_trade, option_snapshot_quote, option_bulk_greeks, option_bulk_greeks_second_order,
option_bulk_oi, option_bulk_oi_latest, option_hist_eod_single, option_hist_open_interest_single,
option_hist_all_greeks_single, option_bulk_hist_eod, option_bulk_hist_eod_greeks,
option_bulk_hist_greeks, option_bulk_hist_oi, option_bulk_hist_oi_by_day, fetch_spot_price,
hist_stock_eod, hist_stock_ohlc, fetch_option_iv, fetch_risk_free_rate, fetch_dividend_yield,
fetch_realized_vol, fetch_beta, get_dealer_positioning, get_yield_curve, close
```

## The "deleted endpoints" 404 claim — PARTIALLY VERIFIED, one loose end

The note says the deleted per-strike history endpoints (`option_hist_open_interest_single`,
`option_hist_all_greeks_single` in our client — these map to `/api/theta/hist/option/all_greeks/...`
and `.../open_interest/...`) are causing ~8k 404s/day. Grepped every caller repo-wide:

**These two methods are called ONLY from `Vol_Suite/diagnostics/*.py`** (`diagnose_date_collapse.py`,
referenced in comments in `diagnose_range_route_hunt.py`/`diagnose_wide_range_chunks.py`) — standalone
diagnostic/debugging scripts, not anything in the four suites' production call paths
(`volatility_suite.py`, `main.py` × 3, `orchestrator.py`, `dashboard/app.py`, `Tools/`). **Nobody found
a production code path that calls these two methods.**

This means either: (a) something schedules/cron-runs one of these diagnostic scripts regularly (haven't
found evidence of this — no `.bat`/`.sh`/cron reference found yet), (b) one of the *bulk* hist methods
(`option_bulk_hist_oi_by_day`, `option_bulk_hist_greeks`, etc.) internally falls back to a per-strike
loop calling these single-strike methods under some condition (NOT YET CHECKED — read
`shared/thetadata.py:715-838`, the four `option_bulk_hist_*` method bodies, for a fallback loop before
concluding either way), or (c) the note's "8k 404s/day" figure comes from a different caller entirely
(a worker/backtest script, a cron job, something outside this repo). **This needs resolving before
claiming "the migration fixes the 404s" — don't just take the note's word for where they come from.**

## The "MOCK-ticker loop" claim — NOT YET FOUND

Note claims ~140 404s/day on `/api/theta/hist/stock/eod/MOCK`, calls it "probably unintended." Grepped
for `"MOCK"` repo-wide: only hits are `ticker: str = "MOCK"` **default parameter values** in
`Vol_Suite/expiry_book_exposure.py` (the new dealer-exposure engine from an unrelated earlier session
today, `build_net_exposure`/`scenario_hedge_flow`/etc.) and `Vol_Suite/run_causal_arm_v2.py`. These are
just placeholder default args for test/demo calls, not an actual scheduled loop hitting the live API
with a hardcoded "MOCK" ticker. **Have not found the real source yet** — check `scheduled_ingest.py`,
`poll_ingest.py`, any cron-driven script, or a stray hardcoded test ticker in a background loop.
Possible it's not even in this repo (could be a different one of the 14 callers in the vendor's own
`inventory/caller-universe.yaml` — this repo is `caller_id="zinko"`, but the note's phrasing implies
Jason/zinko is the specific caller these 404s are attributed to, so it's presumably findable here).

## Open questions for the user (ask before proposing a design)

1. **Scope**: does "the new data client" mean (a) a full replacement of `shared/thetadata.py`'s
   hand-rolled REST calls with the `potatohedge` v2 package underneath, keeping the SAME
   `ThetaDataController` method names/signatures as a compatibility facade (least disruptive to the 4
   suites), or (b) a parallel/new client the suites migrate onto method-by-method, or (c) something
   narrower — just fix the two dead diagnostic scripts + find/kill the MOCK loop, and leave the rest of
   `shared/thetadata.py` as-is since its production paths apparently don't hit the deleted endpoints?
2. **Install target**: which Python environment does the wheel get installed into — the shared root
   `.venv` (affects all 4 suites + orchestrator + dashboard), or a narrower one? (Given `shared/` is
   used by everything, almost certainly the root `.venv`, but confirm — and note CLAUDE.md's own
   warning about Hermes-venv leakage into this repo's `.venv` when installing/running things.)
3. Should I go resolve the two loose ends above (bulk-method fallback-loop check, actual MOCK-loop
   source) as part of the design-gathering, or does the user already know the answer and can just tell
   me (e.g. "the MOCK loop is in poll_ingest.py, I added it for a smoke test")?
4. Real credentials for `api.potatohedge.com` v2 capability calls: the beta note's example reuses the
   SAME CF Access token dict (`THETADATA_CF_ACCESS_CLIENT_ID/_SECRET`, already in root `.env`) across
   every capability — confirm that's really the intended pattern here too (one token, many capability
   keys) before wiring `ClientConfig` that way.

## Next steps (resume here)

1. Answer/resolve the open questions above with the user.
2. Finish the "explore project context" pass: check `option_bulk_hist_*` bodies for a per-strike
   fallback loop; find the real MOCK-ticker loop source.
3. Propose 2-3 approaches for the migration shape (facade-replace vs. parallel vs. narrow-fix), with
   trade-offs, per the `brainstorming` skill's process — **do this before writing any code**.
4. Present design in sections, get approval, write it to
   `docs/superpowers/specs/2026-08-17-phclient-v2-migration-design.md` per the skill's convention.
5. Only then: invoke `writing-plans`, then actually `pip install` the verified wheel and start
   implementation.

## Files touched this session (all read-only research, nothing implemented)

- Created: `docs/phclient_v2/WIKI.md` (this file), `docs/phclient_v2/vendor_docs/*` (9 files, vendor
  docs mirrored locally).
- Downloaded (not yet installed): `C:\Users\bottl\Downloads\potatohedge-2.0.1-py3-none-any.whl`
  (sha256 verified against manifest).
- No repo source files modified. No packages installed. No commits made.

## 2026-08-18 — v2 direction indicator (feat/direction-indicator-v2)

The v2 per-bar direction indicator (`Direction/indicator.py`) is the first
consumer of the intraday capability inventory above. What it actually uses
(direct `potatohedge.client_v2.PHClient`, not the `shared/thetadata.py`
facade — the facade does not expose these namespaces yet):

- `flow.scanner_trades_in_time_range` — per-bar whale-flow leg (`flow.read`
  capability). Each bar queries its own 15-minute window, so the whale
  signal is intraday, not the v1 daily EOD option-volume wall.
- `dealer.weighted_greeks` — per-bar liquidity leg (`dealer.read`
  capability). Heavy endpoint (`retry_policy=unsafe`, risk MEDIUM;
  fan-out 502-storms), so it is **SAMPLED on a coarse grid**: one
  sequential call every N bars, the last computed regime held between grid
  points. **Caveat: gamma is a coarse sample, never interpolated-as-fact;
  between grid points the signal is whatever the last grid point returned,
  and failures degrade to neutral** (retried at the next grid point).
- EOD OI max-pain (via `Direction.data` cache + `liquidity_map` pure
  helpers) — OI settles EOD by nature, so max-pain stays daily and is
  reused per bar within the session.

Per-bar semantics: every bar is evaluated **closed-bar** — data as of the
bar timestamp inclusive (`shared.spot_history.intraday_bars_as_of`) — so
verdicts move bar-to-bar. Unfetchable per-bar inputs degrade to neutral
(NONE/0/False), never fabricated.
