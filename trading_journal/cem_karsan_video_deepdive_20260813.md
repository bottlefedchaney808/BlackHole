# Cem Karsan Video Deep-Dive — 2026-08-13

**Extends:** `trading_journal/cem_karsan_dealer_vanna_deepdive.md` and `trading_journal/knowledge_base_cem_karsan.md`
**Agent date:** 2026-08-13
**Convention:** `[VERIFIED]` = confirmed verbatim against a cited/primary source. `[UNVERIFIED]` = inferred/summary not verbatim-confirmed. Quotes are exact unless bracketed `[...]`.

**Purpose of THIS report:** Watch/extract the 4 Cem Karsan vanna/charm videos and pin the **highest-priority open item**: the "20–30% drawdown AFTER the midterms" current-active call, which Jason's working session flagged as UNVERIFIED. This report covers only the DELTA this video set adds; it does not repeat the established framework (persistent structural carry, rec.vanna = −1×BS pin, ΔIV-signed vannaflow).

---

## 1. What was watched / extracted

| # | Video | Channel / URL | Extracted | Status |
|---|---|---|---|---|
| 1 | **Cem Karsan Called the May Rotation. Now He Sees 30% Down After the Midterms.** (now retitled **"Cem Karsan Said the Strait of Hormuz Wouldn't Reopen. Here's What He Says Happens Next."**) | tastylive + Kai Media, **24:34**, ~10K views, Aug 2026. `https://www.youtube.com/watch?v=FSZalsb6ow0` | **FULL — description + full transcript captured via browser** | ✅ **KEY VIDEO — fully extracted** |
| 2 | Vol Curves and Vanna Charm with Cem Karsan | RCM Alternatives, 1:32:24, 24K views, 2020. `https://www.youtube.com/watch?v=8awiGrquYXI` | FULL — description + chapters + full transcript | ✅ fully extracted |
| 3 | How Vanna and Charm Effects Work | Cboe Vol411, 1:32, 17K views, 2023. `https://www.youtube.com/watch?v=X-RylGDzFr0` | Description + transcript toggle present; **this is the same video already transcribed verbatim in the deep-dive** §1.1/§4.1 | ✅ already in deep-dive (no new delta) |
| 4 | Happy Hour: Cem Karsan & Josh (fio) #gamma #vanna #charm | NexusFi (futures.io), 1:10:14, 21K views, 2021. `https://www.youtube.com/watch?v=YDXxjmItRxc` | FULL — description + chapters + full transcript | ✅ fully extracted |

**Note on #1's retitle:** The video's official upload description still opens with the drawdown call verbatim — the retitle (to the Strait of Hormuz hook) does NOT change the call's substance. This is the same tastylive "tasty Crumbs" segment (Cem + Jeb) from August 2026.

---

## 2. THE CURRENT-ACTIVE CALL — 20–30% drawdown AFTER the midterms

### Verdict: **NOW VERIFIED** ✅

**Two independent verbatim confirmations from the primary source (the video itself):**

1. Official upload description:
> "Cem Karsan says **the drawdown doesn't come before the midterms. It comes after, and he's sizing it at 20 to 30%.** He called the momentum rotation in May and has said since March that the Strait of Hormuz doesn't reopen."
> `[VERIFIED — video description, https://www.youtube.com/watch?v=FSZalsb6ow0]`

2. Transcript, Karsan verbatim (≈15:40), answering whether the administration will "win" the midterms:
> "...they will still want to **take it down to take it up and that's not a 10% decline — expect 20 to 25 maybe even 30% — next year, after post-midterms.** And I think people are starting to see that..."
> `[VERIFIED — transcript, ≈15:40]`

3. Transcript, Karsan verbatim (≈16:06):
> "So **the biggest volatility risk comes for you still after the midterms** with everything that we've seen play out over the past two weeks."
> `[VERIFIED — transcript, ≈16:06]`

### The call, precisely pinned:
- **Direction:** Downside drawdown. **Magnitude:** 20–30% (not just a 10% pullback). `[VERIFIED]`
- **Timing:** Does **NOT** come before the midterms. Comes **after** — the "biggest volatility risk" is "after the midterms"; the 20–30% drawdown is sized "**next year, after post-midterms**." So: no large drawdown in the run-up (admin pushes asset prices up through early November); the risk window opens post-midterm and into 2027. `[VERIFIED — both the "doesn't come before/comes after" framing and the "next year after post-midterms" wording]`
- **Trigger/mechanism (his stated chain):** Three-legs-of-the-stool. Legs currently supportive = (a) admin/flow manipulation ("they are buying the midterms" via capital-gains tax cut, yen intervention = disguised QE, deal-fakery), (b) Summer-of-George index vol compression (structural short-vol/structured-product flows). The third leg (real economy / inflation via Strait of Hormuz) is deteriorating. After the midterms, the administration loses the incentive to keep markets propped ("post midterm... are they as motivated to keep it going aggressively higher?") and may even **want to take it down to take it back up** later. `[VERIFIED — transcript §15:00–16:00]`
| **Post-August-OPEX note:** he expects the summer vol-compression leg to "dissipate" in the last 2–4 weeks of the story (late Aug–Sep), with vol expansion "more likely than not" into Oct–Nov if the administration can't keep a melt-up squeeze going. But the **big** move (20–30%) is explicitly after the midterms. `[VERIFIED — transcript ≈10:19, ≈13:13, ≈21:39]`

