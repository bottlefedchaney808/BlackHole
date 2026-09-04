# How to Read

Reading FinDev output means comparing two honest estimates on the same axes and asking whether their divergence is method or signal.

## Agreement vs divergence

A healthy run returns two panels that line up. The chain-as-it-sits and the flow-built book use the same chain, the same greeks, and the same dollar formulas. The only thing that differs is the position.

- **Where they agree**: the picture is stable. Conviction is cheap.
- **Where they diverge**: that is the trade-relevant surface. But divergence alone is not a signal. The reader has to decide whether the gap is a real positioning change, a data gap, or a sign-convention mismatch.

## Clock the read

The same number means different things at different times.

| Clock | What to watch |
|---|---|
| **Intraday** | ΔIV flow, not IV level. A spike in IV is noise unless the flow that built it is signed. |
| **OpEx** | Pin risk and gamma magnetism rise. 0-DTE magnifies the day; it does not flip the index book. |
| **Event** | Earnings, macro prints, central-bank days. The book may re-price faster than the position changes. |

Rule: change in positioning beats level.

## What a gap looks like

A real gap shows up as a signed divergence between the two estimates. A measurement mistake shows up as:

- a quiet zero where data should be,
- a `status: failed` marker that was not explained,
- the same label on two different objects (GEX vs book gamma vs persist),
- a chart whose axes are not like-for-like.

If the gap is named — `delta_iv_missing`, `status: failed`, `correlation_matrix_missing` — the data is telling you what is wrong. If the gap is silent, the run is suspect.

## Worked intuition, one paragraph

Where the chain-as-it-sits and the flow-built book land on top of each other, the market has already priced the exposure. Where they split, the exposure is either changing faster than the snapshot captures, or the snapshot is missing a signed flow. The reader's job is to decide which one it is. A VIX print is not a regime flip; a path that went −500k → +92k does not mean the back-month book disarmed. Buy-and-hold is not a default.

## What this page is not

It is not a trading manual. It does not tell you to sell vol or buy gamma. It describes how to compare the two estimates the repo produces.

Next: where those estimates are built in the codebase.
