# GENERATED FILE - DO NOT EDIT (SPEC 9.3, 9.4).
# generator: phclient-semantic-generator/1
# product_schema: phclient-client-reference/v1
# manifest: src/potatohedge/_generated/semantic/manifest.json

## Client Methods

| Namespace | Name | Endpoint ID | Query-size warning |
|---|---|---|---|
| `dealer` | `greek_exposures` | `dealer.greek_exposures` | — |
| `dealer` | `positioning` | `dealer.positioning` | — |
| `dealer` | `positions` | `dealer.positions` | Unenforced date span and positioning_days may produce large per-contract responses. |
| `dealer` | `regime_history` | `dealer.regime_history` | — |
| `dealer` | `summary` | `dealer.summary` | Single-root, single-date aggregate; positioning_days is unenforced and may increase query cost. |
| `dealer` | `weighted_greeks` | `dealer.weighted_greeks` | — |
| `dealer` | `weighted_greeks_summary` | `dealer.weighted_greeks_summary` | — |
| `dealer` | `zero_dte_charm` | `dealer.zero_dte_charm` | — |
| `flow` | `analysis` | `flow.analysis` | — |
| `flow` | `anomalies` | `flow.anomalies` | — |
| `flow` | `bearish_alerts` | `flow.bearish_alerts` | — |
| `flow` | `compound_bearish` | `flow.compound_bearish` | — |
| `flow` | `entry_price` | `flow.entry_price` | — |
| `flow` | `iso_sweep_aggregate` | `flow.iso_sweep_aggregate` | — |
| `flow` | `long_alerts` | `flow.long_alerts` | — |
| `flow` | `maturity_alerts` | `flow.maturity_alerts` | — |
| `flow` | `momentum` | `flow.momentum` | — |
| `flow` | `non_roll` | `flow.non_roll` | — |
| `flow` | `option_volume_analysis` | `flow.option_volume_analysis` | — |
| `flow` | `recent` | `flow.recent` | — |
| `flow` | `s1` | `flow.s1` | — |
| `flow` | `s1_alerts` | `flow.s1_alerts` | — |
| `flow` | `s2` | `flow.s2` | — |
| `flow` | `s2_alerts` | `flow.s2_alerts` | — |
| `flow` | `s3` | `flow.s3` | — |
| `flow` | `s3_alerts` | `flow.s3_alerts` | — |
| `flow` | `s4` | `flow.s4` | — |
| `flow` | `s4_alerts` | `flow.s4_alerts` | — |
| `flow` | `s5` | `flow.s5` | — |
| `flow` | `s5_alerts` | `flow.s5_alerts` | — |
| `flow` | `scanner_summary` | `flow.scanner_summary` | — |
| `flow` | `scanner_trades` | `flow.scanner_trades` | — |
| `flow` | `scanner_trades_in_time_range` | `flow.scanner_trades_in_time_range` | — |
| `flow` | `signal` | `flow.signal` | — |
| `flow` | `sweeps` | `flow.sweeps` | — |
| `flow` | `sweeps_intensity` | `flow.sweeps_intensity` | — |
| `flow` | `timeseries` | `flow.timeseries` | — |
| `flow` | `top_movers` | `flow.top_movers` | — |
| `flow` | `top_symbols` | `flow.top_symbols` | — |
| `flow` | `unusual` | `flow.unusual` | — |
| `flow` | `unusual_summary` | `flow.unusual_summary` | — |
| `flow` | `validated_signals` | `flow.validated_signals` | — |
| `market` | `bulk_snapshot_stock_ohlc` | `market.bulk_snapshot_stock_ohlc` | — |
| `market` | `bulk_snapshot_stock_quote` | `market.bulk_snapshot_stock_quote` | — |
| `market` | `correlation_matrix` | `market.correlation_matrix` | — |
| `market` | `earnings_calendar` | `market.earnings_calendar` | — |
| `market` | `eod_readiness` | `market.eod_readiness` | — |
| `market` | `hist_index_ohlc` | `market.hist_index_ohlc` | — |
| `market` | `hist_stock_quote` | `market.hist_stock_quote` | — |
| `market` | `hist_stock_trade` | `market.hist_stock_trade` | — |
| `market` | `hist_stock_trade_quote` | `market.hist_stock_trade_quote` | — |
| `market` | `index_eod` | `market.index_eod` | — |
| `market` | `index_price` | `market.index_price` | — |
| `market` | `is_market_open` | `market.is_market_open` | — |
| `market` | `last_index_price` | `market.last_index_price` | — |
| `market` | `last_trading_day` | `market.last_trading_day` | — |
| `market` | `market_schedule` | `market.market_schedule` | — |
| `market` | `market_session_info` | `market.market_session_info` | — |
| `market` | `next_trading_day` | `market.next_trading_day` | — |
| `market` | `snapshot_stock_ohlc` | `market.snapshot_stock_ohlc` | — |
| `market` | `snapshot_stock_trade` | `market.snapshot_stock_trade` | — |
| `market` | `stock_dividends` | `market.stock_dividends` | — |
| `market` | `stock_eod` | `market.stock_eod` | — |
| `market` | `stock_ohlc` | `market.stock_ohlc` | — |
| `market` | `stock_quote_at_time` | `market.stock_quote_at_time` | — |
| `market` | `stock_splits` | `market.stock_splits` | — |
| `market` | `stock_trade_at_time` | `market.stock_trade_at_time` | — |
| `market` | `ticker_variants` | `market.ticker_variants` | — |
| `market` | `trading_days` | `market.trading_days` | — |
| `market` | `yield_curve` | `market.yield_curve` | — |
| `news` | `company_news` | `news.company` | — |
| `news` | `market_news` | `news.market` | — |
| `options` | `bulk_hist_option_eod` | `options.bulk_hist_option_eod` | — |
| `options` | `bulk_hist_option_eod_greeks` | `options.bulk_hist_option_eod_greeks` | — |
| `options` | `bulk_hist_option_open_interest` | `options.bulk_hist_option_open_interest` | — |
| `options` | `bulk_snapshot_option_all_greeks` | `options.bulk_snapshot_option_all_greeks` | — |
| `options` | `bulk_snapshot_option_greeks` | `options.bulk_snapshot_option_greeks` | — |
| `options` | `bulk_snapshot_option_open_interest` | `options.bulk_snapshot_option_open_interest` | — |
| `options` | `bulk_snapshot_option_quote` | `options.bulk_snapshot_option_quote` | — |
| `options` | `greeks_timeseries` | `options.greeks_timeseries` | — |
| `options` | `hist_option_all_greeks` | `options.hist_option_all_greeks` | — |
| `options` | `hist_option_all_trade_greeks` | `options.hist_option_all_trade_greeks` | — |
| `options` | `hist_option_eod` | `options.hist_option_eod` | — |
| `options` | `hist_option_greeks` | `options.hist_option_greeks` | — |
| `options` | `hist_option_greeks_second_order` | `options.hist_option_greeks_second_order` | — |
| `options` | `hist_option_greeks_third_order` | `options.hist_option_greeks_third_order` | — |
| `options` | `hist_option_implied_volatility` | `options.hist_option_implied_volatility` | — |
| `options` | `hist_option_ohlc` | `options.hist_option_ohlc` | — |
| `options` | `hist_option_open_interest` | `options.hist_option_open_interest` | — |
| `options` | `hist_option_quote` | `options.hist_option_quote` | — |
| `options` | `hist_option_trade` | `options.hist_option_trade` | — |
| `options` | `hist_option_trade_greeks` | `options.hist_option_trade_greeks` | — |
| `options` | `hist_option_trade_greeks_second_order` | `options.hist_option_trade_greeks_second_order` | — |
| `options` | `hist_option_trade_greeks_third_order` | `options.hist_option_trade_greeks_third_order` | — |
| `options` | `hist_option_trade_quote` | `options.hist_option_trade_quote` | — |
| `options` | `list_option_contracts` | `options.list_option_contracts` | — |
| `options` | `list_roots` | `options.list_roots` | — |
| `options` | `oi_concentration` | `options.oi_concentration` | — |
| `options` | `option_quote_at_time` | `options.option_quote_at_time` | — |
| `options` | `option_trade_at_time` | `options.option_trade_at_time` | — |
| `options` | `options_chain` | `options.options_chain` | — |
| `options` | `options_contract` | `options.options_contract` | — |
| `options` | `snapshot_option_ohlc` | `options.snapshot_option_ohlc` | — |
| `options` | `snapshot_option_open_interest` | `options.snapshot_option_open_interest` | — |
| `options` | `snapshot_option_quote` | `options.snapshot_option_quote` | — |
| `options` | `snapshot_option_trade` | `options.snapshot_option_trade` | — |
| `options` | `straddle_intraday` | `options.straddle_intraday` | — |
| `recipes` | `chart_summaries` | `recipes.chart_summaries` | — |
| `recipes` | `macro_bundle` | `recipes.macro_bundle` | — |
| `recipes` | `news_bundle` | `recipes.news_bundle` | — |
| `recipes` | `quant_bundle` | `recipes.quant_bundle` | — |
| `recipes` | `ticker_bundle` | `recipes.ticker_bundle` | — |
| `regsho` | `alerts` | `regsho.alerts` | — |
| `regsho` | `correlation` | `regsho.correlation` | — |
| `regsho` | `current` | `regsho.current` | — |
| `regsho` | `ftd_history` | `regsho.ftd_history` | — |
| `regsho` | `options_correlation` | `regsho.options_correlation` | — |
| `regsho` | `threshold_history` | `regsho.threshold_history` | — |
| `regsho` | `watchlist` | `regsho.watchlist` | — |
| `signals` | `compound_alert_status` | `signals.compound_alert_status` | — |
| `signals` | `for_date` | `signals.for_date` | — |
| `signals` | `strategies` | `signals.strategies` | — |
| `signals` | `validity` | `signals.validity` | — |
| `support_resistance` | `composite_hedge` | `support_resistance.composite_hedge` | — |
| `support_resistance` | `flip_levels` | `support_resistance.flip_levels` | — |
| `support_resistance` | `history_intraday` | `support_resistance.history_intraday` | — |
| `support_resistance` | `hit_rate` | `support_resistance.hit_rate` | — |
| `support_resistance` | `position_matrix` | `support_resistance.position_matrix` | — |
| `support_resistance` | `positioning_context` | `support_resistance.positioning_context` | — |
| `support_resistance` | `quadrant_summary` | `support_resistance.quadrant_summary` | — |
| `support_resistance` | `regime` | `support_resistance.regime` | — |
| `support_resistance` | `snapshot` | `support_resistance.snapshot` | — |
| `support_resistance` | `tiers` | `support_resistance.tiers` | — |
| `support_resistance` | `zero_dte_gamma_wall` | `support_resistance.zero_dte_gamma_wall` | — |
| `volatility` | `iv_rank` | `volatility.iv_rank` | — |
| `volatility` | `iv_surface_snapshot` | `volatility.iv_surface_snapshot` | — |
| `volatility` | `probabilistic_envelope` | `volatility.probabilistic_envelope` | — |
| `volatility` | `surface_change` | `volatility.surface_change` | — |
| `volatility` | `surface_z_history` | `volatility.surface_z_history` | — |
| `volatility` | `term_structure` | `volatility.term_structure` | — |

