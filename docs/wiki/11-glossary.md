# Glossary

Terms are defined by the question they answer, the quantity they measure, and the sign they carry.

| Term | Question answered | Quantity | Sign |
|---|---|---|---|
| **GEX** | Where does the imported gamma exposure sit? | gamma, summed across strikes | call+ / put− (imported) |
| **book gamma** | What gamma exposure does the priced chain carry? | gamma with position sign applied | depends on dealer convention |
| **flow-built book** | What position was actually built, and what hedge pressure remains? | signed accumulation of positioning | from ΔIV and dealer convention |
| **ΔIV (delta-IV)** | What was the signed direction of the flow? | change in implied vol | positive for vol-increasing flow |
| **vanna (live expiry residual)** | How does the book's delta sensitivity to spot change with implied vol right now? | vanna | per-right call+ / put− in dealer frame |
| **persist / carry** | What did the book look like then? | prior-period position or gamma | historical, as of prior close/week |
| **dealer frame** | Who is the marginal counterparty? | dealer sign convention | short customer flow by default |
| **chain as it sits** | Where is the exposure right now? | snapshot of OI, greeks, IV | none until sign is applied |
| **Leisen-Reimer** | What is the default American option price/Greeks? | binomial tree result | n/a — a method, not a signed quantity |
| **SPXW** | What is the correct root for the SPX options chain? | ticker symbol | n/a — `SPXW` for chain, `SPX` for index price |
| **vol surface** | What does the IV smile look like across strikes/maturities? | implied vol by strike/expiry | n/a — a surface, not a position |
| **variance-swap fair strike** | What is the fair strike of a variance swap? | variance-swap strike | n/a — a price, not a position |
| **GARCH conditional vol** | What is the current conditional volatility estimate? | annualized vol | n/a — an estimate |
| **corr_sim** | What is the correlated GBM Monte Carlo VaR? | VaR/CVaR | loss is positive by convention |
| **suite_context.json** | What is the canonical shared context for a run? | JSON object with focus, basket, settings | n/a — data contract |
| **ModuleResult** | What did a module produce? | status, artifacts, metrics, context patch | n/a — execution envelope |

## What this page is not

It is not a circular list of synonyms. If two terms ever appear to mean the same thing, the reader should return to [Objects and Hygiene](./03-objects-and-hygiene.md) and check which inequality has been violated.

Next: the build history and artifact discipline.
