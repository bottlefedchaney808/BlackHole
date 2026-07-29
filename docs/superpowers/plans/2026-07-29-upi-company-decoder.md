# UPI Company Decoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decode `swap_trades.upi` (the DTCC/ANNA-DSB Unique Product Identifier on every single-stock total-return swap row) into a real company name, cache it in the database, and surface it as a "Company" column next to UPI on the `/swaps` dashboard page.

**Architecture:** A two-tier, cache-first decoder. Tier 1 is free and local: DTCC's own "UPI Underlier Name" field (`upi_underlier_name`) already contains the company name as its first backslash-delimited segment for most rows (e.g. `"LIVZON GROUP\REGISTERED\SHARES A\000513"` -> `"LIVZON GROUP"`); we just have to strip it out and recognize the placeholder values ("COM STK", "TWSE LISTED STOCKS", "No name obtainable", blank) that carry no name. Tier 2 is a network fallback for rows where tier 1 fails: the underlier's Reuters Instrument Code (`underlier_id_leg1`, e.g. `"002028.ZK"`) is split into ticker + exchange suffix and resolved to a company name via OpenFIGI's free public mapping API. Results are cached forever in a new `upi_reference` table keyed by UPI (a UPI's company never changes), and a rowid watermark (`upi_decode_state`) means every run only looks at trades ingested since the last run -- it never rescans the full `swap_trades` table, which matters because that table is already ~386GB. The dashboard's `/swaps` query gets a `LEFT JOIN` onto `upi_reference` to add the column; the decoder itself is wired into the existing ingestion jobs (`scheduled_ingest.py`, `backfill.py`) so new UPIs get decoded automatically as they're scraped, with no schedule of its own to maintain.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), `requests` (already a pinned dependency), `pytest` for tests. No new third-party dependencies.

## Global Constraints

- No new pip dependencies -- `requests` and `pytest` are already pinned in `requirements.txt`.
- Every query against `swap_trades` must either use the existing `idx_swap_trades_upi` / `idx_swap_trades_ingested_at` indexes or be bounded by `rowid` range + `LIMIT` -- this table is ~386GB and a bare `COUNT(*)`/full scan takes minutes.
- Decoding must be resumable and idempotent: re-running `decode_upis.py` with no new trades must be a no-op, and it must never re-decode a UPI already present in `upi_reference`.
- Network failures (OpenFIGI unreachable, rate-limited, unmapped exchange) must degrade to `company_name = NULL`, never raise and abort the batch.
- Follow existing repo conventions: `sqlite3.Row` row factory, `CREATE TABLE IF NOT EXISTS` migrations in `setup_db.py`, upsert-via-`ON CONFLICT` pattern (see `db_loader.py`), module-level `DB_PATH = os.path.join(os.path.dirname(__file__), 'swaps.db')`.

---

## File Structure

- Modify: `setup_db.py` -- add `upi_reference` and `upi_decode_state` table definitions to `init_database()`.
- Create: `upi_decoder.py` -- pure decode logic: tier-1 local string parsing, RIC suffix table, `OpenFigiClient` (tier-2 HTTP), `decode_one()` combining both.
- Create: `decode_upis.py` -- the incremental runner: owns the DB cursor, walks `swap_trades` by rowid watermark, calls into `upi_decoder`, upserts `upi_reference`, advances the watermark. Has a CLI entrypoint.
- Modify: `scheduled_ingest.py` -- call `decode_upis.run()` after a poll pass and after a backfill pass, non-fatally.
- Modify: `dashboard/app.py` -- `/swaps` route: `LEFT JOIN upi_reference`, add `company_name` to the returned columns.
- Modify: `dashboard/templates/swaps.html` -- render the `company_name` column next to `upi`.
- Create: `test_upi_decoder.py` -- unit tests for tier-1 parsing, RIC splitting, `OpenFigiClient` (HTTP mocked), `decode_one()`.
- Create: `test_decode_upis.py` -- integration tests for the incremental runner against a temp SQLite file.

---

### Task 1: Add `upi_reference` and `upi_decode_state` tables

**Files:**
- Modify: `setup_db.py:127` (immediately before `conn.commit()` at the end of `init_database()`)
- Test: manual verification (schema-only change, no behavior to unit test beyond "table exists")

**Interfaces:**
- Produces: `upi_reference(upi TEXT PRIMARY KEY, company_name TEXT, decode_tier TEXT, decode_detail TEXT, raw_underlier_name TEXT, decoded_at TIMESTAMP)`
- Produces: `upi_decode_state(id INTEGER PRIMARY KEY CHECK (id = 1), last_rowid INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMP)` -- single-row table, `id` is always `1`.

- [ ] **Step 1: Add the table definitions**

Open `setup_db.py` and insert this block right before the final `conn.commit()` (i.e. after the `orchestrator_runs` index block, around line 126):

```python
    logger.info("Creating upi_reference table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upi_reference (
            upi TEXT PRIMARY KEY,
            company_name TEXT,
            decode_tier TEXT,
            decode_detail TEXT,
            raw_underlier_name TEXT,
            decoded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    logger.info("Creating upi_decode_state table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upi_decode_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_rowid INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
```

- [ ] **Step 2: Run it against a throwaway database to verify**