---

## 3. Framework content extracted — what EXTENDS/REFINES the existing deep-dive

### 3.1 NEW implementable framework from the tastylive video (the big delta)
- **"Long the calendar" vol-term-structure trade.** Karsan is explicit that as summer realized/ATM vol compresses, the **long end of the vol curve (60d/90d/120d) is starting to go up** — the front compresses while the back rises. His actionable signal: "you want to be long calendars... 60-day V, 90-day V, 120-day V will continue to work its way higher as we come out of the summer." `[VERIFIED — transcript ≈10:04–10:43]`
  → **This directly extends the deep-dive's term-structure flag (§5.3/§6).** The existing writeup uses contango vs backwardation as a regime flag. The NEW refinement: watch the **slope at the FRONT (1M) vs the level of the BACK (2M–4M)** separately — a **flattening/collapse at the front with the back rising** is the specific pre-vol-expansion signature (the "cheap far-dated vol to buy, decay the near-dated"). This is his actual current book ("buy the thing that is going to have very little decay"). `[VERIFIED signal; my implementation framing = UNVERIFIED]`
- **Vol is "incredibly cheap" — replace stock with long-dated calls.** "Implied V is incredibly cheap and so you can buy longer-dated V, you can replace your stock with calls on a longer-dated [expiry]... buy the thing that is going to have very little decay." `[VERIFIED — transcript ≈18:15–19:04]` This is a **cheap-far-dated-vol → buy convexity / replace delta** rule, complementing the deep-dive's cheap/rich SVI-RP framing but for the *time* dimension.
- **The "no longer just a floor, now also a ceiling" OpEx observation.** Asked whether OpEx has gone from floor to ceiling, he says at the **index** level the flow is still one-sided supportive (calls offered / puts bid → vanna/charm bid), BUT in the **momentum/call-buying space, call skew has been introduced**, creating a **cap** as well as a floor — "that push-pull is driving a bit of a cap as well as a floor." `[VERIFIED — transcript ≈19:35–21:31]` → This is a **dispersion read**: index compression + momentum call-skew cap = the counter-rotation he "called in May" that is still an overhang. Extends deep-dive §5.4 dispersion. `[VERIFIED]`

### 3.2 NexusFi additions (confirms + 2 small new items)
- **Confirms** the short-put/long-call → short-stock → buy-back-as-vol-and-time-decay mechanism, the "skew is always to the downside," and that the buy-back "in history has always been... buying back stock." `[VERIFIED — transcript]`
- **NEW — explicit "change in positioning is what's important."** "...it's the **change in that positioning that's constantly what's important**, not just the level." `[VERIFIED — transcript ≈335]` → reinforces the ΔIV-signed / Δ-flow framing over the accumulated level.
- **NEW — GEX methodology critique / retail reproducibility.** "Squeeze metrics made the GEX calculation popular... what they're doing... is taking open interest... citing some logic based on that open interest of what positioning it. There's a lot of assumptions... those are pretty basic models... if you can get access to open interest which everybody can, you can download that and start making your own assumptions." `[VERIFIED — transcript ≈1833–1861]` → supports Jason's OI-based GEX/vanna reconstruction as legitimate and reproducible; also flags that dealer-positioning estimates are assumption-heavy. `[VERIFIED]`
- **Fixed-strike vs floating vol** (same conceptual point as deep-dive §5.2, but with a P&L-specific framing): "you're really focused on **fixed-strike vol** because that's what's affecting your P&L, that's what's affecting how options are actually moving... VIX is a moving target." `[VERIFIED — transcript ≈696–707]` Confirms the SVI-RP fixed-strike lens. `[VERIFIED]`

### 3.3 RCM additions (mostly confirms; 1 nuance)
- Confirms fixed-strike-vol as "the correct way to objectively look at what's happening in implied volatility" (true wind vs apparent wind analogy). `[VERIFIED — transcript ≈1025–1037]`
- Confirms the verbatim vanna/charm definitions (already in deep-dive §1.1/§2.1): vanna = "change in delta per change in implied volatility"; charm = "change in delta per change in time / per amount of decay." `[VERIFIED — transcript ≈97–125]`
- **No contradictions found.** Nothing in any of the 4 videos contradicts the rec.vanna = −1×BS pin or the persistent-regime finding. `[VERIFIED — no counter-evidence present]`

### 3.4 Cboe Vol411
- Already fully transcribed in the existing deep-dive (§1.1, §4.1). The video page re-extracted this session confirms the description verbatim ("never short a dull market... vanna & charm... play a role in proving out that phrase"). **No new delta; included for completeness.** `[VERIFIED]`

---

## 4. Maps to Jason's model

Ordered by expected edge density. All mechanics `[VERIFIED]`; implementation thresholds are my suggestions `[UNVERIFIED]`.

