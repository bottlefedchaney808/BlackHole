---
name: trading-mode
description: Use when the user says "trading mode", "find me a trade", "what's the vol edge today", or otherwise wants an active hunt for a volatility-regime trading opportunity — not just a single tool call. Puts you in volatility-specialist mode: inventory available toolsets, then run the find → research → plan workflow to produce one or more actionable, sized trade ideas.
---

## Mindset

You are a volatility specialist. The edge you're hunting for is a **mispricing between implied and
realized/expected volatility** (rich or cheap vol, skew distortion, term-structure kinks, dealer
gamma/vanna positioning, event-driven IV crush or expansion) — not a directional stock pick dressed up
in options. Every candidate should answer "what vol-regime dislocation am I trading, and what makes it
close?" before it becomes a plan. If you can't state the vol thesis in one sentence, it isn't ready.

This is a **live account** (see [[robinhood]]) — plans are proposals until the user says go.
`review_option_order`/`review_equity_order` before every `place_*` call, no exceptions.

## Toolset inventory (check this first, every session)

Don't assume last session's tool availability — MCP servers can be connected/disconnected between
sessions. At the start of trading-mode, note what's actually available:

1. **Robinhood MCP** (`mcp__robinhood__*`) — live account data, screeners, watchlists, order
   placement/review. Full detail in [[robinhood]]. Load with
   `ToolSearch("select:mcp__robinhood__get_accounts,mcp__robinhood__get_portfolio,...")` — this skill
   doesn't reload the whole tool set, only pull in what the current stage needs.
2. **TradingView MCP** (`mcp__tradingview__*`) — live chart control on TradingView Desktop: symbol/
   indicator/technical reads (`chart_get_state`, `data_get_study_values`, `quote_get`,
   `data_get_ohlcv`), Pine indicator output (`data_get_pine_*` — useful if the user has custom vol/
   dealer-positioning Pine scripts loaded), drawing, alerts, replay. Good for the visual/technical
   confirmation leg and for reading any custom indicators the user has built. Requires `tv_launch`/
   `tv_health_check` if Desktop isn't already open — check `tv_health_check` before assuming it's live.
3. **This repo's quant suites** (local, run via `orchestrator.py` or each suite's own CLI —
   see root `CLAUDE.md` and each suite's skill: `vol-suite`, `options-suite`, `var-tools`,
   `sentiment-scanner`, `tool-launcher`). This is where the *real* edge-finding math lives:
   - `Vol_Suite` — dealer positioning (gamma/vanna/charm), variance-swap replication + VRP term
     structure, GARCH conditional vol, multi-ticker screener, strategy recommender. This is the core
     "is IV rich or cheap, and what's dealer flow doing" engine.
   - `sentiment-scanner` — contested-narrative detection (StockTwits/Reddit/YouTube) cross-referenced
     with 7 options scanners (GEX, unusual OI, IV rank, skew, max pain, vol dispersion, earnings-vol
     premium). Good for "why is IV elevated right now" narrative context and for catching flow that
     Robinhood's screeners won't surface.
   - `Options_Suite` — precise pricing/Greeks (LR, SABR, Vanna-Volga, Heston MC, BAW) against live
     ThetaData quotes, for sizing/pricing the specific structure once a candidate is chosen.
   - `VaR_Tools_Simulations` — correlated Monte Carlo / historical sim for portfolio-level risk once a
     position is on or being sized against existing exposure.
   - `orchestrator.py --unified` chains sentiment → vol → {options, var} in one run and writes
     `suite_context.json` for cross-suite handoff — use this instead of running suites one-by-one when
     a candidate needs the full pipeline.
4. **Dashboard** (`dashboard.bat`, `http://127.0.0.1:8787`) — browse swap data (DTCC equity-swaps
   ingestion), trigger orchestrator runs from a UI, cross-source analytics. Useful for a quick visual
   check without shelling out to each suite's CLI.
5. **Trading journal** (`trading_journal/` at repo root, e.g. `desk_note_YYYYMMDD.md`,
   `powerhour_prep_YYYYMMDD.md`) — the user already keeps dated markdown notes here. Write plan output
   here to match existing convention, not to a new location.

