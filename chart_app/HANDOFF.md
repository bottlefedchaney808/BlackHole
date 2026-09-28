# chart_app handoff — 2026-09-18

Written so Grok / Hermes / a future Claude can pick this up cold. Read this
before changing anything in `chart_app/`.

Everything below was **measured on this machine on 2026-09-18**, not assumed.
Where a number appears, the command that produced it is given.

---

## 1. What this is

A standalone chart window at **http://127.0.0.1:8791/**. Not TradingView, not
the dashboard on `:8787`. Jason looks at pixels; an agent reads
`GET /api/state`. They must show the same numbers.

```bash
cd E:/BlackHole_Investments/BlackHole
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791
```

Only one instance may bind 8791. If a restart fails with `WinError 10048`,
the previous uvicorn is still alive — find it with
`Get-NetTCPConnection -LocalPort 8791 -State Listen` and check the command
line before killing it. **Do not kill a healthy 8791 because `curl` returned
`000`** (git-bash curl does that against this server routinely).

The page caches its JS aggressively. After editing anything under
`static/js/`, a plain reload is not enough — **Ctrl+Shift+R**.

---

## 2. Module map

| file | what it owns |
|---|---|
| `server.py` | FastAPI routes, deferred flow fetching, the ThetaData flow pull |
| `ingest.py` | fetch → `BarCache`; routes crypto away from ThetaData |
| `bar_cache.py` | SQLite bar store + last-viewed session |
| `snapshot.py` | assembles the whole `/api/state` payload |
| `score_engine.py` | classic overlays, oscillators, the legacy 0–5 leg score |
| **`elmo.py`** | **NEW** entropy / Hui-Heubel liquidity / ALMA |
| **`signal_engine.py`** | **NEW** the signed conviction line + position state machine |
| **`flow_stamp.py`** | whale binning, magnitude, adaptive threshold, coverage |
| `flow_pane.py` | per-bar call/put/net premium (+ cumulative) |
| **`split_guard.py`** | **NEW** detects/back-adjusts unadjusted stock splits |
| **`crypto_source.py`** | **NEW** BTC/ETH/SOL perps and spot via OKX/Coinbase |
| **`backtest.py`** | **NEW** next-bar-open backtester, sweep, walk-forward, permutation |
| **`backtest_runner.py`** | **NEW** CLI: pull data, run every stage, write a report |
| `static/js/{theme,render,app}.js` | palette+scales / chart construction / controls |
| `static/js/tester.js` | live strategy tester strip (sliders + P&L) |
| `static/js/gears.js` | per-indicator `⚙` settings popovers |

Tests: `chart_app/tests/` — **212 passing**
(`env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests -q`).

---

## 3. The bugs that were fixed, and how to not reintroduce them

### 3.1 "Whale symbols hardly ever show up" — two causes, both structural

**(a) The flow pull could never finish.** `_production_flow_fn` hardcoded
`min_premium=0` with the comment "full tape always". Measured on SPY for
2026-09-17: the full tape is **1,387,630 prints in one day** and takes
**402 seconds** to page down at 10k rows a call. Five sessions is over half an
hour, so the fetch never completed inside any poll, on *any* timeframe.

The distribution is almost entirely dust:

| provider-side floor | rows kept | premium kept |
|---|---|---|
| ≥ $1,000 | 18.78% | 94.98% |
| ≥ $10,000 | 1.94% | 82.36% |
| ≥ $25,000 | 0.69% | 77.32% |

Now passes a real floor — `CHART_APP_FLOW_MIN_PREMIUM`, default **$10,000**.
Five sessions now complete in **~36 seconds**. Lower it toward $1,000 if you
want the flow pane's net-premium series near-exact and will pay ~10x the rows.

**(b) `if not days or len(days) > 5: return []`.** Every chart lookback except
5d spans more than five weekdays (10m=10d, 30m=20d, 1h/4h=30d, 1d=1y), so on
**five of the eight timeframes** this returned an empty list before making a
single call. Now takes the most recent `CHART_APP_FLOW_DAYS` (default 5)
sessions of the window, and `flow_stamp.whale_coverage` publishes
sessions-covered vs sessions-charted so a partly-stamped chart says so.

**Do not** reintroduce `min_premium=0`, and **do not** make the flow pull
synchronous inside `GET /api/state` — see §3.2.

### 3.2 `/api/state` blocked for 60+ seconds

The flow pull ran inline in the request handler, behind a page that polls
every 5 seconds. `server._deferred_flow` now returns `None` on the first ask
(meaning "started, ask later"), runs the fetch on a thread, and
`snapshot.build_state` treats `None` as `status: "loading"` and **does not
cache it**. Endpoint now answers in **0.01–0.07s**.

`_flow_pending` is what stops each 5-second poll launching another fetch.
Removing it produces a thread stampede against ThetaData, and **concurrent
ThetaData callers reliably time each other out** — observed while measuring.
The repo rule (serialise provider calls, never fan out) is load-bearing here.

Flow is cached **per session date**, not per chart window. The window key
includes the last bar's timestamp, so a per-window cache was invalidated by
every new bar and re-pulled all five sessions.

### 3.3 Buy/sell never appeared

The old score counted five booleans of which `liquidity` was hardwired
`False`, capping it at 4. `shared/candlestick_chart.apply_position_gate` then
required `score == 4` to buy and `score == 5` to add — **ADD was
mathematically unreachable** and BUY needed whale+wave3+squeeze+trend on one
bar. Replaced by `signal_engine`: a signed conviction in [−100, +100] and an
explicit state machine. The old gate is still published as `markers_legacy`.

### 3.4 The conviction score was structurally capped (my own bug, then fixed)

First version left `whale`'s 22 weight points in the denominator even with no
flow data, capping the achievable score near 44 (measured: SPY daily max
**+31.9** in a +15% year) so a +35 entry never fired — the *same* failure mode
it replaced. Worse, the live chart has flow and a backtest does not, so the
two ran on different scales and no calibration transferred.

**Rule:** a component with **no input** leaves the denominator
(`available[k] = False`). A component that is **present and reading zero**
(e.g. `breakout` on a non-squeeze bar) stays in. Pinned by
`test_missing_flow_shrinks_the_denominator_rather_than_diluting_the_score`.

### 3.5 Entropy on an absolute scale is not portable

Measured over 800 synthetic bars: a **pure Gaussian random walk reads a median
raw entropy of 83.5**, a strong clean trend **66.3**. The first cut at
`ORDERED_MAX_ENTROPY = 62` therefore called almost nothing ordered. Replaced
by a trailing **percentile rank** (`ORDERED_MAX_RANK = 35`). Raw entropy is
still published for display; **never threshold on it directly.**

### 3.6 The liquidity gate was a flat tax

`liquidity` is a percentile, so `>= 50` should pass half the bars. It passed
12–33% (SPY 33%, NVDA 14%, AMD 12%), because the underlying Hui-Heubel ratio
*trends* and a rank over a non-stationary series is not uniform. The gate now
damps only the **fragile tail** (`fragile_below = 25`); the LQ *pill* still
lights at ≥50 ("healthy book"). Two thresholds, deliberately.

### 3.7 Y-axis did not rescale when zoomed — "amateur hour"

`dataZoom.filterMode` was `"none"`, which tells ECharts to keep off-screen
points, so `yAxis.scale` auto-ranged against the **whole** series. Now
`"filter"`. Verified: zooming to the last 12% of bars took the price-axis span
from **16.00 → 4.50**.

