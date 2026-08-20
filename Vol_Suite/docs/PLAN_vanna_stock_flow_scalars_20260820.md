# PLAN — Vanna stock vs flow (two scalars, two units)

Status: **IMPLEMENTED on master 2026-08-20** (expiry_book live path). Research claims remain descriptive/conditional; nothing auto-promoted.
Author: Hermes Agent (default profile), 2026-08-20
Consult: Cem Karsan skill dispatch `deleg_8e54042b` (2026-08-20). Verdict: stock-vs-flow split is grounded; summing EOD inventory + session flow is a unit smash; “neutral” is the wrong word.

Jason’s ask (verbatim gist): build the vanna view on a 1D lookback, add SVI-fit vanna flow to now, then two outputs — “total delta needed for neutral” and “total vanna needed for neutral” — assuming delta is always made by EOD.

Cem’s correction this plan implements: **do not glue those objects.** Report them adjacent. Rename the scalars. Keep SVI off the vanna series.

**Locked 2026-08-20 (Jason):**
1. One flow clock = **7d** (not 1d session + 7d). Inventory is the live OI book; flow is inventory × 7d vendor ΔIV.
2. **Delete ATM-subtract.** 7d `surface_change` miss → flow unavailable, not `PRIOR_CLOSE_ATM`.
3. Vanna flow only. No `vanna×ΔIV + charm + γ·dS` assumption line.

---

## 0. What this is

A **display + contract** change on the live `expiry_book` production path (`expiry_book_production.py` + plotter interp). It does **not** invent a new engine, does **not** 150d-accumulate vanna, and does **not** auto-promote a directional forecast.

The product is two labeled numbers a dealer would actually look at after the close:

| Field | Unit | Meaning |
|---|---|---|
| `residual_vanna_inventory` | shares / vol-pt | Dealer-frame net vanna you **cannot** kill with stock. Delta the book will manufacture on the *next* 1-vol-point. |
| `session_delta_to_hedge` | shares | Stock/futures the **same** inventory implies *this session*, given measured session ΔIV. Sign: long-vanna + vol down → BUY; vol up → SELL. |

They are **never added**.

Research claims stay descriptive/conditional. Falsifier is CONDITIONAL and does not gate shipping the labels.

---

## 1. Grounding — sources read

- Cem consult transcript: `C:\Users\bottl\AppData\Local\hermes\cache\delegation\subagent-summary-0-20260820_052802_423781.txt`
- `Vol_Suite/expiry_book_production.py` — `ProductionDealerExposure`, `vanna_flow_live`, `d_iv_used`, 7d `get_iv_surface_change`, ATM-subtract fallback, `format_production_interp`
- `Vol_Suite/expiry_book_exposure.py` — `exposure["vanna"]` units `shares/vol-pt`; `vanna_flow` = Σ vanna × (dIV/0.01) → **shares**; `apply_svi_book_signs` stamps `book_sign` + `gamma_book` only
- `Vol_Suite/dealer_positioning.py` — `plot_expiry_book_greek_exposure` vanna panel: raw `exposure_of("vanna") × (dIV/0.01)`, **no** `book_sign` (ATM flip wall)
- `Vol_Suite/svi_rp.py` — OTM-only + sqrt(OI) fit (2026-08-20)
- `Vol_Suite/volatility_suite.py` — dealer artifacts still emit legacy `hedge_requirement = abs(gex*0.01)`
- `shared/thetadata.py` — `get_iv_surface_change`; 503s have been falling back to ATM subtract
- House: `financial-development-planning` + `references/implementing-plan-phases.md`
- Locked: vendor ΔIV unflipped; deadband 0.01; lookback 7d cap 14 **as a separate 7d clock**; live engine = expiry_book

---

## 2. Empirical / context — honest conclusion up front

- Live WMT 2026-08-20: `vanna_flow_provenance = PRIOR_CLOSE_ATM` because `volatility.surface_change` **503’d**. ATM subtract is a moving target; skew makes ATM vol rise on down-moves. Cem: that fallback **manufactures** flow. Session scalar must **fail closed**, not substitute.
- SPY vs QQQ snapshot signs have disagreed in prior falsifier cuts (SPY −0.37 / QQQ +0.35). Do not pool. Do not promote.
- “EOD delta-flat” is an **assumption**, not a desk fact. Index dealers hedge continuously and *carry* short-put / long-call overnight, already stock-hedged. A clean “shares to flatten” number that pretends yesterday’s close zeroed delta **never existed on a desk**. Label the assumption if the full residual (vanna×ΔIV + charm + γ·dS) is ever shown.