## `client.dealer.greek_exposures` -> `dealer.greek_exposures`

Get Greek Exposures

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"start_date","required":true,"schema":{"description":"Start date (YYYYMMDD)","title":"Start Date","type":"string"}}

parameter_description: {"location":"query","name":"end_date","required":true,"schema":{"description":"End date (YYYYMMDD)","title":"End Date","type":"string"}}

parameter_description: {"location":"query","name":"greek_types","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Comma-separated Greek types to include (gamma,delta,vanna,charm). Default: all.","title":"Greek Types"}}

parameter_description: {"location":"query","name":"analysis_mode","required":false,"schema":{"default":"eod","description":"Analysis mode filter (default: eod)","title":"Analysis Mode","type":"string"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"additionalProperties":true,"title":"Response Get Greek Exposures Api Greeks Exposures Ticker Get","type":"object"},"status_code":200},{"content_type":"application/json","schema":{},"status_code":404},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/greeks/exposures/{ticker}","semantic_kind":"history","temporal_parameters":[{"location":"query","name":"start_date","required":true,"schema":{"description":"Start date (YYYYMMDD)","title":"Start Date","type":"string"}},{"location":"query","name":"end_date","required":true,"schema":{"description":"End date (YYYYMMDD)","title":"End Date","type":"string"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.positioning` -> `dealer.positioning`

