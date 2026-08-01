# Wire Unused Sentiment-Scanner Modules Into the Live Loop — Design

## Context

`sentiment-scanner/main.py` runs a loop over trending tickers and pipes six
options scanners (GEX, Unusual OI, IV Rank, Skew, Max Pain, Vol Dispersion)
plus StockTwits/YouTube narrative scoring into a `CorrelationEngine`. Three
modules in `scanner/` are fully built, tested in isolation, and never
imported by `main.py` or `correlation/engine.py`:

- `scanner/earnings_scanner.py` — earnings-vol IV premium scanner, keyed off
  a hardcoded 7-ticker `EARNINGS_CALENDAR` dict.
- `scanner/sector_rotation.py` — ranks 15 sector ETFs vs. SPY by momentum;
  expensive (16 price-history fetches per run), doesn't fit the per-ticker
  loop.
- `scanner/report.py` — `ScannerReport`, a matplotlib/PdfPages PDF report
  builder that consumes scanner output objects.

This spec wires all three in, plus adds a live earnings-date data source
(the hardcoded calendar goes stale and only covers 7 names).

## Goals

1. Earnings-vol scanner runs per-ticker every scan cycle, alongside the
   existing six options scanners, using a live (not hardcoded) earnings
   calendar.
2. A "Upcoming Earnings (7d)" digest prints once per scan cycle, covering
   the full live calendar (not just tickers currently trending).
3. Sector rotation runs as a separate process, launched on request from
   `main.py` (prompted at startup and at shutdown), not on the per-ticker
   loop's cadence.
4. After each scan cycle, the user can opt in to a PDF report (`ScannerReport`)
   summarizing that cycle's scanner output and composite signals.
5. None of this blocks or breaks unattended/non-interactive runs (e.g. cron):
   all new prompts are skipped automatically when stdin isn't a TTY, and can
   be skipped explicitly via CLI flags.

## Non-Goals

- No new options-flow logic. `earnings_scanner.py`'s IV-premium math is
  unchanged.
- No change to `sector_rotation.py`'s ranking algorithm.
- No change to `report.py`'s page layouts — we only wire callers.
- No persistence of report/launcher preferences across runs.

## Design

### 1. Live earnings calendar (`scanner/earnings_calendar.py`, new)

`stockanalysis.com` is the project's only sanctioned non-ThetaData data
source (see `requirements.txt` header comment). It publishes an earnings
calendar at `https://stockanalysis.com/earnings/` as a server-rendered
Next.js page with the calendar data embedded in a
`<script id="__NEXT_DATA__" type="application/json">` tag — no auth, no
JS execution required to read it.

`fetch_earnings_calendar()`:
- Fetches the page via `urllib.request` (matches `youtube.py`'s existing
  no-extra-dependency pattern).
- Extracts the `__NEXT_DATA__` JSON blob with a regex.
- Recursively walks the parsed JSON looking for list-of-dict shapes where
  each dict has a ticker-like key (`symbol`, `s`, `ticker`) and a
  date-like key (`date`, `reportDate`, `d`) — resilient to not knowing the
  exact schema in advance.
- Returns `Dict[str, str]` (`TICKER -> "YYYY-MM-DD"`).
- Caches in-memory with a 60-minute TTL (same pattern as
  `youtube.py`'s `_cache`).
- On any failure (network, parse, empty result), returns `{}` and the
  caller falls back to the static `EARNINGS_CALENDAR` dict — the feature
  degrades to today's behavior rather than breaking.

**Caveat flagged for the implementer:** the exact `__NEXT_DATA__` schema
can't be confirmed from this design session (the page didn't render via
the available fetch tooling). The extractor is written defensively (key
candidates, not a hardcoded path) and covered by a unit test against a
synthetic fixture; the plan includes an explicit manual-verification step
against the live site before this is considered done.

`upcoming_earnings(days=7)`:
- Calls `fetch_earnings_calendar()`, falls back to merging in
  `EARNINGS_CALENDAR` entries for tickers the live fetch didn't cover.
- Filters to entries within `[today, today + days]`.
- Returns `List[Tuple[str, str]]` sorted by date (`ticker`, `date`).

`format_earnings_digest(entries)`:
- One-line-per-entry string, or a placeholder line if empty. Mirrors
  `sector_rotation.format_rotation`'s "no data" convention.

### 2. `EarningsScanner.get_earnings_date()` goes live-first

`scanner/earnings_scanner.py`: `get_earnings_date()` calls
`earnings_calendar.fetch_earnings_calendar()` first; if the ticker isn't
present there, falls back to the static `EARNINGS_CALENDAR` dict (kept as
a last-resort fixture, not removed). Existing tests that patch
`EARNINGS_CALENDAR` directly need the live-fetch path mocked out (returns
`{}`) so they still exercise the static-fallback path deterministically.

### 3. Correlation engine gains earnings

