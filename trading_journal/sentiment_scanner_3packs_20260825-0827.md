# Sentiment scanner — 3 most recent highlighted packs

**Status:** descriptive / conditional. Interpretation of existing scanner exports. Not a trade call. Not promoted.
**As-of:** 2026-08-28 07:19 CDT
**What this is:** the three newest `cns-threshold-alerts` packs on disk (`latest_manifest.json` updated 2026-08-27T19:03:47Z). Separate from `sentiment_x_context_20260826.md` (2-day X+pack handoff) and `market_rumor_watchlist_20260828.md`.
**No 2026-08-28 pack exists yet.** No ticker repeats across these three packs. All three packs: StockTwits only, `volume` = 60 messages, `pump_ratio` = 0.0, `options_suite` hint = false, vol/var hints = true.

Scoring (from `sentiment-scanner/scanner/narrative.py`): CNS is a 0–100 additive flag (`war>0.3` +30, divergence +25, `thesis_ratio>0.15` +25, low-pump +10, account-age +10, `n>50` +10, high-pump −20). `war_score = 2 * min(bull%, bear%)`. Highlight ≠ thesis.

---

## Pack 1 — 2026-08-27 (newest)

- **File:** `sentiment-scanner/data/exports/highlighted_ticker_packs/20260827/cns-threshold-alerts-4366db715a11.json`
- **Run:** `20260827T183233Z` · created 2026-08-27T19:03:47Z · priority high · 3 tickers

| Rank | Ticker | CNS | War | Thesis | Bull% | Bear% | Neutral% (100−B−Be) | Lean |
|---|---|---|---|---|---|---|---|---|
| 1 | MCD | 60 | 0.333 | 0.000 | 30.0 | 16.7 | 53.3 | mild bull, mostly unlabeled |
| 2 | CCL | 60 | 0.333 | 0.000 | 20.0 | 16.7 | 63.3 | near-flat, mostly unlabeled |
| 3 | PATH | 55 | 0.000 | 0.167 | 53.3 | 0.0 | 46.7 | one-sided bull, **not contested** |

**Read:** MCD/CCL clear the CNS=60 bar on war just above 0.3 plus n>50 / low-pump (and likely age). Thesis ratio is zero — split StockTwits tags, not DD. PATH is the odd one: war=0, no bears in the 60-message sample, thesis_ratio 0.167 is the only name in the three packs above the 0.15 thesis bonus. Confidence 0.575 vs 0.6 on the others. Treat PATH as **consensus-bull sample**, not a war name.

Consumer/QSR after WEN the prior day. Cruise (CCL) is the only travel name.

---

## Pack 2 — 2026-08-26

- **File:** `.../20260826/cns-threshold-alerts-ae1694f61186.json`
- **Run:** `20260826T222327Z` · created 2026-08-26T22:46:39Z · priority high · 3 tickers

| Rank | Ticker | CNS | War | Thesis | Bull% | Bear% | Neutral% | Lean |
|---|---|---|---|---|---|---|---|---|
| 1 | WEN | 60 | 0.467 | 0.100 | 40.0 | 23.3 | 36.7 | bull lean, highest war in this pack |
| 2 | ANF | 60 | 0.467 | 0.000 | 23.3 | 23.3 | 53.4 | **dead even** tagged sentiment |
| 3 | DJT | 60 | 0.400 | 0.000 | 20.0 | 30.0 | 50.0 | mild bear lean |

**Read:** Highest war day of the three (0.40–0.47). Still almost no thesis (WEN 0.10, others 0). ANF is a coin-flip of tagged bull/bear with a majority unlabeled — “contested” here means **balanced tags**, not two fleshed-out stories. DJT is the only political-media name; bear tags outnumber bull 30/20 with half unlabeled.

Retail/QSR cluster (WEN, ANF) sits next to the 8/26 retail-earnings tape already logged on [[Trading journal]] (ANF/KSS/BBWI beats that morning). The pack does **not** contain those earnings; it is StockTwits tag mix only.

---

## Pack 3 — 2026-08-25

- **File:** `.../20260825/cns-threshold-alerts-07474b3714d5.json`
- **Run:** `20260825T184753Z` · created 2026-08-25T18:59:59Z · priority high · 4 tickers