Dealer Position Tracking

boundary_summary: {"auth":"dealer.bulk.read","exposure":"ordinary","mutability":"read","retry":"unsafe","risk":"medium","stability":"stable"}

parameter_description: {"location":"query","name":"root","required":false,"schema":{"description":"Filter by underlying symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"expiration","required":false,"schema":{"description":"Filter by expiration date (YYYYMMDD) in Eastern Time","title":"Expiration","type":"string"}}

parameter_description: {"location":"query","name":"strike","required":false,"schema":{"description":"Filter by strike price","title":"Strike","type":"number"}}

parameter_description: {"location":"query","name":"trade_right","required":false,"schema":{"description":"Filter by option right (C/P)","title":"Trade Right","type":"string"}}

parameter_description: {"location":"query","name":"start_date","required":false,"schema":{"description":"Start date (YYYYMMDD) in Eastern Time","title":"Start Date","type":"string"}}

parameter_description: {"location":"query","name":"end_date","required":false,"schema":{"description":"End date (YYYYMMDD) in Eastern Time","title":"End Date","type":"string"}}

parameter_description: {"location":"query","name":"latest_only","required":false,"schema":{"default":false,"description":"Return only the latest entry per contract","title":"Latest Only","type":"boolean"}}

parameter_description: {"location":"query","name":"level","required":false,"schema":{"default":"contract","description":"Aggregation level (contract, root, or date)","title":"Level","type":"string"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return results in CSV format","title":"Use Csv","type":"boolean"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/db/dealer_positioning","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"expiration","required":false,"schema":{"description":"Filter by expiration date (YYYYMMDD) in Eastern Time","title":"Expiration","type":"string"}},{"location":"query","name":"start_date","required":false,"schema":{"description":"Start date (YYYYMMDD) in Eastern Time","title":"Start Date","type":"string"}},{"location":"query","name":"end_date","required":false,"schema":{"description":"End date (YYYYMMDD) in Eastern Time","title":"End Date","type":"string"}},{"location":"query","name":"latest_only","required":false,"schema":{"default":false,"description":"Return only the latest entry per contract","title":"Latest Only","type":"boolean"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.positions` -> `dealer.positions`

Get Dealer Positions

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"root","required":true,"schema":{"description":"Root symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"expiration","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Expiration date (YYYYMMDD)","title":"Expiration"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return CSV format (list of lists)","title":"Use Csv","type":"boolean"}}

parameter_description: {"location":"query","name":"max_dte","required":false,"schema":{"anyOf":[{"maximum":365,"minimum":0,"type":"integer"},{"type":"null"}],"description":"Maximum days to expiration filter (0-365). Use for short-term/0DTE research.","title":"Max Dte"}}

parameter_description: {"location":"query","name":"strike","required":false,"schema":{"anyOf":[{"type":"number"},{"type":"null"}],"description":"Strike price","title":"Strike"}}

parameter_description: {"location":"query","name":"trade_right","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Trade right (C/P)","title":"Trade Right"}}

parameter_description: {"location":"query","name":"start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD)","title":"Start Date"}}

parameter_description: {"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD)","title":"End Date"}}

