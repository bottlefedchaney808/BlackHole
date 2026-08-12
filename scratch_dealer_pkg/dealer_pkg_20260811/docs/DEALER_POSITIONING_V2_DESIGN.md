# Dealer Positioning v2 — Vol-Surface-Implied Net Buying Pressure

Status: design sketch, not yet implemented. Companion to `dealer_positioning.py`
(v1: OI-based call=+/put=- heuristic) and the in-house pricer at
`Monte-Carlo-American-Pricer-Greeks/`.

## 1. What's wrong with v1

`_dealer_sign()` in `dealer_positioning.py` assumes every call contract's OI
is a customer-long/dealer-short position and every put contract's OI is the
opposite, unconditionally, for every strike, every day. It's the SqueezeMetrics
public-GEX convention, and it's a fine baseline — but it's a *constant*,
applied regardless of what actually happened in that name that day. It can't
tell the difference between "retail is buying calls into a rally" and "a fund
is buying calls to unwind a short," and it says nothing about which strikes
are seeing real pressure today versus stale OI sitting there from a trade
three months ago.

## 2. Core idea

Bollen & Whaley (2004, *JF*) showed that net buying pressure — signed
customer order flow — is what actually bends the implied vol surface out of
its frictionless shape: strikes under heavy net buying trade rich (elevated
IV relative to a no-friction benchmark), strikes under net selling trade
cheap. Their paper measures buying pressure directly from signed volume. We
can't always get clean signed volume (see §4), but we *do* have something
most people replicating this literature don't: a working in-house pricing
stack (Standard/CRR, SABR, Vanna-Volga, Heston in
`Monte-Carlo-American-Pricer-Greeks/`) capable of producing a "frictionless"
reference IV surface independent of that day's order flow.

**The modification**: instead of (or alongside) signed volume, use the
*deviation between the live market IV surface and our own model-implied
reference surface* as a continuous, per-strike proxy for net buying
pressure, and let that deviation — not a fixed call/put rule — set the sign
and magnitude of dealer exposure at each strike.

```
deviation(K, T) = IV_market(K, T) − IV_reference(K, T)
```

Where `IV_reference` comes from a SABR (or Vanna-Volga) fit anchored on the
points least likely to be flow-distorted — near-ATM, where two-sided flow
roughly cancels — and left to extrapolate its own frictionless shape into
the wings, rather than being fit to the whole (already flow-distorted)
smile. A strike trading rich to that reference has more net demand pressing
on it than the frictionless model would predict; a strike trading cheap has
net selling pressure. That's a continuous, day-specific signal instead of a
static assumption.

This closes the loop back to the original ask, too: `IV_reference` is
exactly the "run our own calculation to calibrate against ThetaData" idea
from the first pass at this feature, now load-bearing instead of a side
check.

## 3. Signal stack (ship in layers, not one big bang)

Four layers, increasing in rigor and data cost. Each is independently
useful; later layers refine/confirm earlier ones rather than replacing them
outright, so v1 stays as the fallback when a higher layer's data is
unavailable for a name/day.

**Layer 0 — existing OI heuristic.** Already shipped. Stays as the
always-available fallback.

**Layer 1a — vol-surface deviation (EOD, cross-sectional).** The core idea
above. Needs: today's market IV per strike (already pulled via
`option_bulk_greeks`/`all_greeks`) + a same-day SABR fit from the in-house
module. No new ThetaData data requirements beyond what v1 already fetches.
Cheapest layer to ship, and it's the one that's actually "ours" —
everything downstream of this is refinement. The in-house
`Monte-Carlo-American-Pricer-Greeks` module already has a Vanna-Volga fitter
(`VannaVolga.py`) alongside SABR — worth fitting the reference surface both
ways and comparing, not just as a robustness check on Layer 1a itself, but
because VV is FX-native and known to be weaker on equity wings, so a
SABR/VV disagreement at the wings is itself a useful diagnostic rather than
noise to average away.

**Layer 1b — replication-implied reference position (EOD, per-expiry).**
Where Layer 1a asks "how does the market's IV surface deviate from a
frictionless benchmark," Layer 1b asks a different question: "what would
the *position* look like if a hedger were running an optimal variance-
replicating hedge using only the strikes this expiry actually has?" Run
Demeterfi et al.'s Appendix A recursion — the same one behind Figure 3 —
directly on the real strike ladder for that expiry: `w(K₀)` set by the
slope of the log-payoff to the next strike out, each subsequent `w(Kₙ)`
that same local slope minus everything already allocated closer in. That
recursion needs nothing Layer 1a doesn't already have (today's IV per
strike, the per-expiry forward `S*`) and produces a **signed weight at
every strike in the actual chain** — the position itself falls out of the
replication, it isn't asserted by a call/put convention.

Two things worth being precise about, both surfaced by working through the
underlying assumption rather than skipping past it:

- *This is not an assumption that a liquid variance swap trades on the
  name.* It doesn't need one. The claim is about hedging *style*, not
  product liquidity: if a hedger wants a clean, variance-neutral book built
  only from the standard options actually available, this construction —
  the standard result in the variance-swap replication literature — is the
  closest thing to an optimal way to build it, with or without a bespoke
  swap contract existing on top. That's a real assumption (see the
  confidence-weighting idea below for how to stop treating it as a binary
  yes/no), but it's a materially weaker one than "this name has a liquid
  variance swap market."
- *Figure 3's two failure modes stay separate, not blended.* (b) range
  truncation — vega sags once price exits the available strike range — and
  (c) spacing corrugation — ripples between strikes that worsen near
  expiry even with a wide range — are structurally different failures of
  the same recursion, driven by different properties of the chain (how far
  it extends vs. how densely populated it is), and probably want different
  corrections. Track and report them as two separate diagnostics per
  expiry, not one collapsed "imperfectness" score.

