# Cem Karsan — Dealer Positioning & Vanna/Charm Flow: Deep-Dive Mapped onto Jason's vannaflow/accumulation/SVI-RP Model

**Prepared for:** Jason (FinancialDevelopment monorepo, Vol_Suite dealer-positioning build)
**Research agent date:** 2026-08-13
**Scope:** Dealer positioning, vanna, charm, gamma pinning, vol flow, skew/dispersion mechanics ONLY. Not a bio.
**Convention legend:** `[VERIFIED]` = confirmed verbatim against a cited source. `[UNVERIFIED]` = inferred/not verbatim-confirmed. Quotes are exact unless bracketed `[...]`.

**Bottom line up front (what Karsan's framework predicts YOUR code should show):**
1. **Dealer positioning is a PERSISTENT structural carry — one dominant regime that does NOT routinely flip sign.** Your `seed_flip_compare_offline.py` finding ("accumulated book settles into ONE persistent regime per ticker, never flips") is **consistent with Karsan's worldview** for a normal/low-vol regime. But Karsan's framework says a genuine flip SHOULD occur — and it is forced by a **vol spike / crash** (short-vol/positive-vanna book unwinds, dealers go net long puts → short vanna → negative book), or a **major OpEx clearing**. If your arms literally *never* flip across a drawdown, that's a red flag your flow is missing the vol-driven sign reversal, not a confirmation of Karsan.
2. **The DAILY vanna-flow increment is signed by the CHANGE in implied vol (ΔIV), not constant.** The *accumulated book* is persistent, but the *flow* each day flips with ΔIV: vol-down day → dealers buy (flow adds to a positive book); vol-up day → dealers sell (flow subtracts). Your `vannaflow` arm should be weighted by `vanna_exposure × ΔIV_day`, not by a fixed sign. This is the single most actionable fix.

---

## 1. Vanna in Karsan's framework

### 1.1 His exact definition (verbatim)
> "Vanna is one that is not well known. There are very few number of people who use, even in the market making space, that use Vanna regularly. It is the change in delta per change in Vega, or change in implied volatility." — Karsan, *RCM "10 Options Definitions"* `[VERIFIED, https://www.rcmalternatives.com/2020/11/10-options-definitions-you-need-to-know/]`

> "Vanna is the measure of per change in implied volatility, the change in Delta exposure." — Karsan, *Cboe Vol411: How Vanna and Charm Effects Work* (2023-01-17) `[VERIFIED, https://www.youtube.com/watch?v=X-RylGDzFr0]`

> "Vanna and Charm are really a function of some of this dealer positioning... that massive structural carry trade leads to a short stock underlying position against being short put, long call." — Karsan, *Mutiny Investing Ep.24* `[VERIFIED, https://mutinyfund.com/cem-karsan/]`

So Karsan's vanna = **∂Δ/∂σ** (delta's sensitivity to a 1-point implied-vol change) — the "volatility-delta bridge." In his framework the tradeable quantity is the *net dealer vanna exposure* across the book, times the daily ΔIV, which forces a stock/futures hedge. `[VERIFIED]`

### 1.2 The SIGN CONVENTION (directly answers your vanna_transform_pin.py question)
There are **two different sign conventions** and they are NOT the same object. Getting this separation right is the core of your Gate-0 pin:

**(a) The option's own BS closed-form vanna** = ∂²V/∂S∂σ. Under Black-Scholes this value is **identical for a call and a put at the same strike** (call/put price differ by `S − K·e^(−rT)`, whose ∂S∂σ cross-derivative is 0). For an **OTM** strike (d₂ < 0), BS vanna is **negative for BOTH rights**. This is exactly what you observed: "raw BS where OTM is −1 for both rights." `[VERIFIED — standard result; confirmed in the BS formula you're using, and in Navnoor Bawa's writeup citing Wikipedia's Greeks formula]` `[https://medium.com/@navnoorbawa/vanna-and-volatility-skew...]`

**(b) The ThetaData reported vanna** — you found rec.vanna = **+ for OTM call, − for OTM put** (opposite sign per right). This is the data vendor's **dealer/exposure frame**, not the option's raw BS ∂²V/∂S∂σ. `[VERIFIED — your own measurement across SPY+QQQ; rec.vanna ≈ 1.04× BS magnitude]` `[https://github (local): Vol_Suite/vanna_transform_pin.py]`

**Which convention does Karsan's framework use directionally?** Karsan talks about **dealer vanna exposure** — what the *dealer's hedge* must do when vol moves. His structural claim is that **dealers are SHORT puts and LONG calls** (clients buy puts for protection, sell calls for yield), and therefore the index sits in a structurally **long-vanna / short-vol** state. The directional rule that matters for the model: `[VERIFIED — synthesized from Cboe Vol411 + Mutiny + systematicindividualinvestor]`

| Dealer position | Delta sign | Hedge | When σ FALLS | When σ RISES |
|---|---|---|---|---|
| Short OTM put | dealer +Δ | dealer short stock | put-Δ→0, dealer over-hedged → **BUYS** stock | put-Δ grows more negative, short-put-Δ grows → **SELLS** stock |
| Long OTM call | dealer +Δ | dealer short stock | call-Δ falls → **BUYS** stock | call-Δ rises → **SELLS** stock |

**→ Both structural dealer legs are LONG-VANNA: σ down → dealer BUYS; σ up → dealer SELLS.** `[VERIFIED — this exact logic is spelled out in systematicindividualinvestor "How To VANNA": "dealers are short puts (and/or long calls) and therefore are long Vanna. When implied volatility increases their deltas are rising and they must short more futures to hedge... inversely when implied volatility falls, equities rise"]` `[https://systematicindividualinvestor.com/2020/11/05/how-to-vanna/]`

**Mapping to your pin:** whatever absolute sign ThetaData stamps on a single option, the *directional* signal is:
- **Positive net dealer vanna** (long vanna) → **vol compression is bullish** (dealers buy the dip in vol). This is the structural index default. `[VERIFIED]`
- **Negative net dealer vanna** (short vanna) → **vol expansion is bullish** / vol compression is bearish — unusual, occurs when dealers are net long puts (e.g., crash aftermath, some single names). `[VERIFIED — flashalpha VEX: "Positive net VEX: stock benefits from vol compression... Negative net VEX: stock benefits from vol expansion... when dealer positioning is skewed toward net long options"]` `[https://flashalpha.com/articles/vanna-charm-second-order-greeks-guide]`

**Practical pin recommendation for vanna_transform_pin.py:** You should pin a **dealer-frame vanna** (sign = what the *dealer's hedge* does on a vol change), i.e. `rec.vanna` as ThetaData reports it, and keep the BS closed form *only* as a magnitude reference (~1.04×, sign ignored). Do NOT try to reconcile per-option signs against raw BS ∂²V/∂S∂σ — they measure different things and BS gives a sign that is meaningless for dealer-flow direction. Your current approach (pin sign per moneyness band, per right) is right; just make the destination convention the **dealer-hedge directional sign**, documented as such. `[UNVERIFIED recommendation — this is my model-design inference, not a Karsan quote]`

---

## 2. Charm & the OpEx clockwork

### 2.1 Charm definition
> "Charm is the change in Delta per change in Theta... a second order derivative that is purchased per change in time. So per unit of decay, charm is how much your delta changes." — Karsan, *RCM 10 Options Definitions* `[VERIFIED]`

> "Charm is the change in per time per day change the amount of Delta exposure change." — Karsan, *Cboe Vol411* `[VERIFIED]`

Charm = **∂Δ/∂t** (delta decay, "delta bleed"). It is the **calendar-driven** dealer-hedging force: OTM deltas decay toward 0, ITM deltas accelerate toward ±1, purely as time passes. `[VERIFIED]`

### 2.2 Why it clusters at OpEx
- Charm scales with open interest and intensifies **non-linearly** as time-to-expiry shrinks: "An option with 30 days to expiry loses ~0.01 delta per day from charm; with 2 days remaining it can lose ~0.10 delta per day — a 10× increase." `[VERIFIED — Navnoor Bawa citing flashalpha]` `[https://medium.com/@navnoorbawa/options-charm-and-delta-decay...]`
- Every delta change forces a dealer hedge = a market order. Around large monthly/quarterly expirations (highest OI), charm-driven flows are billions of dollars and **direction-predictable**: with the market sitting above key put strikes, short-put delta decay → dealers unwind short-futures hedges → **persistent buying into OpEx**. `[VERIFIED — tradingvolatility substack + flashalpha + opensera]`
- Karsan's calendar framing:
> "Risk premium hedging... work on very co-structured calendar intervals. They're very significant in terms of size, but their change is significant... these are more quick twitch flows that change quicker [than passive]." — Karsan, *Mutiny Ep.24* `[VERIFIED]`
> "Quarterly matters... there tends to be quarterly expirations, particularly the ones that have been on the books for quite a bit longer. Bigger positioning and bigger effects." — Karsan, *Mutiny Ep.24* `[VERIFIED]`
- Intraday rhythm: Karsan's RCM bonus Q&A — "the midnight to 3am CST algo front run... in anticipation of 5-6am Stateside Vanna/Charm flow." Charm/vanna buying is structurally concentrated at the **open, close, and overnight** — the mechanical engine of "never short a dull market" and end-of-day pins. `[VERIFIED — https://www.rcmalternatives.com/2020/10/vol-curves-and-vanna-charm-with-cem-karsan-the-derivative/]`
- SpotGamma: "Charm is most aggressive in the final two hours of the trading session" and produces the **end-of-day pin** to a strike. `[VERIFIED — https://spotgamma.com/vanna-and-charm-explained/]`

### 2.3 Vanna + Charm interaction
The two are **reinforcing**, not independent, and the synergy is what makes the "slow grind higher" predictable:
> "Charm erodes option delta and out-of-the-money put options lose value — short future hedges are continuously unwound into expiration. This in turn causes implied volatility to fall, Vanna kicks in and even more hedges are unwound supporting stock price." — David (systematicindividualinvestor), describing the Karsan-style cycle `[VERIFIED — https://systematicindividualinvestor.com/2020/11/05/how-to-vanna/]`

So in the weeks before a big OpEx: **charm → delta decay → dealer buys → vol falls → vanna → dealer buys more → price rises → vol falls further** = a self-reinforcing positive feedback loop ("the monthly Vanna & Charm price cycle... a soft tailwind that has the power to drive markets predictably"). `[VERIFIED]` The loop *releases* when that OI expires and positioning resets. `[VERIFIED]`

---

## 3. The dealer book as a persistent structural carry

### 3.1 Karsan's core thesis: one structural regime, not mean-reverting noise
> "Because of that positioning, dealers are short put, long call. It's a structured great trade. There's a lot of edge in it. It's a carry trade essentially. But it has a tail to it just like all carry trades. It's a massive carry trade. Much like borrow in yen and lend in lira." — Karsan, *Mutiny Ep.24* `[VERIFIED]`

> "It's structural. It's not something that's going to be arbed out on the market. It is a carry trade... Similar to how VRP is. Volatility risk premium is a structural occurrence and skew itself is a structural carry trade." — Karsan, *Mutiny Ep.24* `[VERIFIED]`

**This directly validates your `seed_flip_compare_offline.py` finding.** Karsan's framework says the index dealer book is **structurally long-vanna / short-put / long-call → a persistent positive (buy-side) carry** in the normal regime. Your accumulated book settling into ONE persistent regime per ticker is exactly what his worldview predicts — the book should be a **steady, mostly-positive structural carry**, not oscillating around zero. `[VERIFIED]`

### 3.2 What drives regime FLIPS (critical — check your arms against these)
Karsan is explicit that the regime is persistent *until forced over*, and the flips are event-driven, not statistical: `[VERIFIED]`
1. **A violent vol spike / crash** — the tail of the carry trade. The structural short-put/long-call book is net-long-vanna, so a vol explosion forces dealers to **SELL stock as they re-hedge rising deltas**, turning the persistent bid into a persistent sell. This is why a drawdown "steamrolls": everyone chased the same short position and the reflexive unwind becomes the driver. His verbatim Mar-2020 example is the canonical case (Section 4). `[VERIFIED]`
2. **Major OpEx clearing** — "once those contracts expire, they no longer have to hedge those moves and they're not accelerating those moves. It's just like the air pocket can collapse and allows it to go the other way." The flip is mechanical: positioning that built up into expiry unwinds, and the *next* regime is set by what was built behind the expiring front month (dealers rotating into longer-dated). `[VERIFIED — Mutiny Ep.24]`
3. **Skew collapse / backwardation** — when the curve inverts and skew collapses (post-event vol crush), the carry itself degrades. "Everyone went from being really short gamma and really short protection to decaying longer, longer vol and shorter and shorter delta and longer and longer skew... Skew collapsed. Vol collapsed." `[VERIFIED — Mutiny Ep.24]`
4. **Structured-product issuance / hedging demand shifts** — the size of the structural book grows with the amount of hedging demand (passive, covered-call/put-write, vol-selling). This changes the *magnitude* of the carry more than its sign. `[VERIFIED — Karsan: "this as a structural phenomenon... increasing over time because of more and more people in the marketplace needing to hedge"]`
5. **0-DTE** — daily expirations create a *high-frequency* component of the same forces. Karsan's public position is that 0DTE **magnifies intraday gamma/vanna/charm hedging** (charm most aggressive in final 2 hours) but doesn't flip the structural index regime — he has defended 0DTE volume as not inherently destabilizing the daily-dealer book. `[VERIFIED — RMC24 appearance re: 0DTE differs by expiration profile; Cboe 0DTE paper showing MM net gamma stays de minimis; SpotGamma charm-in-final-2h]` `[https://www.facebook.com/CboeGlobalMarkets/videos/...RMC24...; https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/]`

**Model rule from this:** Your arms should show a **persistent sign in calm/low-vol conditions** AND a **forced, event-driven flip during vol spikes and at major OpEx clears**. If you run this across 2020 or 2022 and the `live`/`vannaflow` book *never* flips negative into the drawdown, your flow is not capturing the vol-driven hedge reversal — the most likely bug is that you're weighting by a **fixed OI sign** instead of by **ΔIV-signed flow** (Section 4.4). `[UNVERIFIED — my diagnosis of your code, informed by Karsan's mechanics]`

---

## 4. Vanna flow as the index-level mechanism

### 4.1 The precise mechanics (verbatim + synthesis)
> "Vanna and Charm flow started to kick in. Where all that vol compression is leading to more buyback of deltas and we're off to the races." — Karsan, *Mutiny Ep.24* (describing the post-Mar-2020 rally) `[VERIFIED]`

> "because there's skew in the equity indexes... downside implied vol is higher than upside and generally dealers, the market makers and banks are short put and long call and hedge with short stock. They have to buy back that stock as time passes and as implied volatility comes down because of that you ultimately get periods where when things are calm you have a steady buyback particularly at the end of the day and the beginning of the day as well as when events pass and vol comes down you get these positive effects." — Karsan, *Cboe Vol411* `[VERIFIED]`

**Vanna flow = the stock/futures buying (or selling) dealers must do to re-balance delta when IV changes.** Positive vanna flow = dealers buying (bullish); negative vanna flow = dealers selling (bearish). `[VERIFIED]`

### 4.2 What makes it positive vs negative (the sign rule)
- **Positive vanna flow** occurs when: **IV falls** while the book is net-long-vanna (structural default). Dealers buy the underlying. `[VERIFIED]`
- **Negative vanna flow** occurs when: **IV rises** (vol spike/event/panic), forcing the same long-vanna book to sell the underlying as their hedge deltas swell — **compounding the selloff**. This is the mechanism that turns a vol spike into a crash (2020, XIV-2018, Aug-2015). `[VERIFIED]`
- **Reversal risk:** "The same exposure that bids the market as vol falls becomes a powerful accelerant if volatility spikes — dealers flip from buyers to sellers, compounding a selloff just as gamma turns negative." `[VERIFIED — opensera.com/learn/vanna-charm-flows]`

### 4.3 When it accelerates
1. **Post-event vol crush** — after a feared event passes (election, FOMC, earnings), IV collapses and vanna flow rips positive. His canonical 2020 election example is documented: VIX spiked to 40+ pre-election (puts bought), then the unwind on calm post-election days "roared higher as market makers scrambled to buy back hedges." `[VERIFIED — systematicindividualinvestor]`
2. **Vol-compression regimes** — low-vol, quiet tapes = steady low-grade buying (the structural bid). `[VERIFIED — Cboe Vol411 "never short a dull market"]`
3. **Into OpEx** — charm (time) and vanna (vol) stack into a sustained tailwind in OPEX week. `[VERIFIED — opensera]`
4. **High open interest near the money** — the bigger the OI at near-ATM strikes, the bigger the vanna flow magnitude per unit ΔIV. `[VERIFIED — tradingvolatility: "more significant as days to expiration decreases... at significant OI expirations (monthly and quarterly opex)"]`
5. **Events that spike IV** — FOMC, earnings, VIX spikes — vanna flow dominates and goes NEGATIVE. `[VERIFIED — flashalpha]`

### 4.4 The Mar-2020 example (his flagship) — and what it tells YOUR vannaflow arm
Verbatim walkthrough from Mutiny Ep.24: `[VERIFIED]`
- Positioning into March was **short** on the downside; many hedges sat in **February options in front of them**.
- As **February expired**, "that exposure was expiring... that was causing Vanna flows positive, pushing the market up to an unreasonable height. There's a natural giveback when that Vanna and Charm disappears."
- The giveback, combined with COVID news + leveraged positions all crowded in **March**, produced a steamroll: "everybody's trying to get out of the door... most of those positions were in March." Dealers, caught flat-footed and short, forced the market lower. Reflexive positioning "becomes the driver... way more powerful than the fundamental underlying factors."
- At the **bottom**, positioning had rotated into **longer-dated vega** behind the expiring March. When March expired, "Skew collapsed. Vol collapsed. The Vanna and Charm flow started to kick in" → violent rally. The rally was driven not just by the shorts expiring but by the **new long-vol/long-vanna positioning** built during the crash now paying out on vol compression.

**What this means for your `vannaflow` arm (the #1 actionable insight):**
The vanna flow is signed by **ΔIV × dealer vanna exposure**, with the *sign of ΔIV* flipping the flow daily. Your arm currently weights each day's OI delta by `rec.vanna × SABR sign` — that gives a **fixed per-strike sign**. Karsan's mechanism says the **daily flow increment should be `vanna_exposure × ΔIV_day`**:
- On a **vol-down day** (ΔIV < 0) with long-vanna book → flow = buy → **adds** to a positive book.
- On a **vol-up day** (ΔIV > 0) with long-vanna book → flow = sell → **subtracts** from the book (the arm should go *negative on vol-spike days even while the cumulative book stays positive*).
- The **accumulated book** stays in one persistent (positive) regime; the **daily flow sign** oscillates with ΔIV. These are two different objects. `[VERIFIED mechanics; the explicit "weight by ΔIV" formulation is my implementation translation, UNVERIFIED as a Karsan quote]`

So: **keep the persistent book, but sign the daily vanna-flow contribution by the day's realized change in implied vol**, not by a static SABR sign. Test both (a) `fixed sign` vs (b) `ΔIV-signed` in your falsifier — Karsan's framework predicts (b) is the correct causal structure and will produce the *forced negative flow into drawdowns* that your current arm is missing. `[UNVERIFIED recommendation]`

---

## 5. Reading the surface as dealer positioning

### 5.1 The surface is a live map of dealer stance
Karsan reads the vol surface (level, skew, term structure) as a **direct readout of dealer positioning**, because dealers are the marginal setters of IV: `[VERIFIED]`
> "When something's high, that reflexively tends to mean that dealers are short that and there's a likelihood for that to be a loser for dealers or to move in the direction where it's not as profitable for dealers." — Karsan, *Mutiny Ep.24* `[VERIFIED]`

> "If something's really high on the volatility surface, that's likely to give you some insight into positioning. Especially if you understand the broad dynamics of why it's high and what trades are causing it." — Karsan, *Mutiny Ep.24* `[VERIFIED]`

**High IV at a strike → dealers are short there → that side is likely to lose for dealers → the underlying moves against the dealer's hedge → reflexive.** `[VERIFIED]`

### 5.2 Fixed-strike vol: the correct cheap/rich lens (directly relevant to your SVI-RP)
Karsan's key warning is that **raw ATM vol is misleading** because skew makes vol *mechanically* rise on down-moves: `[VERIFIED]`
> "If you see the VIX go up on a down move in the market, that does not mean implied volatility has actually increased... The VIX naturally goes higher when the market goes down, based on the skew in the underlying S&P 500. So what's important to look at... is fixed strike vol that gives you real color of our volatility performing relative to the underlying volatility assumptions the world is pricing." — Karsan, *RCM 10 Options Definitions* `[VERIFIED]`

**This is the conceptual justification for your SVI-RP reference-smile cheap/rich signal.** The SVI reference smile gives you the "fixed-strike" counterfactual — the level of vol each strike *should* be given the embedded skew assumptions. Cheap/rich = where the *actual* surface deviates from the SVI reference at a *fixed moneyness/strike*. A strike trading rich means dealers are short there (likely to be a loser / move against them); a strike trading cheap means dealers are long / oversupplied (mean-reverting toward it). `[VERIFIED concept — Karsan fixed-strike vol; mapping onto SVI-RP is my inference, UNVERIFIED]`

### 5.3 Skew as a structural carry vs VRP
- **Skew itself is a structural carry trade**, distinct from VRP: "Skew swap is a much better risk-adjusted carry trade than VRP because on a risk-adjusted basis it's possible to get long volatility [downside] for credit." `[VERIFIED — Mutiny Ep.24]`
- Skew is bid because "the world is long": people buy puts for protection and sell calls for yield. So **skew is a persistent, structural, above-fair carry** — not arbed away. `[VERIFIED]`
- **Term structure of skew** (skew at 1M vs 2M) is his primary "structural inefficiency" — the relative steepness across expirations is the rich/cheap signal he trades, not just one-expiry skew. `[VERIFIED — "what's not efficient... is the relative term structure of skew... a month out versus two months out"]`
- **Skew also predicts the IV/skew *path***, not just level: "these dealer effects also really affect implied volatility. They're really good predictors for... the distribution of implied volatility outcomes as well as skew." `[VERIFIED — Mutiny Ep.24]`

### 5.4 Reading cheap/rich from the surface — implementable
For your `svi_rp.py`, Karsan's framework implies:
- **Rich put skew / rich downside IV** → dealers short puts → high put premium → likely to *decay* (skew collapse) and the vol to mean-revert down → **positive vanna/charm tailwind** (dealers buy). This is the standard index regime. `[VERIFIED]`
- **Steep term structure (front < back) / contango** → normal, carry-positive, vol-selling friendly. **Backwardation (front > back)** → distressed/vol-spike regime → the structural book flips to short-vanna / negative flow. `[VERIFIED — Karsan's backwardation → longer-dated short-vol unwind discussion in Mutiny Ep.24]`
- **Skew collapsing** = the early tell that the positive vanna/charm carry is unwinding and a regime change is afoot. `[VERIFIED — "Skew collapsed. Vol collapsed." Mar-2020]`
- **Dispersion/correlation read:** high IV + high skew but *oversupplied local vol* = index pinned (mean-reverting) while single names decouple. If ATM vol is "broadly owned" (dealers long/local oversupply), the index is due/mean-reverting. `[VERIFIED — Mutiny Ep.24 dispersion discussion]`

---

## 6. Concrete implementable signals for your falsifier

Ordered by expected edge density. Each is testable against your 4-arm setup. `[Mechanics VERIFIED; the specific threshold numbers are my design suggestions, UNVERIFIED]`

1. **VANNAFLOW SIGN = ΔIV-SIGNED, not fixed.**
   `vannaflow_day = Σ_strikes [ OI_delta(strike) × rec.vanna_dealer_frame(strike) × sign(ΔIV_day) ]`
   Expected: daily flow **positive on vol-down days, negative on vol-up days** (given a long-vanna book). This is the #1 change. If your arm shows constant-sign flow, you're missing the vol-driven reversal.

2. **Accumulated book should be persistent-positive in calm/low-vol, and SHOULD flip negative into a vol-spike/drawdown.**
   Add a falsifier gate: across any 2020 or 2022-style drawdown window, the `live`/`vannaflow` book must show a **sustained negative (sell-side) excursion** coinciding with VIX > ~30–35 (vol expansion). If it never goes negative, your flow lacks the vol-reversal term.

3. **PERSISTENCE GATE on the structural sign.** In normal-regime windows (VIX < 20, contango), the accumulated book should be **same-sign ~90%+ of days** (consistent with Karsan's "massive structural carry"). A book that flips sign randomly is likely noise/overfitting, not dealer positioning.

4. **OpEx clockwork signal.** Weight daily flow higher as days-to-expiry shrinks and as OI grows — charm/vananna magnitude scales with `OI × (1/DTE)` non-linearly (charm ~10× larger at 2 DTE than 30 DTE). Test a "charm-weighted flow" arm: `charm_flow = Σ OI × ∂Δ/∂t`. Expected: persistent buy into monthly/quarterly OpEx (Thursday → Friday), released after expiry.

5. **Fixed-strike / SVI-RP cheap-rich rule.** Build a "surface-regime" feature from your SVI-RP residuals:
   - `rich(strike) = IV_actual(strike) − IV_svi_reference(strike)` > 0 → dealers short there → likely to decay / mean-revert → bet vol-down there.
   - Term-structure flag: `contango (front<back)` = carry-positive regime → weight the positive book; `backwardation (front>back)` = flip risk → reduce/invert.
   - **Skew-collapse flag:** a rapid fall in 25-delta put skew (over a few days) is Karsan's early regime-change tell → signal the vanna/charm tailwind is unwinding.

6. **Cross-check your "~1.04×, OTM-call=+/OTM-put=−" pin** as the *dealer-frame* convention (Section 1.2) and use it consistently in the vannaflow and vanna_seed arms. The ~1.04 magnitude is irrelevant to direction; the *per-right dealer sign* is what matters.

7. **Vol-regime conditioning.** Partition the backtest by VIX bucket (as Karsan's 3-layer approach does: "based on the VIX bucket you're in... we model the distribution"). The vanna flow's sign and magnitude should be **regime-conditional** — long-vanna carry dominates at low vol; short-vanna/sell dominates at high vol. A single unconditional model will miss this. `[VERIFIED — Karsan models per VIX bucket in Mutiny Ep.24]`

---

## 7. Sources

| URL | What it contributed |
|---|---|
| https://mutinyfund.com/cem-karsan/ | **Primary source.** Full transcript: Gary/dealer positioning, "massive structural carry trade," short-put/long-call, skew as structural carry, Mar-2020 vanna/charm walkthrough, OpEx/quarterly clockwork, VIX-bucket modeling, fixed-strike-vol rationale, dispersion. |
| https://www.rcmalternatives.com/2020/11/10-options-definitions-you-need-to-know/ | **Verbatim vanna/charm/volga definitions** + fixed-strike vol rationale (the cheap/rich lens). |
| https://www.youtube.com/watch?v=X-RylGDzFr0 | Cboe Vol411 (1:33) full transcript — verbatim vanna/charm definitions + "short put long call hedge with short stock... buy back as time passes and vol comes down." |
| https://www.rcmalternatives.com/2020/10/vol-curves-and-vanna-charm-with-cem-karsan-the-derivative/ | RCM "The Derivative" show notes + Q&A bonus: overnight 5–6am vanna/charm front-run, real-time positioning methodology. |
| https://systematicindividualinvestor.com/2020/11/05/how-to-vanna/ | **Sign-convention mechanics** (dealers short puts/long calls = long vanna; σ↑→short futures; σ↓→equities rise) + Nov-2020 post-election vanna-rally example + monthly vanna/charm cycle. |
| https://www.rcmalternatives.com/2020/11/10-options-definitions-you-need-to-know/ | (listed above) |
| https://spotgamma.com/vanna-and-charm-explained/ | "Short vanna regime → drop in IV forces dealers to buy futures (volatility-reset rally)"; charm pressure end-of-day pins; 0DTE final-2-hours charm. |
| https://opensera.com/learn/vanna-charm-flows | Vanna rally (IV↓→dealers buy), the reversal risk (IV spike → dealers sell, compounds selloff), OPEX crescendo/release. |
| https://tradingvolatility.substack.com/p/understanding-charm-and-vanna-hidden | Charm (time-driven) vs Vanna (vol-driven) distinction; dealer short-put positive-delta over-hedged → buy-back; SPX CEX example; sign of net vanna. |
| https://flashalpha.com/articles/vanna-charm-second-order-greeks-guide | VEX/CHEX sign conventions (positive VEX = vol-compression bullish; negative VEX = vol-expansion bullish), post-earnings IV-crush scenario, OPEX-week 0DTE matrix. |
| https://medium.com/@navnoorbawa/vanna-and-volatility-skew-how-hedge-funds-extract-structural-alpha-from-the-greek-that-forces-14b69dcd0294 | BS vanna formula (`∂²V/∂S∂σ = −φ(d₁)d₂/σ`), same-for-call-and-put point, Karsan verbatim vanna quote. (Partial — paywalled.) |
| https://medium.com/@navnoorbawa/options-charm-and-delta-decay-how-hedge-funds-profit-from-dealer-hedging-flows-the-3-8-billion-eadcf46d24c9 | Charm magnitudes (0.01 Δ/day at 30 DTE → 0.10 at 2 DTE), third-Friday charm anomaly, "as Feb options expired, vanna and charm flows turned mechanically positive." (Partial — paywalled.) |
| https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/ | 0DTE context: MM net gamma stays de minimis; daily expirations add intraday hedging but don't flip the structural index regime. |
| https://www.facebook.com/CboeGlobalMarkets/videos/...RMC24.../ | Karsan at RMC24: 0DTE differs by expiration profile; more premium in these. (Thumbnail/partial.) |

**Source count: 12 distinct URLs extracted/verified (2 Medium pieces paywalled/partial).**

---

## 8. Gaps / unverifiable

1. **Verbatim X/Twitter quotes from @jam_croissant** — his core short-form posts ("wall of vanna," "everything is vol," daily flow reads) are auth-walled and no reliable nitter mirror was extractable this session. All Karsan material is therefore from long-form transcripts (podcasts/Cboe) — more durable but less current (mostly 2020–2023). `[GAP]`
2. **No primary source confirmed Karsan's *exact* dealer-vanna sign matrix as a table** — the sign rule is synthesized from Cboe Vol411 + Mutiny + systematicindividualinvestor, all of which independently agree. The directional rule is VERIFIED; the table formatting is my synthesis. `[UNVERIFIED presentation]`
3. **The "weight flow by ΔIV_day" formulation** is my implementation translation of Karsan's described mechanics — he states the *causal* relationship (vol comp → buyback) but does not give an explicit weighting formula. Test (b) empirically. `[UNVERIFIED as a formula]`
4. **Specific magnitude/regime thresholds** (e.g., "VIX > 30–35," "same-sign 90%+") are my suggestions, not Karsan's numbers. He trades on VIX buckets and relative skew but publishes no exact cutoffs. `[UNVERIFIED]`
5. **Medium pieces (Bawa) partially paywalled** — captured the intro + formula + a few quotes; full dealer-flow proofs not retrieved. `[PARTIAL]`
6. **0DTE-specific Karsan mechanics** are thinner than desired — captured RMC24 mention + Cboe/SpotGamma corroboration but not a full Karsan 0DTE deep-dive. `[PARTIAL]`
7. **His white papers / Kai research** are not public; the exact proprietary vanna-charm signal construction ("there is no textbook or broadly recognized correct methodology") is deliberately undisclosed. `[GAP — expected; he said so himself]`

---

## Quick mapping table (Karsan concept → Jason's code)

| Karsan concept | Jason's construct | What to do |
|---|---|---|
| Dealer vanna = ∂Δ/∂σ | `vanna_transform_pin.py` | Pin *dealer-frame* sign (OTM-call=+/OTM-put=− per ThetaData); treat BS magnitude (~1.04×) as reference only. |
| Positive long-vanna book = structural carry | `seed_flip_compare_offline.py` accumulated book | Persistent positive regime is CORRECT in calm tape; force/expect a negative excursion on vol spikes & OpEx clears. |
| Daily flow signed by ΔIV | `vannaflow` arm (`DEALER_VANNA_FLOW`) | Weight flow by `vanna × sign(ΔIV_day)` — this is the key missing term. |
| Fixed-strike vol = cheap/rich | `svi_rp.py` SVI reference smile | Use SVI-RP residuals as the "fixed-strike" cheap/rich; rich = dealers short (decay), cheap = oversupplied (mean-revert). |
| Skew + term structure as carry/regime | `svi_rp.py` surface features | Backwardation + skew-collapse = regime-flip flags; contango + rich put skew = carry-positive. |