parameter_description: {"location":"query","name":"latest_only","required":false,"schema":{"default":false,"description":"Return only latest position per contract","title":"Latest Only","type":"boolean"}}

parameter_description: {"location":"query","name":"level","required":false,"schema":{"default":"contract","description":"Aggregation level: contract, strike, expiration, root","title":"Level","type":"string"}}

parameter_description: {"location":"query","name":"positioning_days","required":false,"schema":{"default":20,"description":"Days for positioning calculation","title":"Positioning Days","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/positions","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"expiration","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Expiration date (YYYYMMDD)","title":"Expiration"}},{"location":"query","name":"max_dte","required":false,"schema":{"anyOf":[{"maximum":365,"minimum":0,"type":"integer"},{"type":"null"}],"description":"Maximum days to expiration filter (0-365). Use for short-term/0DTE research.","title":"Max Dte"}},{"location":"query","name":"start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD)","title":"Start Date"}},{"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD)","title":"End Date"}},{"location":"query","name":"latest_only","required":false,"schema":{"default":false,"description":"Return only latest position per contract","title":"Latest Only","type":"boolean"}},{"location":"query","name":"positioning_days","required":false,"schema":{"default":20,"description":"Days for positioning calculation","title":"Positioning Days","type":"integer"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.regime_history` -> `dealer.regime_history`