**Layer 1b + Layer 2 fusion: seed + accumulate, not repeat + sum
(resolved 2026-07-22).** `compute_replication_reference()` (a single day's
snapshot) implicitly assumes the entire hedge position was built TODAY,
anchored at today's spot as S*. Real dealer positions accumulate over many
days at many different reference spots -- whatever day each underlying
trade actually happened. Re-running the snapshot recursion on several past
days and summing the results does NOT fix that: each day's recursion
already represents a *full* variance-hedge notional on its own, so adding
several together double/triple/N-counts the same thing. The only
genuinely additive quantity across days is a real observable -- day-over-
day open interest CHANGE (Layer 2's own data). So the construction is
seed-plus-accumulate: pick a start date, seed an initial position there
(either a single replication snapshot, or fall back to the flat v1
call+/put- heuristic if day 1 itself shouldn't be assumed clean), then
walk forward day by day, pulling each day's real OI change per strike and
signing it with THAT DAY's own replication-implied direction (that day's
own spot as S*, that day's own smile) -- not today's. This makes Layer 1b
the thing that gives Layer 2's OI-flow classification its sign, rather
than the two running as separate, parallel estimates, and it directly
produces the number that should anchor "what to hedge to from this point
forward" instead of a hypothetical "if rebuilt today" re-derivation.
Implemented in `replication_reference.py`'s `compute_accumulated_position()`
/ `_accumulate_from_history()`, validated (aggregation arithmetic only, not
live data) in `tests/test_replication_reference_accumulation.py` --
confirmed the construction doesn't re-count full notional on accumulate
days (each day's net change stays a clear order of magnitude below the
one-time seed's), and reproduces the expected direction on a synthetic
"customers chase a rally with fresh call buying" scenario (dealer position
accumulates short, concentrated at the strikes the rally passed through).
Needs two new, not-yet-live-confirmed ThetaData methods
(`option_bulk_hist_oi`, `option_bulk_hist_greeks` in `thetadata_client.py`)
-- same "smoke-test field names before trusting them" caveat as every other
inferred-path endpoint in this doc. Not yet scaled by each strike's
relative weight within the strip (a central strike and a tail-edge strike
are currently trusted equally once both are in that day's OTM set) --
flagged as a refinement, not a claim it wouldn't help.

**Confidence-weighting Layer 1b, not just validating it after the fact.**
Rather than deciding up front "trust this more on SPY, less on a meme
stock" by hand, measure it: compute an implied-remaining-variance
consistency metric across the expiries actually available for that
ticker/day — in the spirit of Carr & Sun's no-arbitrage construction (see
§4/§8 — their paper is interest-rate swaptions, not equity options, but the
principle ports cleanly: remaining variance implied by different maturities
has to cohere under no-arbitrage, regardless of asset class). Where that
term structure comes out clean and internally consistent, the "hedgers are
running something like an optimal variance hedge here" assumption is
earning its keep, and Layer 1b's position should carry real weight. Where
it's noisy or arbitrage-violating, that's the market telling you this name
isn't being cleanly variance-hedged, and Layer 1b's contribution should be
downweighted in favor of Layer 1a/Layer 0 — not discarded outright, just
trusted less. This turns "is this a liquid-index name or a speculative
single-name" from a hardcoded rule of thumb into something computed fresh
per ticker/day directly off the chain, which is a better answer to the
question than either of us guessing by name recognition.

**CAVEAT (2026-07-22): the "3rd paper" (Implied Remaining Variance in
Derivative Pricing, JFI Spring 2014) this idea leans on turns out to be
compromised, independently confirmed here, not just taken on a pasted
critique's word. Re-derived the paper's own closed-form smile, eq. (40)-(41)
— ω(K) = √2ρ - 2 - a₁ + √D(K/F), D = (√2ρ-2-a₁)² + 2a₀ + 4√2ρ·ln(K/F) +
4ln²(K/F) — symbolically in sympy: completing the square shows D(x) is
IDENTICALLY 4(x-m)² + 4σ² for m=-ρ/√2, σ²=(c²+2a₀-2ρ²)/4 (residual exactly
0), which makes ω(x) = c + 2√((x-m)²+σ²) — the SYMMETRIC special case of
Gatheral's SVI (2004, a decade prior, uncited), with the ρ-tilt term that
gives SVI its skew locked at a fixed coefficient (2) rather than a free
parameter. Consequence, also confirmed symbolically (`sp.limit` of the
wing derivative as x→±∞): wing slope is EXACTLY ±2 — the Lee moment
boundary itself — for every possible (a₀,a₁,ρ), not a fitted quantity. Real
equity index skews need sub-maximal (<2) AND asymmetric (put wing ≠ call
wing) slopes, which this family cannot produce at all, structurally, ever.
The paper's own "positivity" condition a₀>ρ² (eq. 45) is the same
minimum-total-variance-≥0 condition SVI already has. Empirics are also
thin as reported: one date (2012-06-28), seven swaption smiles, no error
metric beyond eyeballing a table, no out-of-sample check, no parameter
stability check, and no test of the actual claimed contribution (the
dynamic/PDE argument, not just the static fit).

Practical impact: NONE on shipped code — grepping the whole module tree
confirms IRV was never implemented, only referenced here as a planned
confidence-weight (this section, §6's schema sketch, §7's staged build
plan, §9's risk list — 7 references total, all "planned"/"deferred," zero
in any `.py` file). So there's nothing to roll back. But every one of those
references implicitly assumed THIS paper's construction as the mechanism;
if the IRV confidence-weight idea is still worth building when Layer 1b
gets there, it needs a different foundation — real (asymmetric, full
5-parameter) SVI or SSVI cross-expiry consistency, not this paper's
symmetric-SVI-in-disguise. The one piece worth keeping, per the same
critique and my own read: the "remaining variance" clock framing itself
(ω as a function of time-to-expiry, not the specific parametric family) is
a reasonable adjacent idea to the vanna/charm decay projections already in
this codebase — as a conceptual bridge, not a fitting engine.

**Layer 2 — OI-change flow (EOD, day-over-day).** Compare today's per-strike
OI to yesterday's to classify each strike as net-opening or net-closing, and
combine that with the Layer 1a deviation as a confirming/conflicting signal
(e.g. rich + growing OI is a stronger buy-pressure read than rich +
shrinking OI, which is more likely a stale mispricing or short-covering).
Use the **bulk** historical OI endpoint (root+exp → whole chain in one
call), not the single-contract `hist/option/open_interest` (needs
root+exp+strike+right *per call*, tier-gated at **Value or higher** — fine
for cost, wrong shape for pulling a full chain efficiently).

Important precision on "real-time" here (confirmed from ThetaData's own
docs, both the historical and snapshot OI endpoints say this explicitly):
OPRA only publishes open interest **once per day**, ~06:30 ET, reflecting
the prior session's close. The *snapshot* endpoint (bulk/single, same tier
gate) just serves that day's already-published figure on demand instead of
waiting for a next-day historical/batch pull — it resets its cache at
midnight and does not change again intraday. So Pro tier + polling the
snapshot removes ingestion lag on Layer 2 (today's OI is usable the moment
OPRA publishes it, not tomorrow), but it does **not** make OI an intraday
signal — OI fundamentally isn't one. Actual intraday positioning shifts are
Layer 3's job (trade+greek flow), not Layer 2, regardless of how fast the OI
feed is polled.

**Layer 3 — trade-level signed flow (tick, Lee-Ready style).** For strikes
where it matters most (near the gamma flip, largest OI, whatever the
earlier layers flag as ambiguous), pull `hist/option/trade_quote` (or the
live streams — see §4) and classify each print as buy- or sell-initiated by
comparing trade price to the prevailing NBBO midpoint at that timestamp,
then weight by the trade's own greeks (`all_trade_greeks` gives you greeks
computed *at the trade*, not just EOD) to get actual signed
gamma/vanna/charm flow instead of an inferred sign on end-of-day OI. This is
the "as good as it gets without being the dealer" layer — expensive in data
and engineering, so it's scoped to specific strikes/situations rather than
run universally.

## 4. Data inventory (confirmed live, 2026-07-21)

Pulled from `https://http-docs.thetadata.us` directly — this supersedes any
guesses in earlier code comments.

REST, historical (all confirmed to exist as endpoints; options history goes
back to 2012-06-01, equities/greeks generally to 2020-01-01+ depending on
ticker/tape):
- `hist/option/open_interest` and bulk variant — daily OI history per
  strike. This is what makes Layer 2 possible without having to start
  recording our own snapshots from scratch.
- `hist/option/trade_quote` and bulk variant — trade prints paired with the
  quote in effect at trade time. This is the Lee-Ready input for Layer 3.
- `hist/option/all_greeks`, `all_trade_greeks` — full greek history,
  including *at-trade* greeks (not just EOD), and implied vol history
  directly.

  **`bulk_hist/option/all_trade_greeks` confirmed live (checked
  2026-07-21)** — this is the single best endpoint for Layer 3 and worth
  calling out on its own. One row *per trade*, tick-level (`ivl=0`), already
  carrying the full greek stack computed at that exact trade:
  `[ms_of_day, sequence, ext_condition1-4, condition, size, exchange, price,
  condition_flags, price_flags, volume_type, records_back, delta, theta,
  vega, rho, epsilon, lambda, gamma, vanna, charm, vomma, veta, vera, speed,
  zomma, color, ultima, d1, d2, dual_delta, dual_gamma, implied_vol,
  iv_error, ms_of_day2, underlying_price, date]`. That means for Layer 3 we
  don't need to separately reconstruct greeks per trade — ThetaData already
  did it — we only need to add the buy/sell classification on top (trade
  `price` vs. the prevailing NBBO at `ms_of_day`, pulled from
  `trade_quote`/quote history, since this endpoint doesn't carry the NBBO
  itself). Once classified, "signed vanna flow at this trade" is just
  `sign * vanna * size * CONTRACT_MULTIPLIER`, summed per strike per day —
  this is the actual Layer 3 build target, not a derived approximation of
  one.

  One catch worth flagging: this is a **Pro-tier** endpoint per ThetaData's
  own docs, same gate as the Full Trade Stream. On the connection path —
  ThetaData's own sample URL for it hits `http://127.0.0.1:25510/v2/bulk_hist/...`,
  which is the local Theta Terminal's own HTTP server, but per Jason,
  `api.potatohedge.com` *is* that Theta Terminal instance (the proxy runs it
  and fronts its REST API) — same reason `bulk_snapshot/option/all_greeks`
  already works through it today. So the analogous
  `/api/theta/bulk_hist/option/all_trade_greeks/{root}/{exp}` path should
  work the same way with no new REST plumbing; still worth a one-line smoke
  test the first time (same as `debug_print_greek_fields`) before building
  on top of it, same as any endpoint this codebase hasn't called yet.
- Flat file bulk downloads exist too (`flat-file/option/{trade-quote,
  open-interest, eod}`) — probably the more efficient path once we're
  pulling full-name history rather than paginating REST for months of data;
  worth checking against the request-sizing guidance
  (`Performance-And-Tuning/Request-Sizing`) before committing to one or the
  other.

Streaming (the one still-open question, not a confirmed gap): `Full Trade
Stream`, `Trade Stream`, and `Quote Stream` are **WebSocket** feeds at
`ws://127.0.0.1:25520/v1/events` — a different port and protocol from the
REST endpoints (`:25510`) that `api.potatohedge.com` already fronts. Since
the proxy *is* the running Theta Terminal instance, the REST side (port
25510, everything else in this section) should already work end to end with
no new infrastructure. Whether the proxy also forwards the WebSocket port
(25520) is the one thing that genuinely needs a direct check — an HTTP
reverse proxy fronting one port doesn't automatically forward a different
port/protocol unless it's been explicitly configured to. Cheap to test:
point a `websockets.connect()` at `wss://api.potatohedge.com/v1/events` (or
whatever the equivalent path turns out to be) with the same Cloudflare
Access headers and see what comes back. Subscription tiers still apply
regardless of routing: bulk "Full Trade Stream" needs **Options Pro**
(already confirmed in hand); single-contract `Trade Stream`/`Quote Stream`
need **Options Standard** or better. Worth noting: the Full Trade Stream
already brackets each trade with the last NBBO quote before it and two
after, which is basically a free Lee-Ready input with no separate
quote-stream subscription needed.

Practical read: Layers 1 and 2, and Layer 3's *batch/historical* mode, all
run on REST and should work today through the existing proxy with no new
infrastructure. Only Layer 3's *live* mode depends on the WebSocket check
above — that's the one piece worth confirming before planning around it,
not the whole of Layer 3.

One more thing worth building Layer 3 on top of rather than around: ThetaData
also exposes **raw, unaggregated trade rows** (`/v3/{stock,option,index}/history/trade`,
per their "Build your own OHLC" docs) where every row carries the actual SIP
trade condition code, not just a pre-filtered summary. ThetaData's own OHLC
aggregation already does useful cleanup we'd otherwise have to reinvent —
dropping cancelled trades (condition codes 40-44), a ±5% price-deviation
sanity check against phantom prints (skipped for sub-$5 references, where
that swing is normal), and SIP-condition-based filtering of what's allowed
to set Open/High/Low/Close. For Layer 3 we want the **raw** trades, not the
aggregated bars, precisely so we can make our own inclusion/exclusion calls
per print before Lee-Ready-classifying it — e.g. explicitly deciding whether
to keep or drop late reports and Form T (pre/post-market) prints, which
ThetaData's OHLC path excludes from H/L/O/C by design but which might still
be legitimate signed customer flow we want in a positioning model. The
condition-code table is at `Articles/Data-And-Requests/Values/Trade-Conditions`;
the exchange-code table (`.../Values/Exchanges`) is worth keeping alongside
it — knowing whether a print (or the quote we're comparing it to) came from
a lit exchange vs. an off-exchange/dark venue matters for how much we trust
that particular NBBO-vs-trade-price comparison. Same idea applies to quote
quality: filter by quote condition (`.../Values/Quote-Conditions`) before
trusting a given NBBO snapshot as the reference for classifying a trade
against it — a quote flagged as non-firm or from a slow/away market maker is
a bad basis for a buy/sell call. Worth writing the Layer 3 classifier
against these raw endpoints from the start rather than layering custom
filtering on top of ThetaData's already-filtered OHLC/trade_quote output.

## 5. Where this lives in the codebase

- `vol_surface_reference.py` (new, Layer 1a): builds
  the `IV_reference` surface for a given ticker/day by calling into
  `Monte-Carlo-American-Pricer-Greeks`'s SABR *and* Vanna-Volga fitting
  (imports across the two projects the same way `dealer_positioning.py`
  already imports `thetadata_client`), anchored on near-ATM strikes only.
  Fits both and reports where they disagree, especially at the wings,
  rather than picking one and discarding the comparison.
- `replication_reference.py` (built; Layer 1b, now
  also the Layer 1b+2 fusion point -- see §3's "seed + accumulate"
  resolution). Two entry points: `compute_replication_reference()` for a
  single day's snapshot (Stage 2 real-chain sanity checks), and
  `compute_accumulated_position()` for the seed-plus-accumulate cumulative
  position over a lookback window. Both syntax-checked and smoke-tested
  against mocked data in this sandbox (no live network access here); the
  single-day path has also been run live against real SPY/GME chains by
  Jason, which is what caught the range-truncation and delta-hedge-drift
  normalization bugs described above.
  Implements the Demeterfi Appendix A recursion per-expiry on the actual
  strike ladder — signed weight `w(K)` per strike, plus the two failure-
  mode diagnostics (range truncation, spacing corrugation) computed and
  reported separately. Also computes the implied-remaining-variance
  consistency metric across that ticker's available expiries, used to
  confidence-weight how much Layer 1b's output should count relative to
  1a/Layer 0 for that ticker/day (see §3).
  **Resolved (was an open question): the financing leg is a synthetic
  delta hedge via a high-delta option in the same chain, not a stock
  position, and it does land at a strike after all.** The option strip is
  long-only by construction (convexity of the log-payoff approximation
  guarantees every recursion weight is non-negative); the only genuine
  short in the whole construction is the linear financing leg. Rather than
  express that leg as stock/futures (which has no natural home on a
  strike-indexed chart and pulls the model outside the options universe
  this whole project is built on), assume the hedger delta-neutralizes the
  *whole* book — documented standard practice in both source papers (the
  replicating portfolio "must be delta-hedged," per JPM §4.10) — using the
  **highest-|delta|, still-liquid strike actually available in that
  expiry's chain** as a stock proxy: short a deep-ITM call or long a
  deep-ITM put (whichever sign the net delta requires), sized so
  `contracts × delta × 100` zeroes the strip's net delta. This isn't just
  operationally convenient, it's the right instrument for the reason that
  matters here: deep-ITM gamma and vega are both small (gamma peaks ATM and
  decays going deep ITM; vega does too), so parking the delta-hedge there
  barely touches that strike's gamma/vega/vanna/charm signal — consistent
  with, not in tension with, the "only delta gets neutralized" rule below.
  Strike selection needs real OI/liquidity at the deep-ITM end, not just
  the theoretical highest-delta strike on paper — pick among the liquid
  deep-ITM strikes, not a zero-OI one nobody actually trades. There's a
  clean anchor for the target delta itself: the ideal continuous
  log-contract has delta Δ = -(2/T)(1/S), i.e. a **constant $(2/T)
  delta-equivalent**, independent of S, in the frictionless limit
  (Demeterfi Eq. 8-9 region). The real, discrete strip's *actual* summed
  delta will drift from that constant because it's only approximating the
  log contract — track that drift as a **third** failure-mode diagnostic
  (`delta_hedge_drift`) alongside range truncation and spacing corrugation,
  same reasoning as those two: it's the same finite-chain-vs-infinite-ideal
  problem showing up in a different greek, not a new phenomenon.

  **Explicitly does NOT extend to neutralizing gamma/vega/vanna/charm.**
  Delta gets neutralized because it's cheap, always available (trade the
  underlying), and standard desk practice. Gamma and vega are the opposite
  case — the strip is deliberately built to have a specific non-zero,
  roughly-constant variance-vega (Figure 3's whole point) and a specific
  non-zero gamma (Demeterfi Eq. 11, Γ = (2/T)(1/S²) by design). Those are
  the model's *output*, not something to hedge away — zeroing them would
  erase the exact signal this whole exercise exists to produce.
- `dealer_positioning.py`: **built (2026-07-22)**.
  `compute_dealer_positioning()` now takes `sign_model` (`'oi_heuristic'` =
  v1 default, `'replication'` = Layer 1b -- `'vol_surface'`/Layer 1a and
  `'vol_surface_oi_flow'`/Layer 1a+2 remain future work, not implemented
  yet). `_resolve_sign()` replaces the flat `_dealer_sign()` call: under
  `'replication'`, each expiry's chain is classified into its OTM
  replicating set once (via `replication_reference._otm_leg_weights()`,
  that expiry's own spot/IV), and only the SIGN/GATE from that
  classification is reused (flat -1 on included OTM legs, 0 on ITM ones) --
  not the weight magnitude itself, so gamma/delta/vanna/charm stay in the
  same units as the v1 chart for a fair like-for-like comparison per §8,
  rather than being rescaled by the tiny natural units the recursion's own
  weights carry. `GammaRecord.applied_sign` records which sign each record
  actually got, reused (not re-derived) by the gamma-flip surface
  calculation downstream so the whole chart -- bars, gamma-flip level, and
  hedging surface -- agrees on one convention per run. `run_dealer_positioning()`
  and `main()` both thread `sign_model` through, and the chart title now
  shows which convention produced it. v1 stays a real, still-supported
  mode, not replaced. Validated (6 tests) with a fake ThetaDataController
  stand-in: both models run without exceptions, produce genuinely
  different net gamma, and `'replication'` correctly zeroes ITM legs while
  leaving OTM ones nonzero.

  **Run live on SPY and QQQ (2026-07-22) -- surfaced a real structural
  limit of `'replication'` alone.** Every strike came back negative
  gamma, on both tickers, 100% of the time: confirmed by direct CSV
  inspection (7,812 SPY records: 1,847 OTM calls + 2,968 OTM puts, every
  one signed -1, zero exceptions). Root cause isn't a bug -- raw
  Black-Scholes gamma has no call/put sign asymmetry, and the recursion's
  own weights are non-negative by construction (the same convexity
  property that makes Layer 1b's math sound at all), so a flat -1 gate
  can NEVER produce a positive gamma bar, on any ticker, full stop.
  Delta/Vanna/Charm didn't collapse the same way because those greeks
  *do* have real call/put sign asymmetry that survives a flat multiplier
  -- confirming Gamma specifically, not the sign model generally, is
  what's structurally blind under `'replication'` alone. See the new
  Layer 1a entry below for the fix.

  Also caught and fixed two rendering bugs from that same live run: (1)
  the header title collided with the stats row once the sign-model tag
  was appended to the same string -- fixed by moving to well-separated
  `fig.text` rows instead of a cramped shared-axis title (two attempts;
  the first, a taller mini-axes with the tag on its own internal line,
  still bled into the subplot title beneath it). (2) the "LONG Γ /
  DAMPEN" and "SHORT Γ / AMPLIFY" regime labels were hardcoded to always
  show both, which actively lied on a run where gamma was one-signed
  everywhere (no dampening region at all) -- now conditional on whether
  each regime is actually present in the data.

  Also added, per a live question about the existing `hedge_requirement`
  (shares/1%) metric: that number is the standard GEX-style "if hedged
  continuously via the underlying" convention -- real and still valid on
  its own terms (continuous rehedging is standardly done via stock/
  futures for liquidity reasons), but it was never reconciled with this
  project's own preference for keeping the *model* option-chain-native.
  Added a second, option-contract-equivalent hedge figure (highest-|delta|
  real-OI strike in the chain, same selection logic as
  `replication_reference.py`'s delta hedge) shown alongside the
  share-based number, not replacing it -- both readings tell you the same
  *direction*, just in different instruments.

- **Layer 1a, built (2026-07-22): `vol_surface_reference.py`.**
  Fixes the exact blind spot above. Fits a lightweight quadratic-in-log-
  moneyness reference curve on near-ATM strikes only (±15%, upgradeable
  later to the SABR/Vanna-Volga fitters already in
  `Monte-Carlo-American-Pricer-Greeks/`), then computes
  `deviation(K, right) = IV_market - IV_reference` for every strike.
  Rich (positive deviation) = net buying = dealer short, matching Layer
  1b's default. Cheap (negative deviation) = net selling/overwriting flow
  = dealer long, FLIPPING Layer 1b's default -- the one thing a flat sign
  gate can never do on its own. Per Jason: this is deliberately reading
  institutional footprint, not retail noise -- retail flow is too diffuse
  to bend a strike's IV away from a smooth curve; only size (structured
  products, overwriting programs, protective-put buying programs) does
  that, so a per-strike deviation is a plausible read on which kind of
  flow a strike actually saw.

  Wired into `dealer_positioning.py` as a third sign model,
  `'vol_surface_replication'`: still gated by Layer 1b's OTM
  classification (only WHICH sign applies to an included leg changes, not
  whether it's included at all), falls back to Layer 1b's flat -1 for any
  leg where Layer 1a has no usable deviation. `main()`'s interactive
  prompt and both chart titles updated to offer/show it as option (3).
  Validated (10 tests): the fit succeeds/fails gracefully depending on
  data availability, a deliberately-cheapened synthetic strike correctly
  flips to dealer-long while its neighbors keep Layer 1b's default, and
  -- the actual point of building this -- `sign_model='vol_surface_replication'`
  produces a genuine positive gamma bar on a synthetic overwriting-flow
  scenario where `sign_model='replication'` alone is proven (same test
  file) to stay 100% non-positive. One real bug caught before shipping:
  the first wiring attempt computed the vol-surface reference but never
  actually passed it into `_resolve_sign` (a plain missing-argument typo),
  so every leg silently fell back to Layer 1b's default and no sign ever
  flipped -- the smoke test with a deliberately-distorted synthetic
  strike is what caught it; a synthetic OI-symmetric mock (like the
  earlier `sign_model` wiring test) wouldn't have, since it never
  distorts the smile in the first place. Not yet run against real data
  from this sandbox (no network access here) -- next real step is
  running `sign_model='vol_surface_replication'` against SPY/QQQ and
  checking whether the flipped strikes correspond to plausible real
  overwriting/structured-flow names (e.g. near round-number strikes
  popular with covered-call programs), before Stage 3's realized-vol
  validation.
- `oi_flow_history.py` (new, Layer 2): thin wrapper
  around the bulk historical OI endpoint that goes through the shared cache
  below before hitting the network, so repeated day-over-day diffs don't
  re-pull the same history every run.
- `trade_flow.py` (new, Layer 3, later): Lee-Ready
  classifier over `trade_quote` / `all_trade_greeks`, scoped to a strike
  list rather than a whole chain, writing classified results through the
  same shared cache.
- `thetadata_cache.py` (new, §6): the shared SQLite
  cache + fetch-manifest module both of the above sit on top of — one
  place that owns "have we already fetched this," so Layer 2 and Layer 3
  don't each reinvent their own caching logic.

## 6. Caching layer (cost control) — BUILT, THEN REMOVED

> **STATUS (2026-07-24): removed. `thetadata_cache.py` no longer exists.**
> Everything below is kept as the design record of what was built and why it
> was pulled — the reasoning matters more than the code did.
>
> **What went wrong.** The cache's central rule — "a (root, expiration,
> data_type, date) that has been fetched is never fetched again, full stop" —
> is only safe if the fetch layer can reliably distinguish *"I looked and
> there is genuinely nothing here"* from *"the request failed."* Against
> `api.potatohedge.com` it can't. The proxy returns false-negative 404s
> (proven non-monotonically in `diagnostics/diagnose_wide_range_chunks.py`)
> and transient 502s in bulk, so a single bad response during a backfill got
> written into `fetch_manifest` as a completed date — permanently. Every
> later run then served that hole straight out of SQLite without ever
> touching the network to discover it was wrong. Observed live: a 5-month
> requested range ended up with rows for one single day, with the manifest
> claiming full coverage.
>
> **Why it wasn't just patched.** The failure was fixed twice — first by
> making enumeration failures `raise` instead of returning `[]`, then by
> adding retry-before-trust on 404s. Both fixes were correct and both are
> still in `thetadata_client.py`. But the cache had by then inverted its own
> value proposition: every debugging iteration began with deleting the DB
> file, because otherwise the newly-fixed code would never run against a
> range the broken code had already marked done. A cache that must be
> cleared before each run to be trusted is a liability, not an optimization.
>
> **What replaced it.** Nothing. `backtest_stage3.py` and
> `replication_reference.py` call the client directly. Runs are slower and
> always correct, which is the right trade for a research tool where a wrong
> answer is far more expensive than a slow one. The cost concern that
> motivated this section is now handled at the request layer instead —
> bounded retry plus tunable concurrency (`THETADATA_HIST_CONCURRENCY`) —
> which reduces wall-clock time without ever introducing a stale-truth path.
>
> If caching is revisited: cache only responses **confirmed non-empty**, and
> never let a manifest entry be written by any path that didn't see real
> rows. "Absence of data" must not be a cacheable fact on this data source.

**Built (2026-07-22), removed (2026-07-24): `thetadata_cache.py`.**
`get_or_fetch_oi_history()` / `get_or_fetch_greeks_history()` wrap
`option_bulk_hist_oi` / `option_bulk_hist_greeks`, diff the requested date
range against a `fetch_manifest` table, only fetch the missing gap
(collapsed into contiguous ranges so scattered gaps don't turn into one
call per day), and always treat today as uncached. Rows round-trip in the
exact same shape the live client returns, so `replication_reference.py`'s
`compute_accumulated_position()` needed zero parsing changes to sit behind
the cache -- only a different function call. Validated (7 tests, all
passing) with a fake call-counting client standing in for
`ThetaDataController`, confirming: first call fetches, repeat calls for
the same historical range don't, extending a range only fetches the new
gap, today is always re-fetched, and OI/greeks cache independently. Not
yet exercised against a real cache-miss-then-hit cycle with live data
(same sandbox network limitation as everything else in this doc) -- the
schema below reflects what's actually implemented (two data tables +
`fetch_manifest`), not the fuller table list originally sketched (spot
history, IV reference fits, replication weights/diagnostics, IRV
consistency) -- those get added as their own modules reach this same
build-it-when-there's-a-real-caller point, not preemptively.

Jason's call: build this with caching from the start, not bolted on later
— both because ThetaData's own docs explicitly rate/size-limit large
requests (the `Request-Sizing` guidance, and the `429 OS_LIMIT` /
`570 LARGE_REQUEST` error codes in §4's error table), and because there's no
reason to re-pull the same historical day twice when it's Pro tier and
otherwise "free anything" — the cost that matters here is request volume
and wall-clock time, not per-call metering we've confirmed exists.

**Core principle: closed trading days are immutable, cache them forever;
only "today" is ever re-fetched.** OI, trades, greeks, and EOD data for a
date that's already closed do not change on a later re-pull (OI restatement
is essentially never observed in practice) — so once a given
(root, data_type, date) has been fetched, it should never be fetched again,
full stop. This is the single biggest cost lever: a backfill run only ever
needs to request the *gap* between "what's cached" and "what's needed," not
the whole history, every time.

**Storage.** One SQLite file per data type under a new
`cache/` directory (gitignored, same pattern as `.env`)
rather than scattering CSVs — SQLite gives indexed point-lookups by
`(root, expiration, strike, right, date)` cheaply, handles concurrent reads
fine for a single-user tool like this, and is one file to inspect/back up.
Suggested tables:
- `oi_history(root, expiration, strike, right, date, open_interest)` — Layer
  2's core cache. Primary key `(root, expiration, strike, right, date)`.
- `trade_greeks(root, expiration, strike, right, date, ms_of_day, price,
  size, sign, delta, gamma, vanna, charm, ...)` — Layer 3's *classified,
  reduced* output, not raw ticks. Cache the result of Lee-Ready
  classification, not the megabytes of raw prints that produced it, unless
  a specific re-audit is underway.
- `iv_reference_fits(root, date, sabr_params_json, vv_params_json)` —
  Layer 1a's SABR *and* VV fits per ticker/day, so re-rendering the same
  day's chart later doesn't recalibrate from scratch, and the two fits'
  wing disagreement can be diffed without redoing either.
- `replication_weights(root, expiration, date, strike, right, weight)` —
  Layer 1b's per-strike `w(K)` output from the Demeterfi recursion, all
  non-negative by construction (see §5 — the strip is long-only; the one
  genuine short lives separately, below).
- `replication_diagnostics(root, expiration, date, range_truncation_score,
  spacing_corrugation_score, delta_hedge_drift, delta_hedge_strike,
  delta_hedge_right, delta_hedge_contracts)` — one row per expiry/day: the
  two chain-shape failure modes from §3, the delta-hedge-drift diagnostic
  (how far the real strip's summed delta strays from the ideal constant
  $(2/T)), and which strike/right/contract-count was selected as the
  synthetic delta-hedge instrument (§5's resolution — a high-delta option
  in the chain, not stock). **`spacing_corrugation_score` must be stored as
  a relative ratio (local std of successive differences ÷ local mean
  absolute level), not an absolute standard deviation** — confirmed in the
  §8 Stage 1 synthetic test: the absolute ripple shrinks near expiry while
  the relative ripple grows, and only the relative version matches the
  paper's claimed direction.

  **`range_truncation_score` is NOT a flat-across-tickers absolute vega
  gap — caught running Stage 2 live on SPY vs. GME.** The first version
  measured the vega gap between spot and a fixed +/-30%-of-spot window;
  SPY scored 0.57 (much "worse") against GME's 0.0146, backwards from the
  obvious prior that SPY's deep, liquid 221-leg chain should score better
  than GME's sparse 34-leg one. Root cause: that metric was actually
  measuring BS-vega decay rate with moneyness, which is a function of IV
  level and T, not of chain completeness -- GME's much higher IV gives it
  a wide, slow-decaying vega bell curve, so a fixed % window looks "less
  truncated" for GME purely because of its vol level, independent of how
  many real strikes are actually listed. Fixed version measures the
  ACTUAL listed strike range in implied-vol-standardized (d1) units --
  `min(ln(S/K_min), ln(K_max/S)) / (mean_iv * sqrt(T))` -- i.e. how many
  IV-sigma-equivalent the real chain extends before running out of
  strikes on its narrower side. Dimensionless, comparable across any two
  tickers regardless of price or vol regime. **Sign convention flipped
  from the original design: LOWER now means MORE truncated/risky** (a
  narrower vol-standardized safety margin), the opposite of "higher =
  worse absolute gap."

  **`delta_hedge_drift` needs a spot-normalized variant to be comparable
  across tickers -- same live SPY/GME run caught this too.** The ideal
  continuum target `-(2/T)/S` scales as 1/spot by construction, so its
  raw drift looks far worse for a low-priced name purely from the price
  difference: GME's raw drift (0.398) looked ~36x SPY's (0.011), but
  `drift * spot` -- which cancels the 1/S scaling since
  `ideal_delta_target * spot = -(2/T)` is a universal constant depending
  only on T -- came out within ~4% of each other (SPY 8.30 vs. GME 8.59,
  both close to the `-2/T` scale itself). Store both: raw
  `delta_hedge_drift` (per-share, useful for sizing the actual hedge
  trade) and `delta_hedge_drift_normalized` = `drift * spot` (the one to
  actually compare ticker-to-ticker).
- `irv_consistency(root, date, consistency_score)` — the implied-remaining-
  variance no-arbitrage consistency metric that confidence-weights Layer
  1b, cached per ticker/day since it's derived entirely from already-cached
  IV data and shouldn't be recomputed every time Layer 1b's weight is
  looked up.
- `fetch_manifest(root, data_type, date, fetched_at)` — the "have we already
  got this" index; every fetcher checks this table first and only requests
  what's missing.

**Per-layer rules:**
- **Layer 1a/1b**: cache the SABR/VV fits, the replication weights, and the
  IRV consistency score per (ticker, date) in the tables above. All three
  are derived from data v1 already fetches — nothing new to backfill, just
  don't recompute a fit or a recursion that's already sitting in SQLite
  from earlier today.
- **Layer 2**: `oi_flow_history.py` (from §5) checks `fetch_manifest` for
  the requested (root, date range), fetches only the missing dates via the
  **bulk** historical OI endpoint, writes to `oi_history` +
  `fetch_manifest`, and always treats "today" as a cache-miss (OPRA hasn't
  published yet, or just did — see the "real-time" note above) while every
  earlier date is served straight from SQLite.
- **Layer 3**: raw trade-tape pulls are the expensive one — scope every
  fetch to an explicit strike list (never "the whole chain," per §3's own
  scoping rule), classify immediately, persist only the classified rows in
  `trade_greeks`, and let raw ticks fall out of memory once reduced rather
  than caching them long-term. If a specific day/strike needs re-auditing
  later, that's a deliberate re-pull, not a standing cache.
- **All layers**: reuse the request-chunking pattern
  `thetadata_client.hist_stock_eod` already established (paginating into
  <=1-month chunks after hitting a real 502 from asking for too much in one
  call) — same discipline, applied to OI/trade history pulls instead of
  stock EOD.

**What this buys, concretely:** the first backfill for a ticker is the only
expensive one. Every subsequent run — re-rendering yesterday's chart,
re-validating the model on a date already analyzed, adding one more ticker
to the regular rotation — touches SQLite, not the network, for every date
except the current session. That's the actual cost control: not fewer
features, just never doing the same historical fetch twice.

## 7. Staged build plan

1. **Layer 1a only, first.** Build `vol_surface_reference.py`, wire
   `sign_model='vol_surface'` into `dealer_positioning.py`, render it as a
   third mode in `plot_greek_exposure_comparison`. No historical data
   needed — this runs on the same live snapshot v1 already pulls.
2. **Validation harness (see §8)** before doing anything else with it — the
   whole point of this exercise is that it should be *provably* better than
   v1, not just more sophisticated-looking.
3. **Layer 1b**: the replication-implied reference and its two failure-mode
   diagnostics, plus the IRV consistency score that confidence-weights it
   — but only once §5's open financing-leg/sign-formulation question has an
   actual answer, not a placeholder.
4. **Layer 2**: OI history + flow classification (on top of the caching
   layer in §6 from day one), combined with Layer 1a/1b as
   confirm/conflict.
5. **Layer 3, historical/batch first**: `trade_quote`-based Lee-Ready
   classification for a short list of high-priority strikes, validated the
   same way as Layer 1/2 before it's trusted.
6. **Layer 3, live** (optional, separate effort, pending the WebSocket
   proxy check in §4): streaming integration, once the batch version has
   proven the signal is worth running in real time.

## 8. Testing & validation plan — three stages, each testing a different thing

Three distinct stages, run in order, because each one tests a genuinely
different failure mode — passing stage 1 says nothing about stage 2, and
passing stage 2 says nothing about stage 3. Conflating them (e.g. treating a
clean synthetic-math result as evidence the economic assumption is right)
is exactly the mistake this staging is meant to prevent.

**Stage 1 — synthetic math-correctness test (free, no network, run first,
already built).** Tests the arithmetic only: does our from-scratch
implementation of the Demeterfi Appendix A recursion + variance-vega
surface actually reproduce the paper's own published result (its Figure 3)
under a flat-vol Black-Scholes world where the right answer is known in
closed form? No ThetaData, no live tickers, no economic assumption is
tested here — only "did we code the recursion correctly."

Implemented as `tests/test_variance_swap_replication.py`
(pytest, `unit`-marked, no API access needed — runs anywhere, including CI).
Checks, one per chain shape from Figure 3:
- **Convexity/superhedge property** (all three chain shapes): every
  recursion weight is ≥ 0. Structural — true for any chain, since it just
  reflects the log-payoff being convex with its minimum at `S*` — so if
  this ever fails, the recursion itself is broken, independent of chain
  shape.
- **(a) Continuum (fine, wide chain):** the variance-vega surface collapses
  to the closed-form ideal `V(τ) = τ/T`, flat across spot. Matches to
  ~1.4% mean absolute error. This check is what caught a real bug during
  development: the first version used *standard* Black-Scholes vega
  (`d(Price)/dσ`) instead of *variance* vega (`d(Price)/dσ²`), which
  mismatched the ideal by ~30%; dividing the summed portfolio vega by `2σ`
  fixed it. Confirms the chain-rule factor is right before it's trusted on
  a real chain.
- **(b) Narrow/dense chain ($75–125 × $1) — range truncation:** the surface
  sags away from the continuum ideal near the edges of the available
  strike range, and that sag is **larger in absolute terms far from
  expiry** (more remaining time = more room for spot to drift into the
  truncated region, and the ideal target itself is larger far from expiry).
- **(c) Wide/sparse chain ($20–200 × $10) — spacing corrugation:** ripples
  appear between strikes because each option's own vega localizes near its
  strike. Counterintuitive finding worth flagging on its own: the
  **absolute** ripple size actually *shrinks* near expiry (overall vega
  scale shrinks toward zero), but the **relative** ripple (ripple divided
  by the local average level) *grows* sharply near expiry — measured
  0.068 far-from-expiry vs. 0.397 near-expiry in the synthetic test. That's
  what the paper's "corrugations grow more pronounced closer to
  expiration" actually refers to; an absolute std-of-diff metric gives the
  wrong verdict. **This means (b) and (c) have opposite tau-directionality
  in absolute terms** — real, structurally distinct failure modes, not two
  readings of the same thing — which is exactly why §3 already insists on
  tracking `range_truncation_score` and `spacing_corrugation_score` as two
  separate diagnostics. Consequence for §6's schema:
  `spacing_corrugation_score` should be stored as the **relative** ratio
  (std of successive differences ÷ mean absolute level in a mid-band away
  from the range edges), not an absolute standard deviation, or it will
  rank chains backwards.

**Stage 2 — real-chain sanity test (cheap, live data, a handful of
tickers).** Tests whether Stage 1's validated math produces *sensible,
correctly-ordered* diagnostics once it's pointed at real, messy strike
chains instead of a synthetic evenly-spaced one — not yet whether the
underlying economic assumption is correct, just whether the machinery
degrades in the direction it should. Pick tickers with deliberately
different chain shapes and pull one real EOD chain per name (cheap, no
history needed):
- A dense, wide, liquid chain (SPY, QQQ) — expect low scores on all three
  diagnostics (range truncation, spacing corrugation, delta-hedge drift).
- A sparser small-cap or meme name (e.g. BE, GME) — expect visibly worse
  range-truncation and/or corrugation scores, and check they degrade in a
  way that's consistent with that name's actual strike spacing/range, not
  just "worse across the board" for no traceable reason.
- Confirm the non-negative-weight property still holds on a real (not
  synthetic, evenly-spaced) chain — real chains have uneven strike spacing
  near the money vs. the wings, which is exactly the kind of irregularity
  the synthetic test doesn't exercise.
- Confirm the synthetic delta-hedge strike selection (§5) actually lands on
  a strike with real OI/liquidity for each of these names, not a
  theoretically-ideal but untraded one — this is the one piece of §5 that
  can only be checked against real chains, never a synthetic one.

Pass/fail here is about internal consistency and correct ordering, not a
numeric threshold — the point is catching "the diagnostic is nonsense on
real data" before it's ever used to weight anything in §3's confidence
scheme.

**Stage 3 — realized-vol behavioral backtest (expensive, the actual
economic claim).** Tests the thing that actually matters: does the v2
sign convention predict real dealer behavior better than v1? Borrowing the
empirical structure from Barbon & Buraschi ("Gamma Fragility") and Bollen &
Whaley: dealer gamma sign only matters because of its claimed effect on
realized volatility (short gamma → dealers trade with the tape →
amplification; long gamma → dealers trade against it → dampening). So the
test is direct:

- For each ticker/day, compute the v1 sign-implied net gamma and the v2
  (vol-surface-implied and/or replication-implied) net gamma.
- Look at realized intraday volatility / realized price-move dampening
  around the reported gamma-flip level, for both.
- Whichever model's sign more consistently lines up with the
  amplify/dampen behavior actually observed wins. This should be run over
  enough tickers/days to be a real backtest, not a handful of anecdotes —
  which is exactly what the historical OI/trade endpoints in §4 make
  possible.
- Also worth checking: does v2's deviation signal at least *reproduce* v1
  on the (presumably common) cases where the naive heuristic is obviously
  right (e.g., heavily one-sided, uncontested flow), and only diverge where
  it should?

Stage 3 is the only stage that actually validates the core hypothesis
(§2); Stages 1–2 exist purely to make sure that by the time Stage 3 runs,
a failure there is telling us something real about the economic
assumption — not masking a units bug or a mis-specified diagnostic that
Stage 1/2 would have caught for free.

**Built (2026-07-22): `backtest_stage3.py`.** For each historical trading
day in a single-expiry lookback window, classifies the dealer book as net
long/short gamma under BOTH v1 (`oi_heuristic`) and v2
(`vol_surface_replication`) — reusing `dealer_positioning._dealer_sign` /
`_resolve_sign` directly rather than a third reimplementation of the sign
logic — then labels that day with the ANNUALIZED forward realized vol over
the next `forward_window_days` trading days (default 5), and runs Welch's
t-test comparing short-gamma-day vol against long-gamma-day vol for each
model. Hypothesis: short-gamma days should show higher forward realized vol
(dampening/amplification per §2); whichever model shows the larger, more
significant gap is reading something real.

Deliberate scope limits, both cost-driven: (1) single near-dated expiry per
day, not a full multi-expiry aggregate — same simplification
`replication_reference.compute_accumulated_position` already makes, since a
historical multi-expiry pull multiplies cost across both days AND expiries;
(2) `thetadata_cache.py` gained a `price_history` table (keyed on
`(root, date)`, using the `fetch_manifest` sentinel `expiration=''` for
non-option-chain data) and a `gamma` column on `greeks_history` (added via
an idempotent `_migrate()` step on connect, since `CREATE TABLE IF NOT
EXISTS` doesn't retroactively add columns to a `cache/thetadata_cache.db`
that predates this — the original Layer 1b accumulation only ever needed
`implied_vol`/`delta`, not `gamma`) — both now feed off the same
fetch-once-cache-forever discipline as the rest of §6.

Network-free regression coverage
(`tests/test_backtest_stage3.py`, 8 tests) proves the STATISTICAL MACHINERY
itself before ever pointing it at real data — same Stage-1 discipline
applied one level up: a synthetic 28-day sample with a deliberately
embedded relationship (heavy put OI + a large next-day price move on
"short" days, heavy call OI + a small move on "long" days) is correctly
recovered (positive diff, p < 0.05), and a matched null sample (same OI
skew, but forward vol independent of the label) correctly shows no
significant difference — so the bucketing/windowing/t-test logic is proven
sound before it's asked to say anything about real dealer behavior. v2's
own correctness is `vol_surface_reference.py`'s job (already covered by its
own 14 tests), so this suite only smoke-tests that v2 runs cleanly end to
end, not that it reproduces v1's easy synthetic signal.

**Not yet run against real data.** `run_backtest(ticker, ...)` is the live
entry point (`python backtest_stage3.py TICKER [lookback_days]`) — this
needs an environment with real ThetaData access (this dev sandbox has
none, same limitation as every other live-data module in this project) and
a real decision about scope (which ticker(s), how many lookback days,
whether 90 days on one name is enough of a first read or whether it needs
multiple names before the result means anything) before spending the API
calls on it.

## 9. Honest risks

- **This is still a model, not ground truth.** No amount of surface-fitting
  recovers the actual dealer book; we're inferring, the same way v1 infers,
  just with a richer signal.
- **Reference-surface risk.** If the SABR/VV fit used for `IV_reference` is
  itself mis-calibrated, the deviation signal inherits that error directly
  — this is why Layer 1a needs to be validated against v1 and against
  realized behavior, not assumed better because it's more complex.
- **Layer 1b's sign formulation is resolved on paper, not yet proven in
  practice.** §5 now has an actual answer (delta-neutralize the whole
  strip via a synthetic high-delta option position at a real, liquid
  strike in the chain — not stock — anchored to the ideal continuous
  $(2/T) constant, gamma/vega left alone since they're the signal) — but
  "resolved on paper" and "correct" aren't the same thing until it's
  implemented and the `delta_hedge_drift` diagnostic is checked against
  real chains, and until the liquid-deep-ITM-strike selection logic is
  actually tested against chains where the theoretically-ideal strike has
  no real OI. Don't skip validating this piece just because the
  formulation question feels closed now.
- **The IRV confidence-weight is itself a model choice.** How the implied-
  remaining-variance consistency score gets turned into a weight on Layer
  1b (a hard cutoff vs. a smooth blend, what counts as "noisy") is a design
  decision that needs the same validate-before-trust treatment as
  everything else here — it's a better answer than hardcoding "trust index
  names more," but it's not automatically correct just because it's
  computed rather than guessed.
- **Overfitting the backtest.** Don't tune the reference-surface anchoring
  or the deviation thresholds on the same sample used to declare victory in
  §8 — hold out tickers/dates.
- **Data cost/rate limits.** Historical bulk pulls (Layer 2/3) are
  explicitly rate/size-limited per ThetaData's request-sizing guidance;
  the caching layer in §6 is the mitigation, and it's designed in from the
  start rather than retrofitted.
- **Cache correctness is now itself a risk to track.** A caching layer that
  silently serves stale data on a day that actually *did* see a rare OI
  restatement, or that double-counts a partially-cached date, is worse than
  no cache — the `fetch_manifest` table in §6 needs to record *complete*
  fetches only (write it after a fetch fully succeeds, not before), so a
  crashed mid-fetch never gets mistaken for a cached day.
- **Streaming is a separate infrastructure project.** Don't let "Layer 3
  live" scope-creep into blocking Layers 1-2, which deliver value with
  zero new infrastructure.

### 9a. Layer 1a's reference-surface risk, realized (2026-07-22)

The "Reference-surface risk" bullet above stopped being theoretical the
same day Layer 1a shipped. Two real bugs, found back-to-back:

**Bug 1 -- quadratic extrapolation artifact.** Jason ran
`sign_model='vol_surface_replication'` live on SPY and it looked great (a
genuinely two-sided Gamma Exposure panel, `Gamma: DAMPENING` for the first
time). Rather than take the pretty chart at face value, refit the exact
same quadratic directly on his exported SPY chain (20260930 expiry, 318
near-ATM points) and printed deviation strike-by-strike. It was a smooth,
monotonic function of moneyness across hundreds of strikes on both wings
-- e.g. put deviation drifting from -0.50 vol points at $445 (x=-0.544 log-
moneyness, 3.6x past the fitter's own +/-0.15 fit window) up through ~0 near
$675 -- not spiky/isolated the way real per-strike overwriting flow should
look. That's a quadratic's x^2 term diverging on extrapolation, not
economics. The net "DAMPENING" flip was largely an artifact.

**Fix attempt 1 -- swap in SABR.** `vol_surface_reference.py` already
pointed at `Monte-Carlo-American-Pricer-Greeks/SABRModel.py` as the planned
upgrade path; ported the Hagan formula + ATM-pinned alpha-solve (bisection)
in self-contained form (no ThetaDataController dependency) and fit it
vega-weighted across the whole OTM strip, mirroring
`SABRCalibrator.calibrate()`'s own approach exactly.

**Bug 2 -- vega-weighting aliasing, found via a dense synthetic grid scan.**
Built a synthetic "true" smile FROM a known SABR parameterization (not a
quadratic -- using the same functional family as the fitter would trivially
recover the generator regardless of weighting, which wouldn't test
anything real) and fit against it. The vega-weighted fit converged to
(rho=-0.61, nu=0.3) against a true (rho=-0.3, nu=0.6) -- and a dense 40x40
(rho, nu) grid scan confirmed this isn't a bad optimizer start, it's the
actual weighted-objective minimum: vega decays sharply away from the money,
so multiple very different (rho, nu) pairs achieve near-identical
*weighted* error while diverging by ~2x in raw IV forty-plus percent OTM
(0.054 fit vs. 0.103 true at 90% above spot). Vega-weighting is the right
choice for `SABRCalibrator`'s own purpose (pricing/hedging, where P&L
exposure concentrates near the money) -- it's the wrong choice for Layer
1a, which specifically needs to read deviations at the deep-OTM strikes
where covered-call/cash-secured-put overwriting programs concentrate.
Discounting exactly those strikes in the fit defeats the point.

**Fix 2 -- unweighted (equal-weight-per-strike) least squares.** Same
ATM-pinned bisection for alpha, but the (rho, nu) objective is now a plain
mean-squared IV error, no vega weight. Re-ran the dense grid scan: recovered
(rho=-0.317, nu=0.6) against the true (-0.3, 0.6) -- and the 90%-OTM
residual dropped from 0.05 to 0.0004 vol points.

**Re-verified against the same real SPY chain that motivated the original
fix.** Refitting Layer 1a (now SABR + unweighted) directly on the
20260930 SPY expiry: max OTM deviation anywhere in the chain dropped from
the quadratic's 0.50 vol points to ~0.013, and the sign pattern became
spatially coherent instead of a smooth gradient -- essentially every deep
OTM put stays dealer-short (consistent with the well-known persistent
protective-put-buying flow in SPY), with a cluster of calls in the
$800-890 range (roughly 4-16% OTM on this expiry) flipping dealer-long,
consistent with a plausible call-overwriting band rather than noise.

Regression coverage: `tests/test_vol_surface_reference.py` now includes a
dedicated section (`_make_wide_smile`, generated from a known SABR
parameterization, not a quadratic) proving (a) the quadratic fallback
still exhibits the extrapolation artifact on a wide chain, (b) SABR does
not, and (c) SABR still isolates a genuinely distorted strike rather than
either smearing it everywhere or being too smooth to detect it at all --
14/14 passing. `compute_vol_surface_reference()`'s original call signature
(no forward/T) is preserved and still exercised (falls back to the
quadratic), so all pre-existing callers/tests are unaffected.

Lesson for this project generally, stated plainly: neither "it produced a
prettier chart" nor "it's a more sophisticated model" is evidence of
correctness on its own. Both bugs here were caught the same way every prior
bug in this project was caught -- recomputing the exact math independently
on real or deliberately-distorted synthetic data and inspecting the
actual numbers, not just checking whether the code ran without an
exception.

## Appendix: ThetaData REST/Streaming endpoint reference

Compiled directly from `https://http-docs.thetadata.us` (checked 2026-07-21)
so this doesn't need to be re-derived by clicking through the docs sidebar
again. Only a handful of these (marked ✓) have had their exact path/params
confirmed against the live docs pages in this conversation — for the rest,
the *name and category* are confirmed real, but confirm the exact path/param
names on ThetaData's page for that operation before wiring it up, rather
than guessing from the naming convention.

**Options — Snapshots** (current/live only): Quotes, OHLC, Trade, Open
Interest.

**Options — Bulk Snapshots**: Bulk Quotes, Bulk Open Interest ✓ (used today
by `option_bulk_oi`), Bulk OHLC, Bulk Greeks, Bulk Greeks Second Order, Bulk
Greeks Third Order, Bulk All Greeks ✓ (used today by `option_bulk_greeks`,
fields confirmed live in the "check it" exchange above — delta/gamma/vanna/
charm/etc. all present, lowercase keys).

**Options — At Time / Bulk At Time**: Trade At Time, Quote At Time, Bulk
Trade At Time, Bulk Quote At Time — point-in-time lookups, useful for
spot-checking a specific print rather than pulling a whole day.

**Options — Historical Data**: EOD Report, Quotes, OHLC, Open Interest ✓
(confirmed available back to 2012-06-01 — this is what Layer 2 runs on),
Trades, Trade Quote (Layer 3's Lee-Ready input — trade price + prevailing
quote paired together).

**Options — Historical Greeks**: Implied Volatility, Greeks, Greeks Second
Order, Greeks Third Order, All Greeks, All Trade Greeks, Trade Greeks, Trade
Greeks Second Order, Trade Greeks Third Order — all narrower slices of the
same data `all_trade_greeks` already returns in full (see §4); no need to
call these individually if pulling the "all" variant.

**Options — Bulk Historical Data**: Bulk EOD, Bulk Quote, Bulk OHLC, Bulk
Open Interest, Bulk Trade, Bulk Trade Quote.

**Options — Bulk Historical Greeks**: Bulk EOD Greeks, Bulk Greeks, Bulk
Greeks Second/Third Order, Bulk All Greeks, **Bulk All Trade Greeks** ✓ (the
Layer 3 endpoint — full path, params, and response schema confirmed live,
see §4), Bulk Trade Greeks.

**Flat Files (beta)**: bulk per-day file downloads for Option {Trade Quote,
Open Interest, EOD} and Stock {Trade Quote, EOD} — worth benchmarking
against paginated REST once Layer 2/3 are pulling full-history backfills;
likely faster for that specific job per ThetaData's own request-sizing
guidance.

**Raw trade endpoints** (for custom OHLC/filtering, §4): `/v3/stock/history/trade`,
`/v3/option/history/trade`, `/v3/index/history/trade` — one row per trade
with the real SIP condition code, exchange code, and price/condition flags,
unfiltered by ThetaData's own OHLC cleanup logic.

**Streaming (local Theta Terminal, `ws://127.0.0.1:25520/v1/events`, not
proxied through `api.potatohedge.com` today)**: Full Trade Stream ✓
(bulk, all OPRA option trades, brackets each trade with the last NBBO quote
before + 2 after — Pro subscription required), Trade Stream ✓ / Quote
Stream ✓ (single-contract, Standard subscription required). Equivalent
Stock and Index streams also exist (Full Trade Stream / Trade Stream /
Quote Stream per asset class).

**Reference/enum tables** (not data endpoints, but needed to interpret the
above correctly): Error Codes, Exchanges ✓ (full code→name table pulled),
Quote Conditions, Trade Conditions (condition codes referenced in §4's raw
trade discussion, and in the `all_trade_greeks` response fields
`condition`/`ext_condition1-4`).
