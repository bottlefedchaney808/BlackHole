# GENERATED FILE - DO NOT EDIT (SPEC 9.3, 9.4).
# generator: phclient-semantic-generator/1
# product_schema: phclient-api-reference/v1
# manifest: src/potatohedge/_generated/semantic/manifest.json

## dealer.greek_exposures

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

## dealer.positioning

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

## dealer.positions

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

**Operational query-size warning:** Unenforced date span and positioning_days may produce large per-contract responses.

This is an operational query-size hazard, not a sensitive-data or separate-key boundary.

## dealer.regime_history

Get Regime History

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"start_date","required":true,"schema":{"description":"Start date YYYYMMDD","title":"Start Date","type":"string"}}

parameter_description: {"location":"query","name":"end_date","required":true,"schema":{"description":"End date YYYYMMDD","title":"End Date","type":"string"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/regime-history/{ticker}","semantic_kind":"history","temporal_parameters":[{"location":"query","name":"start_date","required":true,"schema":{"description":"Start date YYYYMMDD","title":"Start Date","type":"string"}},{"location":"query","name":"end_date","required":true,"schema":{"description":"End date YYYYMMDD","title":"End Date","type":"string"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## dealer.summary

Get Dealer Summary

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"root","required":true,"schema":{"description":"Root symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date for summary (YYYYMMDD)","title":"Date"}}

parameter_description: {"location":"query","name":"positioning_days","required":false,"schema":{"default":20,"description":"Days for positioning calculation","title":"Positioning Days","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/dealer/summary/{root}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date for summary (YYYYMMDD)","title":"Date"}},{"location":"query","name":"positioning_days","required":false,"schema":{"default":20,"description":"Days for positioning calculation","title":"Positioning Days","type":"integer"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

**Operational query-size warning:** Single-root, single-date aggregate; positioning_days is unenforced and may increase query cost.

This is an operational query-size hazard, not a sensitive-data or separate-key boundary.

## dealer.weighted_greeks

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

## dealer.weighted_greeks_summary

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

## dealer.zero_dte_charm

Get Dealer Charm Exposure

boundary_summary: {"auth":"dealer.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Stock ticker","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"format":"date","type":"string"},{"type":"null"}],"description":"Date (default: today)","title":"Signal Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/CharmResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/zero-dte/charm/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"format":"date","type":"string"},{"type":"null"}],"description":"Date (default: today)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.analysis

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

## flow.anomalies

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

## flow.bearish_alerts

Get Bearish Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/BearishAlertsResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/bearish-alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.compound_bearish

Get Compound Bearish

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/compound-bearish","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.entry_price

Get Entry Price Distribution

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Stock ticker symbol","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Maturity date (YYYY-MM-DD), default yesterday","title":"Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200}]}}

temporal_summary: {"path":"/api/swap-flow/entry-price/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Maturity date (YYYY-MM-DD), default yesterday","title":"Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.iso_sweep_aggregate

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

## flow.long_alerts

Get Long Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/LongAlertsResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/long-alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.maturity_alerts

Get Maturity Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Max tickers to scan","maximum":100,"minimum":1,"title":"Limit","type":"integer"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/swap-flow/maturity-alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.momentum

Flow Momentum Comparison Across Multiple Windows

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Trading date (YYYYMMDD). Defaults to latest available option-trade date.","title":"Date"}}

parameter_description: {"location":"query","name":"root","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Filter by specific ticker (optional)","title":"Root"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":20,"description":"Number of tickers to return","maximum":500,"minimum":1,"title":"Limit","type":"integer"}}

parameter_description: {"location":"query","name":"min_volume","required":false,"schema":{"default":0,"description":"Minimum total volume across all windows","minimum":0,"title":"Min Volume","type":"integer"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return CSV format if true, JSON if false","title":"Use Csv","type":"boolean"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"items":{"$ref":"#/components/schemas/FlowMomentumItem"},"title":"Response 200 Get Flow Momentum Api Flow Momentum Get","type":"array"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/flow/momentum","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Trading date (YYYYMMDD). Defaults to latest available option-trade date.","title":"Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.non_roll

Get non-roll termination analysis

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Ticker symbol","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD, default: yesterday)","title":"Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200}]}}

temporal_summary: {"path":"/api/swap-flow/non-roll/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD, default: yesterday)","title":"Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.option_volume_analysis

Option Volume Analysis

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"root","required":true,"schema":{"description":"Requested ticker/root symbol","title":"Root","type":"string"}}

