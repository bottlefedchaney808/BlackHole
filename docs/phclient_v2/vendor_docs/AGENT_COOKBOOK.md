# PHClient v2 — Agent Cookbook

**Recipes limited to conformance-verified methods; expands as verification coverage grows.**
Every method below is one of the 19 routes marked passing in
`conformance/results/triage-2026-07-24.md`. Params use real names/locations from the generated
descriptors. Client construction, auth, and the `ResponseEnvelope`/error shapes are in
[AGENT_QUICKSTART.md](AGENT_QUICKSTART.md). All calls return an envelope; read the payload from
`env.data`. Examples are synchronous; the async client awaits the identical signatures.

Dates are `YYYYMMDD`. EOD data for the current day is not available — use the prior trading day
for `signal_date` / `date` params.

---

## 1. Zero-DTE intraday positioning read

Intent: assemble a same-day options positioning picture for a ticker — session context, dealer
gamma wall, expected move, and support/resistance brackets.

```python
sess = client.market.market_session_info(root="SPY")
wall = client.support_resistance.zero_dte_gamma_wall(ticker="SPY", signal_date="20260723")
strad = client.options.straddle_intraday(ticker="SPY", date="20260723", interval="MINUTE5")
sr = client.support_resistance.snapshot(ticker="SPY")
```

Read:
- `sess.data` — session mode and the resolved option series expiry to anchor the day.
- `wall.data` — gamma-wall / pin strike(s).
- `strad.data` — intraday straddle / breakeven series (the market-implied expected move).
- `sr.data` — S/R levels around spot.

Composition: `market_session_info` fixes which session and expiry you are reasoning about;
`zero_dte_gamma_wall` gives the dealer pin; `straddle_intraday` gives the expected move around
it; `snapshot` brackets that move with S/R levels. Only `MINUTE5`/`MINUTE15` intervals are
supported by the straddle writer.

---

## 2. Swap-flow directional sweep

Intent: cross-reference validated swap-flow signals against directional and maturity alerts for
a date, then rank by top movers.

```python
d = "20260723"
validated = client.flow.validated_signals(signal_date=d, limit=50)
bearish = client.flow.bearish_alerts(signal_date=d, limit=50)
longs = client.flow.long_alerts(signal_date=d, limit=50)
compound = client.flow.compound_bearish(signal_date=d, limit=50)
maturity = client.flow.maturity_alerts(signal_date=d, limit=50)
movers = client.flow.top_movers(signal_date=d, limit=25)
```

Read:
- `validated.data` — the validated signal set (the trustworthy baseline).
- `bearish.data` / `longs.data` — directional alerts to split the set.
- `compound.data` — higher-conviction stacked bearish signals.
- `maturity.data` — maturity/roll-driven alerts (filter out expiry noise).
- `movers.data` — ranking to prioritize names.

Composition: start from `validated_signals`, partition by direction (`bearish_alerts` vs
`long_alerts`), escalate on `compound_bearish`, use `maturity_alerts` to discount roll-driven
entries, and order the survivors by `top_movers`. All six accept the same `signal_date`/`limit`.

---

## 3. Signal validity + strategy registry gate

Intent: confirm that a per-ticker signal is worth acting on by checking it against the active
strategy registry and the day's fired signals.

```python
registry = client.signals.strategies(is_active=True)
fired = client.signals.for_date(signal_date="20260723")
valid = client.signals.validity(ticker="AAPL", signal_type="bullish")
alerts = client.signals.compound_alert_status(date="20260723")
```

Read:
- `registry.data` — active strategy names/types (the allowlist).
- `fired.data` — signals that fired on the date.
- `valid.data` — validity of current signals for the ticker (optionally filter with
`strategies="<comma,separated>"` from the registry).
- `alerts.data` — consumer-facing compound-alert status (a status read; does not fire alerts).

Composition: `strategies` enumerates what can legitimately fire; `for_date` shows what did;
`validity` confirms the specific ticker; `compound_alert_status` is the current consumer state.
Pass registry-derived names into `validity(strategies=...)` to scope the check.

---

## 4. Cross-ticker correlation + dealer-regime context

Intent: find co-moving names by a positioning metric, then explain each with its dealer regime
history.

```python
corr = client.market.correlation_matrix(
tickers="SPY,QQQ,IWM", metric="gamma", window=20, method="pearson")
spy_reg = client.dealer.regime_history(ticker="SPY", start_date="20260601", end_date="20260723")
qqq_reg = client.dealer.regime_history(ticker="QQQ", start_date="20260601", end_date="20260723")
```

Read:
- `corr.data` — the correlation matrix + notable pairs (by `metric`, over `window` trading days).
- `spy_reg.data` / `qqq_reg.data` — dealer positioning regime timeseries per name. Dealer
exposures are position-weighted; do not OI-weight them, and read vanna signs ticker-aware
(`-vanna = bullish`, except IWM `+vanna = bullish`).

Composition: `correlation_matrix` surfaces which names move together on the chosen metric
(`returns|gamma|vanna|delta|charm|flow`, `window` ∈ `5|10|20|60`); `regime_history` (path
`ticker`, required `start_date`/`end_date`) explains the dealer-positioning regime driving each
leg. Run one `regime_history` per name of interest from the matrix.

---

## 5. Chain → IV surface → contract drill-down

Intent: pull the options universe for a ticker, contextualize with the IV surface, then inspect
a single contract by its OCC symbol.

```python
chain = client.options.options_chain(ticker="SPY", date="latest")
surface = client.volatility.iv_surface_snapshot(ticker="SPY")
# take an OCC symbol from chain.data, then:
contract = client.options.options_contract(occ="SPY260919C00500000")
```

Read:
- `chain.data` — EOD chain contracts, including OCC identifiers and per-contract fields.
- `surface.data` — IV surface snapshot for the same underlying.
- `contract.data` — the single contract keyed by the OCC symbol from step 1.

Composition: `options_chain` (`date` accepts `latest`, `YYYYMMDD`, or `YYYY-MM-DD`) yields the
contract set and OCC ids; `iv_surface_snapshot` frames the vol context; `options_contract`
(path `occ`) drills into one contract using an id read from the chain. The OCC symbol already
encodes the strike in thousandths — do not re-scale it.