"""OTC swap data adapter stub.

This is a template for implementing an OTC (over-the-counter) data source adapter.
Replace the fetch_trades() implementation with actual OTC data API/file parsing logic.
"""

from typing import Dict, List, Optional, Any
from datetime import date
import logging

# Import from the repo root
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.data_source import DataSourceAdapter, TradeRecord

logger = logging.getLogger(__name__)


class OTCAdapter(DataSourceAdapter):
    """OTC swap trade data source adapter.

    Fetches trades from OTC swap data feeds (Bloomberg, Markit, ICAP, etc.).
    This is a stub implementation; replace with actual OTC data provider client.
    """

    def __init__(self):
        """Initialize the OTC adapter."""
        self.name = "OTC"
        self.schema_version = 1
        # TODO: Initialize OTC data provider client
        # self.client = BloombergSwapClient(credentials=...)

    def get_name(self) -> str:
        """Return the adapter's name."""
        return self.name

    def get_schema_version(self) -> int:
        """Return the schema version for OTC trade normalization."""
        return self.schema_version

    def fetch_trades(
        self,
        date_range: Optional[tuple[date, date]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[TradeRecord]:
        """Fetch trades from OTC data provider.

        Args:
            date_range: Optional (start_date, end_date) tuple
            filters: Optional filter dict (e.g., {'asset_class': 'IR'})

        Returns:
            List of TradeRecord objects normalized to swap_trades schema

        Raises:
            Exception on API errors, auth failures, etc.
        """
        logger.info("OTCAdapter.fetch_trades() called (stub implementation)")

        # TODO: Replace with actual OTC provider API call:
        # 1. Authenticate to OTC data provider
        # 2. Query for trades in the date_range
        # 3. Apply filters
        # 4. Normalize each trade to swap_trades schema
        # 5. Return list of TradeRecord objects

        # Stub: return empty list for now
        logger.warning("OTC adapter is not yet implemented; returning no trades")
        return []


def _normalize_otc_trade(raw_trade: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw OTC trade dict to swap_trades schema.

    Args:
        raw_trade: Raw trade dict from OTC provider

    Returns:
        Normalized dict with keys matching swap_trades columns
    """
    # Example transformation (customize for actual OTC format)
    return {
        'dissemination_id': raw_trade.get('trade_id'),
        'original_dissemination_id': raw_trade.get('trade_id'),
        'regulator': 'OTC',
        'asset_class': raw_trade.get('asset_class'),
        'action_type': 'NEW',
        'event_type': 'TRADE',
        # ... map remaining fields ...
    }