---

## 3. Object split (do not negotiate)

```
STOCK  residual_vanna_inventory  = snapshot.net("vanna")     # shares/vol-pt
FLOW   session_delta_to_hedge    = STOCK * (session_dIV / 0.01)  # shares
                                 # 0 if |session_dIV| <= 0.01
                                 # None (unavailable) if no vendor session ΔIV
```

7-day ΔIV (existing `VANNA_FLOW_LOOKBACK_DAYS=7`, cap 14) stays as **`vanna_flow_7d`** with its own provenance. It is **not** the session scalar and is **not** added to inventory.

Per-strike vanna chart: dealer-frame, **no** `book_sign`. ATM flip wall stays. SVI cheap/rich stays on **gamma** (`gamma_book`). No `vanna_book = vanna × book_sign`.

---

## 4. Phased tasks (TDD, network-free)

Venue (every run):

```bash
cd C:/Users/bottl/FinancialDevelopment/Vol_Suite
env -u PYTHONPATH -u VIRTUAL_ENV ../.venv/Scripts/python.exe -m pytest tests/test_expiry_book_production_contract.py tests/test_svi_robust_fit.py tests/test_expiry_book_phase4_structural.py tests/test_expiry_book_phase5_svi_overlay.py -q -p no:cacheprovider
```

### Phase 1 — Contract: two fields, two units, never sum

**Files:**
- Modify: `Vol_Suite/expiry_book_production.py` (`ProductionDealerExposure`)
- Test: `Vol_Suite/tests/test_expiry_book_production_contract.py`

**Produces:**
- `residual_vanna_inventory: Optional[float]`
- `session_delta_to_hedge: Optional[float]`
- `session_div: Optional[float]`
- `session_div_provenance: str`  # `SURFACE_CHANGE:session` | `SURFACE_CHANGE:session:deadband` | `unavailable`
- units keys: `"residual_vanna_inventory": "shares_per_vol_point"`, `"session_delta_to_hedge": "shares"`

- [ ] **Step 1: failing tests**

```python
def test_vanna_stock_and_session_flow_are_not_added():
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        d_iv_measured=0.02, d_iv_source="SURFACE_CHANGE",
        session_div=0.02, session_div_source="SURFACE_CHANGE:session",
    )
    stock = result.residual_vanna_inventory
    flow = result.session_delta_to_hedge
    assert result.units["residual_vanna_inventory"] == "shares_per_vol_point"
    assert result.units["session_delta_to_hedge"] == "shares"
    assert stock is not None and flow is not None
    # The glue Jason first described is illegal:
    assert abs((stock + flow) - stock) > 0 or abs(flow) > 0
    assert "neutral" not in format_production_interp(result).lower()


def test_session_delta_is_inventory_times_session_div():
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        session_div=0.02, session_div_source="SURFACE_CHANGE:session",
    )
    expected = result.residual_vanna_inventory * (0.02 / 0.01)
    assert result.session_delta_to_hedge == pytest.approx(expected)


def test_session_deadband_zeros_flow_keeps_inventory():
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        session_div=0.005, session_div_source="SURFACE_CHANGE:session",
    )
    assert result.session_delta_to_hedge == 0.0
    assert result.residual_vanna_inventory != 0.0
    assert result.session_div_provenance.endswith(":deadband")


def test_missing_session_div_fails_closed():
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
    )
    assert result.session_delta_to_hedge is None
    assert result.session_div_provenance == "unavailable"
    assert result.residual_vanna_inventory is not None
```

- [ ] **Step 2: run RED** — `pytest tests/test_expiry_book_production_contract.py::test_vanna_stock_and_session_flow_are_not_added -q` fails on missing fields.
- [ ] **Step 3: minimal fields on the dataclass + `production_result_from_rows`**
  - `residual_vanna_inventory = snapshot.net("vanna")`
  - session flow as above; `VANNA_FLOW_DIV_DEADBAND` reused
  - 7d path (`d_iv_measured` / `vanna_flow_live`) **unchanged** except it is no longer the session scalar
- [ ] **Step 4: GREEN** those four tests + existing 11 contract tests
- [ ] **Step 5:** do not commit unless Jason says so

### Phase 2 — Session clock fetch (fail closed)