Get Regime History

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"start_date","required":true,"schema":{"description":"Start date YYYYMMDD","title":"Start Date","type":"string"}}

parameter_description: {"location":"query","name":"end_date","required":true,"schema":{"description":"End date YYYYMMDD","title":"End Date","type":"string"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/regime-history/{ticker}","semantic_kind":"history","temporal_parameters":[{"location":"query","name":"start_date","required":true,"schema":{"description":"Start date YYYYMMDD","title":"Start Date","type":"string"}},{"location":"query","name":"end_date","required":true,"schema":{"description":"End date YYYYMMDD","title":"End Date","type":"string"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.summary` -> `dealer.summary`

Get Dealer Summary

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"root","required":true,"schema":{"description":"Root symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date for summary (YYYYMMDD)","title":"Date"}}

parameter_description: {"location":"query","name":"positioning_days","required":false,"schema":{"default":20,"description":"Days for positioning calculation","title":"Positioning Days","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/summary/{root}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date for summary (YYYYMMDD)","title":"Date"}},{"location":"query","name":"positioning_days","required":false,"schema":{"default":20,"description":"Days for positioning calculation","title":"Positioning Days","type":"integer"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.weighted_greeks` -> `dealer.weighted_greeks`

Get Dealer Weighted Greeks Vectorized

boundary_summary: {"auth":"dealer.bulk.read","exposure":"ordinary","mutability":"read","retry":"unsafe","risk":"medium","stability":"stable"}

parameter_description: {"location":"path","name":"root","required":true,"schema":{"description":"Option underlying symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"exp","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":0,"description":"Expiration filter (0 for all)","title":"Exp"}}

parameter_description: {"location":"query","name":"normalize","required":false,"schema":{"default":false,"description":"Normalize weighted Greeks to [-1, 1]","title":"Normalize","type":"boolean"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return CSV format (list of lists)","title":"Use Csv","type":"boolean"}}

parameter_description: {"location":"query","name":"include_oi","required":false,"schema":{"default":false,"description":"Include open interest data in response","title":"Include Oi","type":"boolean"}}

parameter_description: {"location":"query","name":"columns","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Comma-separated list of columns to include in response. Always includes: strike, expiration, right, date, root, ms_of_day. Example: columns=weighted_gamma,weighted_delta,weighted_vanna,weighted_charm,net_positioning","title":"Columns"}}

parameter_description: {"location":"query","name":"use_cache","required":false,"schema":{"anyOf":[{"type":"boolean"},{"type":"null"}],"description":"Use cache for historical data. Auto-enabled for non-realtime queries unless explicitly disabled.","title":"Use Cache"}}

parameter_description: {"location":"query","name":"use_calculated_greeks","required":false,"schema":{"default":true,"description":"Use server-side calculated Greeks (J\u00e4ckel IV + analytical formulas). False = ThetaData Greeks (legacy)","title":"Use Calculated Greeks","type":"boolean"}}

parameter_description: {"location":"query","name":"max_dte","required":false,"schema":{"anyOf":[{"maximum":365,"minimum":0,"type":"integer"},{"type":"null"}],"description":"Maximum days to expiration filter (0-365). Use for short-term/0DTE research.","title":"Max Dte"}}

parameter_description: {"location":"query","name":"start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD)","title":"Start Date"}}

parameter_description: {"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD)","title":"End Date"}}

parameter_description: {"location":"query","name":"positioning_start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD) for dealer positioning lookback","title":"Positioning Start Date"}}

parameter_description: {"location":"query","name":"positioning_end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD) for dealer positioning","title":"Positioning End Date"}}

parameter_description: {"location":"query","name":"positioning_days","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"[DEPRECATED] Days for positioning calculation. Use positioning_start_date/positioning_end_date instead","title":"Positioning Days"}}

parameter_description: {"location":"query","name":"interval","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Interval type: DAY (default), HOUR, MINUTE30, MINUTE15, MINUTE5, MINUTE","title":"Interval"}}

parameter_description: {"location":"query","name":"ms_of_day","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"Milliseconds from midnight ET for specific time bucket (intraday only)","title":"Ms Of Day"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/weighted-greeks/{root}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"exp","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":0,"description":"Expiration filter (0 for all)","title":"Exp"}},{"location":"query","name":"max_dte","required":false,"schema":{"anyOf":[{"maximum":365,"minimum":0,"type":"integer"},{"type":"null"}],"description":"Maximum days to expiration filter (0-365). Use for short-term/0DTE research.","title":"Max Dte"}},{"location":"query","name":"start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD)","title":"Start Date"}},{"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD)","title":"End Date"}},{"location":"query","name":"positioning_start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD) for dealer positioning lookback","title":"Positioning Start Date"}},{"location":"query","name":"positioning_end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD) for dealer positioning","title":"Positioning End Date"}},{"location":"query","name":"positioning_days","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"[DEPRECATED] Days for positioning calculation. Use positioning_start_date/positioning_end_date instead","title":"Positioning Days"}},{"location":"query","name":"interval","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Interval type: DAY (default), HOUR, MINUTE30, MINUTE15, MINUTE5, MINUTE","title":"Interval"}},{"location":"query","name":"ms_of_day","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"Milliseconds from midnight ET for specific time bucket (intraday only)","title":"Ms Of Day"}},{"location":"query","name":"use_cache","required":false,"schema":{"anyOf":[{"type":"boolean"},{"type":"null"}],"description":"Use cache for historical data. Auto-enabled for non-realtime queries unless explicitly disabled.","title":"Use Cache"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.weighted_greeks_summary` -> `dealer.weighted_greeks_summary`

Get Dealer Weighted Greeks Summary

boundary_summary: {"auth":"dealer.bulk.read","exposure":"ordinary","mutability":"read","retry":"unsafe","risk":"medium","stability":"stable"}

parameter_description: {"location":"path","name":"root","required":true,"schema":{"description":"Option underlying symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"exp","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":0,"description":"Expiration filter (0 for all)","title":"Exp"}}

parameter_description: {"location":"query","name":"use_calculated_greeks","required":false,"schema":{"default":true,"description":"Use calculated Greeks","title":"Use Calculated Greeks","type":"boolean"}}

parameter_description: {"location":"query","name":"start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD)","title":"Start Date"}}

parameter_description: {"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD)","title":"End Date"}}

parameter_description: {"location":"query","name":"positioning_start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Positioning start date (YYYYMMDD)","title":"Positioning Start Date"}}

parameter_description: {"location":"query","name":"positioning_end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Positioning end date (YYYYMMDD)","title":"Positioning End Date"}}

parameter_description: {"location":"query","name":"positioning_days","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"Days for positioning lookback","title":"Positioning Days"}}

parameter_description: {"location":"query","name":"interval","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Interval: DAY, HOUR, MINUTE30, MINUTE15, MINUTE5, MINUTE","title":"Interval"}}

parameter_description: {"location":"query","name":"ms_of_day","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"Milliseconds from midnight ET","title":"Ms Of Day"}}

parameter_description: {"location":"query","name":"use_cache","required":false,"schema":{"anyOf":[{"type":"boolean"},{"type":"null"}],"description":"Use cache","title":"Use Cache"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/weighted-greeks/{root}/summary","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"exp","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":0,"description":"Expiration filter (0 for all)","title":"Exp"}},{"location":"query","name":"start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Start date (YYYYMMDD)","title":"Start Date"}},{"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"End date (YYYYMMDD)","title":"End Date"}},{"location":"query","name":"positioning_start_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Positioning start date (YYYYMMDD)","title":"Positioning Start Date"}},{"location":"query","name":"positioning_end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Positioning end date (YYYYMMDD)","title":"Positioning End Date"}},{"location":"query","name":"positioning_days","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"Days for positioning lookback","title":"Positioning Days"}},{"location":"query","name":"interval","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Interval: DAY, HOUR, MINUTE30, MINUTE15, MINUTE5, MINUTE","title":"Interval"}},{"location":"query","name":"ms_of_day","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"description":"Milliseconds from midnight ET","title":"Ms Of Day"}},{"location":"query","name":"use_cache","required":false,"schema":{"anyOf":[{"type":"boolean"},{"type":"null"}],"description":"Use cache","title":"Use Cache"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.dealer.zero_dte_charm` -> `dealer.zero_dte_charm`

Get Dealer Charm Exposure

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Stock ticker","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"format":"date","type":"string"},{"type":"null"}],"description":"Date (default: today)","title":"Signal Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/CharmResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/zero-dte/charm/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"format":"date","type":"string"},{"type":"null"}],"description":"Date (default: today)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.analysis` -> `flow.analysis`

Options Flow Analysis with Trade Size Classification

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"root","required":true,"schema":{"description":"Option underlying symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date for analysis (YYYYMMDD)","title":"Date"}}

parameter_description: {"location":"query","name":"exp","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":0,"description":"Expiration date as YYYYMMDD, 0 for all","title":"Exp"}}

parameter_description: {"location":"query","name":"aggregate_by","required":false,"schema":{"default":"trade_size","description":"Aggregation level","enum":["trade_size","strike","expiry"],"title":"Aggregate By","type":"string"}}

parameter_description: {"location":"query","name":"include_execution_metrics","required":false,"schema":{"default":true,"description":"Include execution quality metrics","title":"Include Execution Metrics","type":"boolean"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return CSV format if true, JSON if false","title":"Use Csv","type":"boolean"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"items":{"$ref":"#/components/schemas/FlowAnalysisItem"},"title":"Response 200 Get Flow Analysis Api Flow Analysis Root Get","type":"array"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/flow/analysis/{root}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date for analysis (YYYYMMDD)","title":"Date"}},{"location":"query","name":"exp","required":false,"schema":{"anyOf":[{"type":"integer"},{"type":"null"}],"default":0,"description":"Expiration date as YYYYMMDD, 0 for all","title":"Exp"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.anomalies` -> `flow.anomalies`

