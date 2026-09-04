# FinDev wiki style template
## Distilled from the Dealer Book method walk (consult, not a build)

Source of style: `dashboard/templates/dealer_book.html` “About this tab” (01 The Idea → 02 The Engine → 05 Standing Conventions), plus the A-vs-B framing on the tab itself.
This is a writing template. It is not a strategy, not a model change, not an order. Do not auto-promote anything in here into live code.

The aha the dealer book already has, and most code wikis do not:

Every surface answers a hidden question — *whose object is this?* The page names two honest estimates, says what each is allowed to answer, then shows the engine. Knobs come last. A file tree is not a method.

If a page starts with “clone the repo / here is `app.py`,” it has already lost.

---

## Voice / rules

Write like the tab copy, not like an OpenAPI dump.

1. Open with the question, not the module.
   First sentence names the object and the question it answers. Function names wait until the reader already knows *why* the object exists.

2. Introduce terms by the question they answer, then the quantity, then the sign.
   Dealer Book pattern (copy this triad every time you name a thing):
   - quantity
   - sign
   - question answered
   Example from the tab: Side A quantity = today’s OI; sign = dealer-frame per right; question = where is the exposure, as it sits?

3. Mechanics before knobs.
   Walk: idea → objects you must not fuse → how the thing is built → how to read agreement/divergence → then commands, flags, file paths, config.
   Dead-band, window length, expiry picker, registry names are knobs. They do not teach.

4. Two honest estimates beat one confident number.
   When the codebase has two reads of the same greek (chain-as-it-sits vs flow-built; GEX-from-OI vs book-gamma; persist vs a vendor path), put them side by side. Name the single degree of freedom that is allowed to differ. If two charts disagree, the reader must know whether that is method or scaling.

5. Like-for-like by construction, or do not compare.
   Say explicitly: same chain, same greeks, same dollar formulas; only the position differs. If you cannot say that, do not put the objects on one axis.

6. Worked intuition is one paragraph, not a tutorial farm.
   “Where they agree, conviction is cheap. Where they diverge — that’s the trade-relevant surface.” Then stop. Do not narrate every strike.

7. Standing conventions are convictions with consequences, not vibes.
   Table: Conviction | Consequence in the code (or in the read).
   Example: “Units are contracts, not dollars” → B’s `total_net` is not comparable to A until re-priced.

8. Failure must be loud.
   A missing input is a named gap (`delta_iv_missing`, `status=failed`), never a quiet zero or a fake `ok`. Docs that say “if this is empty, the run succeeded” are lying.

9. What to ignore is a first-class section, not a footnote.
   GEX is a labeled imported reference, not the book. A VIX print is not a regime flip. A path that went −500k → +92k does not mean the back-month book disarmed. Buy-and-hold is not a default. 0-DTE magnifies the day; it does not flip the index book. Say the traps in the same tone as the method.

10. Clock the read.
    Intraday / OpEx / event. Daily flow is signed by ΔIV, not the level. Change in positioning > level. If the tape is missing, say the gap; do not invent a signed hedge.

11. No dump, no sermon, no auto-promote.
    Descriptive and conditional. “This is how the pipeline thinks.” Not “therefore sell index vol.” Commands are copy-pasteable only after the mental model.

12. Do not fuse measurement objects.
    GEX (imported call+/put−) ≠ book gamma ≠ persist/carry. Live expiry residual vanna ≠ last week’s vendor greek path. Spell the inequality once per page that uses more than one of them.

Tone: terse. Homework first. Contempt for consensus metrics that collapse two objects into one number. Never invent mechanics to sound like a desk.

---

## (1) Section order

### Start Here (one page — zero to first mental model and first run)

Keep it one page. The coder’s job on `t_6b0e0a77` is this spine, not a wiki.