**Files:**
- Modify: `Vol_Suite/expiry_book_production.py` (`fetch_production_result`)
- Modify: `shared/thetadata.py` only if session baseline needs an explicit prior-session date (already have `get_iv_surface_change`)
- Test: same contract file, fake TD

Session ΔIV = `get_iv_surface_change(ticker, expiry, baseline=prior_trading_day)`. **Not** 7d. **Not** ATM subtract.

- [ ] Fake TD with `get_iv_surface_change` returning `{atm_change: 0.02}` for a 1-day baseline → `session_div_provenance == "SURFACE_CHANGE:session"` and `session_delta_to_hedge` set
- [ ] Fake TD whose session call raises / returns None → `session_delta_to_hedge is None`, provenance `unavailable`. 7d field may still populate from its own clock.
- [ ] **No ATM-subtract path for the session scalar.** Prior-close ATM remains allowed **only** as provenance on `vanna_flow_7d` if we keep that fallback at all; preferred: 7d also fail closed, ATM subtract deleted. SELECTED for session; CONDITIONAL delete of ATM fallback on 7d (see §6).

### Phase 3 — Interp + suite artifacts (labels, not a forecast)

**Files:**
- Modify: `Vol_Suite/expiry_book_production.py` `format_production_interp`
- Modify: `Vol_Suite/volatility_suite.py` `_run_production_dealer_positioning` interp (today it does **not** call `format_production_interp` — wire it or duplicate the two lines)
- Modify: dealer artifact dict in `volatility_suite.py` — **add** optional keys; do **not** remove `hedge_requirement` (schema still requires it when `available=True`)

Interp must contain, exactly this shape:

```
Vanna inventory (shares/vol-pt): {residual_vanna_inventory:,.0f}
Session delta from vanna (shares, dIV={session_div:+.4f}, {session_div_provenance}): {session_delta_to_hedge or 'unavailable'}
Vanna flow 7d (shares, {vanna_flow_provenance}): {vanna_flow_live or 'n/a'}
```

Forbidden strings in interp: `neutral`, `flatten`, `needed for`.

- [ ] Test: `test_interp_names_stock_and_session_flow_not_neutral` asserts the three lines and rejects those words.

### Phase 4 — Plotter caption only

**Files:**
- Modify: `Vol_Suite/dealer_positioning.py` `plot_expiry_book_greek_exposure`

Vanna **panel series unchanged** (flip wall × session dIV if session dIV is available; else show inventory level with ylabel `Vanna (shares / 1pp IV)` and do not pretend it is flow).

Figure subtitle / axis label:
- session dIV present: `Session vanna→delta (shares, dIV={session_div:+.4f})`
- else: `Vanna inventory (shares / 1pp IV) — session ΔIV unavailable`

- [ ] Extend `C:\Users\bottl\AppData\Local\Temp\render_check_gex.py` (or a Vol_Suite test) to assert ylabel contains `Session vanna→delta` when `session_div` is set, and does **not** contain `book_sign` multiplication (mixed-sign flip wall still holds).

### Phase 5 — Pin: SVI does not multiply vanna

- [ ] Test already implied by render check + `test_svi_robust_fit`. Add:

```python
def test_apply_svi_book_signs_does_not_create_vanna_book():
    # existing snapshot after apply_svi_book_signs
    assert not hasattr(row, "vanna_book") or row.vanna_book is None
    assert row.book_sign in (-1.0, 0.0, 1.0)
    assert hasattr(row, "gamma_book")
```

---

## 5. Falsifier (CONDITIONAL — not in the live path)

**Claim (descriptive only):** signed `session_delta_to_hedge` associates with same-session (or next-open) returns in the hedge direction.

**Pre-register then stop:**
- Clock match: session ΔIV with session returns. No 7d vol into 1d returns.
- Null: shuffled ΔIV on the **same** inventory; deadband-zero control.
- Split SPY vs QQQ. Do not pool.
- Report hit rate + sign of corr vs null. No auto-promote.
- Scalar B (`residual_vanna_inventory`) should **not** predict same-session returns once A is in the regression.

Do not implement this phase until Jason asks. The live labels can ship without it.

---

## 6. Scope taxonomy