Run: `cd /path/to/FinancialDevelopment && python3 -c "import setup_db, tempfile, os, sqlite3; p = tempfile.mktemp(suffix='.db'); setup_db.init_database(p); conn = sqlite3.connect(p); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]); os.remove(p)"`

Expected: prints a list of table names including `upi_reference` and `upi_decode_state` (alongside `swap_trades`, `ingestion_state`, `scrape_log`, `orchestrator_runs`).

- [ ] **Step 3: Run it against the real, live `swaps.db`**

Run: `cd /path/to/FinancialDevelopment && python3 setup_db.py`

Expected output ends with: `Database ready: /path/to/FinancialDevelopment/swaps.db` -- `CREATE TABLE IF NOT EXISTS` is a no-op on the existing 26 columns of `swap_trades` and only adds the two new tables, so this is safe to run against the live file with data already in it.

- [ ] **Step 4: Commit**

```bash
git add setup_db.py
git commit -m "feat: add upi_reference and upi_decode_state tables"
```

---

### Task 2: Tier-1 local decoder (parse `upi_underlier_name`)

**Files:**
- Create: `upi_decoder.py`
- Test: `test_upi_decoder.py`

**Interfaces:**
- Produces: `decode_local(upi_underlier_name: Optional[str]) -> Optional[str]`
- Produces: `_NON_NAMES: set[str]` (module-level constant, the known ANNA-DSB placeholder strings)

- [ ] **Step 1: Write the failing tests**

Create `test_upi_decoder.py`:

```python
"""Tests for upi_decoder.py."""
import pytest

from upi_decoder import decode_local, split_ric


class TestDecodeLocal:
    def test_extracts_first_segment_before_backslash(self):
        assert decode_local("LIVZON GROUP\\REGISTERED\\SHARES A\\000513") == "LIVZON GROUP"

    def test_extracts_name_with_punctuation(self):
        assert decode_local("CAPITAL SECURITIES CO.,LTD\\BEARER\\SHARES A\\601136") == "CAPITAL SECURITIES CO.,LTD"

    def test_single_segment_with_no_backslash_is_returned_as_is(self):
        assert decode_local("ACME CORP") == "ACME CORP"

    @pytest.mark.parametrize("placeholder", [
        "COM STK", "TWSE LISTED STOCKS", "No name obtainable",
        "N/A", "NA", "None", "com stk",
    ])
    def test_known_placeholders_return_none(self, placeholder):
        assert decode_local(placeholder) is None

    def test_blank_returns_none(self):
        assert decode_local("") is None

    def test_none_returns_none(self):
        assert decode_local(None) is None


class TestSplitRic:
    def test_splits_ticker_and_suffix(self):
        assert split_ric("002028.ZK") == ("002028", "ZK")

    def test_splits_ticker_with_dot_suffix_lowercased_input(self):
        assert split_ric("6902.t") == ("6902", "T")

    def test_no_dot_returns_none(self):
        assert split_ric("ACMECORP") is None

    def test_blank_returns_none(self):
        assert split_ric("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/FinancialDevelopment && pytest test_upi_decoder.py -v`
Expected: `ModuleNotFoundError: No module named 'upi_decoder'` (or collection error) -- the module doesn't exist yet.

- [ ] **Step 3: Write `upi_decoder.py` (tier-1 portion only for this task)**

Create `upi_decoder.py`:

```python
"""upi_decoder.py -- best-effort company-name decoder for DTCC swap UPI codes.

The DTCC/CFTC public dissemination feed carries an "UPI Underlier Name" field
(upi_underlier_name in swap_trades) sourced from ANNA DSB's UPI reference data
at the time DTCC reported the trade. For most rows that field already *is* a
usable company name -- the first backslash-delimited segment, e.g.
"LIVZON GROUP\\REGISTERED\\SHARES A\\000513" -> "LIVZON GROUP". For the rest
("COM STK", "TWSE LISTED STOCKS", "No name obtainable", or blank) we fall
back to looking up the underlier ID (underlier_id_leg1 /
underlier_id_source_leg1, e.g. "002028.ZK" / "RIC") against the free OpenFIGI
mapping API.

This module has no database dependency -- decode_upis.py owns the DB cursor
and calls decode_one() here per UPI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Placeholder values DTCC/ANNA DSB use when no real underlier name was
# available at reporting time. These never count as a usable company name.
_NON_NAMES = {
    "", "COM STK", "TWSE LISTED STOCKS", "NO NAME OBTAINABLE",
    "N/A", "NA", "NONE",
}


def decode_local(upi_underlier_name: Optional[str]) -> Optional[str]:
    """Tier 1: pull a company name out of the DTCC-supplied underlier name.

    Returns the cleaned name, or None if the field is blank or one of the
    known ANNA DSB placeholder strings that carry no company information.
    """
    if not upi_underlier_name:
        return None
    first_segment = upi_underlier_name.split("\\", 1)[0].strip()
    if first_segment.upper() in _NON_NAMES:
        return None
    return first_segment or None


def split_ric(underlier_id: Optional[str]) -> Optional[tuple[str, str]]:
    """Split a RIC like '002028.ZK' into ('002028', 'ZK'). None if unparsable."""
    if not underlier_id or "." not in underlier_id:
        return None
    ticker, suffix = underlier_id.rsplit(".", 1)
    ticker, suffix = ticker.strip(), suffix.strip().upper()
    if not ticker or not suffix:
        return None
    return ticker, suffix
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /path/to/FinancialDevelopment && pytest test_upi_decoder.py -v`
Expected: all `TestDecodeLocal` and `TestSplitRic` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add upi_decoder.py test_upi_decoder.py
git commit -m "feat: add tier-1 local UPI decoder (parse upi_underlier_name)"
```

---

### Task 3: Tier-2 OpenFIGI fallback + `decode_one()`

**Files:**
- Modify: `upi_decoder.py`
- Test: `test_upi_decoder.py`

**Interfaces:**
- Consumes: `decode_local(str) -> Optional[str]`, `split_ric(str) -> Optional[tuple[str, str]]` (Task 2)
- Produces: `RIC_SUFFIX_TO_MIC: dict[str, str]`
- Produces: `class OpenFigiClient: def __init__(self, api_key=None, session=None); def lookup_ticker_batch(self, jobs: list[tuple[str, str]]) -> list[Optional[str]]`
- Produces: `@dataclass class DecodeResult: company_name: Optional[str]; tier: str; detail: str`
- Produces: `decode_one(upi_underlier_name, underlier_id, underlier_id_source, figi_client=None) -> DecodeResult`

- [ ] **Step 1: Write the failing tests**

Append to `test_upi_decoder.py`:

```python
from unittest.mock import MagicMock, patch

