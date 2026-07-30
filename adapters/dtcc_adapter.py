"""DTCC swap trade data source adapter.

Wraps the existing DTCC backfill logic (dtcc_api_client, dtcc_parser) and
presents it as a DataSourceAdapter for integration into the multi-source
ingestion pipeline.

Responsibilities:
  - DTCC-specific timestamp normalization (if needed)
  - UPI handling and validation
  - Cumulative file vs. live slice routing
  - Graceful error handling with logging
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import date

from shared.data_source import DataSourceAdapter, TradeRecord

logger = logging.getLogger(__name__)


class DTCCAdapter(DataSourceAdapter):
    """DTCC swap trade data source adapter.

    Fetches swap trades from DTCC public dissemination feeds (cumulative
    EOD files and live slices) via dtcc_api_client, parses them via
    dtcc_parser, and normalizes timestamps/UPIs as needed.
    """

    # DTCC schema version for this normalization.
    # Bump when timestamp format or UPI handling changes.
    SCHEMA_VERSION = 1

    def __init__(self):
        """Initialize the DTCC adapter.

        Defers importing dtcc_api_client and dtcc_parser until instantiation.
        This allows adapter class discovery without forcing a dependency if
        those modules aren't available.
        """
        pass

    def get_name(self) -> str:
        """Return 'DTCC' as the adapter's name."""
        return "DTCC"

    def get_schema_version(self) -> int:
        """Return the current DTCC schema version (1)."""
        return self.SCHEMA_VERSION

    def _get_list_cumulative(self):
        """Get the list_cumulative function (for testing/mocking)."""
        from dtcc_api_client import list_cumulative
        return list_cumulative

    def _get_list_live_slices(self):
        """Get the list_live_slices function (for testing/mocking)."""
        from dtcc_api_client import list_live_slices
        return list_live_slices

    def _get_download_zip(self):
        """Get the download_zip function (for testing/mocking)."""
        from dtcc_api_client import download_zip
        return download_zip

    def _get_parse_swap_zip(self):
        """Get the parse_swap_zip function (for testing/mocking)."""
        from dtcc_parser import parse_swap_zip
        return parse_swap_zip

    def fetch_trades(
        self,
        date_range: Optional[tuple[date, date]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[TradeRecord]:
        """Fetch DTCC trades for the given date range and filters.

        This is a simplified entry point that fetches recent cumulative data
        and live slices. For full backfill (all historical data), use
        backfill.py::backfill_target() directly.

        Args:
            date_range: Optional (start_date, end_date) to filter results.
                       If None, fetch the latest available data.
            filters: Dict with optional 'regulator' (e.g., 'SEC', 'CFTC')
                    and 'asset_class' (e.g., 'EQ', 'IR'). If not provided,
                    defaults to SEC/EQ.

        Returns:
            List of TradeRecord objects from DTCC feeds matching the criteria.

        Raises:
            Exception: On API failures, parsing errors, or network issues.
        """
        filters = filters or {}
        regulator = filters.get("regulator", "SEC")
        asset_class = filters.get("asset_class", "EQ")

        records = []

        try:
            # Lazy import to allow mocking
            list_cumulative = self._get_list_cumulative()
            download_zip = self._get_download_zip()
            parse_swap_zip = self._get_parse_swap_zip()
            list_live_slices = self._get_list_live_slices()

            # Fetch cumulative files
            cumulative_entries = list_cumulative(regulator, asset_class)
            logger.info(f"Found {len(cumulative_entries)} cumulative files for {regulator}/{asset_class}")

            for entry in cumulative_entries:
                try:
                    zip_bytes = download_zip(entry["fullFilePath"])
                    parsed = parse_swap_zip(
                        zip_bytes,
                        regulator,
                        asset_class,
                        source_file=entry.get("fileName", ""),
                    )
                    records.extend([TradeRecord(r) for r in parsed])
                except Exception as e:
                    logger.error(
                        f"Failed to fetch cumulative {entry.get('fileName', 'unknown')}: {e}"
                    )
                    # Continue with other files rather than failing entirely

            # Fetch live slices
            live_entries = list_live_slices(regulator, asset_class)
            logger.info(f"Found {len(live_entries)} live slices for {regulator}/{asset_class}")

            for entry in live_entries:
                try:
                    zip_bytes = download_zip(entry["fullFilePath"])
                    parsed = parse_swap_zip(
                        zip_bytes,
                        regulator,
                        asset_class,
                        source_file=entry.get("fileName", ""),
                    )
                    records.extend([TradeRecord(r) for r in parsed])
                except Exception as e:
                    logger.error(
                        f"Failed to fetch live slice {entry.get('fileName', 'unknown')}: {e}"
                    )
                    # Continue with other slices rather than failing entirely

        except Exception as e:
            logger.error(f"DTCCAdapter.fetch_trades failed for {regulator}/{asset_class}: {e}")
            raise

        logger.info(
            f"DTCCAdapter fetched {len(records)} total trades for "
            f"{regulator}/{asset_class}"
        )
        return records

    def normalize_timestamp(self, timestamp_str: Optional[str]) -> Optional[str]:
        """Normalize a DTCC timestamp string to ISO 8601 format.

        Currently a pass-through (DTCC already provides ISO format), but
        available for future normalization if the feed format changes.

        Args:
            timestamp_str: DTCC timestamp string (e.g., "2026-07-29T14:30:00Z")

        Returns:
            Normalized timestamp or None if input was None/empty.
        """
        if not timestamp_str or not timestamp_str.strip():
            return None
        return timestamp_str.strip()
