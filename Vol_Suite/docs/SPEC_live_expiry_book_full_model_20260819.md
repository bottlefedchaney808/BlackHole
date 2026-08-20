# SPEC — Live expiry-book as a FULL working model (not GEX lobby)

Status: **SUPERSEDED 2026-08-20.** Live path is expiry_book (`fetch_production_result`). ΔIV is vendor `surface_change` 7d, fail-closed (no ATM subtract). Keep as CARL R1 record. Current contract: `PLAN_vanna_stock_flow_scalars_20260820.md`.
Author: Hermes Agent standing in for Jason
Date: 2026-08-19
Supersedes: draft v1 hash `a7f2c173bf7c2d06eb785fbe5247d511d9999c2d4c625c9cb305a2a458d01f1f`
Worktree: `Dealer-Exposure-Dev`
CARL mode: SPEC

Identity: hash this file after write. That hash is the artifact identity, not a git ref.

Jason is not a debate participant. Implementation does not start until
Hermes and Cem converge on a hashed revision.

---

## CARL ledger (R1)

| ID | Sev | Disposition | Action |
|---|---|---|---|
| R1-F1 | critical | AGREE | 4.1 is option A. SVI cheap/rich is the book-sign. `build_structural_regime` must stop using `ne.gex()`. |
| R1-F2 | critical | AGREE | T0 must fail if book == GEX on a put-heavy fixture. |
| R1-F3 | major | AGREE | Fail the structural *arm*, not A/B/H/I. DTE buckets. Usable-book rule. `gex_scanner` must not emit signed-zero on error. |
| R1-F4 | major | AGREE | Hypothetical ±1% / ±1vol are full-shock on purpose (activation=1), labeled. Measured activation is a separate path. |
| R1-F5 | major | AGREE | One live ΔIV source: prior-close ATM IV via `prior_atm_iv` + `prior_iv_asof`. Not optional if F stays in the model. |
| R1-F6 | major | AGREE | SVI marks ARE the book-sign. Deadband required. T0 ≥1 mark is not a gate. |
| R1-F7 | major | AGREE | T0 asserts finite values + interp numbers + plotter path. Keys-only is forbidden. |
| R1-F8 | major | AGREE | One gamma field `gamma_book` is what locus, structural, and scanner read. `gex()` is reference only. |
| R1-F9 | minor | AGREE | Split/delete duplicate event flags; `dated_vol_shock` EXCLUDED; pass `q`; persistence ignores zeros; `gamma_tol` as fraction of mean \|per-expiry book gamma\|. |
| R1-F10 | minor | AGREE | Signature written in §5. |

R1 §8 correction accepted: multi-expiry of GEX is still GEX. Multi-expiry is how structural *aggregates* a non-GEX book-sign. It is not itself a sign.

---

## 0. Why this exists

Live path today prints GEX/DEX/three locus numbers and leaves the rest of
`expiry_book_exposure.py` unused. Confirmed by Hermes and Cem
(`20260819_233416_3e80bd`).

Jason: **full working models to test**. Workflow **build complete → test →
tune → test**. Hookup is an engineering gate. No predictive claim.

---

## 1. Locked decisions (do not relitigate)

1. Live engine remains `expiry_book_exposure` via `expiry_book_production`.
   Do not resurrect `compute_dealer_positioning` as live.
2. Dealer-frame composition after 2026-08-17 stays for **delta / vanna /
   charm / vega / volga** (signed once). Do not re-add a second −1 on
   `bs_vanna` / `bs_charm`.
3. Real spot, never median-strike.
4. Charm scaled `×(1/365)` if annualized, never `×(1/DTE)`.
5. Vanna flow when ΔIV exists:
   `SUM signed_vanna * OI * 100 * 0.01 * (dIV / 0.01)`, dIV decimal vol.
6. Classic GEX (call+/put− · Γ · OI · 100 · S² · 0.01) remains a
   **labeled imported reference only**. It is not the dealer book.
7. No PRE_WINDOW causal campaign. No auto-promotion of predictive claims.
8. Jason is not in the debate.

---

## 2. Failure we will not repeat

Functions existed; adapter called 3 of N; interp showed 1 of 3; tests
greened the engine in isolation; activation always 1.0; structural was
term-weighted `ne.gex()`.

