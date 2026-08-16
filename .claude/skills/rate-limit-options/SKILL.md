---
name: rate-limit-options
description: Use when acquiring options history via ThetaData (option_bulk_hist_oi_by_day / greeks routes) and hitting transient 502s or rate limits — distinguish rate-limit noise from genuinely missing data, and apply backoff/concurrency discipline.
---

# Rate-limit options acquisition (ThetaData)

Guidance for ThetaData options-history acquisition. Invoked via `/rate-limit-options`.

## Core rule
**Transient 502s are RATE LIMITS, not missing data.** A 502 / empty response on an options bulk
route usually means the proxy is rate-limited, not that the ticker/expiry has no data. Do NOT
record a hard gap or impute a zero from a 502.

## Acquisition discipline (from `Vol_Suite/seed_data_maker.py`)
- `option_bulk_hist_oi_by_day` is the **proxy-fragile leg** — treat every response as possibly
  rate-limited.
- **Retry up to 3× with backoff** on transient 502/5xx.
- **Run ONE TICKER AT A TIME** — never concurrent whole-chain fan-outs on this route.
- Prefer the DENSE EOD route (`option_bulk_hist_eod`, IV solved from bid/ask via
  `implied_vol.implied_vol`) + `hist_stock_eod` for spot over the sparser per-contract
  `option_bulk_hist_greeks` route, which is what starves history in the first place.

## Handling
- Transient 502 → backoff and retry (3× max) before classifying anything.
- After retries exhausted and the response is still empty/5xx, only then record a genuine
  gap/INELIGIBLE — never an imputed zero.
- Persist response status + retry count so the provenance census is honest.
- Respect `THETADATA_HIST_CONCURRENCY`; keep sequential for OI-by-day.

## Falsifier / provenance note
A rate-limited acquisition must be labeled as such (retried, backoff), never silently treated as
"data absent" — otherwise it poisons later causal/provenance claims.