| # | Section | What it does | What it is not |
|---|---------|--------------|----------------|
| 0 | Title + audience | Who this is for. One line: FinDev is a method for reading dealer-style positioning and running that method in this repo. | Feature list. |
| 1 | The question | *What are we trying to see?* One short frame. Markets here are flows and hedges; the dealer is the marginal counterparty. Ask: where is the hedge, and which way does it push? | Macro essay. |
| 2 | The objects | Name the 2–3 objects the reader will meet on a first run (e.g. chain-as-it-sits vs flow-built book; or raw fetch vs priced book). Each gets the triad: quantity / sign / question answered. One line on what they must not fuse. | Module registry dump. |
| 3 | The walk | Sequence of *thinking*, numbered, 4–6 steps. Fetch as-it-sits → apply book-sign / flow accumulation → price like-for-like → read agreement vs divergence → only then interpret. | CLI flags. |
| 4 | What good output looks like | One concrete picture: two panels, same axes, difference is method not scale; loud failure if fetch died; named gap if ΔIV missing. | Screenshot gallery with no legend. |
| 5 | First run | Setup + copy-pasteable commands. After the walk, not before. Point at one ticker/expiry that actually fills a chart. | Every suite. |
| 6 | What to ignore on day one | Three traps max. Example: GEX print ≠ book; blank pane is a bug not “no positioning”; `auto` expiry is 1-DTE and looks empty. | Full glossary. |
| 7 | Next | One link into the wiki (method page, then architecture). | “See also” laundry list. |

Do not put “The Build” (commit archaeology) on Start Here. That is wiki appendix material.

### Full wiki (method book, then the repo)

Coder on `t_c6326d36`. Same voice. Depth, not a second Start Here.

| # | Section | What it does |
|---|---------|--------------|
| 01 | The Idea | Same opening frame as Start Here, expanded. Why two estimates. Why-then-how. |
| 02 | Locked decisions / standing conventions | Conviction → consequence table. Do not relitigate these in later pages; link here. |
| 03 | Objects and hygiene | Glossary-quality pages for each named object. Explicit “is not” lines (GEX is not the book; persist is not live residual vanna). |
| 04 | The Engine | Stage table copied from the dealer-book pattern: Stage / Mechanism / Detail that matters. Real inputs and outputs. Topology of `run_selected_modules` belongs here as *why the order exists*, not as a graphviz flex. |
| 05 | How to read | Agreement vs divergence. Clock (intraday / OpEx / event). What a gap looks like. Worked intuition on one dated run, labeled descriptive. |
| 06 | Architecture and module map | File paths *after* 01–05. Group by the question they serve, not by folder name. |
| 07 | Workflows | Pipeline pages: morning scan, dealer-book load, unified run, archive. Each: trigger → inputs → stages → artifacts → what “good” is. |
| 08 | Knobs and config | Window, dead-band, expiry, registry checkboxes. Each knob: what mechanic it gates. Never lead a page with YAML. |
| 09 | Failure and what to ignore | Loud status, vendor gaps / parity fill, stale-server, fused metrics. |
| 10 | Extending / debugging | How to add a module without teaching a new method. Tests that fail when a value is missing, default, or secretly equal to GEX. |
| 11 | Glossary | Term = question answered + quantity + sign. No circular “vanna is vanna.” |
| App | The Build | Dated commits, gates, “every chart is a real artifact” — history, not the lesson. |

TOC at the top of the wiki index should be this order, not alphabetical files.

---

## (3) Sample paragraph (mimic this voice)

Use this as the house paragraph for any “generic analysis pipeline” page. Do not paste dealer knobs into it; the cadence is the deliverable.

A pipeline is not a list of scripts. It is a sequence of questions each stage is allowed to answer, and only those. Start with the object as it sits — no owner, no story. Then ask what flow actually built, on a clock, with a sign that comes from a measured *change* (not a level). Price both answers with the same greeks so the only degree of freedom is the position itself. Where they agree, stop arguing. Where they diverge, that is the surface worth reading. Knobs come last: window, dead-band, which expiry. If you lead with the knobs you taught a UI, not a method. If an input is missing, say the gap out loud; a quiet zero is a lie.

---

## Writer checklist (fail the page if any of these are true)

- First heading is a directory or a function name.
- A term is used before the reader knows the question it answers.
- Two objects are compared without a like-for-like sentence.
- Config/flags appear before the walk.
- GEX / book-gamma / persist are treated as synonyms.
- Failure is described as empty-but-ok.
- The page auto-promotes a trade from a chart.
- Start Here is longer than one sitting (if they need the wiki, you failed Start Here).

---

## What this consult is not

Not an implementation of START_HERE or the wiki (`t_6b0e0a77`, `t_c6326d36`).
Not a change to dealer math, signs, or live models.
Not a blessing of GEX-from-OI as the book.
Charm remains a real, minor, calendar term in the verified frame; do not write wiki copy that says charm is 100% of dealer hedge flow.