from upi_decoder import DecodeResult, OpenFigiClient, decode_one


class TestOpenFigiClient:
    def test_lookup_ticker_batch_parses_matched_response(self):
        client = OpenFigiClient(session=MagicMock())
        client.session.post.return_value.raise_for_status = lambda: None
        client.session.post.return_value.json.return_value = [
            {"data": [{"name": "SIEYUAN ELECTRIC CO LTD", "figi": "BBG000ABCXYZ"}]},
        ]
        names = client.lookup_ticker_batch([("002028", "SHE")])
        assert names == ["SIEYUAN ELECTRIC CO LTD"]

    def test_lookup_ticker_batch_handles_unmatched_entry(self):
        client = OpenFigiClient(session=MagicMock())
        client.session.post.return_value.raise_for_status = lambda: None
        client.session.post.return_value.json.return_value = [
            {"error": "No identifier found."},
        ]
        names = client.lookup_ticker_batch([("BADTICKER", "SHE")])
        assert names == [None]

    def test_lookup_ticker_batch_handles_request_exception(self):
        import requests
        client = OpenFigiClient(session=MagicMock())
        client.session.post.side_effect = requests.RequestException("boom")
        names = client.lookup_ticker_batch([("002028", "SHE")])
        assert names == [None]

    def test_lookup_ticker_batch_empty_input_returns_empty(self):
        client = OpenFigiClient(session=MagicMock())
        assert client.lookup_ticker_batch([]) == []

    def test_api_key_added_to_headers_when_set(self):
        client = OpenFigiClient(api_key="secret-key")
        assert client._headers()["X-OPENFIGI-APIKEY"] == "secret-key"

    def test_no_api_key_header_omitted(self):
        client = OpenFigiClient()
        assert "X-OPENFIGI-APIKEY" not in client._headers()


class TestDecodeOne:
    def test_prefers_tier1_local_name_without_calling_openfigi(self):
        figi_client = MagicMock()
        result = decode_one("LIVZON GROUP\\REGISTERED\\SHARES A\\000513", "000513.ZK", "RIC", figi_client=figi_client)
        assert result == DecodeResult("LIVZON GROUP", "local", "upi_underlier_name")
        figi_client.lookup_ticker_batch.assert_not_called()

    def test_falls_back_to_openfigi_when_local_is_placeholder(self):
        figi_client = MagicMock()
        figi_client.lookup_ticker_batch.return_value = ["SIEYUAN ELECTRIC CO LTD"]
        result = decode_one("COM STK", "002028.ZK", "RIC", figi_client=figi_client)
        assert result.company_name == "SIEYUAN ELECTRIC CO LTD"
        assert result.tier == "openfigi"
        figi_client.lookup_ticker_batch.assert_called_once_with([("002028", "SHE")])

    def test_unresolved_when_ric_suffix_unmapped(self):
        figi_client = MagicMock()
        result = decode_one("COM STK", "1234.XX", "RIC", figi_client=figi_client)
        assert result == DecodeResult(None, "unresolved", "no local name, no OpenFIGI mapping")
        figi_client.lookup_ticker_batch.assert_not_called()

    def test_unresolved_when_source_is_not_ric(self):
        figi_client = MagicMock()
        result = decode_one("COM STK", "US0378331005", "ISIN", figi_client=figi_client)
        assert result.tier == "unresolved"
        figi_client.lookup_ticker_batch.assert_not_called()

    def test_unresolved_when_no_figi_client_given(self):
        result = decode_one("COM STK", "002028.ZK", "RIC", figi_client=None)
        assert result.tier == "unresolved"

    def test_unresolved_when_openfigi_returns_no_match(self):
        figi_client = MagicMock()
        figi_client.lookup_ticker_batch.return_value = [None]
        result = decode_one("COM STK", "002028.ZK", "RIC", figi_client=figi_client)
        assert result.tier == "unresolved"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/FinancialDevelopment && pytest test_upi_decoder.py -v`
Expected: `ImportError: cannot import name 'OpenFigiClient' from 'upi_decoder'` (Task 2's version of the module doesn't have it yet).

- [ ] **Step 3: Add tier-2 + `decode_one()` to `upi_decoder.py`**

Append to `upi_decoder.py` (after `split_ric`):

```python
import time
from typing import List, Sequence