Also fixed in the same pass: x-axis printed raw ISO timestamps
(`2026-09-14T11:15:00`) overlapping each other — now date-on-session-change,
time otherwise; candle width is a **proportion of the slot** (`barWidth:
"72%"`, cap 38px) rather than a fixed pixel cap that looked wrong at every
zoom but one; price axis moved to the right with a last-price tag; markers are
**transitions only** (the old gate drew a grey `hold` dot on nearly every bar).

### 3.8 `price_scores` was O(n²) on a 5-second poll

It re-ran Elliott + Bollinger + a full ADX scan over the whole prefix for
**every** bar. At 250 daily bars that is ~31k redundant band computations per
poll. Now `price_legs_series`, computed once, O(n).

---

## 3.9 The live tester and the gears (added same day)

`POST /api/backtest` and `POST /api/indicators` both re-run against the bars
already in the cache. Neither touches a data provider -- pinned by
`test_backtest_endpoint_never_calls_a_data_provider` and
`test_indicators_endpoint_never_calls_a_data_provider`, which inject fetchers
that raise. A slider drag must stay free.

`score_engine.INDICATOR_DEFAULTS` is the single source of truth for every
period the gears expose, served to the client by `GET /api/indicator-defaults`
rather than duplicated in JavaScript. If you add a tunable, add it there and
to `gears.js`'s `TABLE`; do not hardcode a default in the panel.

Series keys are stable under retuning (`ema20` stays `ema20` at period 5).
That is deliberate -- the key is the wire contract `render.js` draws against.

**Indicators are configured in ONE place, and it is not the tester
(2026-09-19).** `gears.js` `TABLE.elmo` owns all nine ELMo parameters. The
tester panel used to duplicate four of them (`entropy_window`,
`entropy_rank_window`, `liq_window`, `alma_window`) with *different slider
ranges* -- so one parameter had two UIs that could disagree, and the reading
the backtest scored was then not the reading the chart drew.

The split now: **indicators decide what is READ, the tester decides what is
DONE with the read** (levels, ATR stop, cooldown, costs). `tester.js` takes
its ELMo settings from `CGear.current()` and owns none of its own.

`POST /api/backtest` merges in this order -- **active profile, then request
override**. An omitted `elmo`/`config` inherits the profile, NOT shipped
defaults; otherwise a chart running a saved profile got scored against
different indicator reads than the ones on screen, and the P&L belonged to a
chart nobody was looking at. The response carries `elmo_config` and
`profile_source` so a run can always say which reads produced it. Pinned by
`test_backtest_inherits_the_active_profile_so_it_scores_what_the_chart_draws`
and `test_an_explicit_override_still_beats_the_profile`.

---

## 3.10 Lookback was too short for the indicators (the question that killed the session)

Jason asked, after the first handoff: "did you adjust lookback at all? it will have shit results if you dont." The first pass had not. Two separate holes:

**(a) The default chart is 15m with a 5d pull.** That is ~130 bars. EMA200 never defined, and ELMo's 200-bar percentile ranks never filled. 10m already asked for 10d; 15m was the outlier. Now **20d** (~520 bars), matching 30m. `_LOOKBACK` in `server.py` and `static/js/theme.js` must stay in lockstep.

**(b) ~~1h/4h cannot be fixed by pulling more.~~ THEY CAN, AND NOW ARE (corrected 2026-09-19).** The claim was that `shared/spot_history.py` capped intraday at 30d because history is aggregated from one-minute rows. The cap was real; **the reason was not.** It was never the provider -- it was `httpx`'s default **5.0s** timeout inside PHClient, which `ClientConfig` gave no way to set. Every one-minute span from 10d to 25d failed at exactly 5.0s, then served in **0.3-0.5s** with the timeout raised (25d = 7,425 rows in 0.5s). See CLAUDE.md and the `phclient-v2` skill.

The cap is gone, `hist_stock_ohlc` chunks at 21d, and the windows are now sized by what the INDICATORS need: **1h 60d** (~294 bars), **4h 180d** (~252 bars), both of which seed EMA200. Verified live: 90d of 15m = 1,678 bars in 61.7s, a span that was refused outright the day before.

`30m` was also raised **20d -> 30d**: 20d is ~179 bars, short of 200, and had been that way unnoticed because the old test asserted lookback *strings* instead of the bar count they produce. `test_every_intraday_lookback_can_seed_ema200` now asserts the bar count for every intraday timeframe, which is what caught it.

(The cascade below still stands as a safety net for a short tape, but it is **no longer load-bearing** for 1h/4h now that they can reach 200 bars.) The trend stack cascades the slow EMA **200 → 100** (fixed period, not `min(200, n)` -- a period that depends on series length would break causality) and **renormalises** over the terms that exist, same rule as a missing whale input. `ready` is ADX-seeded (~28 bars), not EMA50-seeded. Measured on a monotonic ramp: 162-bar trend vote was **0.7 vs 1.0**; it matches now. Pinned by `test_trend_still_votes_on_a_1h_length_tape_that_cannot_seed_ema200` and `test_trend_and_ready_work_on_a_4h_length_tape`.

Per-symbol window tuning was also tested in that same pass and is curve-fitting: in-sample median **+11.5pp**, out-of-sample **+0.55pp**, 4/8 series. Do not add per-ticker lookbacks. The one global window that survived a paired comparison is `liq_window` **10 → 5** (mean +3.98pp, 5/8, bootstrap P(improvement>0)=0.967). `entropy_rank_window` 200 looks bad on pick-frequency (60 won 11×) and **worse** as a default (mean 9.27 vs 12.15); leave it.

---

## 3.11 The denominator regression the lookback fix exposed (2026-09-19)

§3.4 made a missing component leave the denominator. That flag
(`ConvictionResult.available`) is **series-wide**, which was correct only while
the flow window and the chart window were the same length. §3.10 changed that:
15m now spans 20d (14 sessions) while the flow pull is still capped at
`CHART_APP_FLOW_DAYS` = 5 session dates.

Measured live on SPY 15m, 2026-09-19 (373 bars, flow covering 2026-09-14 →
2026-09-18):

| | |
|---|---|
| bars outside flow coverage | **266 / 373 (71%)** |
| of those, understated | **252 bars, by a factor 1.282 (28.2%)** |
| bars that cross `entry_long` / `exit_long` once corrected | **31** |

The denominator is now decided **per bar** from
`flow_stamp.flow_observed_bars(records, trades)`, which keys on the session
dates the provider actually answered for. It is deliberately **not**
`net_premium != 0`: a covered session bar with no qualifying print is a real
measurement reading zero and must stay in the divisor, or quiet tape gets
rewarded with a higher score. `conviction_series(..., flow_observed=None)`
keeps the old series-wide behaviour, so backtests -- which have no flow at all
-- are unaffected and their calibration still holds.

Pinned by `test_flow_observed_shrinks_the_denominator_per_bar_not_per_series`,
`test_a_covered_bar_reading_zero_premium_stays_in_the_denominator`,
`test_omitting_flow_observed_keeps_the_series_wide_behaviour` and
`test_flow_observed_bars_keys_on_session_dates_not_on_premium`.

---

## 3.12 Window coherence: liquidity is the floor (2026-09-19)

The three legs are summed with fixed weights, which assumes they are
commensurable. Their horizons were not ordered on purpose -- at 15m the
normalization windows read 7.7 / 7.7 / 3.8 / 1.9 sessions
(`entropy_rank_window` 200, `liq_norm_window` 200, `bandwidth_window` 100,
`whale_z_window` 50) while the measurement windows ran 0.2 to 3.8.