Rule: if an arm is in the model it is computed, on the dataclass, in
artifacts, in interp, on a chart/table, and pinned by a test that fails
when the **value** is missing, default, or secretly equal to GEX.

---

## 3. The full live model (one object)

`ProductionDealerExposure` is the only live result.

| Arm | Field | Required for `status=available` | Notes |
|---|---|---|---|
| A Snapshot | `snapshot` | yes | Per-strike dealer-frame six-greek LEVEL. Gamma stored as `gamma_raw` (unsigned BS), `gamma_gex` (imported), `gamma_book` (unsigned × SVI book-sign). |
| B Locus | `execution_locus` | yes | Zero-gamma / walls / band / slope computed from **`gamma_book`**, not `gamma_gex`. |
| C Budget | `scenario_budget` | yes | Hypothetical full-shock cells labeled. Measured activation separate when ret/ΔIV exist. |
| D Structural | `structural: StructuralRegime \| None` | no | `None` + `structural_reason` if < usable near/mid/far. Does **not** fail A/B/H/I. |
| E SVI | `svi_overlay` | yes | Marks **are** the book-sign. Deadband required. |
| F Vanna flow | `vanna_flow_live` | yes if `prior_atm_iv` provided; else field present with `provenance=delta_iv_missing` | See §5. Not silently 0. |
| G Charm | `charm_1d` | yes | Shares/day from snapshot charm. |
| H GEX ref | `gex_reference` | yes | Imported call+/put−. Interp says `GEX (imported call+/put- reference)`. |
| I DEX | `dex` | yes | Carry descriptor, never a forecast. |

`status`:

- `available` — A,B,C,E,G,H,I populated; D may still be None.
- `available_partial` — A,B,H,I populated; C/E/G degraded with reasons.
- Never invent signed-zero GEX on fetch failure.

`gex_scanner` on `ExpiryBookUnavailable`: `error` field only. **Forbidden:**
`total_net_dollar_gamma = 0.0` as a stand-in.

---

## 4. Math contract

### 4.1 Book-sign is SVI cheap/rich (option A — F1)

**This is the dealer book.** It is not GEX.

1. `gamma_raw` = unsigned BS gamma (always ≥ 0).
2. `gamma_gex` = `_right_sign(right) * gamma_raw` — imported reference only.
3. SVI: `svi_rp.calibrate_svi` + `mark_chain` with **deadband**
   `IV_DEADBAND_VOL = 0.01` (1 vol point). `|mkt − ref| ≤ 0.01` → unmarked
   (book-sign 0). Above ref → SHORT (dealer short that strike, sign −1).
   Below ref → LONG (dealer long, sign +1).
4. **OTM-only:** ITM legs get book-sign 0 (same OTM set as
   `replication_reference._otm_leg_weights`).
5. `gamma_book` = `gamma_raw * book_sign * OI * 100 * S² * 0.01`
   (dollar-gamma-per-1% units, SVI-signed).
6. `snapshot.net("gamma")` **is defined as sum of `gamma_book`**.
7. `NetExposure.gex()` is renamed in production use to
   `gex_reference()` / field H. Callers of `.gex()` on the live path must
   not exist after this spec, or they must be the labeled reference only.

**One owner (F8):** locus zero-gamma, structural per-expiry carry, and
scanner gamma bars **all read `gamma_book`**. Not `greeks["gamma"]` if
that field remains GEX-signed.

`build_structural_regime` **must stop calling `ne.gex()`**. It sums
term-weighted `gamma_book` per expiry (same units). Pass `q` through.
`dated_vol_shock` is EXCLUDED (hardcoded False deleted or ignored).
`opex_crescendo` and `short_dte_anchor` must not be the same predicate —
keep one, delete the other, or split with distinct DTE cutoffs written
here: `short_dte_anchor` iff `dte <= 3`; `opex_crescendo` iff
`0 < dte <= 5` **and** expiry is on the monthly/quarterly OpEx calendar
(`opex_calendar.py` if present, else EXCLUDE opex_crescendo too).
Persistence = fraction of expiries with **nonzero** `gamma_book` that
agree with the weighted sign. Zeros do not agree with both sides.
`gamma_tol` = `0.05 * mean(|per_expiry gamma_book|)` (relative), not
`$1` of dollar-gamma.