Flow Anomaly Detection (Z-Score)

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Target date (YYYYMMDD). Defaults to the EOD-safe prior trading day (skips weekends/holidays).","title":"Date"}}

parameter_description: {"location":"query","name":"root","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Ticker symbol. Default: all tickers.","title":"Root"}}

parameter_description: {"location":"query","name":"lookback","required":false,"schema":{"default":20,"description":"Rolling window in trading days","maximum":252,"minimum":5,"title":"Lookback","type":"integer"}}

parameter_description: {"location":"query","name":"min_severity","required":false,"schema":{"default":"normal","description":"Minimum severity","enum":["normal","elevated","extreme"],"title":"Min Severity","type":"string"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max results","maximum":500,"minimum":1,"title":"Limit","type":"integer"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return CSV format","title":"Use Csv","type":"boolean"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"items":{"$ref":"#/components/schemas/FlowAnomalyItem"},"title":"Response 200 Get Flow Anomalies Api Flow Anomalies Get","type":"array"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/flow/anomalies","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Target date (YYYYMMDD). Defaults to the EOD-safe prior trading day (skips weekends/holidays).","title":"Date"}},{"location":"query","name":"lookback","required":false,"schema":{"default":20,"description":"Rolling window in trading days","maximum":252,"minimum":5,"title":"Lookback","type":"integer"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.bearish_alerts` -> `flow.bearish_alerts`

Get Bearish Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/BearishAlertsResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/bearish-alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.compound_bearish` -> `flow.compound_bearish`

Get Compound Bearish

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/compound-bearish","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.entry_price` -> `flow.entry_price`

Get Entry Price Distribution

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Stock ticker symbol","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Maturity date (YYYY-MM-DD), default yesterday","title":"Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200}]}}