| Rank | Ticker | CNS | War | Thesis | Bull% | Bear% | Neutral% | Lean |
|---|---|---|---|---|---|---|---|---|
| 1 | MBLY | 60 | 0.533 | 0.000 | 26.7 | **70.0** | 3.3 | **strong bear** — only real directional print |
| 2 | INTU | 60 | 0.467 | 0.100 | 30.0 | 23.3 | 46.7 | mild bull |
| 3 | TSLA | 60 | 0.400 | 0.000 | 20.0 | 20.0 | 60.0 | flat tags, majority unlabeled |
| 4 | DKS | 60 | 0.333 | 0.033 | 20.0 | 16.7 | 63.3 | near-flat |

**Read:** MBLY is the only name in all three packs with a one-sided tagged majority **and** high war (war uses `min(bull,bear)`, so 26.7/70 still scores 0.533). Neutral is only 3.3% — people tagged this one. Thesis still 0.0, so it is tagged-bear flow, not written theses.

TSLA prints CNS 60 with 60% unlabeled and a 20/20 split. That is a **volume + war-threshold** hit, not a Tesla narrative. It does not reappear 8/26 or 8/27.

INTU is the only software name besides PATH; DKS continues the consumer-discretionary streak (ANF/DKS/WEN/MCD).

---

## Cross-pack interpretation

1. **Universe collapsed vs 8/24.** Manifest still lists many 8/17–8/24 packs; the last three calendar days of output are **one pack per day**, 3–4 names, 10 unique tickers, **zero overlap**. Scanner is not tracking a persistent contested set — it is emitting a fresh StockTwits CNS slice each run.

2. **Source is thin.** Every ticker `social_sources = ["stocktwits"]`. Reddit and YouTube are absent. Do not read these as multi-platform contested narrative. Same limitation as the 8/19 desk note pack.

3. **CNS is maxed by construction, not by thesis.** Nine of ten names are CNS **60**. With n=60 you already get +10; pump_ratio=0 gets +10; war>0.3 gets +30. That is 50 before age/divergence. Thesis bonus (+25) only fires on **PATH**. So the highlight list is mostly “enough messages + some opposing tags + not pump-spam,” not “deep fight over a catalyst.”

4. **Only two names have a usable lean:**
   - **MBLY (8/25):** 70.0% bear / 26.7% bull / 3.3% unlabeled. Strongest directional sample. Did not persist into 8/26–8/27 packs.
   - **PATH (8/27):** 53.3% bull / 0% bear / war=0. Opposite shape — uncontested bull, thesis_ratio 0.167. Different animal from the war names.

5. **The rest are unlabeled-majority or coin-flip.** TSLA, ANF, CCL, DKS, MCD: 53–63% unlabeled. Treating CNS=60 as “the street is fighting” overstates the tags.

6. **Sector shape, not a mega-cap tape.** Consumer/QSR/retail (MCD, WEN, ANF, DKS, CCL) plus one auto-ADAS (MBLY), one software (PATH), one tax/software (INTU), TSLA once, DJT once. No NVDA/SPY/QQQ in these three packs.

7. **Downstream hints are identical and unused here.** All three packs flag vol+VaR true, options false. This note does **not** pull Vol_Suite / VaR / chain files for these 10 names. If you want a tape, run those suites; do not infer vol from CNS.

8. **Do not stack with the rumor watchlist.** `market_rumor_watchlist_20260828.md` is a different pass (NVDA print, Warsh, Cybercab). These packs do not mention those events.

---

## Conditional takeaway

If the question is “what is the scanner actually highlighting right now,” the honest answer is: **a rotating, StockTwits-only, n=60 CNS cut, mostly unlabeled, almost no thesis text.** The two exceptions worth remembering as **descriptions of the sample**, not setups:

- 8/25 **MBLY** — tagged-bear crowd, then gone from later packs.
- 8/27 **PATH** — tagged-bull, uncontested, only thesis-ratio > 0.15.

Everything else (MCD/CCL/WEN/ANF/DJT/INTU/TSLA/DKS) is a threshold artifact until a later pack repeats the ticker or a non-StockTwits source shows up.

No orders placed.