### 4.2 Imported GEX reference

`gex_reference = SUM gamma_gex * OI * 100 * S² * 0.01`

Interp: `GEX (imported call+/put- reference)`. Never `Dealer GEX`.

### 4.3 Activation (F4)

Named hypothetical shocks (`up_1pct`, `down_1pct`, `iv_up_1pt`,
`iv_down_1pt`) are **full hypothetical shocks**. Activation = 1.0
**on purpose**. Interp and budget metadata must say
`full hypothetical shock, not a gate`.

Measured activation (separate fields, only when inputs exist):

- spot: `|session_ret| / tolerance_band`, clip [0, 1]
- IV: `|d_iv_measured| / 0.01`, clip [0, 1]
- time: 1.0 (charm is calendar)

`session_ret` source if present: `(spot - prior_close) / prior_close`
from an optional `prior_close` on the same signature as `prior_atm_iv`.
If absent, measured activation is omitted (not faked as 1.0).

T0: free-function probe at d_iv=0.005 still asserts 0.5 on the
**measured** helper. Adapter call `scenario_hedge_flow(..., d_iv=0.01)`
asserts hypothetical `activation["iv_up_1pt"]==1.0` **and** the label
string `hypothetical`.

### 4.4 q

Fetch; on error `q=0.0` and `q_source=fallback_zero`. Do not fail the
snapshot. Do not hide the fallback. Structural must receive the same `q`.

### 4.5 ΔIV source (F5)

One source, testable:

- Pure function takes `prior_atm_iv: float | None` and
  `prior_iv_asof: str | None` (YYYYMMDD).
- `d_iv_measured = atm_iv_now - prior_atm_iv` when both finite.
- Provenance: `PRIOR_CLOSE_ATM` (never `PRE_WINDOW`, never `CAUSAL`).
- Live `fetch_production_result` pulls prior-close ATM IV from
  `hist_stock`/`eod` ATM IV for the primary expiry’s previous session
  (one extra bounded request). If that fetch fails: F is present,
  `vanna_flow_live=None`, `vanna_flow_provenance=delta_iv_missing`.
  Snapshot still `available`.
- Tests inject `prior_atm_iv` on the fixture. No network.

---

## 5. Adapter API (F10)

```python
def production_result_from_rows(
    ticker: str,
    primary_expiry: str,
    spot: float,
    books: list[dict],   # each: {expiry, rows, dte}
    *,
    q: float = 0.0,
    prior_atm_iv: float | None = None,
    prior_iv_asof: str | None = None,
    prior_close: float | None = None,
) -> ProductionDealerExposure:
```

`books` may have length 1. Then A/B/C/E/G/H/I still compute;
`structural is None` and `structural_reason="insufficient_tenor_buckets"`.
Does **not** raise solely because `len(books) < 3`.

**Usable book:** finite IV, OI ≥ 0, strike count ≥ 8, `dte > 0`.

**Tenor buckets for structural (not 3 nearest weeklies):**

| Bucket | DTE |
|---|---|
| near | 2–10 |
| mid | 20–45 |
| far | 80–180 |

Structural runs only if **each** bucket has ≥1 usable book. Three weeklies
in 2–10 do **not** qualify. T0: a 3-weekly fixture ⇒ `structural is None`.

`fetch_production_result(td, ticker, primary_expiry)`:

1. Spot + q + primary chain.
2. `list_expirations`; probe candidates into the three buckets
   (sequential; stop when each bucket has one usable book).
3. Prior-close ATM IV + prior close (best-effort).
4. Calls `production_result_from_rows(...)`.
5. Never 6-way fan-out.

Drop unused `sign_model` from the production runner.

`volatility_suite._run_production_dealer_positioning`:

- Interp from `format_production_interp(result)` (new pure).
- Chart files from `plot_production_exposure(result, output_dir)` (new).
- Artifacts include every arm + reasons + `q_source` + ΔIV provenance.
- Returns those file paths, not `[]`.

Scanner / gex_scanner consume `gamma_book` / the same dataclass. No third
gamma path.

---

## 6. Workflow: complete → test → tune → test

### T0 — Hookup + anti-GEX pins (network-free, RED first)

