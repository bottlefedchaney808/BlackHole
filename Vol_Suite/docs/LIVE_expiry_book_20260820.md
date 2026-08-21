# LIVE dealer model — 2026-08-20

**Charts (2026-08-21):** GEX/VEX/CEX panels, flip, and hedge are VannaCharm stock + today's `scanner_trades`, not SVI `gamma_book`. See `LIVE_expiry_book_20260821.md`.

Production: `Vol_Suite/volatility_suite.py::_run_production_dealer_positioning`
→ `expiry_book_production.fetch_production_result`

Not v1, not v2, not `direction` / SABR.

## What a run prints

| Object | Unit | Notes |
|---|---|---|
| `gamma_book` | $ / 1% (SVI OTM cheap/rich) | Still stamped. **Not** the 4-panel as of 2026-08-21 |
| `residual_vanna_inventory` | shares / vol-pt | Structural dealer-frame net vanna |
| `vanna_flow_live` | shares | Inventory × 7d vendor ΔIV. Never added to inventory. Separate from session dVEX |
| GEX / `hedge_requirement` | legacy schema | Still emitted so `validate_vol_result` does not drop the payload |

Missing `volatility.surface_change` → 7d vanna flow **unavailable** (no ATM subtract).

Theta strikes: thousandths. Scale `abs(strike) >= 1000`.

SVI: OTM-only + sqrt(OI). Per-strike vanna is **not** × `book_sign`.

## Docs

- Chart contract: `LIVE_expiry_book_20260821.md`
- 7d ΔIV contract: `PLAN_vanna_stock_flow_scalars_20260820.md`
- CARL v2 plan: `PLAN_expiry_book_exposure_v2_20260814.md` (superseded)
- Skill: `dealer-positioning-model`

Research stays descriptive/conditional.
