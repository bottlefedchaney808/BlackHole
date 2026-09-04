# Start Here — FinDev in one sitting

> For a new reader who wants the method first, the commands second, and the full
> wiki after that. No trading, no construction pricing, no auto-promotion from a
> chart to a position.

This page answers one question: **How do I get from zero to a first successful
run of FinDev, and what should I be looking at once I get there?**

If you want file trees, every suite’s module map, and the full architecture, see
the wiki. Start Here is the one-page lesson.

---

## 1. What FinDev is for

FinDev is a dealer-style reading of option-market positioning. It does not tell
you what *will* happen. It tells you how the market is already positioned, so
you can ask better questions about where pressure might build and where the next
hedge flow could push.

**Audience:** You can run Python, you know what a call and a put are, and you
want to stop reading consensus dashboards and start reading the book.

The central idea is that there are *two honest estimates* of the same
underlying:

1. **Chain as it sits.** Open interest, dealer-frame sign, per expiry — the
   static snapshot of where exposure already lives.
2. **Flow-built book.** Signed by measured changes — delta of implied volatility
   (ΔIV), not the level — accumulated on a clock.

Where they agree, conviction is cheap. Where they diverge is the trade-relevant
surface. If you fuse them into one number, you lose the only thing that matters.

---

## 2. The three objects you will meet first

| Object | Quantity | Sign | Question answered |
|--------|----------|------|-------------------|
| **Chain-as-it-sits** | Open interest by strike/expiry | Dealer-frame per right: call +, put − | Where does the exposure already live? |
| **Flow-built book** | Signed position accumulated from ΔIV flow | ΔIV tells you whether the flow added long or short delta | What flow actually built this positioning? |
| **Priced book** | Same greeks, same dollar formulas, different position input | N/A — the position is the only allowed degree of freedom | Are the two reads methodologically comparable? |

**What you must not fuse:**

- GEX (imported call+/put− from a scanner) is **not** the book.
- Book gamma is **not** GEX.
- Persist / carry is **not** live residual vanna.

Say the inequality once, then compare what is actually comparable.

---

## 3. The walk — six steps of thinking

FinDev is a sequence of questions, not a list of scripts.

1. **Fetch the chain as it sits.** Strikes, expiries, open interest, implied
   vols. No narrative yet.
2. **Apply the dealer-frame sign.** For every right, ask: if this were dealer
   inventory, is it long delta or short delta? Calls read one way, puts the
   other.
3. **Build the flow-built book.** Look at measured *changes* in implied
   volatility (ΔIV), not the IV level itself. Accumulate with a sign that comes
   from the flow, not your opinion of the tape.
4. **Price both books with the same greeks.** Same chain, same dollar formulas.
   The only degree of freedom must be the position itself.
5. **Read agreement vs divergence.** Where the two books agree, stop arguing.
   Where they diverge, you have found the surface worth reading.
6. **Interpret conditionally.** A divergence is not a trade signal. It is a
   place to ask the next question: what flow would have to arrive to resolve it?

That is the method. The commands below are just the mechanism.

---

## 4. What good output looks like

After a successful run you should be able to open the output directory and see
two things side by side:

- A `vol_result.json` with a dealer-positioning block that says **where** the
  exposure lives by expiry, and **what sign** it carries.
- An `options_result.json` with a priced option — method, IV, price, greeks —
  built from the same context.

A healthy run is loud about failure. Missing data shows up as `status: failed`,
`delta_iv_missing`, or a named gap. A quiet zero is a bug, not a clean run.

A good first-run ticker has liquid options, a near-dated expiry, and enough
open interest that the chain-as-it-sits is not empty. SPY, QQQ, NVDA, AAPL are
reasonable starting points. 0-DTE looks empty by construction — do not start
there.

---

## 5. First run

### 5.1 Setup

From the repo root:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
python setup_db.py
```

Create a root `.env` with your ThetaData credentials:

```text
THETADATA_CF_ACCESS_CLIENT_ID=...
THETADATA_CF_ACCESS_CLIENT_SECRET=...
```

These credentials are required for any run that touches live market data.

### 5.2 Run the pipeline

```bash
orchestrator.bat --unified --ticker NVDA --expiry 2026-10-16
```

Or on Linux / Mac:

```bash
./orchestrator.sh --unified --ticker NVDA --expiry 2026-10-16
```

The orchestrator runs three phases in order:

1. **Vol_Suite** — volatility surface, dealer positioning, gamma exposure.
2. **Market Signals** — option-chain scanners, simulations, Direction suite.
3. **Options + VaR** — priced option greeks and a one-day 99% portfolio risk
   number.

Output lands in `orchestrator_output/<run_id>/`.

### 5.3 Inspect the result

```bash
cd orchestrator_output/<run_id>
cat vol_result.json | python -m json.tool | head -40
cat options_result.json | python -m json.tool
cat var_result.json | python -m json.tool
```

A healthy `options_result.json` looks like this:

```json
{
  "suite": "options",
  "status": "ok",
  "ticker": "NVDA",
  "method": "LeisenReimer",
  "sigma": 0.4878,
  "price": 12.34,
  "greeks": {
    "delta": 0.52,
    "gamma": 0.03,
    "theta": -0.15,
    "vega": 0.21,
    "rho": 0.08
  }
}
```

If any marker file says `"status": "error"`, read the `error` field and the
orchestrator log before re-running.

---

## 6. What to ignore on day one

1. **GEX print is not the book.** A scanner’s call+/put− GEX number is a rough
   reference. It is not the flow-built book and it is not a hedge-flow forecast.
2. **A blank pane is a bug, not “no positioning.”** If a chart is empty, check
   that the ticker has options data, that the expiry is not 0-DTE, and that your
   credentials are valid.
3. **The `auto` expiry is usually 1-DTE and often looks empty.** Pick an
   explicit expiry for your first run.

---

## 7. Where to go next

- **Method:** `docs/guides/FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md`
  — the full pipeline, its inputs and outputs, and how to tell a healthy run
  from a broken one.
- **Architecture:** `docs/guides/FINANCIAL_DEVELOPMENT_INVENTORY.md` — repo
  layout, suites, key modules, and known gotchas.
- **Wiki:** `docs/wiki/` — the full method book. Start with
  `docs/wiki/README.md` and `docs/wiki/01-the-idea.md`.
- **Style:** `docs/guides/FINDEV_DEALER_BOOK_DOC_STYLE.md` — the dealer-book
  voice used across the wiki.
- **Quick reference:** `CLAUDE.md` — the canonical agent and developer
  context.

When you are ready to dig into a suite, run a single suite first:

```bash
orchestrator.bat --suite vol --ticker AAPL --target-years 0.25
```

That is the smallest step from here to fluency.
