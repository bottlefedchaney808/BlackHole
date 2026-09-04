# The Idea

## The question

What does the options chain say about where the exposure sits, and what does the signed flow that built it say about which way the hedge is likely to push?

That is the FinDev question. The repo exists to answer it for a single ticker at a time.

## Markets are flows and hedges

A listed option chain is a snapshot. It tells you what is there: strikes, open interest, implied vol, greeks. But it does not tell you who is on the other side, or why the position got there. Dealers, as the marginal counterparty, hedge the net order flow they have taken home. Their hedge is signed — long gamma hedges differently than short gamma — and it has a clock (intraday, around OpEx, into an event).

FinDev treats the market as two honest estimates of the same underlying reality:

| Estimate | What it sees | Question it answers |
|---|---|---|
| **Chain as it sits** | Snapshot of open interest and greeks | Where is the exposure, as it sits right now? |
| **Flow-built book** | Signed accumulation of positioning over a window | What position was actually built, and what hedge pressure remains? |

These two estimates should agree where the data is clean and the sign convention is consistent. Where they diverge, there is something to read.

## The method in five steps

1. **Fetch the chain as it sits.** No owner, no story. Just strikes, OI, IV, greeks.
2. **Apply a sign convention.** Who is the marginal counterparty? The dealer is usually assumed short customer flow, but the exact convention matters (`oi_heuristic`, `replication`, `vol_surface_replication`).
3. **Price like-for-like.** Same chain, same greeks, same dollar formulas. The only degree of freedom allowed to differ is the position itself.
4. **Read agreement vs divergence.** Where the two estimates land on top of each other, conviction is cheap. Where they split, that is the trade-relevant surface.
5. **Clock the read.** Intraday, OpEx, event. Change in positioning beats level. A missing input is a named gap, not a quiet zero.

## What this repo does

`C:\Users\bottl\FinancialDevelopment` is a single-machine, localhost-only quant development monorepo. It turns live option-chain / swap / sentiment data into the two estimates above, prices the focus option, and reports cross-suite results. The entry point is the orchestrator (`orchestrator.bat`), which runs the suites in dependency order and validates the handoff JSON at each stage.

The output is not a trade recommendation. It is a set of named, signed, comparable measurements. Interpreting them is a separate step, and the wiki leaves it to the reader.

## What this page is not

- It is not a macro essay about dealer balance sheets.
- It is not a list of every module in the registry.
- It is not a claim that any single number — GEX, IV rank, max pain — is the book.

The rest of the wiki assumes this frame. Next: the locked decisions that make the two estimates comparable.