temporal_summary: {"path":"/api/swap-flow/entry-price/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Maturity date (YYYY-MM-DD), default yesterday","title":"Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.iso_sweep_aggregate` -> `flow.iso_sweep_aggregate`

ISO Sweep Print Aggregate (OPRA codes 125-128)

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"experimental"}

parameter_description: {"location":"query","name":"ticker","required":true,"schema":{"description":"Underlying symbol (required)","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Trading date (YYYYMMDD or YYYY-MM-DD). Defaults to the EOD-safe prior trading day (skips weekends/holidays).","title":"Date"}}

parameter_description: {"location":"query","name":"window","required":false,"schema":{"$ref":"#/components/schemas/IsoSweepWindow","default":"day","description":"Aggregation window. P1: 'day' only. OQ1 still open."}}

parameter_description: {"location":"query","name":"sentiment","required":false,"schema":{"$ref":"#/components/schemas/IsoSweepSentimentFilter","default":"all","description":"Sentiment filter (bullish/bearish/neutral/all)"}}

parameter_description: {"location":"query","name":"min_premium","required":false,"schema":{"default":0.0,"description":"Minimum per-print gross premium ($)","minimum":0,"title":"Min Premium","type":"number"}}

parameter_description: {"location":"query","name":"min_size","required":false,"schema":{"default":0,"description":"Minimum per-print contract size","minimum":0,"title":"Min Size","type":"integer"}}

