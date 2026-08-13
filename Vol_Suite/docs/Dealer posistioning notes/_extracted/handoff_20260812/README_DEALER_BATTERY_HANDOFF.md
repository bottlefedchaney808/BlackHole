# Dealer-Seed / Vanna Battery — Full Handoff (WSL → Windows)

**Author:** Hermes default profile, 2026-08-12
**Purpose:** Self-contained context so a fresh Hermes (Windows) can pick up the
dealer-positioning vanna/seed investigation with full memories, decisions,
test results, and runnable code + saved data — without re-doing the research or
re-pulling the proxy.

---

## 0. TL;DR — where the project landed

The dealer-positioning **seed axis is dead**; the **vanna-weighted FLOW is the
live signal**. Built a reusable SVI-RP smile module, made SVI the default smile
fitter everywhere, and wired vannaflow into the live dealer model.

Three headline facts a fresh agent must internalize:

1. **The seed is a hallway poster.** Across 12 tickers at 150d, all three seed
   constructions (replication / vanna / svi_rp) produce near-identical end
   books — seed_share 0.000–0.009, flow utterly dominates. No seed construction
   matters.
2. **Vanna-weighted flow is the signal.** Weighting daily OI flow by
   `rec.vanna × sabr_deviation sign` moves the dealer-short read on 10/12
   tickers, always toward LESS short. Two regime flips (SPY SHORT→LONG, NFLX
   LONG→SHORT). Cuts of −39% to −80%.
3. **The measurable vanna convention is `rec.vanna = −1 × BS_vanna`** (Gate-0
   pin: SPY sign-consistency −0.956, QQQ −0.989, |scale| ≈ 1.0). Consumers must
   read `rec.vanna` directly, never a local BS closed form + never stack a
   right_dir (double-signing).

---

## 1. Decisions (chronological, final)