`elmo.resolve_windows` now enforces **liquidity as the floor**: `liq_window`
sets it, and `entropy_window` / `alma_window` are the only knobs allowed to go
deeper. A violating value is clamped UP (a sweep must not die on a corner of
its own grid) and the clamp is published on `ElmoResult.windows`, so a tuning
run cannot mistake a clamped grid point for the one it asked for.
`entropy_scale_window` is exempt: it sizes the sigma buckets rather than
measuring anything.

This is a **structural** constraint, not a fitted one -- it removes degrees of
freedom rather than adding them, and the direction it encodes is the one
change here that replicated out of sample (`liq_window` 10 -> 5, P = 0.967).
Shipped defaults clamp nothing. Pinned by three tests in `test_elmo.py`.

**Still open, and the highest-value window question:** `liq_norm_window` is
200 while `liq_window` is 5 -- the fastest measurement in the system ranked
against the longest history, over a series §3.6 established is **non-stationary
(it trends)**. That is the same mechanism that produced the flat-tax bug.
Shortening it is a hypothesis with a mechanism behind it; settle it with a
paired cross-series comparison (mean improvement, series won, bootstrap
P(improvement>0)), never a single-symbol sweep.

---

## 3.13 Profiles, dollars, date ranges, and the volume pane (2026-09-19)

**Profiles (`chart_app/profiles.py`).** Four call sites build an `ElmoResult`
and the LIVE one -- `snapshot.py` -- took no overrides, so a knob moved in the
tester changed the tester's P&L and nothing else. `snapshot.build_state` now
resolves a profile per `(ticker, interval)` and feeds it to both ELMo and the
signal engine. Resolution is specific -> general:

    (SPY, 15m) -> (*, 15m) -> (SPY, *) -> (*, *) -> shipped defaults

Per **timeframe** is structural: every window is counted in BARS, so
`entropy_rank_window = 200` is 7.7 sessions at 15m and 200 at 1d. Per
**symbol** is the half the walk-forward warned about (+11.5pp in sample,
+0.55pp out, 4/8 series), so every profile carries a `validation` stamp --
`in_sample` is the default because a slider drag against visible bars IS an
in-sample fit, and `walk_forward` has to be claimed explicitly by something
that actually ran folds. A corrupt store reads as empty rather than taking the
chart down. `GET/POST/DELETE /api/profiles`; store at
`artifacts/chart_app_profiles.json` (`CHART_APP_PROFILES` overrides).

**Dollars.** `backtest._metrics` now also emits `capital`, `pnl_dollars`,
`buy_hold_dollars`, `excess_vs_bh_dollars`, `max_drawdown_dollars` -- the
percent SCALED by `capital` (default $100k), not a position-sizing model:
no share count, no partial fill, no margin. The tester shows both, because the
dollar figure reads at a glance while tuning and the percent is what compares
across symbols and windows.

**Date ranges.** `POST /api/backtest` takes `start`/`end` (inclusive ISO) and
returns the `window` it actually scored, flagged `narrowed`. It can only ever
narrow bars ALREADY cached -- never a provider pull, so a slider drag stays
free -- and a range the cache cannot serve is reported with what the cache
does hold rather than silently scored. This is what makes an out-of-sample
check possible by hand: fit on one span, score another.

**Backtest cache coverage.** `backtest_runner.pull` skipped on
`len(have) >= 60`, so raising a universe entry from 3y to 5y found 752 cached
bars, printed "cached", and scored three years while the caller believed it
had five. The hit test is now about SPAN (`_covers`), with a 5% + 5-day grace
so a weekend start does not refetch the universe. Staleness is reported, never
auto-refreshed -- every refresh is billed.

**The whale header was being read as P&L.** It rendered
`103k prints >$1.5mm`, and a bare dollar figure in a header reads as money
made. That number is the *adaptive whale cutoff* -- the premium a print must
clear to count. It now reads
`103k prints · whale cutoff $1.5mm (top 0.03% of tape, 26 hits)`.

**The "load history" button (2026-09-19).** The date pickers only ever NARROW
cached bars -- a slider drag must never fetch -- so a longer backtest was
impossible until something explicitly went and got the history, and that was
refused above 30 days for reasons that turned out to be ours (§3.16). The
tester now has a span select plus a `load history` button that calls
`POST /api/refresh`, rebinds the pickers from the span the cache then holds,
and re-scores. Spans offered depend on the timeframe (a 3m chart over two
years is hundreds of thousands of one-minute rows to aggregate). It fetches
only when pressed.

**Volume moved inside the price pane** on a hidden axis capped at
`VOLUME_FRACTION` (18%) of the pane, and the tester became a floating panel
instead of a strip. Both for the same reason: the strip cost 62-87px of chart
height and the volume pane 11-22%, and opening either reflowed the plot under
the cursor mid-tune.

---

## 3.14 The component weights are measured now, not hand-picked (2026-09-19)

`WEIGHTS` (trend 26 / momentum 22 / whale 22 / elmo 18 / breakout 12) were
chosen by hand and, unlike every window in `elmo.DEFAULTS`, nothing stood
behind them -- they could not even be swept, because `conviction_series` read
the module-level dict directly. They now resolve through
`signal_engine.resolve_weights(config)`, so `config["weights"]` can override
them per run. That is a **test seam, not a UI lever**: weights are deliberately
absent from the gears and the tester.

First use of it. Paired across all 11 cached series, fixed params, same method
as liq_window:

| elmo weight | mean d | median d | won | bootstrap P(d>0) |
|---|---|---|---|---|
| 12 | -2.17pp | -1.28pp | 4/11 | 0.142 |
| **18 (shipped)** | -- | -- | -- | -- |
| 26 | -2.49pp | -0.44pp | 4/11 | 0.275 |
| 34 | -5.65pp | -1.79pp | 2/11 | 0.127 |
| 45 | -12.06pp | -6.05pp | 1/11 | 0.079 |

Monotonically worse going up, and the per-series argmax is scattered
(12/18/26/45, no consensus) -- noise, not a preference. ELMo already acts
through the liquidity gate and the ordered-entropy filter; paying it more
crowds out trend and momentum. **18 stays.** The prompt for this was an
eyeball read of ELMo's entries on one panel on one symbol looking good, which
is the in-sample view -- exactly what the table tested and rejected.

The other four weights remain hand-picked and untested. Same method, same
cache, no billed calls: that is the cheapest real measurement left in the app.

---

## 3.15 The backtester priced a strategy the chart was not running (2026-09-19)

**The bug.** `signal_engine.run_state_machine` scales **1 -> 3 units** via
`add_long` / `trim_long`; the chart draws `add` and `trim` marks and publishes
`position` 1/2/3. `backtest.run_backtest` tracked `in_pos` as **-1/0/+1** and
had no `qty` concept at all. So the two levels moved the marks and had
**exactly zero** effect on the P&L -- measured on SPY 15m, `add 35` (reaching
3 units), `add 80` (never leaving 1) and three other settings all returned
**-0.50% to the basis point**.

Both sides were internally consistent, which is why nothing caught it. Found
by asking why the levels did nothing, not by a test.

**The capital model is now explicit, because it was implicitly 100%.**
`in_pos = 1` multiplied the whole bar return, so every entry was the entire
book and a 1-unit and a 3-unit conviction were the same bet.

| knob | meaning |
|---|---|
| `max_units` (3) | units in a full-conviction position, both sides |
| `unit_fraction` (None = 1/`max_units`) | share of capital per unit |
| `exit_style` (`full`) | `full` closes on an exit signal; `scale` sheds one unit per action. A **stop always closes everything** either way. |

