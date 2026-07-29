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
    "", "COM STK", "COMMON SHARES", "SHS", "TWSE LISTED STOCKS",
    "TPEX LISTED STOCKS", "EMERGING STOCKS", "NO NAME OBTAINABLE",
    "N/A", "NA", "NONE",
}

# Beyond the exact placeholder strings above, DTCC also sends generic
# share-class/instrument-type descriptors instead of a company name (e.g.
# "REGISTERED SHARES - CLASS C PREFERENCIALS"). Observed live in this feed's
# single-segment (no-backslash) rows, alongside genuine company names like
# "WOOSHIN SYSTEMS" or "DHP KOREA.CO., Ltd" that must NOT be caught by this.
# A real company name in this dataset has never been observed to contain any
# of these words, so treating them as a substring match is safe.
_GENERIC_DESCRIPTOR_KEYWORDS = ("SHARES", "STOCKS", "LISTED")


def decode_local(upi_underlier_name: Optional[str]) -> Optional[str]:
    """Tier 1: pull a company name out of the DTCC-supplied underlier name.

    Returns the cleaned name, or None if the field is blank, one of the known
    ANNA DSB placeholder strings, or a generic share-class/instrument-type
    descriptor -- none of which carry company information.
    """
    if not upi_underlier_name:
        return None
    first_segment = upi_underlier_name.split("\\", 1)[0].strip()
    upper = first_segment.upper()
    if upper in _NON_NAMES:
        return None
    if any(keyword in upper for keyword in _GENERIC_DESCRIPTOR_KEYWORDS):
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


import time
from typing import List, Sequence

import requests

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH_SIZE = 100                  # OpenFIGI's documented max per POST body
OPENFIGI_MIN_SECONDS_BETWEEN_CALLS = 0.3   # stay well under the anonymous rate limit

# Reuters Instrument Code exchange suffix -> real ISO 10383 MIC, as required
# by OpenFIGI's "micCode" mapping field. Verified against the live API (each
# of these was checked with a real ticker and returned a matching company
# name) -- OpenFIGI's own "exchCode" enum uses a different, non-MIC vocabulary
# ("TAIWAN", "TOKYO", etc.) that does NOT resolve tickers, so micCode is the
# only field that works here. Deliberately small and extend-as-you-go: covers
# the exchanges observed in this dataset's single-stock total-return swaps.
# An unmapped suffix is not an error -- decode_one() just falls back to
# "unresolved" instead of guessing.
RIC_SUFFIX_TO_MIC = {
    "T": "XTKS",    # Tokyo Stock Exchange
    "TW": "XTAI",   # Taiwan Stock Exchange
    "SS": "XSHG",   # Shanghai Stock Exchange (RIC convention)
    "SH": "XSHG",   # Shanghai Stock Exchange (also seen as .SH in this feed)
    "SZ": "XSHE",   # Shenzhen Stock Exchange
    "ZK": "XSHE",   # Shenzhen Stock Exchange, registered-share variant
    "HK": "XHKG",   # Hong Kong Stock Exchange
    "L": "XLON",    # London Stock Exchange
}


@dataclass
class DecodeResult:
    company_name: Optional[str]
    tier: str    # "local" | "openfigi" | "unresolved"
    detail: str  # human-readable provenance, e.g. "RIC:ZK->SHE"


class OpenFigiClient:
    """Thin, rate-limited wrapper around OpenFIGI's free /v3/mapping endpoint.

    See https://www.openfigi.com/api/documentation for the request/response
    shape -- this client sends {"idType": "TICKER", "idValue": ..., "micCode": ...}
    jobs and reads back the first match's "name" per job. Uses "micCode" (a
    real ISO 10383 MIC, e.g. "XTKS") rather than OpenFIGI's own "exchCode"
    field -- exchCode uses a separate, non-MIC vocabulary ("TOKYO", "TAIWAN",
    etc.) that was tested live and does not resolve plain ticker lookups.
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
        """jobs: sequence of (ticker, mic_code) -- mic_code is a real ISO 10383
        MIC, e.g. "XTKS" for Tokyo, NOT OpenFIGI's own exchCode vocabulary.

        Returns one name-or-None per job, same order/length as jobs. Never
        raises on a bad individual job or a failed HTTP call -- callers get
        None entries instead so a network hiccup can't abort a whole batch.
        """
        if not jobs:
            return []

        names: List[Optional[str]] = []
        for start in range(0, len(jobs), OPENFIGI_BATCH_SIZE):
            chunk = jobs[start:start + OPENFIGI_BATCH_SIZE]
            body = [{"idType": "TICKER", "idValue": t, "micCode": mic} for t, mic in chunk]

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