import requests

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH_SIZE = 100                  # OpenFIGI's documented max per POST body
OPENFIGI_MIN_SECONDS_BETWEEN_CALLS = 0.3   # stay well under the anonymous rate limit

# Reuters Instrument Code exchange suffix -> OpenFIGI/MIC exchange code.
# Deliberately small and extend-as-you-go: covers the exchanges observed in
# this dataset's single-stock total-return swaps. An unmapped suffix is not
# an error -- decode_one() just falls back to "unresolved" instead of guessing.
RIC_SUFFIX_TO_MIC = {
    "T": "TSE",     # Tokyo Stock Exchange
    "TW": "TAI",    # Taiwan Stock Exchange
    "SS": "SHH",    # Shanghai Stock Exchange (RIC convention)
    "SH": "SHH",    # Shanghai Stock Exchange (also seen as .SH in this feed)
    "SZ": "SHE",    # Shenzhen Stock Exchange
    "ZK": "SHE",    # Shenzhen Stock Exchange, registered-share variant
    "HK": "HKG",    # Hong Kong Stock Exchange
    "L": "LSE",     # London Stock Exchange
}


@dataclass
class DecodeResult:
    company_name: Optional[str]
    tier: str    # "local" | "openfigi" | "unresolved"
    detail: str  # human-readable provenance, e.g. "RIC:ZK->SHE"


class OpenFigiClient:
    """Thin, rate-limited wrapper around OpenFIGI's free /v3/mapping endpoint.

    See https://www.openfigi.com/api/documentation for the request/response
    shape -- this client sends {"idType": "TICKER", "idValue": ..., "exchCode": ...}
    jobs and reads back the first match's "name" per job.
    """

    def __init__(self, api_key: Optional[str] = None, session: Optional["requests.Session"] = None):
        self.api_key = api_key
        self.session = session or requests.Session()
        self._last_call = 0.0

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        return headers

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < OPENFIGI_MIN_SECONDS_BETWEEN_CALLS:
            time.sleep(OPENFIGI_MIN_SECONDS_BETWEEN_CALLS - elapsed)

    def lookup_ticker_batch(self, jobs: Sequence[tuple[str, str]]) -> List[Optional[str]]:
        """jobs: sequence of (ticker, mic_exchange_code).

        Returns one name-or-None per job, same order/length as jobs. Never
        raises on a bad individual job or a failed HTTP call -- callers get
        None entries instead so a network hiccup can't abort a whole batch.
        """
        if not jobs:
            return []

        names: List[Optional[str]] = []
        for start in range(0, len(jobs), OPENFIGI_BATCH_SIZE):
            chunk = jobs[start:start + OPENFIGI_BATCH_SIZE]
            body = [{"idType": "TICKER", "idValue": t, "exchCode": mic} for t, mic in chunk]

            self._throttle()
            try:
                resp = self.session.post(
                    OPENFIGI_MAPPING_URL, json=body, headers=self._headers(), timeout=15,
                )
                resp.raise_for_status()
                results = resp.json()
            except requests.RequestException as e:
                logger.warning("OpenFIGI mapping call failed for %d job(s): %s", len(chunk), e)
                names.extend([None] * len(chunk))
                continue
            finally:
                self._last_call = time.monotonic()

            for entry in results:
                data = entry.get("data") if isinstance(entry, dict) else None
                names.append(data[0].get("name") if data else None)
        return names


def decode_one(
    upi_underlier_name: Optional[str],
    underlier_id: Optional[str],
    underlier_id_source: Optional[str],
    figi_client: Optional[OpenFigiClient] = None,
) -> DecodeResult:
    """Decode a single UPI's company name: tier 1 (local), then tier 2 (OpenFIGI)."""
    local_name = decode_local(upi_underlier_name)
    if local_name:
        return DecodeResult(local_name, "local", "upi_underlier_name")

    if figi_client is not None and (underlier_id_source or "").upper() == "RIC":
        split = split_ric(underlier_id)
        if split:
            ticker, suffix = split
            mic = RIC_SUFFIX_TO_MIC.get(suffix)
            if mic:
                names = figi_client.lookup_ticker_batch([(ticker, mic)])
                if names and names[0]:
                    return DecodeResult(names[0], "openfigi", f"RIC:{suffix}->{mic}")

    return DecodeResult(None, "unresolved", "no local name, no OpenFIGI mapping")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /path/to/FinancialDevelopment && pytest test_upi_decoder.py -v`
Expected: all tests in `TestOpenFigiClient` and `TestDecodeOne` PASS (23 tests total in the file so far).

- [ ] **Step 5: Sanity-check the OpenFIGI response shape against the live API once**

Run: `cd /path/to/FinancialDevelopment && python3 -c "
from upi_decoder import OpenFigiClient
c = OpenFigiClient()
print(c.lookup_ticker_batch([('2382', 'TAI')]))
"`