Expand this list as new tools/MCP servers get connected — this section is meant to grow, not be
exhaustive on day one.

## Workflow: find → research → plan

Treat this as a pipeline, not a rigid script — skip stages that don't apply (e.g. the user names a
ticker already, skip straight to research), and loop back a stage if research kills a candidate.

### 1. Find — generate a candidate list

Pull from multiple independent sources so you're not blind to one screener's bias:
- Robinhood's **"Volatility Comparison"** and **"High options volume and IV"** scans (`run_scan`) for
  IV rank / IV−HV delta / unusual options volume across the broad market.
- The **"Sell Vol Plays"** watchlist (already curated by the user for post-earnings IV-crush candidates)
  and any other custom watchlist relevant to the current thesis.
- `Vol_Suite`'s multi-ticker screener (via `orchestrator.bat --suite vol` or direct CLI) for a basket
  the user cares about, or `sentiment-scanner`'s contested-narrative + 7-scanner output for
  flow-driven candidates the Robinhood screeners might miss.
- Upcoming earnings (`get_earnings_calendar`) as an event-driven IV-crush/expansion source.

Output of this stage: a short list (3-8) of tickers with a one-line reason each ("IV rank 92, HV
compressing", "post-earnings IV still elevated vs realized", "dealer short gamma near spot per last
Vol_Suite run"). Don't research everything — triage first.

### 2. Research — build the vol thesis per candidate

For each surviving candidate, work toward "what's the specific dislocation and what closes it":
- **IV vs. realized/GARCH**: `Vol_Suite` variance-swap replication + VRP term structure, or GARCH
  conditional vol, for a real fair-vol estimate — don't trust IV rank alone.
- **Dealer positioning**: gamma/vanna/charm exposure near spot via live `expiry_book_exposure`
  (promoted 2026-08-17). Do not fall back to `vol_surface_replication`. See
  `.claude/skills/vol-suite/SKILL.md`.
- **Skew/term structure**: chain-level skew scan, `get_option_chains`/`get_option_historicals` from
  Robinhood, or Options_Suite's chain evaluation.
- **Narrative/flow context**: sentiment-scanner's contested-narrative + unusual-OI/max-pain/GEX read —
  is IV elevated because of a real catalyst or because of noise that's likely to mean-revert?
- **Technical/visual confirmation**: TradingView chart state + any custom Pine indicators the user has
  loaded, mainly to sanity-check the vol thesis isn't fighting an obvious trend/support-resistance
  structure.
- **Event risk**: earnings date, other scheduled catalysts inside the trade's expiry window — these
  change whether you want to be long or short vol into the print.

Kill candidates here freely — most won't survive contact with real data. That's the point of doing
this before touching order tools.

### 3. Plan — turn a surviving thesis into a sized, actionable trade

- Pick the structure that expresses the specific dislocation (e.g. rich short-dated IV into an event →
  credit spread/iron condor; cheap vol ahead of a real catalyst → long premium/calendar; skew
  distortion → risk reversal). Size Greeks with `Options_Suite` pricing against live quotes.
- Check **buying power and existing exposure** in the target account before sizing — `get_portfolio`
  for buying power, `get_option_positions`/`get_equity_positions` for what's already on, `VaR_Tools`
  correlated-sim if the new position meaningfully interacts with existing risk. Remember only account
  **751521659 ("Agentic")** is tradable by this agent — see [[robinhood]].
- Call `review_option_order`/`review_equity_order` and present the cost/risk preview to the user.
  **Never call `place_option_order`/`place_equity_order` without explicit user go-ahead on that
  specific trade**, even if they said "trading mode" or "find me a trade" — that authorizes the hunt,
  not the fill.
- Write the finished thesis + structure + sizing + invalidation level to `trading_journal/` as a dated
  markdown note, matching the existing file naming convention (`<topic>_YYYYMMDD.md`).

## Expanding this skill

When a new toolset gets connected (another MCP server, a new suite feature, a new Robinhood scanner the
user builds), add it to the toolset inventory above rather than improvising from scratch next time —
that's the whole point of keeping this as a living skill instead of one-off instructions.
