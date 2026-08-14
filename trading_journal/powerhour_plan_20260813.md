# Power-Hour Plan — 20260813 (Thu)
Generated: 2026-08-13 power hour window (3-4 PM ET / 2-3 PM CT)
Source: powerhour_prep_20260813.md (ThetaData + Direction 5-signal) + live quotes

## Market read
**No clean buy candidates today.** Rule = Direction >=3 AND whale=True. No name clears it:
all 9 tickers carry **NONE conviction**; whale=True only on SOUN/UUUU, but both are weak
direction (1-2). This is a **hold/trim session, not a fresh-entry session.** Do not force a
trade on weak confirmation.

Live quotes vs prep (all holding names green except KOS):
| Ticker | Prep spot | Live | Dir | Conv | whale | 1d move | read |
|---|---|---|---|---|---|---|---|
| SOUN | 7.265 | 7.325 | 1 | NONE | True | 3.45% | weak dir, whale only |
| UUUU | 14.515 | 14.541 | 2 | NONE | True | 3.81% | whale+squeeze, mod |
| TGB | 8.300 | 8.400 | 3 | NONE | False | 17.68% | **strongest dir, high IV** |
| KOS | 2.525 | 2.507 | 1 | NONE | False | 3.14% | weak, drifting |
| INR | 13.560 | 13.527 | 1 | NONE | False | 26.96% | high move, no dir |
| CRGY | 11.930 | 11.890 | 2 | NONE | False | 2.49% | trend, no whale |
| PTEN | 10.905 | 10.935 | 2 | NONE | False | 11.76% | squeeze, no whale |
| QQQ | 732.43 | 733.03 | 1 | NONE | False | 0.77% | quiet |
| SPY | 776.89 | 777.69 | 2 | NONE | False | 1.31% | trend, no whale |

## Per candidate

### TGB — HOLD, power-hour add candidate (strongest direction, highest IV move)
- Direction 3 (wave3+squeeze+trend), **NONE conviction, whale FALSE** → not a clean buy, but the
  strongest directional holding. 17.68% 1d implied move = **the one genuine power-hour volatility
  candidate** on the board.
- Bias: long if it holds above 8.40 and pushes.
- Entry/add: limit **$8.55** — only on a strong intraday push above current 8.40 with volume.
  Size cap **<= $60** (max ~7 shares).
- Scale-out: trim 1/3 at **$9.00**, rest to **$9.50**.
- Stop already in place: GTC **7.50** (keep, never lower into a dip).
- Power hour suits it: YES (high move + top direction). Watch it first; do not chase a spike.

### UUUU — HOLD, small add on strength (whale+squeeze)
- Direction 2, NONE conv, **whale=True + squeeze=True** — moderately constructive, best-confirmed
  name after TGB.
- Entry/add: limit **$14.70** only on a break-and-hold of 14.55; cap **<= $60** (max ~4 shares).
- Scale-out: **$15.50** first, **$16.00** rest.
- Stop in place: GTC **13.00**.
- Power hour: YES/OK (3.81% move, squeeze).

### SOUN — HOLD, trim into strength (weak direction, whale only)
- Direction 1 NONE. whale=True is the only flag — **insufficient**. Prefer reducing exposure.
- Do NOT add. Scale-out/trim: sell **1-2** on any push above **$7.50** (limit). No chase above 7.6.
- Stop in place: GTC **6.40**.
- SOUN bull call spread: **NOT fundable/justified today** — weak direction + NONE conviction. Skip.

### KOS — HOLD, trim/reallocate candidate (weakest direction)
- Direction 1 NONE, drifting down to 2.507. Weak direction = **reallocate candidate** toward
  TGB/UUUU when capital frees. Do not add.
- Trim: sell **1** on any bounce toward **$2.60** (limit). Stop in place: GTC **2.20**.

### Not held — NO new entries
- INR (26.96% move, dir 1), CRGY (2), PTEN (2): none clear the >=3 + whale gate. **Stand aside.**
- QQQ/SPY: quiet, no edge. Not trading indices today.

## PLAN (the day-trader follows)
1. **Hold** TGB 8, UUUU 3. These are the priority names.
2. **TGB add**: limit $8.55, <= $60, only on a strong hold above 8.40 (power-hour volatility play).
3. **UUUU add**: limit $14.70, <= $60, only on break-and-hold of 14.55.
4. **Trim into strength**: SOUN 1-2 @ $7.50+, KOS 1 @ $2.60 — frees capital for TGB/UUUU adds.
   Do NOT trim TGB/UUUU below their scale-outs; those are the strong names.
5. **No new equity entry** without a >=3 + whale confirmation (none today). No SOUN spread.

## DO-NOT list
- **No averaging down** on any position.
- **No market orders** — every order is a GFD limit.
- **No new orders after 3:30 PM ET** (15:30 ET / 14:30 CT).
- **Never RNW**.
- No forced trades; holding cash on weak confirmation is a valid outcome.