parameter_description: {"location":"query","name":"lookback_sessions","required":false,"schema":{"default":60,"description":"Number of latest trading sessions to analyze","maximum":120,"minimum":5,"title":"Lookback Sessions","type":"integer"}}

parameter_description: {"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Optional max session date (YYYYMMDD or YYYY-MM-DD)","title":"End Date"}}

parameter_description: {"location":"query","name":"source_roots","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Optional comma-separated option roots already resolved by /api/market/ticker-variants/{root}. Defaults to the requested root only.","title":"Source Roots"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/flow/option-volume-analysis/{root}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"lookback_sessions","required":false,"schema":{"default":60,"description":"Number of latest trading sessions to analyze","maximum":120,"minimum":5,"title":"Lookback Sessions","type":"integer"}},{"location":"query","name":"end_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Optional max session date (YYYYMMDD or YYYY-MM-DD)","title":"End Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.recent

Recent Option Flow with Momentum Windows

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"window","required":false,"schema":{"$ref":"#/components/schemas/FlowWindow","default":"30","description":"Lookback window in minutes (5, 15, 30, 60)"}}

parameter_description: {"location":"query","name":"limit","required":false,"schema":{"default":50,"description":"Number of results to return","maximum":1000,"minimum":1,"title":"Limit","type":"integer"}}

parameter_description: {"location":"query","name":"sort_by","required":false,"schema":{"$ref":"#/components/schemas/routes__api_router_flow__SortBy","default":"premium","description":"Sort by premium or volume"}}

parameter_description: {"location":"query","name":"direction","required":false,"schema":{"anyOf":[{"$ref":"#/components/schemas/routes__api_router_flow__Direction"},{"type":"null"}],"description":"Filter for bullish/bearish flow","title":"Direction"}}

parameter_description: {"location":"query","name":"use_csv","required":false,"schema":{"default":true,"description":"Return CSV format if true, JSON if false","title":"Use Csv","type":"boolean"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"items":{"$ref":"#/components/schemas/FlowRecentItem"},"title":"Response 200 Get Recent Flow Api Flow Recent Get","type":"array"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/HTTPValidationError"},"status_code":422}]}}

temporal_summary: {"path":"/api/flow/recent","semantic_kind":"snapshot","temporal_parameters":[{"location":"query","name":"window","required":false,"schema":{"$ref":"#/components/schemas/FlowWindow","default":"30","description":"Lookback window in minutes (5, 15, 30, 60)"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.s1

Get S1 Signal

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Stock ticker symbol","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD), default yesterday","title":"Signal Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/S1SignalResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400}]}}

temporal_summary: {"path":"/api/swap-flow/s1/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD), default yesterday","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.s1_alerts

Get S1 Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/S1AlertsResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400}]}}

temporal_summary: {"path":"/api/swap-flow/s1/alerts","semantic_kind":"collection","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD)","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.s2

Get S2 Signal

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}

parameter_description: {"location":"path","name":"ticker","required":true,"schema":{"description":"Stock ticker symbol","title":"Ticker","type":"string"}}

parameter_description: {"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD), default yesterday","title":"Signal Date"}}

response_summary: {"contract":{"statuses":[{"content_type":"application/json","schema":{"$ref":"#/components/schemas/S2SignalResponse"},"status_code":200},{"content_type":"application/json","schema":{"$ref":"#/components/schemas/ErrorResponse"},"status_code":400}]}}

temporal_summary: {"path":"/api/swap-flow/s2/{ticker}","semantic_kind":"canonical","temporal_parameters":[{"location":"query","name":"signal_date","required":false,"schema":{"anyOf":[{"type":"string"},{"type":"null"}],"description":"Date (YYYY-MM-DD), default yesterday","title":"Signal Date"}}]}

policy_classification (policy metadata, not an API-stability guarantee): {"confidence":["authoritative"],"release_class":["ordinary"]}

## flow.s2_alerts

Get S2 Alerts

boundary_summary: {"auth":"flow.read","exposure":"ordinary","mutability":"read","retry":"safe","risk":"low","stability":"stable"}


[output truncated at 50000 of 279274 characters. Pass a larger max_chars (default 50000) to see more, or use read_page with a ref_id to focus on a smaller section.]