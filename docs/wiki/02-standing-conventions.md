# Standing Conventions

These are locked decisions. They are not derived in each page; they are pre-conditions. If you change one, you change the method.

| Conviction | Consequence in the code |
|---|---|
| **Units are contracts, not dollars.** | A `total_net` in contracts is not comparable to a dollar exposure until re-priced. |
| **SPX options chain is rooted under SPXW; SPX index price stays SPX.** | `shared/thetadata.py` maps `SPXW` back to `SPX` for price lookups. Calling `option_bulk_greeks("SPX", ...)` returns a `v2 payload is None` error. |
| **Default American option pricer is Leisen-Reimer, not CRR.** | `Options_Suite/main.py` resolves via `method="LeisenReimer"`. A regression to plain CRR on the default path is a known bug pattern. |
| **Three suites share one root venv; sentiment-scanner keeps its own.** | Root `.venv` serves Options_Suite, Vol_Suite, VaR_Tools_Simulations, Tools, and the dashboard. Sentiment must run from `sentiment-scanner/.venv`. |
| **Vol_Suite owns the `suite_context.json` schema.** | `Vol_Suite/suite_context.py` defines the contract; `shared/schemas.py` and `shared/suite_validation.py` enforce it. |
| **The dashboard is localhost-only and has no auth.** | `cloudflared tunnel` can expose it, but anyone with the link can see data and trigger billed ThetaData runs. |
| **Failure is loud.** | A missing input returns `status: failed` or a named gap (`delta_iv_missing`, `status: error`). A quiet zero is a bug. |
| **Charm is a real, minor calendar term; it is not 100% of hedge flow.** | The live expiry book separates charm from gamma/vanna/delta. Do not conflate the two. |
| **Like-for-like or do not compare.** | Same chain, same greeks, same dollar formulas; only the position differs. If that sentence cannot be written, the comparison is invalid. |
| **Change in positioning > level.** | Daily flow is signed by ΔIV, not by the level of IV. A high IV rank is not itself a signal. |

## Why these matter

The two-estimate method only works if the units are stable. If one estimate is in contracts and the other in dollars, the chart is meaningless. If the SPX chain is queried under the wrong root, the chain is empty. If the default pricer silently regresses to CRR, the greeks change. These conventions keep the two estimates on the same axis.

## What is not a convention

- **Model choice in Vol_Suite**: `vol_surface_replication` is the current default sign convention, but the others (`oi_heuristic`, `replication`) are still selectable. That is a knob, not a locked decision.
- **Expiry selection**: explicit expiry vs. target-years. A workflow choice, not a convention.
- **Which scanner runs**: the registry checkboxes select modules. A knob.

Next: the named objects these conventions protect.