`unit_fraction = 1/max_units` is **fractional**: first entry 33%, full
conviction 100%, never borrows. `unit_fraction = 1.0` is **pyramiding**: the
first entry is already the whole book and adds lever it to 300%.

Measured, SPY 1d 3y:

| model | return | maxDD | avg capital used | return / exposure |
|---|---|---|---|---|
| fractional | 17.33% | -8.13% | 43.6% | 0.40 |
| pyramiding | 55.51% | -23.01% | 130.9% | 0.42 |
| binary (old) | 27.36% | -8.16% | 62.8% | 0.44 |

**Pyramiding's 55% is not edge.** It is 3x the exposure for 3x the drawdown at
the same return-per-unit-risk -- the same trap as widening the ATR stop (§6).
Scale-out, by contrast, returned **+2.8pp at identical drawdown** on that tape.
`avg_exposure_pct` and `capital_model` are published on every result so a
return can always be read against how much money was actually at work: trailing
buy-and-hold at 40% invested is not the same failure as trailing it fully
invested.

**Shorts are symmetric now.** They had entry and exit only -- no pyramiding, no
scale-out -- so a short could never express more or less conviction than "on".
`add_short` (-55) and `trim_short` (-12) mirror the long ladder with the signs
flipped: a short adds as conviction falls further and trims as it recovers
toward zero. Implemented in **both** engines.

**Everything measured before this used the binary model.** §6's baseline,
walk-forward and permutation numbers are pre-change. The ELMo weight table in
`signal_engine.WEIGHTS` was re-run under both models and the conclusion held
(18 stays, raising it is worse under both); **§6 has not been re-run and
should be.**

Pinned by `tests/test_position_model.py` -- six tests, including
`test_the_two_engines_agree_on_how_big_the_position_gets`, which is the one
that would have caught the original bug.

---

## 3.16 The five-second lie (2026-09-19)

**Years of "ThetaData rate limits / transient proxy 502s / the vendor cannot
serve that range" were, for a large class of failures, `httpx`'s default 5.0s
timeout on OUR side.**

`PHClient` builds `httpx.Client(follow_redirects=...)` with no timeout, so it
inherits httpx's 5.0s default, and `ClientConfig` exposes no way to change it.
Measured live on SPY one-minute history (`market.stock_ohlc`):

| span | httpx default (5.0s) | raised to 120s |
|---|---|---|
| 7d | OK | OK |
| 10d | PHTimeoutError at 5.0s | **OK — 3,519 rows in 0.3s** |
| 14d | PHTimeoutError at 5.0s | **OK — 3,909 rows in 0.3s** |
| 21d | PHTimeoutError at 5.0s | **OK — 5,862 rows in 0.3s** |
| 25d | PHTimeoutError at 5.0s | **OK — 7,425 rows in 0.5s** |
| 30d+ | PHTimeoutError | PHAPIError — real vendor ceiling |

Same proxy, same requests; only our own client changed. Several then returned
in **0.3 seconds** — never slow, never rate-limited, data there all along.

Fixed in `shared/thetadata.py::_apply_http_timeout`
(`THETADATA_HTTP_TIMEOUT_S`, default 120), applied to every thread-local
client.

**Diagnose with the clock.** A failure at *almost exactly 5.0s* is the old
default and the request was fine. An **instant** `PHAPIError` is a real
`LARGE_REQUEST` refusal.

**What is still real — do not overcorrect.** The vendor ceiling exists: 30d of
one-minute returns `PHAPIError` at any timeout, so dense routes still chunk
(**21d** one-minute, 28d EOD — one minute is ~390 rows a session against EOD's
one). Concurrent PHClient callers still time each other out; keep serialising.
Genuine 502s on options bulk routes under load are still rate limits.

Downstream of this: the intraday cap in `shared/spot_history.py` is removed
(`d/w/m/y` all parse now), and chart_app's lookbacks were resized — see §3.10.

---

## 3.17 The backtest path still had the §3.4 bug (2026-09-19)

Found because Jason looked at a saved profile and said "these settings look
like edge to me". Reproducing it offline gave **+13.66%** where the server had
reported **+44.94%** -- same bars, same config. Two paths, two answers.

`flow_observed` was threaded into the LIVE chart (§3.11) and **not** into
`backtest.run_backtest` or `POST /api/backtest`. So the whale weight sat in
the denominator for all 6,679 bars of a one-year 15m window while the flow
pull covered **5 of 251 sessions** — inflating a hand-tuned result by 31
points. That inflated number is what the hand-tune was aiming at.

Fixed: `run_backtest(..., flow_observed=...)`, and the route passes
`flow_stamp.flow_observed_bars(records, rows)` to both `evaluate` and
`run_backtest`. Pinned by
`test_the_backtest_honours_per_bar_flow_coverage_too`.

**The lesson is the one this file keeps relearning:** when two code paths
compute the same quantity, they drift, and the drift shows up as a number
somebody trusts. The position model (§3.15) was the same shape. If you add a
third consumer of `conviction_series`, give it the same inputs or expect this
again.

### The verdict on that profile

| | |
|---|---|
| as reported (diluted) | +44.94% |
| corrected | **+13.71%** vs buy-and-hold **+15.11%** — **loses by 1.40pp** |
| at 152.9% average exposure | pyramiding, `unit_fraction=1`, `max_units=5` |
| same rules, no leverage | **+3.06%** vs +15.11% — loses by 12.05pp |
| maxDD | **-23.83%** |
| permutation, 60 surrogates | **p = 0.115** — not distinguishable from shuffled |
| walk-forward, 4 folds | OOS mean **+0.76%**, 3/4 folds positive, worst -0.25% |

Underperforms buy-and-hold while using 1.5x the capital and taking a 24%
drawdown. The walk-forward is the only mildly encouraging line and it is
+0.76% a fold.

---

## 3.18 Does tuning transfer? Ranking yes, weakly; level no (2026-09-19)

The open question left over from the profile work: an in-sample sweep always
produces a winner, so the winner proves nothing. The thing worth knowing is
whether the **ordering** the sweep produces means anything on symbols it never
saw — because if it does, tuning on one universe is a legitimate way to pick a
default for another even though the in-sample number is a fiction.

Two claims hide inside "the tune worked", and they fail independently:

| claim | what it is | does it transfer |
|---|---|---|
| **level** | the return the winner posted in-sample | **no** — it is the maximum of a noisy sample, biased up by construction |
| **ranking** | the order the combos came in | **partly** — measured below |

`--stage rank` measures the second. It scores all 60 `SWEEP_GRID` combos on
all 8 daily series once (480 backtests, ~20s, no billed calls — the cache
already holds them), then re-uses that matrix for every cut.

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m chart_app.backtest_runner \
  --stage rank --report artifacts/chart_app_rank_transfer.json