Expected: a list with one non-None string (a real company name for Taiwan-listed ticker 2382, Largan Precision). If this comes back `[None]`, re-check the request body shape against https://www.openfigi.com/api/documentation before moving on -- the mocked tests above only prove the client parses *a* response shape correctly, not that it matches OpenFIGI's current live shape.

- [ ] **Step 6: Commit**

```bash
git add upi_decoder.py test_upi_decoder.py
git commit -m "feat: add tier-2 OpenFIGI fallback and decode_one()"
```

---

### Task 4: Incremental runner (`decode_upis.py`)

**Files:**
- Create: `decode_upis.py`
- Test: `test_decode_upis.py`

**Interfaces:**
- Consumes: `setup_db.init_database(db_path)` (existing), `upi_decoder.OpenFigiClient`, `upi_decoder.decode_one(upi_underlier_name, underlier_id, underlier_id_source, figi_client) -> DecodeResult` (Task 3)
- Produces: `run_batch(conn: sqlite3.Connection, batch_size: int, figi_client: Optional[OpenFigiClient]) -> dict`
- Produces: `run(db_path: Optional[str] = None, batch_size: int = 5000, max_batches: int = 20, use_openfigi: bool = True) -> dict`

- [ ] **Step 1: Write the failing tests**

Create `test_decode_upis.py`:

```python
"""Integration tests for decode_upis.py against a temp SQLite file."""
import os
import sqlite3
import tempfile

import pytest

import setup_db
from decode_upis import run, run_batch


@pytest.fixture
def db_path():
    path = tempfile.mktemp(suffix=".db")
    setup_db.init_database(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


def _insert_trade(conn, dissemination_id, upi, underlier_id, underlier_source, underlier_name):
    conn.execute(
        """
        INSERT INTO swap_trades (
            dissemination_id, regulator, asset_class, source_file, raw_json,
            upi, underlier_id_leg1, underlier_id_source_leg1, upi_underlier_name
        ) VALUES (?, 'CFTC', 'EQ', 'test.csv', '{}', ?, ?, ?, ?);
        """,
        (dissemination_id, upi, underlier_id, underlier_source, underlier_name),
    )
    conn.commit()


class TestRunBatch:
    def test_decodes_new_upi_via_tier1(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats == {"rows_scanned": 1, "new_upis": 1, "decoded": 1, "max_rowid": 1}
        row = conn.execute("SELECT company_name, decode_tier FROM upi_reference WHERE upi = 'UPI-A';").fetchone()
        assert row["company_name"] == "LIVZON GROUP"
        assert row["decode_tier"] == "local"

    def test_skips_upi_already_in_reference_table(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")
        conn.execute(
            "INSERT INTO upi_reference (upi, company_name, decode_tier, decode_detail) "
            "VALUES ('UPI-A', 'ALREADY DECODED', 'local', 'test-seed');"
        )
        conn.commit()

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats["new_upis"] == 0
        row = conn.execute("SELECT company_name FROM upi_reference WHERE upi = 'UPI-A';").fetchone()
        assert row["company_name"] == "ALREADY DECODED"

    def test_deduplicates_repeated_upi_within_one_batch(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")
        _insert_trade(conn, "D2", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats["new_upis"] == 1
        assert conn.execute("SELECT COUNT(*) FROM upi_reference;").fetchone()[0] == 1

    def test_advances_watermark_so_second_call_sees_no_new_rows(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")

        run_batch(conn, batch_size=100, figi_client=None)
        stats_second = run_batch(conn, batch_size=100, figi_client=None)

        assert stats_second == {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "max_rowid": 1}

    def test_unresolvable_upi_stored_with_null_company_name(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", "UPI-B", "1234.XX", "RIC", "COM STK")

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats["decoded"] == 0
        row = conn.execute("SELECT company_name, decode_tier FROM upi_reference WHERE upi = 'UPI-B';").fetchone()
        assert row["company_name"] is None
        assert row["decode_tier"] == "unresolved"

    def test_null_upi_rows_are_skipped(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _insert_trade(conn, "D1", None, None, None, None)

        stats = run_batch(conn, batch_size=100, figi_client=None)

        assert stats == {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "max_rowid": 0}


class TestRun:
    def test_run_processes_multiple_batches_until_caught_up(self, db_path):
        conn = sqlite3.connect(db_path)
        for i in range(5):
            _insert_trade(conn, f"D{i}", f"UPI-{i}", "000513.ZK", "RIC", f"COMPANY{i}\\REGISTERED\\SHARES A\\000513")
        conn.close()

        totals = run(db_path=db_path, batch_size=2, max_batches=10, use_openfigi=False)

        assert totals["batches"] == 3  # 2 + 2 + 1(partial, stops the loop)
        assert totals["new_upis"] == 5
        assert totals["decoded"] == 5

    def test_run_is_a_no_op_when_already_caught_up(self, db_path):
        conn = sqlite3.connect(db_path)
        _insert_trade(conn, "D1", "UPI-A", "000513.ZK", "RIC", "LIVZON GROUP\\REGISTERED\\SHARES A\\000513")
        conn.close()

        run(db_path=db_path, batch_size=100, max_batches=10, use_openfigi=False)
        totals_second = run(db_path=db_path, batch_size=100, max_batches=10, use_openfigi=False)

        assert totals_second["new_upis"] == 0
        assert totals_second["batches"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /path/to/FinancialDevelopment && pytest test_decode_upis.py -v`
