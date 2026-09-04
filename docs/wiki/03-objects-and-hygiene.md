# Objects and Hygiene

A measurement is defined by the question it answers, not by the file that computes it. This page names the objects FinDev works with and states the inequalities that keep them distinct.

## Named objects

### GEX (gamma exposure)

- **Quantity**: gamma, summed across strikes
- **Sign**: call+ / put− (imported convention)
- **Question answered**: Where does the imported gamma exposure sit, assuming calls are long and puts are short in the market aggregate?

GEX is a labeled, imported reference. It is useful as a first scan. It is not the dealer book.

### Book gamma

- **Quantity**: gamma, priced with the position sign applied
- **Sign**: depends on the dealer sign convention in use
- **Question answered**: What gamma exposure does the priced chain carry, given an assumed dealer position?

Book gamma uses the same chain and the same greeks as GEX, but the sign comes from the book model, not from a blanket call+/put− rule.

### Flow-built book

- **Quantity**: signed accumulation of positioning
- **Sign**: from measured change in implied vol (ΔIV) and the dealer sign convention
- **Question answered**: What position was actually built over the window, and what hedge pressure remains?

The flow-built book is the second honest estimate. It needs a clock and a sign. The level of IV is not the sign; the change in IV is.

### Persist / carry

- **Quantity**: prior-period position or gamma
- **Sign**: historical, as of the prior close or prior week
- **Question answered**: What did the book look like then?

Persist is a reference point. It is not live residual vanna. It is not the current flow-built book.

### Vanna (live expiry residual)

- **Quantity**: vanna
- **Sign**: per-right call+ / put− in the dealer frame
- **Question answered**: How does the book's delta sensitivity to spot change with implied vol, right now?

Live expiry residual vanna is computed from today's chain and today's position. It is not last week's vendor path. It is not a carry figure.

### ΔIV (delta-IV)

- **Quantity**: change in implied vol
- **Sign**: positive for vol-increasing flow, negative for vol-decreasing flow
- **Question answered**: What was the signed direction of the flow that built this positioning?

ΔIV is the clocked, signed measure of flow. The level of IV is not.

## The hygiene rule

If two objects appear on the same axis, the page must be able to say: "same chain, same greeks, same dollar formulas; only the position differs." If that sentence cannot be written, the objects do not belong on the same axis.

## Common fusions to avoid

| Do not write | Because |
|---|---|
| "GEX is the book" | GEX is an imported aggregate; the book is a priced, signed position. |
| "book gamma is the same as flow-built" | One is a snapshot with sign applied; the other is an accumulation over time. |
| "persist is the live residual" | Persist is historical reference; live residual is computed from today's chain. |
| "vanna from last week's vendor path is today's vanna" | The vendor path is a historical estimate; live expiry residual vanna is from today's chain. |
| "a high IV rank means buy vol" | IV rank is a level. Flow is signed by ΔIV. |

## What this page is not

It is not a list of every column in every CSV. The point is to keep the named objects separate so the reader can tell whether a divergence is method or a measurement mistake.

Next: how these objects move through the engine.