**SELECTED**
- Two fields, two units, never summed
- `residual_vanna_inventory` from live snapshot net vanna (dealer-frame)
- `session_delta_to_hedge` from vendor session ΔIV × inventory; deadband 0.01 → 0; missing ΔIV → None
- Interp + artifact keys + plot ylabel
- Fail closed on session `surface_change` (no ATM subtract)
- SVI stays on gamma; vanna chart keeps ATM flip wall

**CONDITIONAL**
- Delete ATM-subtract fallback on the **7d** clock too (Cem wants it; 7d currently uses it)
- Full residual `vanna×ΔIV + charm_1d + gamma_book×dS` as a **third** labeled assumption-line (`eod_delta_flat_assumption=true` in provenance) — only if Jason still wants “everything after overnight delta”
- Falsifier (§5)
- Session ΔIV from `surface_change` with `asof_date` pinned to last completed session if vendor requires it (probe live; if 503 persists, session scalar stays unavailable — that is success, not a bug)

**PRESERVED**
- Live engine `expiry_book_exposure` / `fetch_production_result`
- `gamma_book` SVI OTM + sqrt(OI) smile
- Per-strike vanna **not** × `book_sign`
- Vendor ΔIV unflipped; deadband 0.01
- `VANNA_FLOW_LOOKBACK_DAYS = 7` / cap 14 as the **7d** clock only
- `hedge_requirement` schema key (legacy GEX formula) — still emitted so `validate_vol_result` does not discard the payload
- `CONTRACT_MULTIPLIER`, `VANNA_PP_SCALE`, `BOOK_SIGN_DEADBAND`

**EXCLUDED**
- `EOD_vanna + session_flow` as one number
- Names: “vanna to flatten”, “needed for neutral”, “delta-neutral vanna”
- `vanna_book = vanna × book_sign`
- Treating 1 trading day as the structural book (inventory is the live OI reconstruction; 1D is only the **flow clock**)
- 150d vanna accumulation
- Auto-promotion / causal claims
- Touching `dealer_positioning.py` sign model / v1 / v2
- Scanner re-deriving vanna (same-solver rule)

---

## 7. What pins current behavior

- `tests/test_expiry_book_production_contract.py` (11+ tests; vanna flow 7d + deadband)
- `tests/test_expiry_book_phase4_structural.py` (SVI cheap/rich regime — do not change the fitter)
- `tests/test_expiry_book_phase5_svi_overlay.py`
- `tests/test_svi_robust_fit.py` (OTM + sqrt OI)
- `tests/test_hedge_requirement.py` (legacy GEX hedge formula — leave it)
- `shared/schemas.py` `dealer_positioning` required keys when `available=True`
- Plotter flip-wall: put-side vanna +, call-side − (render check)

---

## 8. Risks / pitfalls

1. **EOD-delta-flat axiom** — printing `session_delta_to_hedge` as “the leftover after they flattened” overclaims. Caption: “vanna × session ΔIV (shares)”, not “delta still needed.”
2. **503 → ATM fallback manufactures flow** — session path fail-closed. If every name prints `unavailable`, that is the vendor, not a reason to bring ATM back.
3. **Clock mismatch** — 7d ΔIV on a session return is a fake magnitude. Two fields, two clocks.
4. **Unit glue** — any helper named `vanna_view = inventory + flow` is a bug. Reviewer rejects the PR.
5. **SVI × vanna** — collapses the ATM wall. Already burned once this week.
6. **Schema** — adding required keys to `validate_vol_result` will discard whole vol payloads. New keys optional.
7. Model may not predict anything. Acceptable. Do not paper over.

---

## 9. Open questions (Jason)

1. Keep 7d `vanna_flow_live` on the interp as a second flow clock, or hide it until session ΔIV is reliable?
2. Kill ATM-subtract on 7d now (CONDITIONAL) or leave it labeled `PRIOR_CLOSE_ATM`?
3. Do you still want the **assumption** line `vanna×ΔIV + charm + γ·dS`, or is session vanna-flow enough?

---

## Appendix — 30-second version

- Jason was right that **book ≠ today’s print** and that vanna is an **additive hedge insight next to delta**.
- He was wrong to **add** EOD vanna (shares/vol-pt) to vanna flow (shares) and to call either “neutral.”
- Ship two adjacent scalars: inventory, session delta-from-vanna.
- SVI marks gamma, not vanna.
- Session ΔIV fail-closed. Research only.

Cem: *If the plan still says “EOD prior + vanna flow, marked long/short per SVI, two neutrals” — that plan is wrong.* This document does not say that.