Expected: `ModuleNotFoundError: No module named 'decode_upis'`.

- [ ] **Step 3: Write `decode_upis.py`**

Create `decode_upis.py`:

```python
"""decode_upis.py -- incrementally decode swap_trades.upi into company names.

Walks swap_trades in rowid order starting from the watermark recorded in
upi_decode_state, collects UPIs not yet in upi_reference, decodes each via
upi_decoder.decode_one(), and upserts the result. Safe to re-run: it never
rescans rows it has already looked at, so cost scales with new trades
ingested since the last run, not with the size of swap_trades (~386GB and
growing).
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from typing import Optional

from upi_decoder import OpenFigiClient, decode_one

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'swaps.db')
DEFAULT_BATCH_SIZE = 5000
DEFAULT_MAX_BATCHES = 20


def _connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _get_watermark(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT last_rowid FROM upi_decode_state WHERE id = 1;").fetchone()
    return row["last_rowid"] if row else 0


def _set_watermark(conn: sqlite3.Connection, rowid: int) -> None:
    conn.execute(
        """
        INSERT INTO upi_decode_state (id, last_rowid, updated_at)
        VALUES (1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            last_rowid = excluded.last_rowid,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (rowid,),
    )


def run_batch(conn: sqlite3.Connection, batch_size: int, figi_client: Optional[OpenFigiClient]) -> dict:
    """Process one batch of up to batch_size new swap_trades rows.

    Returns {"rows_scanned", "new_upis", "decoded", "max_rowid"}.
    """
    watermark = _get_watermark(conn)

    rows = conn.execute(
        """
        SELECT rowid, upi, underlier_id_leg1, underlier_id_source_leg1, upi_underlier_name
        FROM swap_trades
        WHERE rowid > ? AND upi IS NOT NULL AND upi != ''
        ORDER BY rowid
        LIMIT ?;
        """,
        (watermark, batch_size),
    ).fetchall()

    if not rows:
        return {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "max_rowid": watermark}

    max_rowid = rows[-1]["rowid"]
    seen_upis = set()
    new_rows = []
    for r in rows:
        upi = r["upi"]
        if upi in seen_upis:
            continue
        seen_upis.add(upi)
        already_decoded = conn.execute(
            "SELECT 1 FROM upi_reference WHERE upi = ?;", (upi,)
        ).fetchone()
        if already_decoded is None:
            new_rows.append(r)

    decoded = 0
    for r in new_rows:
        result = decode_one(
            r["upi_underlier_name"], r["underlier_id_leg1"], r["underlier_id_source_leg1"],
            figi_client=figi_client,
        )
        conn.execute(
            """
            INSERT INTO upi_reference (upi, company_name, decode_tier, decode_detail, raw_underlier_name, decoded_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(upi) DO UPDATE SET
                company_name = excluded.company_name,
                decode_tier = excluded.decode_tier,
                decode_detail = excluded.decode_detail,
                raw_underlier_name = excluded.raw_underlier_name,
                decoded_at = CURRENT_TIMESTAMP;
            """,
            (r["upi"], result.company_name, result.tier, result.detail, r["upi_underlier_name"]),
        )
        if result.company_name:
            decoded += 1

    _set_watermark(conn, max_rowid)
    conn.commit()

    return {"rows_scanned": len(rows), "new_upis": len(new_rows), "decoded": decoded, "max_rowid": max_rowid}


def run(
    db_path: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    use_openfigi: bool = True,
) -> dict:
    """Run up to max_batches batches, stopping early once caught up."""
    conn = _connection(db_path or DB_PATH)
    figi_client = OpenFigiClient(api_key=os.getenv("OPENFIGI_API_KEY")) if use_openfigi else None

    totals = {"rows_scanned": 0, "new_upis": 0, "decoded": 0, "batches": 0}
    try:
        for _ in range(max_batches):
            stats = run_batch(conn, batch_size, figi_client)
            totals["rows_scanned"] += stats["rows_scanned"]
            totals["new_upis"] += stats["new_upis"]
            totals["decoded"] += stats["decoded"]
            totals["batches"] += 1
            if stats["rows_scanned"] < batch_size:
                break
        return totals
    finally:
        conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description='Decode UPI codes into company names.')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--max-batches', type=int, default=DEFAULT_MAX_BATCHES)
    parser.add_argument('--no-openfigi', action='store_true', help='Skip tier-2 OpenFIGI lookups (local decode only).')
    args = parser.parse_args()

    result = run(batch_size=args.batch_size, max_batches=args.max_batches, use_openfigi=not args.no_openfigi)
    logger.info("Decode run complete: %s", result)
    print(result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /path/to/FinancialDevelopment && pytest test_decode_upis.py -v`
Expected: all tests in `TestRunBatch` and `TestRun` PASS.

- [ ] **Step 5: Run the full test suite together**

Run: `cd /path/to/FinancialDevelopment && pytest test_upi_decoder.py test_decode_upis.py -v`
Expected: all tests PASS (no import collisions between the two files).

- [ ] **Step 6: Commit**

