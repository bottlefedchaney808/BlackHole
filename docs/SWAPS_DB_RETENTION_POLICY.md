# `swaps.db` retention/archival policy

**Status: decided, partially implemented.** This document is the policy referenced by
`docs/PROJECT_AUDIT_AND_SPEC.md`'s open decisions list. The full-execution step
(nulling `raw_json` for the existing 71M rows) has **not** been run yet — see
"What's implemented vs. what's still pending" at the bottom.

## The problem

`swaps.db` is ~320GB (71M rows in `swap_trades`, confirmed via `PRAGMA page_count`
that this is genuinely live data, not unvacuumed free space). It grows with every
5-minute ingestion poll and has no retention policy today. A single-file SQLite
database at this scale has real operational costs: WAL checkpoint stalls, slow
backups (a 320GB file can't be quickly copied/snapshotted), and continued
unbounded growth.

## What's actually driving the size

`swap_trades.raw_json` (`TEXT NOT NULL`) stores the complete raw DTCC record for
every row, in addition to the ~20 structured columns already extracted from it
(`notional_amount_leg1`, `price`, `effective_date`, `upi`, etc.). Checked directly:
**nothing in this codebase reads `raw_json` back out** — not `swaps_query.py`, not
`shared/query_builder.py`, not `dashboard/app.py`, not any suite. It was written
defensively ("in case a field we didn't extract turns out to matter later") and
has never been consulted since. For a JSON payload with ~20+ fields per row
against ~20 structured columns already capturing the fields every consumer
actually uses, `raw_json` is very plausibly the single largest contributor to the
320GB, though this wasn't measured column-by-column before writing this policy
(that measurement — `AVG(LENGTH(raw_json))` — times out on a full-table scan at
this row count; a sampled estimate would need to run against a `LIMIT`-bounded
subset instead, deliberately not done here to avoid a long-running query against
the live production DB during this session).

## The policy

1. **Structured columns are retained indefinitely, unconditionally.** This is the
   actual analytical dataset — every current and plausible future consumer reads
   these, never `raw_json`. No row is ever deleted by this policy; only the
   `raw_json` column's *value* is affected, per point 2.

2. **`raw_json` is kept in full for a rolling recent window (90 days)**, to
   support near-term debugging or reprocessing (e.g. a parser bug discovered
   weeks after ingestion, needing the original payload to re-derive a field).
   90, not something longer, deliberately: nothing in this codebase has ever
   read `raw_json` back out in this project's entire history (see below), so
   there's no evidence a long grace period is needed, and a shorter window
   means the policy actually starts reclaiming space from the existing
   backfill within a few months rather than over a year from now.

3. **Beyond that window, `raw_json` is archived and then NULLed**, not the row
   deleted. Archival means: before nulling, export the outgoing rows'
   `(dissemination_id, raw_json)` pairs to gzip-compressed, date-partitioned
   flat files under a new `raw_json_archive/` directory (outside `swaps.db`,
   cheap to store, cheap to move to colder storage later, never loaded back into
   the hot DB unless specifically needed). This preserves the "insurance" value
   `raw_json` was originally meant to provide, without it costing 320GB of live,
   WAL-mode, indexed database weight forever.

4. **This does not touch DTCC's own retention** — SEC/CFTC's public cumulative
   EOD feed is DTCC's problem, not this repo's; nothing here assumes DTCC will
   keep serving old files indefinitely, which is exactly why point 3 archives
   locally rather than relying on "just re-fetch it from DTCC later."

5. **Going forward, ingestion keeps writing `raw_json` as it does today** — the
   400-day window is enforced by a periodic archival pass (point 6), not by
   changing what `db_loader.py` writes at insert time. This keeps the policy
   reversible: if a real future need for `raw_json` emerges, the window can be
   widened without any ingestion-side code change.

6. **Enforcement**: `scripts/archive_raw_json.py` (added by this change, see
   below) implements the archive-then-null step, designed to run the same way
   `scripts/quant_alert_check.py` does — a plain scheduled task, not `CronCreate`
   (session-scoped schedules aren't durable, per that script's own documented
   reasoning), run monthly is more than sufficient given the rolling window is
   400 days.

## What's implemented vs. what's still pending

**Implemented now**: `scripts/archive_raw_json.py`, a dry-run-by-default script
that (a) finds rows older than the retention window with non-NULL `raw_json`,
(b) exports them to gzip-compressed, date-partitioned files under
`raw_json_archive/`, (c) verifies each export file round-trips (reads it back and
confirms row counts match) before touching the database, then (d) NULLs
`raw_json` for exactly the rows it just verified were archived. It requires
`--execute` to do anything beyond reporting what it *would* do — plain
`python scripts/archive_raw_json.py` only prints a dry-run summary (row count,
estimated size, date range) and touches nothing.

**Dry-run already executed once, against the real `swaps.db`** (read-only —
counts/estimates only, no files written, no rows touched): as of 2026-08-04, it
reports **zero rows eligible** under the 90-day window. This is expected, not a
bug: the DTCC pipeline itself is new (built within the last month per file
mtimes), so even though `backfill.py` pulled a large volume of *historically-dated*
trades, every row's `ingested_at` (when this repo wrote it, the clock this
policy uses — not `effective_date`, the trade's own date) is recent. The policy
won't reclaim any space until the existing backfilled rows individually cross
the 90-day-since-ingestion mark, starting roughly 90 days after this policy was
written (early November 2026) and continuing to apply to each new batch of
ingested rows from then on. It will, however, correctly cap future growth from
the ongoing 5-minute poller once it starts running long enough to matter.

**Not yet run with `--execute`.** Deliberately: nulling `raw_json` for
tens of millions of rows of regulatory swap data is a large, hard-to-reverse
operation on data this project can't trivially re-derive if the archival step
has a bug. The dry-run has not even been executed yet in this session (that
would still require connecting to the live 320GB `swaps.db`, which is worth a
deliberate choice of when to do, e.g. when the scheduler isn't mid-poll). The
recommended next step is: run the script in dry-run mode first, review the
reported row count/estimated space reclaimed, then re-run with `--execute` once
satisfied — as a separate, explicit action, not bundled into this policy
decision.