| # | Decision | Date | Evidence |
|---|---|---|---|
| D1 | Dealer sign model = V5 Direction (sabr_deviation per-expiry, 150d accumulation, NO_CALL gate, fallback_bias gate RESTORED) | 08-09 | backtest panel; see memory |
| D2 | **Seed-flip DITCHED** — only initial book state could be flipped, never daily flow/SABR/gates | 08-11 | Jason |
| D3 | RP-quantized seed ABANDONED — w_K ~1e-5 at ATM, OI/w in millions, no meaningful whole-RP anchor; breaks on thin chains | 08-11 | real data |
| D4 | vega-scaled flat σ_ref ABANDONED — vega→0 at tails, every far-OTM strike marked SHORT | 08-11 | real data |
| D5 | **Convention measured, not assumed**: `rec.vanna = −1 × BS_vanna` (resolves Jason's circularity objection) | 08-11 | SPY/QQQ pin |
| D6 | M2 joint test = GREY everywhere → lead-lag (k∈{0,1,2}) is the pre-registered decider | 08-11 | 4 samples |
| D7 | **vannaflow = LIVE default** (flow × rec.vanna × sabr_deviation), safety fallback when vanna absent | 08-12 | 12-ticker run |
| D8 | **All smile features use SVI** — scanner, strategy tool (cached loader), dealer sign resolver; `VOL_SURFACE_FITTER=svi\|sabr\|quadratic` toggle keeps SABR accessible | 08-12 | Jason explicit |
| D9 | SVI-RP smile → standalone reusable module (`svi_rp.py`) | 08-12 | Jason "use it over and over" |

---

## 2. The measurable vanna convention (Gate-0)

**Pinned rule (2-ticker, real spot):**
```
rec.vanna = -1 × BS_vanna(IV, spot, TTE)
```
- SPY (spot 770.89): sign-consistency **−0.956**, |v/b| 0.951, uniform across
  moneyness + rights.
- QQQ: **−0.989**, |v/b| 1.005.

**CRITICAL pitfall:** estimating spot as the **median strike** produces a FALSE
right-dependent sign reading (SPY OTM-call 0/112+ vs QQQ 98/98+). Use real spot
(`underlying_price` / `hist_stock_eod` close).

**Why this matters for recompute:** recomputing historical vanna from solved
EOD IV is NON-circular because the convention is measured first (the only
residual assumption is flip-stability over the lookback, testable separately).

---

## 3. M2 joint test results (all GREY)

The M2 net delta-OI relation (pooled p 0.0245, inverted-positive) was the target.

| Sample | n days | sign-agreement | Spearman ρ (p) | book_b coef (p) | Verdict |
|---|---|---|---|---|---|
| SPY 30d | 40 | 0.600 | −0.004 (0.98) | 0.0036 (0.67) | GREY |
| SPY+QQQ | 71 | 0.561 | 0.165 (0.17) | ns | GREY |
| AAPL+NVDA | 278 | 0.481 | 0.003 (0.95) | ns | GREY |
| AMD+TSLA | 279 | 0.523 | 0.158 (0.008) | ns | GREY |

**Interpretation:** book_b (Δvanna flow leg) is **orthogonal to M2** and not
predictive of realized vol on its own. Bounds: ≥0.80 = collinear/artifact ROBUST;
≤0.40 = separate volga book; GREY → lead-lag decides (NOT yet run).

---

## 4. The 12-ticker seed/flow comparison (150d, 4 arms)

Arms: `live(repl)` / `vanna_seed` / `live+vannaflow` / `svi_rp_seed`, all on the
same SABR-signed accumulation flow. Sequential fetches (OI-by-day is
proxy-fragile — single ticker only).

| Ticker | live | vanna_seed | live+vannaflow | svi_rp_seed | vf effect |
|---|---|---|---|---|---|
| SPY  | −1,282,622 S | −1,279,793 | **+275,735 L** | −1,281,090 | FLIP→LONG |
| QQQ  | −78,082 S | −76,608 | −32,390 | — | −59% |
| AAPL | −187,635 S | −165,095 | −114,435 | — | −39% |
| NVDA | −201,396 S | −144,348 | −109,785 | — | −45% |
| AMD  | −84,854 S | −64,875 | −50,999 | — | −40% |
| TSLA | −73,042 S | −52,440 | −77,212 | — | +6% (odd) |
| MSFT | −152,965 S | −152,892 | −57,209 | −152,855 | −63% |
| META | −108,906 S | −108,903 | −60,791 | −108,892 | −44% |
| GOOGL| +7,873 L | +7,878 | +202 L | +7,893 | −97% (near-0) |
| AMZN | −79,407 S | −79,375 | −45,894 | −78,391 | −42% |
| NFLX | +50,260 L | +50,018 | **−35,689 S** | +50,330 | FLIP→SHORT |
| JPM  | −17,164 S | −17,157 | −3,402 | −17,044 | −80% |

**Findings:**
- Seed axis dead (all 3 seed arms ~identical books).
- vannaflow moves the book 10/12, toward less dealer-short, uniform direction.
- Odd ones out: GOOGL (near-flat small book ≈ noise), TSLA (+6% more short).

---

## 5. Why the earlier "flip" work failed (so you don't repeat it)

- **Design A (flipped seed):** plan-only, `DEALER_SEED_SIGN` never existed in
  code until implemented; falsifier seed_share 26.08% (repl) on 3 usable days —
  not decision-grade. On the dense 150d path replication seed_share collapsed
  to **0.1%** → flow dominates.
- **RP-quantized seed:** w_K ~1e-5 at ATM, sum(w)=0.0165, OI/w in millions.
- **vega-scaled flat:** σ_ref→0 at tails, 102/141 strikes SHORT.
- **v5 is NOT accumulation** — never compare seed/accumulation arms against
  `net_gamma_v5` (that's the level-OI direction model).

---

## 6. Data sources & proxy rules (CRITICAL for any re-run)

- **Proxy:** PotatoHedge ThetaData (api.potatohedge.com), creds in `.env`.
- **Fragility:** `option_bulk_hist_oi_by_day` is the MOST fragile route — cannot
  handle even 2 concurrent tickers (502s → oi=0 garbage). Run OI work
  SEQUENTIALLY with 3× whole-fetch retry.
- **EOD route:** `option_bulk_hist_eod` handles staged pairs at
  THETADATA_HIST_CONCURRENCY=4-6; 4×4 concurrent whole-chain fan-outs open the
  breaker (needs ~60s cooldown).
- **oi=0 after genuine errors = GARBAGE** (nothing to seed/flow), NOT a valid
  "flow dominates" result. If OI is 0, re-fetch.
- **Dense EOD recompute:** 31 days × ~240 strikes vs ~2 dates/40d on the sparse
  greeks route. IV solved from bid/ask via `implied_vol.implied_vol`.

---

## 7. Code / module inventory (the reusable pieces)

### Core modules
- **`Vol_Suite/svi_rp.py`** — THE reusable SVI-RP smile module. `calibrate_ssvi()`
  → `SviRpReference` (`.sigma_ref`, `.mark_chain`, `.seed`), pure `ssvi_w`/
  `svi_w`/`bs_price`/`bs_vega`. Calibration = 3-observable SSVI (Gatheral &
  Jacquier, arXiv:1204.0646): ATM vol (level anchor) + ATM skew + put-wing
  slope. No network. `test_svi_rp.py`.
- **`Vol_Suite/svi_rp_smile.py`** — thin CLI wrapper over the module (plots the
  smile + marking). Network fetch.
- **`Vol_Suite/replication_reference.py`** — the accumulation engine.
  `_accumulate_from_history(ticker, expiry, lookback, seed_mode, greeks, oi,
  spot)` is the pure function. seed_mode ∈ replication|vanna|svi_rp. vannaflow
  is LIVE default (`DEALER_VANNA_FLOW` defaults "1"). `DEALER_SEED_SIGN` knob
  (flip) remains but unused.
- **`Vol_Suite/vol_surface_reference.py`** — dealer sign resolver. Default
  fitter now SVI (`VOL_SURFACE_FITTER=svi|sabr|quadratic`). `resolve_vol_surface_sign`
  reads `deviation_by_strike` (fitter-agnostic).
- **`Vol_Suite/options_chain_scanner.py`** — scanner. `fit_svi_smile()` uses SVI
  by default, same contract as SABR/quadratic.

### Comparison / reproduction runners
- **`Vol_Suite/seed_flip_compare.py`** — the 4-arm seed/flow comparison. Set
  `SEED_FLIP_DAYS` (default 150). `python seed_flip_compare.py SPY 150`.
- **`Vol_Suite/seed_data_maker.py`** — **fetch-once, save-to-disk** (THE data
  maker). `python seed_data_maker.py SPY 150 out_dir` → writes
  `seed_data_SPY_<expiry>_150d.json`.
- **`Vol_Suite/seed_data_loader.py`** — load saved payload offline:
  `greeks, oi, spot = load_seed_data(path)`. **Use this on Windows to avoid the
  proxy.**
- **`seed_compare_150.sh`**, **`save_seed_data.sh`**, **`staged_m2_pairs.sh`** —
  sequential runner scripts (proxy-safe).
- **`Vol_Suite/vanna_transform_pin.py`** — the Gate-0 convention pin (rec.vanna
  vs BS across moneyness).
- **`Vol_Suite/recompute_vanna_from_eod.py`** — dense EOD IV → BS vanna → −1
  flip → per-day vanna history (non-circular).

### Tests (the verification battery)
- `tests/test_svi_rp.py` — SVI module known-answers
- `tests/test_chain_scanner_svi.py` — scanner SVI fitter
- `tests/test_vol_surface_reference.py` — sign resolver incl. `test_svi_is_default_fitter`
- `tests/test_replication_reference_accumulation.py` — accumulation + vanna-flow + seed modes
- `tests/test_book_b_eod_derivation.py` — book_b EOD derivation
- `tests/test_vanna_parity.py` — Gate-0 parity fixture
- `tests/test_backtest_stage3.py` — v5 wiring (pins SABR), book_b
- `tests/test_pooled_panel_backtest.py` — M2 joint

### Specs / research
- `docs/superpowers/specs/battery-consolidated-20260811.md` — the master battery
  spec (appended with every finding/decision/result).
- `docs/superpowers/specs/research-variance-smile-20260811.md` — the SVI-smile
  research deep-dive.
- `.hermes/desktop-attachments/SVI Smile.pdf` — Gatheral & Jacquier paper.

---

## 8. The saved data (what's in `seed_data/`)

Each `seed_data_<TICKER>_<expiry>_150d.json` contains the full dense EOD greeks
(IV solved, vanna injected), OI-by-day, and spot for that ticker — so the
comparison can be re-run offline with `seed_data_loader.py` + a modified
`seed_flip_compare` that reads from disk. No proxy needed.

---

## 9. Verification status & how to verify

- **The authoritative check is the PER-SUITE scrubbed pytest** (bare `pytest`
  from repo root FAILS collection — documented cross-suite collision:
  `ModuleNotFoundError: var_engine`, `AttributeError: expiry_selector` across
  flat sibling suites). Use:
  ```
  cd Vol_Suite && env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 -m pytest tests -q
  ```
  → **408 passed / 5 skipped** on current HEAD.
- `svi_rp.py` is pure (network-free) → unit-testable anywhere. The scanner /
  dealer modules need network for live runs but are logic-tested offline.
- `hermes verify` from root reports collection errors — that is the documented
  monorepo limitation, NOT a regression.

---

## 10. What's NOT done / next steps

1. **Lead-lag test (k∈{0,1,2})** — the pre-registered M2 decider, not yet run.
2. **OOS falsifier (battery step 7)** — does the vannaflow read (book_b) predict
   forward realized vol/skew better than locked V5? THE capital-relevant test.
3. **SVI right-tail** — SVI's falling right tail under-marks SPY's call-wing
   upturn (known limitation; 2-sided/asymmetry refinement possible).
4. **archive/dealer-positioning-alternates/** — still empty.

---

## 11. Environment facts (so the Windows Hermes doesn't fight the tooling)

- Project interpreter: `Financial_Dev_Env/bin/python3` (Python 3.14).
- Package manager: pip (`Financial_Dev_Env/bin/python3 -m pip install -r requirements.txt`).
- Data creds: `.env` → PotatoHedge ThetaData proxy.
- ThetaData row normalization: EOD rows may use `CALL`/`PUT`, dollar-string
  strikes, `created` date; `_normalize_eod_greeks_rows` preserves extras.
- Never estimate spot as median strike for vanna (false right-dependence).
- Canonical path rule: `/home/bottl/Financial_Development` is the ONLY editable
  copy; `/mnt/c/...` is a stale Windows snapshot.