```bash
git add decode_upis.py test_decode_upis.py
git commit -m "feat: add incremental UPI decode runner"
```

---

### Task 5: Wire the decoder into ingestion

**Files:**
- Modify: `scheduled_ingest.py:1-40` (`run_ingestion_job`) and `:95-107` (the `--backfill` CLI branch)

**Interfaces:**
- Consumes: `decode_upis.run(max_batches=int, use_openfigi=bool) -> dict` (Task 4)

- [ ] **Step 1: Import `decode_upis` and call it at the end of `run_ingestion_job()`**

In `scheduled_ingest.py`, change the import block near the top:

```python
import poll_ingest
import backfill
import decode_upis
```

Then change `run_ingestion_job()` to decode after the poll completes:

```python
def run_ingestion_job():
    """Main job: run an incremental poll pass against DTCC's live slice feed."""
    logger.info("=" * 60)
    logger.info("Starting DTCC swaps poll")
    logger.info("=" * 60)

    summary = poll_ingest.run()

    logger.info("=" * 60)
    logger.info("Poll complete")
    for key, stats in summary.items():
        logger.info(f"  {key}: new_slices={stats['new_slices']} records={stats['records']}")
    logger.info("=" * 60)

    try:
        decode_stats = decode_upis.run()
        logger.info("UPI decode pass: %s", decode_stats)
    except Exception as e:
        # Decoding new UPIs is best-effort -- a broken OpenFIGI call or a
        # locked db must never fail the ingestion job itself.
        logger.warning("UPI decode pass failed (non-fatal): %s", e)

    return summary
```

- [ ] **Step 2: Also decode after a manual `--backfill` run**

In the `elif args.backfill:` branch (after the summary table is printed), add:

```python
    elif args.backfill:
        logger.info("Running backfill for all configured targets...")
        summaries = []
        for regulator, asset in backfill.TARGETS:
            summaries.append(backfill.backfill_target(regulator, asset))

        header = f"{'Regulator':<10}{'Asset':<8}{'Files':<10}{'Records':<12}{'Errors':<8}"
        print("\n" + header)
        print("-" * len(header))
        for s in summaries:
            print(
                f"{s['regulator']:<10}{s['asset']:<8}{s['files_processed']:<10}"
                f"{s['records_loaded']:<12}{s['errors']:<8}"
            )

        print("\nDecoding UPIs seen during backfill...")
        try:
            decode_stats = decode_upis.run(max_batches=10_000)
            print(f"Decode complete: {decode_stats}")
        except Exception as e:
            print(f"UPI decode pass failed (non-fatal): {e}")
```

- [ ] **Step 3: Verify by hand with `--run-now` against the real db**

Run: `cd /path/to/FinancialDevelopment && python3 scheduled_ingest.py --run-now`
Expected: log output includes a line like `UPI decode pass: {'rows_scanned': ..., 'new_upis': ..., 'decoded': ..., 'batches': ...}` after the poll summary, and the command exits 0.

- [ ] **Step 4: Commit**

```bash
git add scheduled_ingest.py
git commit -m "feat: run UPI decode pass after each ingestion job"
```

---

### Task 6: Backfill existing UPIs and confirm cache coverage

**Files:**
- None (operational step, no code change)