parameter_description: {"location":"query","name":"include_neutral","required":false,"schema":{"default":true,"description":"Include neutral-sentiment prints in totals + by_sentiment","title":"Include Neutral","type":"boolean"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/FlowIsoSweepAggregateResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/flow/iso-sweep-aggregate","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Trading date (YYYYMMDD or YYYY-MM-DD). Defaults to the EOD-safe prior trading day (skips weekends/holidays).","title":"Date"}},{"location":"query","name":"window","required":false,"schema":{"$ref":"#/components/schemas/IsoSweepWindow","default":"day","description":"Aggregation window. P1: 'day' only. OQ1 still open."}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.long_alerts` -> `flow.long_alerts`

Get Long Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/LongAlertsResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/long-alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.maturity_alerts` -> `flow.maturity_alerts`

Get Maturity Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/maturity-alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## `client.flow.momentum` -> `flow.momentum`

Flow Momentum Comparison Across Multiple Windows

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Trading date (YYYYMMDD). Defaults to latest available option-trade date.","title":"Date"}}

parameter_description: {"location":"query","name":"root","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Filter by specific ticker (optional)","title":"Root"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":20,"description":"Number of tickers to return","maximum":500,"minimum":1,"title":"Limit","type":"integer"}}

parameter_description: {"location":"query","name":"min_volume","required":false,"schema":{"default":0,"description":"Minimum total volume across all windows","minimum":0,"title":"Min Volume","type":"integer"}}


[output truncated at 50000 of 293798 characters. Pass a larger max_chars (default 50000) to see more, or use read_page with a ref_id to focus on a smaller section.]