```

### What it measured (60 combos × 8 symbols × 3y daily, 2026-09-19)

**All 35 disjoint half-cuts** (4 symbols fit, 4 tested, both directions = 70):

| | |
|---|---|
| Spearman ρ between the two halves' rankings | median **+0.552** (p10 −0.185, p90 +0.765), **6/35 cuts negative** |
| percentile the fit-half's pick landed at in the test half | median **78.3** (p10 29.1) — coin-flip is 50 |
| directions below coin-flip | **17/70** |
| in-group minus out-group return of the same combo | **+4.19pp** median — the part that does not transfer |
| chosen combo vs a blind pick, out of group | **+3.52pp** median |

**Leave-one-symbol-out** — read these instead. The 70 directions above are 35
overlapping cuts of the same 8 names, so SPY sits on the test side of 17 of
them; quoting a p-value over them quotes the overlap. Here each symbol is the
held-out test exactly once, so the test sides are disjoint, and it is also the
procedure a real default-picker follows: fit on everything you have, deploy on
the name you do not.

| held out | percentile | chosen ret% | best possible% | blind pick% | trades |
|---|---|---|---|---|---|
| SPY | 79.2 | 15.21 | 20.49 | 6.98 | 13 |
| QQQ | 90.8 | 22.25 | 25.19 | 5.16 | 14 |
| IWM | 60.8 | −1.47 | 6.78 | −2.52 | 16 |
| NVDA | 95.8 | 54.36 | 68.52 | 22.73 | 19 |
| AAPL | 80.8 | 12.55 | 17.87 | 11.25 | 13 |
| **MSFT** | **2.5** | **2.05** | 14.71 | **9.19** | 15 |
| TSLA | 45.8 | −10.24 | 13.94 | −8.89 | 13 |
| AMD | 59.2 | 55.41 | 96.00 | 42.47 | 17 |

median percentile **70.0**, **6/8** above coin-flip, **+4.77pp** median versus
a blind pick. Sign test **p = 0.145** — and that is the ceiling, not a near
miss: with 8 symbols even a clean 8/8 only reaches p = 0.004, and 6/8 *cannot*
clear 5%. The fix is more symbols, not more grid.

### How to read this

1. **Ranking carries, weakly and unreliably.** ρ median +0.55 is real
   structure, not noise — but a sixth of the cuts are negative, and the p10
   percentile of 29 says that a quarter of the time tuning elsewhere leaves
   you worse off than picking blind. MSFT is the case in point: fitting on
   the other seven names put it in the **bottom 3%** of its own grid, turning
   a 9.19% blind pick into 2.05%.
2. **The level gap is +4.19pp, not the ~90pp** the earlier per-symbol tuning
   showed. That is not a contradiction — it is the point. This grid has four
   knobs and 60 cells; the hand-tune had 43 knobs. Overfitting capacity is
   what sets the inflation, so the honest reading is *the smaller the grid,
   the smaller the lie*, and the 90pp figure was measuring the tuner, not the
   market.
3. **The payoff is small even where it works**: ~+4.8pp median over three
   years versus picking a cell at random, against an engine that already
   loses to buy-and-hold (§6). Tuning is not where this becomes tradeable.

### What this licenses, and what it does not

- **Licensed**: using a pooled sweep to pick *shipped defaults*. It beats a
  blind pick more often than not, which is all a default has to do.
- **Not licensed**: a per-symbol profile tuned on that symbol's own history
  and then trusted. That is the in-sample level, the number that does not
  transfer, and §3.17 is what believing it costs.
- **Watch for**: a symbol landing in the bottom decile of its own grid (MSFT
  here). If per-symbol profiles stay, they need this check run per symbol, not
  a single universe-wide blessing.

Pinned by `tests/test_rank_transfer.py` (cut enumeration, the sign-test tail,
selection reading the fit half only, and the level/ranking separation) and the
math helpers in `tests/test_backtest.py`. `chart_app/backtest.py` grew
`rank_average` / `spearman_rho` / `percentile_of` — midrank-based, because a
sweep produces real ties (every combo disqualified by `min_trades` scores
`-inf` together) and breaking them by array order would correlate the order
the grid was expanded in.

## 3.19 The poll was a queue, and the presets were write-only (2026-09-20)

Two complaints, one shape: **a value with a write path and no read path.**

### The chart was slow because the poll outran the build

`GET /api/state` rebuilds entropy, ELMo, the conviction series and every
overlay over the whole cached window. Measured live on BTC-PERP 15m
(35,168 bars):

| | |
|---|---|
| `build_state` | 3.9s |
| FastAPI `jsonable_encoder` on the result | 1.2s |
| `json.dumps` | 0.5s |
| payload | **32 MB** |
| `static/js/app.js` poll interval | **5 s**, unconditional |

Six seconds of work arriving every five seconds is not a slow endpoint, it is
an **unbounded queue**. FastAPI runs a sync route in a 40-thread pool, so the
backlog grows for as long as the window is open and every build contends for
the GIL with the ones stacked behind it. The same endpoint measured **30s**
end-to-end from the browser against 5.6s in isolation — **that gap is the
backlog, and it is the diagnostic**. If an endpoint is far slower in situ than
standalone, stop optimising the endpoint and go count its callers.

A slider drag (`POST /api/backtest`) queued behind that pile, which is the
whole of "tuning takes forever".

**Fixed** by not rebuilding what has not changed:

- `BarCache.fingerprint()` — `(COUNT(*), MAX(ts))` in one indexed query. Count
  **and** max, because a backfill can add bars behind the newest one without
  moving the timestamp.
- `session["epoch"]` — bumped by every mutation the bars cannot show: symbol
  switch, refresh, RH position, profile save/delete, and a deferred flow pull
  landing. A missed bump is worse than the original bug: it is a chart
  silently drawing stale parameters. `tests/test_state_cache.py` pins one per
  trigger.
- The cache stores the **bytes**, not the dict, and the route returns a
  `Response` — which skips `jsonable_encoder` entirely (the payload is already
  JSON-native by the time `build_state` returns).
- An **ETag**, with `app.js` sending `If-None-Match` and returning early on
  304. `poll()` also drops a tick that is still in flight.

| | before | after |
|---|---|---|
| first load | 30s / 29.6 MB | 4.8s / 32 MB |
| steady poll | 30s / 29.6 MB | **0.01s / 0 bytes (304)** |
| slider drag | queued behind the above | 3.5s |

`GET /api/state/version` reports `(bars, last_ts, epoch, etag)` without
building anything, for a caller that wants to ask "did it move?" cheaply.

**`_epoch_lock` is separate from `_state_lock` on purpose.** `get_state` holds
`_state_lock` across a build; a build calls `_deferred_flow`, which takes
`_flow_lock`; the flow worker holds `_flow_lock` and then bumps the epoch.
Bumping under `_state_lock` closes that into a lock-order inversion and hangs
the chart. Do not merge them.

### The slider drag still paid for ELMo twice

`POST /api/backtest` computed ELMo to drive the chart's conviction line, then
called `run_backtest`, which called `compute_elmo` again with the identical
overrides over the identical records — ~2.3s of the 5.7s. `run_backtest` now
takes an optional `elmo=` object; `elmo_overrides` stays for callers with
nothing to hand over (the walk-forward runner sweeps parameters, so it wants
the recompute). Drag: **5.7s → 3.5s**. Equivalence is pinned, because this is
only allowed to be a saving, never a behaviour change.

### The presets loaded into the engine and nowhere else

`snapshot.build_state` has resolved the active profile and fed it to the live
chart since profiles existed, and published a `profile` block saying so.
**Nothing on the client ever read either.** `gears.js` and `tester.js` seeded
their controls from `/api/indicator-defaults` — shipped values — and never
called `GET /api/profiles`.

So the chart drew the saved parameters while every slider sat at its shipped
value, and because both panels post **all** their keys (`CGear.current()` sends
nine ELMo windows; the tester sends fourteen config levels), the first knob you
touched shipped 22 defaults along with it and wiped the profile out of the run.
That is the whole of "I have to reset everything on restart". Measured on the
BTC-PERP|15m profile, the knobs that were being silently discarded:
`entry_long` 60→30, `exit_long` −60→−12, `trim_long` −40→12, `add_long` 57→55,
`cooldown_bars` 1→3.

**Fixed:** the baseline for a knob is now *the profile's value if it has one,
else shipped*, in both panels. Consequences worth keeping:

- the sliders open where the chart actually is;
- the gear's dirty dot means "differs from what is **saved**", not "differs
  from shipped";
- `reset` returns to the **profile**, not past it;
- `#profileTag` in the header names the active profile and colours it by
  validation — `in_sample` is amber, because it is a warning, not a credential
  (see §3.18: in-sample median +11.5pp vs out-of-sample +0.55pp).