**Interfaces:**
- Consumes: `decode_upis.run(max_batches=int)` (Task 4), invoked via its CLI (Task 5's wiring only covers *future* ingestion; the ~386GB of already-ingested trades still need an initial pass)

- [ ] **Step 1: Run an initial full backfill decode pass (may take a while on first run)**

Run: `cd /path/to/FinancialDevelopment && python3 decode_upis.py --batch-size 5000 --max-batches 100000`

This walks the entire existing `swap_trades` table once, 5000 rows at a time, decoding every UPI it has never seen. Expected: the process eventually prints a dict like `{'rows_scanned': N, 'new_upis': M, 'decoded': K, 'batches': B}` and exits. If it's still running after a long time, that's expected for a 386GB table on the first pass -- it is safe to `Ctrl+C` and re-run later; the watermark means it picks up where it left off rather than restarting.

- [ ] **Step 2: Spot-check coverage**

Run: `cd /path/to/FinancialDevelopment && python3 -c "
import sqlite3
conn = sqlite3.connect('swaps.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM upi_reference;')
print('decoded UPIs:', cur.fetchone()[0])
cur.execute(\"SELECT decode_tier, COUNT(*) FROM upi_reference GROUP BY decode_tier;\")
print(cur.fetchall())
"`

Expected: a non-zero count, with a breakdown across `local`, `openfigi`, and `unresolved` tiers. `unresolved` rows are expected (unmapped RIC suffixes, non-RIC underlier sources) -- they show up as `--` in the dashboard, not an error.

---

### Task 7: Dashboard "Company" column

**Files:**
- Modify: `dashboard/app.py:562-604` (the `swaps()` route)
- Modify: `dashboard/templates/swaps.html:66-84`

**Interfaces:**
- Consumes: `upi_reference(upi, company_name, ...)` (Task 1)

- [ ] **Step 1: Update the `/swaps` query to join `upi_reference`**

In `dashboard/app.py`, replace the `swaps()` function body from the `columns = [...]` line through the `try:` query block:

```python
    columns = ['dissemination_id', 'regulator', 'asset_class', 'action_type',
               'event_type', 'effective_date', 'expiration_date', 'cleared',
               'notional_amount_leg1', 'notional_currency_leg1', 'price',
               'underlying_asset_name', 'upi', 'company_name', 'upi_underlier_name',
               'ingested_at']
    notional_cols = {'notional_amount_leg1'}

    where, params = [], []
    if regulator:
        where.append('st.regulator = ?')
        params.append(regulator)
    if asset_class:
        where.append('st.asset_class = ?')
        params.append(asset_class)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows: List[Dict[str, Any]] = []
    total = 0
    regulators: List[str] = []
    asset_classes: List[str] = []
    error: Optional[str] = None

    conn = _db()
    if conn is None:
        error = f'swaps.db not found at {DB_PATH}'
    else:
        try:
            select_cols = ", ".join(
                'ur.company_name' if c == 'company_name' else f'st.{c}'
                for c in columns
            )
            total = conn.execute(
                f'SELECT COUNT(*) AS n FROM swap_trades st {where_sql};',
                params).fetchone()['n']
            rows = [dict(r) for r in conn.execute(
                f'SELECT {select_cols} FROM swap_trades st '
                'LEFT JOIN upi_reference ur ON ur.upi = st.upi '
                f'{where_sql} '
                'ORDER BY st.ingested_at DESC, st.dissemination_id DESC LIMIT ? OFFSET ?;',
                params + [per_page, (page - 1) * per_page])]
            regulators = [r['regulator'] for r in conn.execute(
                'SELECT DISTINCT regulator FROM swap_trades '
                'WHERE regulator IS NOT NULL ORDER BY regulator;')]
            asset_classes = [r['asset_class'] for r in conn.execute(
                'SELECT DISTINCT asset_class FROM swap_trades '
                'WHERE asset_class IS NOT NULL ORDER BY asset_class;')]
        except Exception as e:
            error = f'{type(e).__name__}: {e}'
        finally:
            conn.close()
```

The rest of the function (the `pages = ...` line through the `return TEMPLATES.TemplateResponse(...)` call) is unchanged.

- [ ] **Step 2: Render the new column in the template**

In `dashboard/templates/swaps.html`, change the cell-rendering block (inside the `{% for c in columns %}` loop in the table body) from:

```html
            {% if c in notional_cols %}
              <td class="num">{{ row[c] | usd }}</td>
            {% elif c in ('underlying_asset_name', 'upi_underlier_name') %}
              <td class="wrap">{{ row[c] if row[c] is not none else '--' }}</td>
            {% elif c in ('ingested_at',) %}
              <td class="mono small">{{ row[c] | ts }}</td>
            {% else %}
              <td class="small">{{ row[c] if row[c] is not none else '--' }}</td>
            {% endif %}
```

to:

```html
            {% if c in notional_cols %}
              <td class="num">{{ row[c] | usd }}</td>
            {% elif c in ('underlying_asset_name', 'upi_underlier_name', 'company_name') %}
              <td class="wrap">{{ row[c] if row[c] is not none else '--' }}</td>
            {% elif c in ('ingested_at',) %}
              <td class="mono small">{{ row[c] | ts }}</td>
            {% else %}
              <td class="small">{{ row[c] if row[c] is not none else '--' }}</td>
            {% endif %}
```

(The header row already loops over `columns` generically, so `company_name` gets a `<th>company_name</th>` automatically -- consistent with how every other column header is rendered as its raw column name.)

- [ ] **Step 3: Verify by hand**

Run: `cd /path/to/FinancialDevelopment && python3 -m uvicorn dashboard.app:app --port 8000` (or however the project normally starts the dashboard -- check `dashboard.bat`), then open `http://localhost:8000/swaps` in a browser.

Expected: the trades table now has a `company_name` column between `upi` and `upi_underlier_name`, showing decoded company names (or `--` for rows whose UPI hasn't been decoded yet or is `unresolved`).

- [ ] **Step 4: Commit**

```bash
git add dashboard/app.py dashboard/templates/swaps.html
git commit -m "feat: show decoded company name next to UPI on /swaps"
```

---

## Self-Review Notes

- **Spec coverage:** "decoder for the UPI code" -> Tasks 1-4 (schema, tier-1, tier-2, runner). "wire into the scraper" -> Task 5. "backfill existing data" -> Task 6 (implicit requirement: the decoder is useless on the dashboard until the ~386GB of already-ingested trades get an initial pass). "add a column in the dashboard next to UPI showing company name" -> Task 7.
- **Placeholder scan:** no TBD/TODO markers; every step has literal runnable code or an exact command with expected output.
- **Type/name consistency checked:** `DecodeResult(company_name, tier, detail)` fields match between Task 3's definition and every usage in Task 3/4 tests. `run_batch(conn, batch_size, figi_client)` and `run(db_path, batch_size, max_batches, use_openfigi)` signatures match between Task 4's implementation and Task 5's call site (`decode_upis.run()` with defaults, and `decode_upis.run(max_batches=10_000)` in the backfill branch). The dashboard's `columns` list order (`upi` immediately followed by `company_name`) matches what Task 7 asks the template to render, and `company_name` was added to the `wrap`-styled column set alongside the other free-text columns so long names don't break the table layout.