`Vol_Suite/tests/test_live_full_model_hookup.py` calls
`production_result_from_rows` (the signature above).

Must fail on today’s adapter. Pass only when:

1. 3-bucket fixture (DTE 5 / 30 / 120) returns `StructuralRegime` with
   **finite** `carry`, and `per_expiry_gamma[exp] != build_net_exposure(...).gex()`
   on the put-heavy primary (F2).
2. Put-heavy / call-light fixture: `gex_reference < 0` AND `sum(gamma_book)`
   is computed from SVI marks (not from `_right_sign`). On a smile that
   marks the heavy puts LONG, `sum(gamma_book)` and `gex_reference` are
   **required-different**.
3. `svi_overlay` has unmarked strikes on a flat-smile fixture
   (`|diff|<=0.01`). Not every strike marked.
4. `scenario_budget.scenarios[name][channel]` finite for all named
   scenarios; hypothetical cells carry the `hypothetical` label;
   measured helper at d_iv=0.005 → 0.5.
5. `format_production_interp(result)` contains the **numeric** structural
   carry (or the structural_reason string), the numeric `gex_reference`,
   the substring `imported`, and must **not** contain `Dealer GEX`.
6. `plot_production_exposure` returns ≥1 existing PNG path (temp dir).
   T0 includes the plotter. T2 may not land without it.
7. Single-bucket (primary only) fixture does **not** raise; `structural is None`.
8. 3-weekly (DTE 2,5,8) fixture ⇒ `structural is None` (not a fake term
   structure).
9. `gamma_book` / `gamma_gex` / `gex_reference` are three names; a
   fixture asserts they are not silently aliased (identity check).
10. `gex_scanner`-shaped error path: unavailable ⇒ `error` set,
    `total_net_dollar_gamma` absent or null, never `0.0`.

### T1 — Engine math pins

Keep phase 0–6 tests. Add: FD sign agreement for dealer-frame
delta/vanna/charm; `vanna_flow(ne,+0.01) == -vanna_flow(ne,-0.01)`;
`build_structural_regime` on a fixture whose SVI signs oppose GEX
produces carry whose sign follows SVI, not `ne.gex()`.

### T2 — Adapter until T0 green

One series. No stub structural. No keys-only interp.

### T3 — subsumed into T0 for plotter/interp

No adapter land without render.

### T4 — Tune only knobs with tests

`tolerance_pct`, SVI deadband (now required), structural relative
`gamma_tol` fraction. One knob per cycle. Fixture hashes before/after.

### T5 — Optional live smoke fixture after T4

Captured JSON, then network-free.

---

## 7. Scope

**SELECTED:** §3–§6 as written (4.1A). Deadband. `gamma_book` owner.
Partial structural. Prior-close ATM IV contract. Hookup tests that
prove the book is not GEX. Plotter in T0. `gex_scanner` error contract.

**CONDITIONAL:** T5 live smoke. `opex_crescendo` only if `opex_calendar`
exists.

**PRESERVED:** 8/17 dealer-frame on non-gamma greeks. Engine module
(extend, don’t fork). Legacy `dealer_positioning` locked to backtests.
Existing phase tests, updated where they asserted GEX-as-structural.

**EXCLUDED:** PRE_WINDOW campaign. New live sign-model menu.
`dated_vol_shock`. Fail-closed whole suite on missing far expiry.
Predictive-edge claims. Keys-only hookup tests.

---

## 8. Hermes position (v2)

Cem was right that v1 left GEX in the load-bearing walls. v2 pulls it
out: SVI+OTM+deadband is the book; GEX is a labeled reference; locus and
structural read the same field; T0 fails if they are the same number on
a put-heavy smile; missing far expiry degrades structural, it does not
kill the desk; hypothetical shocks are honest full-shocks; measured ΔIV
has a real input.

Attack v2 if 4.1A is still underspecified against `svi_rp.mark_chain`,
if `gamma_book` units disagree with locus, or if T0 can still green a
disguise. Do not relitigate locked §1 or the choice of A vs B — A is
the driver decision.

---

## 9. Acceptance (debate)

Converged when no critical/major finding remains without AGREE-patched
language or DISAGREE-with-file:line. Then `convergence: yes` and only
then may T0 be opened as RED tests.