Knobs reseed only on a **symbol** change; the tag repaints on any profile
change. Reseeding is destructive, and doing it on a save would wipe the tuning
you were part-way through at the moment you checkpointed it.

### "It needs a restart a dozen times to see a change"

Two problems wearing one coat. A **Python** edit genuinely needs one — uvicorn
is not run with `--reload`. A **JS/CSS** edit never did: `/static/js/app.js` is
the same URL before and after an edit, so the browser served what it had, and
restarting uvicorn does not change that URL either — which is why restarting
appeared to work only sometimes. `root()` now rewrites `?v=__V__` to each
file's mtime, so an edited asset is a different URL and a plain reload picks it
up.

### The one to not repeat

Testing profile invalidation against the **live server** overwrote
`artifacts/chart_app_profiles.json`'s BTC-PERP|15m record. That store is not in
git; there is no undo. It was recoverable only because the identical parameters
had been saved to `*|15m` six seconds earlier (same `metrics.bars` = 35128,
confirming both came from the same panel state). `profiles.store_path()` reads
**`CHART_APP_PROFILES`** — set it to a scratch file before any test that
writes, as `tests/test_state_cache.py`'s fixture does.

---

## 3.20 The perp sleeve, and three numbers that were wrong (2026-09-20)

Jason tuned `BTC-PERP|15m` in the tester and asked for an overnight sleeve
running that profile. Building it surfaced three separate wrong numbers, each
wrong in a different direction. None of them were the strategy.

### (a) CAGR divided a one-year window by 5.37

`BARS_PER_YEAR["15m"] = 26 * 252 = 6552` assumes a 6.5-hour US equity
session. A perpetual swap trades 24/7, so 15m is **96 bars a day, not 26**.
The engine read the cached BTC-PERP window (35,178 bars, 2025-09-19 ->
2026-09-21, **1.003 years**) as **5.37 years**:

| | stored | measured |
|---|---|---|
| CAGR | 21.9% | **188.2%** |
| Sharpe | 0.67 | **1.55** |

Fixed by `backtest._bars_per_year`, which MEASURES the span off the
timestamps rather than extending the table. The reason to measure rather than
add a `is_crypto` branch is that it self-checks where the table was already
right: SPY 15m measures 6,697 bars/yr and QQQ 6,543 against a tabled 6,552,
inside 2.3%. Metrics now publish `bars_per_year` and `years` so the
annualisation can be audited instead of trusted. Pinned by four tests in
`tests/test_backtest.py`.

Both old errors were **conservative**, which is why this survived a year.
Pessimistic bugs do not announce themselves.

### (b) The edge was a fee assumption

Same tune, same bars, only the cost per side moving:

| cost/side | return | maxDD |
|---|---|---|
| 0 bp | +349.0% | -47.2% |
| **2 bp** (`DEFAULT_COST_BPS`, an equity-ETF number) | +189.2% | -52.3% |
| **5 bp** (perp taker) | **+49.5%** | -61.6% |
| 7.5 bp | -13.8% | -67.9% |
| 10 bp | -50.3% | -79.7% |

Break-even is **~7bp/side** at 429 trades, and **perp funding is not modelled
at all**. `DEFAULT_COST_BPS = 2.0` is right for a liquid ETF and overstates a
perp result by ~4x. `perp_sleeve.COST_BPS = 5.0` is declared separately from
the backtest default precisely so the sleeve cannot inherit the wrong venue's
fees by accident.

### (c) The sleeve's own P&L overstated by 2.3x

The sleeve first kept a trade-level book -- P&L booked at each reduction
against an average entry. On the same bars at the same 5bp it read **+133.8%**
(summed), **+113.2%** (compounded), against the tester's **+49.5%**.

Compounding closed part of the gap. The residual is **volatility drag**: the
tester marks the position EVERY BAR at the leverage actually held, so variance
compounds against a levered book roughly as `-(sigma^2/2)(L^2 - L)` per bar.
Entry/exit accounting structurally cannot see it. The sleeve now has **no P&L
implementation at all** and quotes `run_backtest`. One number, one place.

### Leverage: `unit_fraction` is the knob, `max_units` is not

**Jason's standing instruction (2026-09-20): sleeves run at 1x max notional.**

Max notional is `max_units * unit_fraction`. A profile saved from the tester
defaults `unit_fraction` to **1.0**, which silently turns `max_units` into a
leverage multiplier -- the BTC-PERP tune as first saved was a **5x** book with
a -61.6% in-sample drawdown. A cash account that never borrows is
`unit_fraction = 1/max_units`.

The tempting fix is to cut `max_units`. It is wrong, and monotonically so --
all rows below capped at 1x, 5bp, four anchored folds:

| max_units | first entry | in-sample | out-of-sample | maxDD | Sharpe |
|---|---|---|---|---|---|
| 1 | 100% | +1.9% | **-4.9%** (1/4) | -26.7% | 0.21 |
| 2 | 50% | +8.8% | +6.0% (2/4) | -21.6% | 0.46 |
| 3 | 33% | +14.8% | +9.5% (3/4) | -18.6% | 0.72 |
| **5** | 20% | **+17.4%** | **+15.4%** (3/4) | **-15.7%** | 0.90 |

**The scale-in/scale-out IS the risk control.** At `max_units=1` every false
signal is taken at full size across 434 trades. Keep the units, cut the
fraction. (6 and 8 measured better still -- +19.7% OOS, -13.2% DD at 8 -- but
that is a new fit and needs its own refit-per-fold walk-forward first.)

All four saved profiles are now 1.00x:

| profile | max_units | unit_fraction | notional |
|---|---|---|---|
| `BTC-PERP|15m` | 5 | 0.2 | 1.00x |
| `*|15m` | 5 | 0.2 | 1.00x |
| `SPY|15m` | 3 | 0.333333 | 1.00x |
| `QQQ|15m` | 3 | 0.333333 | 1.00x |

`run_sleeve` prints sizing on every launch and **warns** above 1.0x, so this
cannot drift back silently.

### Does the tune survive out of sample?

Fixed params, 4 anchored folds, 5bp, at the 1x sizing now saved:
**+15.35% compounded vs buy-hold -4.96%**, 3/4 folds positive, worst fold
-3.16%. Permutation (100 shuffles, at 5x): p = 0.0099.

**The caveat that bounds all of it:** Jason hand-tuned these params against
the *whole* year, so every "test" fold is data the tuner had seen. It is an
upper bound, not an unbiased estimate. The clean test is a refit-per-fold grid
search, which has NOT been run on this symbol. Report:
`artifacts/perp_sleeve/BTC-PERP_validation.json`.

