# LIVE dealer model — 2026-08-20

Production: `Vol_Suite/volatility_suite.py::_run_production_dealer_positioning`
→ `expiry_book_production.fetch_production_result`

Not v1, not v2, not `direction` / SABR.

## What a run prints

| Object | Unit | Notes |
|---|---|---|
| `gamma_book` | $ / 1% (SVI OTM cheap/rich) | Plot gamma panel |
| `residual_vanna_inventory` | shares / vol-pt | Structural dealer-frame net vanna |
| `vanna_flow_live` | shares | Inventory × 7d vendor ΔIV. Never added to inventory. |
| GEX / `hedge_requirement` | legacy schema | Still emitted so `validate_vol_result` does not drop the payload |

Missing `volatility.surface_change` → flow **unavailable** (no ATM subtract).

Theta strikes: thousandths. Scale `abs(strike) >= 1000`.

SVI: OTM-only + sqrt(OI). Per-strike vanna is **not** × `book_sign`.

## Docs

- Current contract: `PLAN_vanna_stock_flow_scalars_20260820.md` (implemented)
- CARL v2 plan: `PLAN_expiry_book_exposure_v2_20260814.md` (superseded)
- Skill: `dealer-positioning-model` (live banner 2026-08-20)

Research stays descriptive/conditional.