`correlation/engine.py`:
- `_earnings: Dict[str, object] = {}` alongside the other five `_x` dicts.
- `record_earnings(ticker, scan)` — same shape as `record_gex` etc.
- `_earnings_module()` lazy import of `EarningsResult`, matching the
  existing `_gex_module()` etc. pattern.
- Two new signal rules in `correlate_with_oi`, inserted after the existing
  Vol Dispersion block:
  - `premium_pct >= HIGH_PREMIUM_THRESHOLD and cns > 50` →
    `EARNINGS_VOL_PLUS_NARRATIVE`, severity `HIGH`.
  - `premium_pct >= HIGH_PREMIUM_THRESHOLD` (narrative not elevated) →
    `EARNINGS_VOL_PREMIUM`, severity `MEDIUM`.
- `earnings` block added to `get_scanner_summary()`'s returned dict,
  matching the `status`/metric shape of the other five blocks.

### 4. Per-ticker wiring + digest in `main.py`

- `run_options_scanners()` gains a 7th scanner call (earnings), same
  try/except-and-append-a-line pattern as the existing six. Uses the
  already-shared `get_td()` client via `EarningsScanner(get_td())`.
- `scan_trending()` prints the earnings digest once per cycle, right after
  the `"Trending: N symbols"` header line — before the per-ticker loop —
  since the digest reflects the *calendar*, not that cycle's scan results.

### 5. Sector rotation launcher (new file + prompts)

`sentiment-scanner/sector_rotation_launcher.py` — standalone CLI:
- `main(argv)` parses `--loop` (repeat every `SCAN_INTERVAL_MINUTES`,
  default off — single run) and runs `rank_sectors()` +
  `format_rotation()`, printing to stdout.
- Importable and independently testable (`main([])` with `rank_sectors`
  monkeypatched) as well as runnable via
  `python sector_rotation_launcher.py`.

`main.py` changes:
- `_prompt_yes_no(question)` helper — returns `False` immediately if
  `sys.stdin.isatty()` is `False` (no hang in cron/CI), otherwise prompts
  and parses y/n.
- At startup (after the banner, before the scan loop) and at shutdown (in
  the `KeyboardInterrupt`/normal-exit path before `finally`), call
  `_prompt_yes_no("Launch Sector Rotation scanner?")`; if yes,
  `subprocess.run([sys.executable, "sector_rotation_launcher.py"])`
  (foreground, blocking — same style as the existing `_launch_vol_suite`).
- New `--skip-sector-prompt` CLI flag bypasses both prompts unconditionally.

### 6. On-demand PDF report

- `run_options_scanners()`'s return type changes from `List[str]` to
  `Tuple[List[str], Dict[str, object]]` — `(lines, raw_results)` where
  `raw_results` keys are `"gex"`, `"unusual_oi"`, `"iv_rank"`, `"skew"`,
  `"max_pain"`, `"dispersion"`, `"earnings"` mapping to the scan result
  objects (or `None` on error) — the same objects already passed to
  `engine.record_*`. `scan_trending()` is updated for the new return
  shape (its one caller).
- New `main.py` helper `_maybe_build_report(engine, cycle_results, tickers)`:
  prompts (via `_prompt_yes_no`, same TTY guard) "Generate PDF report for
  this run?"; if yes, builds a `ScannerReport`, calls
  `add_ticker_results(ticker, name, data)` for each of the 7 raw-result
  dicts per ticker (skipping `None`s), `add_signals(ticker, signals,
  severity)` from `engine.correlate_with_oi(ticker, {})`, then
  `.save(out_dir=config.OUTPUT_DIR)`.
- Called once per completed `scan_trending()` pass in `main()`, both the
  initial run and each loop iteration.
- New `--skip-report-prompt` CLI flag.

## Testing Strategy

- `scanner/earnings_calendar.py`: unit tests against a synthetic
  `__NEXT_DATA__`-shaped fixture (extractor logic), TTL-cache behavior,
  and fallback-on-failure behavior — all with `urllib.request.urlopen`
  mocked, no live network in the test suite (matches `youtube.py`
  convention of no network calls in tests).
- `earnings_scanner.get_earnings_date()`: existing tests updated to mock
  the live-fetch call to `{}` so the static-fallback path is exercised
  deterministically; one new test confirms the live path wins when it has
  data.
- `correlation/engine.py`: new `tests/test_correlation_engine.py` covering
  `record_earnings`, the two new signal rules (each threshold branch), and
  the `earnings` block in `get_scanner_summary`.
- `sector_rotation_launcher.py`: `main([])` and `main(["--loop"])` tested
  with `rank_sectors`/`time.sleep` monkeypatched — no real loop in tests.
- `main.py`: extract the new pieces (`_prompt_yes_no`,
  `_maybe_build_report`, the digest print) into functions that take
  explicit arguments (engine, stdin-check, io) so they're unit-testable
  without running the full scan loop.

## Open Risk

The `stockanalysis.com` scrape target's exact JSON schema is unverified —
flagged explicitly in the plan as a manual-verification step before the
earnings-calendar task is marked done.