### The sleeve itself

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -u   -m chart_app.run_sleeve --ticker BTC-PERP --interval 15m --hours 12
```

`perp_sleeve.py` + `run_sleeve.py`. **It places no orders** -- every venue in
`crypto_source` is a public, unauthenticated, read-only endpoint, no key and
no signing. It is a shadow book: position, stop, fills, pending, P&L, written
to `artifacts/perp_sleeve/<ticker>_<stamp>.{log,jsonl}` with a morning
summary.

Three properties it is built around, each with a test:

1. **The forming bar is dropped.** A 15m candle at minute 3 can print any
   close by minute 15; acting on it is repainting.
2. **A decision on bar `i` fills at bar `i+1`'s OPEN**, matching `backtest.py`.
   The newest closed bar is therefore `pending`, not filled -- reporting it as
   done would claim a price we could not have got.
3. **The window's left edge must not move.** The first build re-fetched a
   rolling 30d each poll. Measured: sliding the left edge by ONE bar changes
   **14 of ~135 fills**, some weeks downstream, because dropping a bar
   re-seeds the 200-bar ranks. Appending on the right with the left edge
   fixed changes **0**. The sleeve therefore keeps its own **append-only** bar
   store (`artifacts/perp_sleeve/sleeve_bars.db`, separate from
   `chart_app_bars.db` so it never races the running server) and replays that.
   Without this it announced revised history at 3am as live fills.

The book is a **pure function of (bars, profile)** -- replayed from scratch
every tick, never accumulated -- so a restart at 3am reproduces it exactly and
the journal is just a diff. Fills predating the session are logged
`prior_fill`; later revisions of old bars are logged `revision` and never
announced.

---

## 4. Data integrity — READ THIS, IT IS NOT ONLY A CHART ISSUE

**`shared/spot_history.fetch_daily_candles` serves UNADJUSTED prices.**

Measured 2026-09-18: NVDA `2024-06-07 close 1208.88` → `2024-06-10 close
121.79`. That is the 10-for-1 split, delivered as a genuine **−89.9% session**.
Over a 3y window it made NVDA's buy-and-hold read **−49.6%** when the true
figure is **+404%**, and it produced a fabricated −92% "out-of-sample" result
before it was caught.

`chart_app/split_guard.py` detects and back-adjusts this for `chart_app`, and
`backtest_runner._loaded` prints every adjustment it makes.

**Not fixed elsewhere, and it should be looked at:**
`Vol_Suite/correlation_engine.py::fetch_price_history` uses the same path. A
multi-year pull there would put a fabricated −90% observation into a
correlation matrix and a realized-vol estimate. Deliberately out of scope for
a chart change — flag it to Jason before touching `shared/`.

---

## 5. ELMo — what it is and what it is not

ELMo = **Entropy, Liquidity, Momentum**, after the TradingView strategy of the
same name (source protected — nothing was copied; the published description
names the ingredients, each implemented here from the literature).

- **Entropy** — Shannon entropy of the bucketed return distribution, bucketed
  in rolling-sigma units, normalised by `log2(min(bins, window))`. Consumed as
  a **trailing percentile rank**. Unsigned: it says whether to trust a
  direction, never which one.
- **Liquidity** — Hui-Heubel ratio, range-per-share-traded. Shares outstanding
  cancels out because the series is consumed as a percentile. Note the sign:
  the textbook ratio is **illiquidity** (higher = worse); `liquidity_score`
  publishes the **inverted** percentile so high = liquid, matching the
  TradingView author's convention.
- **Momentum** — ALMA + its slope. Direction comes from here.

It **refuses to fabricate**: a symbol with `volume=None` gets
`liquidity = None` and `has_volume = False`, and the UI says "no volume: LQ
unavailable". Do not "fix" that by defaulting volume to 1.0 the way `_vwap`
does — a made-up share count is the entire denominator of the ratio.

**LQ was dark since the app was written.** It is now real, and it is ELMo's:
`hui_heubel` → `liquidity_score` → `elmo.liquid` → the LQ pill, and separately
→ the conviction gate. If real dealer liquidity is ever wanted instead, it is
a one-line swap at `compute_elmo`'s liquidity leg.

---

## 6. The algo — and the honest verdict on it

`signal_engine.conviction_series` produces one signed number per bar from five
weighted components (trend 26, momentum 22, whale 22, elmo 18, breakout 12),
multiplied by a liquidity gate. `run_state_machine` turns it into
buy/add/trim/sell with an ATR trailing stop.

**Everything is causal**, pinned by `test_conviction_is_causal`, which
recomputes on a truncated series and demands bit-identical earlier values.
That single test is what makes every backtest number below meaningful. If you
add a component, add it to that test's coverage.

### The verdict — do not trade this mechanically

Measured on 8 equities × 3y daily + 3 intraday series, split-adjusted, 2bp a
side (`python -m chart_app.backtest_runner --stage all`):

| test | result |
|---|---|
| baseline, shipped defaults | 9/12 series profitable, 13–17 trades per 3y |
| walk-forward, 4 folds, params refit per fold | **3/11 series positive OOS, 33% fold hit rate** |
| permutation test, 60 shuffled surrogates/series | **median p = 0.598; 0/12 beat their own null at 10%** |

The engine's P&L is **not distinguishable from running the same rules on
shuffled data**. Widening the ATR stop raises returns only by raising exposure
toward 84% — i.e. by converging on buy-and-hold, which still beat it on 8 of 8
daily series.

So: the defaults are calibrated for a **legible chart** (entries a few times a
month on a daily tape), not as an edge claim. Treat the line as a fast read of
where trend, momentum, order and flow agree. Before changing a default, re-run
the suite and put the new numbers here.

### Where it does earn its keep

Downtrends. NVDA daily, split-adjusted: strategy **−15.8%** vs buy-and-hold
**+404%** (bad). But BTC-PERP daily: **+4.1%** vs buy-and-hold **−30.3%**, and
in the pre-adjustment NVDA sample it cut a −49.6% hold to −5.0%. It is a
drawdown filter, not an alpha source.

---

## 7. Crypto / perps

ThetaData is equities-only. **It will answer for the ticker `BTC` — with the
Grayscale Bitcoin Mini Trust ETF, $33.82 on 2026-09-17.** That is a real
instrument at a real price that is not bitcoin. `crypto_source.is_crypto`
therefore routes on ticker *shape*, and a bare `BTC` deliberately does **not**
route to crypto.

Type `BTC-PERP`, `ETH-PERP`, `SOL-PERP`, … or `BTC-USD` for spot, or any OKX
instrument id (`BTC-USDT-SWAP`).

Venues probed from this host on 2026-09-18:

```
binance perp/spot   HTTP 451   geo-blocked
bybit    perp       HTTP 403   geo-blocked
okx      swap       OK         <- real perpetuals, used first
coinbase spot       OK         <- fallback
binance.us / kraken OK         (spot, not wired)
```

`CandlePayload.source` names the venue that answered, because "BTC-PERP off
Coinbase spot" and "off the OKX swap" are different instruments with different
basis. Verified live: BTC-PERP 1d = 365 bars, 15m = 960 bars, $80,991.

---

## 8. Environment knobs

| var | default | effect |
|---|---|---|
| `CHART_APP_REFRESH_SECONDS` | 45 | background bar refresh (1d backs off to ≥900) |
| `CHART_APP_FLOW_DAYS` | 5 | session dates per flow pull — **billed** |
| `CHART_APP_FLOW_MIN_PREMIUM` | 10000 | provider-side premium floor — see §3.1 |
| `CHART_APP_WHALE_MODE` | adaptive | `adaptive` \| `fixed` ($25k) |
| `CHART_APP_ENTRY_LONG` / `_EXIT_LONG` / `_ATR_STOP` | 30 / −12 / 4.0 | conviction levels |
| `CHART_APP_ALLOW_SHORT` | false | enable short entries |

---

## 9. Backtest CLI

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m chart_app.backtest_runner --stage all
#   --stage pull|base|sweep|wf|perm|all   --tickers SPY,QQQ   --folds 4   --trials 60
```