1. **REFINE falsifier signal #2 (forced flip gate) with the midterm post-event window.** Pin the "20–30% after the midterms" call as a testable hypothesis: over a window spanning post-midterm 2026 → 2027, the `live`/`vannaflow` accumulated book should show a **sustained negative excursion** coinciding with (a) administration incentive removal, (b) term-structure flip to vol expansion. If the model shows a clean positive book through that window, it's missing the vol-reversal term. `[call VERIFIED; gate design UNVERIFIED]`

2. **NEW SIGNAL — front-vs-back term-structure divergence (long-calendar signal).** Feature: `long_cal = IV_2M/3M − IV_1M` rising while `IV_1M` falling = the pre-expansion signature. Condition the "vol regime" feature on this rather than raw term slope. `[VERIFIED mechanics; my feature = UNVERIFIED]`

3. **REFINE vannaflow (signal #1) — add the "cap vs floor" dispersion split.** At the INDEX level weight ΔIV-signed vannaflow as before (persistent bid). Add a **momentum/call-skew overlay**: when call skew in the growth/momentum names is rich (call buying large), that flow adds a **ceiling** — i.e., a separate `momentum_vannaflow` sign that flips the net flow negative into stretched call-buying even while index flow stays positive. This operationalizes the "cap as well as a floor" observation. `[VERIFIED observation; my split = UNVERIFIED]`

4. **CONFIRMED — persist the ΔIV-signed daily flow** (signal #1): NexusFi's "change in positioning is what's important" + tastylive's whole thesis independently reinforce weighting daily flow by ΔIV/sign-of-change, not a fixed sign. `[VERIFIED]`

5. **Cheap far-dated vol → long-calendar / replace-delta rule.** When `IV_far_dated` is cheap relative to realized and the front is compressing, the model's vol-regime conditioning should flag "buy convexity / long-dated calls" rather than short-dated vol. `[VERIFIED thesis; implementable rule = UNVERIFIED]`

6. **No changes required to the sign-convention pin (Gate-0).** rec.vanna = −1×BS (dealer frame) remains correct; nothing in these videos contradicts it. `[VERIFIED — no counter-evidence]`

---

## 5. Sources

| URL | What it contributed |
|---|---|
| `https://www.youtube.com/watch?v=FSZalsb6ow0` | **PRIMARY for the active call.** tastylive "tasty Crumbs" (Aug 2026). Full description + full transcript. Verbatim "20 to 30% next year after post-midterms" drawdown call; term-structure long-calendar signal; cheap-far-dated-vol; OpEx cap-vs-floor dispersion. |
| `https://www.youtube.com/watch?v=8awiGrquYXI` | RCM "Vol Curves and Vanna Charm" (2020). Full transcript. Confirms vanna/charm definitions + fixed-strike-vol rationale. |
| `https://www.youtube.com/watch?v=X-RylGDzFr0` | Cboe Vol411 (2023). Already transcribed in deep-dive; re-confirmed description this session. |
| `https://www.youtube.com/watch?v=YDXxjmItRxc` | NexusFi Happy Hour (2021). Full transcript. "Change in positioning is important"; GEX/OI methodology critique; fixed-strike-vol P&L framing; dispersion/correlation; 1.5σ/2σ technical signals. |

---

## 6. Gaps / what could NOT be fully extracted

1. **No other video contradicted anything established** — no gaps on the sign pin or persistent-regime. `[GAP NONE]`
2. **The tastylive video transcript was extracted via a live browser session** (YouTube served 401 to the stateless extractor). If Jason wants it re-verified by the main agent's authenticated session, that's the way to get an independent pass — but the verbatim quotes in §2 are captured directly from the transcript panel and are sufficient. `[PARTIAL — browser-captured, not auth-session; quotes are verbatim]`
3. **Karsan's exact per-strike vanna magnitude/matrix** is still not published — the sign logic remains synthesized (as flagged in the deep-dive §8.2). `[UNVERIFIED — unchanged]`
4. **The "30% down" is an event/directional macro+flow call, not a per-ticker dealer-positioning signal** — it's Karsan's proprietary regime read. His exact sizing model is undisclosed (same as deep-dive §8.7). The *call* is now VERIFIED; the *proprietary mechanics behind it* are not. `[UNVERIFIED mechanics]`
5. **Timing ambiguity within the call:** "next year, after post-midterms" — the 20–30% is most-clearly framed as a 2027 event, while the near-term (Aug–Oct 2026) is "more volatility, maybe 10% pullback, but still net higher/supported." Both halves are verbatim; the precise calibration between "post-midterm vol risk" and "next year 20–30%" is not pinned to a date. `[VERIFIED wording; UNVERIFIED exact calendar]`

---

## Bottom line
The **#1 open item is resolved: the "20–30% drawdown AFTER the midterms" call is now VERIFIED**, with two independent verbatim confirmations (upload description + transcript), including the crucial timing nuance that it is explicitly "next year, after post-midterms," NOT a pre-midterm drawdown. The single most actionable new signal this set adds is the **front-vs-back term-structure divergence (long-calendar / cheap-far-dated-vol)** feature, which extends the deep-dive's term-structure regime flag with a specifically testable pre-vol-expansion signature.
