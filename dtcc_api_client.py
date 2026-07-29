import requests
import time
from typing import List, Dict

# Module-level configuration
BASE = "https://pddata.dtcc.com/ppd/api"
USER_AGENT = "FinancialDevelopment-DTCC-Ingest/1.0"
TIMEOUT = 30
MAX_RETRIES = 3

# Module-level session
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get_with_retry(url: str) -> requests.Response:
    """GET request with exponential backoff retry."""
    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            response = _session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt  # 1, 2, 4 seconds
                time.sleep(wait_time)
    raise last_exception


def list_live_slices(regulator: str, asset: str) -> List[Dict]:
    """
    Fetch live slices for a given regulator and asset.

    Returns list of dicts with keys: sliceId, fileName, startTs, endTs, rowCount, dissemDTM, fullFilePath
    """
    url = f"{BASE}/slice/{regulator}/{asset}"
    response = _get_with_retry(url)
    return response.json()


def list_cumulative(regulator: str, asset: str) -> List[Dict]:
    """
    Fetch cumulative files for a given regulator and asset.

    Returns list of dicts with keys: sliceId, fileName, startTs, endTs, rowCount, dissemDTM, fullFilePath
    fullFilePath points to daily EOD zip files like SEC_CUMULATIVE_EQUITIES_2026_07_28.zip
    """
    url = f"{BASE}/cumulative/{regulator}/{asset}"
    response = _get_with_retry(url)
    return response.json()


def download_zip(url: str) -> bytes:
    """
    Download a zip file from a public URL (S3).

    Returns the response content as bytes.
    """
    response = _get_with_retry(url)
    return response.content