Data caches to `artifacts/chart_app_backtest.db` — a **separate** DB from the
live chart's `artifacts/chart_app_bars.db`, so a sweep cannot repoint the
window Jason is looking at. Report lands in
`artifacts/chart_app_backtest_report.json`.

Pulls are serialised with a 0.5s gap and retried on `PHTimeoutError`.

~~Two intraday pulls failed on the first run and succeeded on retry — that is
rate-limit noise.~~ **Corrected 2026-09-19:** that was at least partly httpx's
5.0s default timeout on our side, not the provider (§3.16). Serialising is
still right — concurrent PHClient callers genuinely do time each other out —
and the retry still covers real 502s and the vendor LARGE_REQUEST ceiling.
Diagnose with the clock: a failure at ~5.0s exactly means the timeout is not
being applied.

---

## 9b. Sizing model (2026-09-28) — supersedes the `max_units` × `unit_fraction` notes above

The backtest is a **ledger** now: cash, shares and a margin loan, in dollars,
from a **$1,000,000** pool (`backtest.DEFAULT_CAPITAL`). Every fill is in
`BacktestResult.orders` / `/api/backtest` `orders` and in the tester's order
list: `BUY 150 SPY @ 663.41 = $99.5k`, not "1/3 of a unit". Model and venue
terms live in `sizing.py`.

| key (fraction in config, % in the panel) | meaning |
|---|---|
| `position_size` | gate 1 — the full position as a share of equity |
| `entry_slice` | gate 2 — share of THAT per entry/add order; 1.0 = all in at once |
| `exit_style` `all`/`scale`, `exit_slice` | exit all at once, or sell `exit_slice` of the full position per action (cooldown bars apart) |
| `trim_long`/`trim_short` (panel: **de-risk**) | sell one `exit_slice` when conviction WEAKENS to this level |
| `take_profit` e.g. `[5, 10]` | resting limit sells, one `exit_slice` each at +5% / +10% off the average entry, once per trade, cooldown-free |
| `margin_pct` | share of each buy that is BORROWED. Never changes the share count. Hard cap 80%; venue caps: stock 50% (Reg T), perp 1−1/max-lev, spot crypto 0% |
| `fractional` | whole shares unless ticked (BRK-A) |

Why: the unit ladder made an order a fraction of a position rather than an
amount of money, so a 5-unit cap turned every order into a fifth of a share
on a small book (the live ADA sleeve printed $0.25 buys and sells). It also made
"pyramiding" into leverage nobody financed: each added unit was a whole extra
book, with no loan and no interest.

Margin is real now: interest on the loan every bar at Robinhood's tiered rate
(5.0/4.8/4.5%, first $1k free), a maintenance line (25% stock, ~0.9/max-lev on a
perp) that forces a `margin_call` sale, repayment pro rata as the position comes
off. Perps charge no interest; **funding is not modelled** (no history).

**Old profiles** (no `position_size`) resolve to the exact ladder they always
walked, and `position` still publishes `level × max_units`, so
`position × unit_fraction` stays the exposure for the live runners. The one
deliberate difference: a pyramiding profile (`unit_fraction=1.0`) is now a
margin account, borrowed share capped by the venue, and pays interest.
The tester saves new profiles with `max_units=1, unit_fraction=position_size`
so the live runners' 1x guard still reads the real exposure.

**Not done yet:** the live runners (`run_live_perp`, `run_live_equity`,
`run_sleeve`) still size with `units × unit_fraction × full` and do not place
take-profit orders, and `perp_sleeve.replay` calls `run_backtest` without a
`ticker`, so its quoted metrics use stock (Reg T) terms on a perp.

---

## 10. Open items, highest value first

Lookback / EMA200 / `liq_window` 5 are done (see §3.10). Still open:

1. **Whale flow may lead price *inversely*.** Measured on SPY 15m, 134 bars:
   net premium at lag +1 correlates **−0.17 to −0.26** with the next bar's
   return — heavy call buying preceding falls. That is the dealer-hedging
   "fade the flow" signature, and if it holds the whale component's **sign is
   backwards**. But: permutation p = **0.065** (not significant), split-half
   **−0.02 vs −0.28** (does not replicate), and the sign test has n = 10.
   **Do not flip the sign on this.** Pull flow for several symbols over
   several weeks and re-test properly. This is the single highest-value open
   question in the app.
2. **Split adjustment in `Vol_Suite/correlation_engine.py`** — see §4.
3. **Per-symbol profiles need a per-symbol transfer check.** §3.18 measured
   tuning transfer across the 8 daily names: ranking carries (rho +0.55
   median, 6/8 leave-one-out above coin-flip) but a quarter of the time it
   does not, and MSFT landed in the bottom 3% of its own grid. A profile
   blessed by a universe-wide sweep is not blessed for the symbol it runs on.
4. **The conviction engine has no edge (§6).** If it is meant to become
   tradeable rather than decision-support, the next honest step is regime
   conditioning (only take longs above a higher-timeframe filter) — tested by
   walk-forward, not by staring at an in-sample sweep.
5. ~~`exit_long` barely matters — the ATR trail is doing nearly all the
   exiting.~~ **Both halves of that were wrong (measured 2026-09-19, all 11
   cached series).** Exits actually split `atr_stop` **50.8%** / `score_exit`
   **43.8%** / `end_of_data` 5.5% — the trail does about half, not "nearly
   all". And `exit_long` moves the result a lot (median 7.9% at −12 vs 31.3%
   at −40), but **do not loosen it on that**: −40 means you effectively never
   exit on score, so the gain is bought with exposure, the same trap §6
   describes for widening the ATR stop. `atr_stop_mult` is non-monotonic
   across 2.0–9.0 (12.4 / 6.5 / 7.9 / 1.1 / 9.1) — that is noise, not a
   surface with an optimum on it.
6. Dealer/liquidity lines from `dealer.weighted_greeks_summary` +
   `support_resistance.snapshot` — the original "next" item, still not started.
7. `flow.recent(window=15)` for the live last bar between daily stamps.

---

## 11. Rules that must not be broken

- **No order route.** There is no `/api/order` and there must never be one.
  Live Robinhood only via `mcp__robinhood__place_equity_order` when Jason says
  so *in chat*, then `POST /api/rh` to display it. `perp_sleeve` is a shadow
  book and must stay one — `crypto_source` is read-only public endpoints with
  no key, and that is a feature.
- **1x max notional.** `max_units * unit_fraction` must not exceed 1.0.
  `unit_fraction` defaults to 1.0 when a profile is saved from the tester,
  which turns `max_units` into a leverage multiplier — say the product out
  loud whenever a profile is saved or reviewed. Fix leverage by cutting
  `unit_fraction`, **never** `max_units`: the scale-in/scale-out is the risk
  control, and collapsing it to 1 unit measured worse on every axis
  (§3.20).
- **Never call ThetaData per bar.** One flow query per session date, cached.
- **Never fan out ThetaData calls.** Concurrent callers time each other out.
- **Localhost only.** No auth exists by design.
- A fallback must never be readable as a measurement. `vol_source`-style
  provenance (`sigma_source`, `whale.status`, `elmo.has_volume`,
  `ConvictionResult.available`) is the house pattern — keep it.
