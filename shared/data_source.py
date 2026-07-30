"""DataSourceAdapter interface for multi-source data ingestion.

Defines the abstract base class that all data source adapters must implement,
enabling pluggable sources (DTCC, CME, OTC, etc.) in the ingestion pipeline.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from datetime import date


class TradeRecord:
    """Represents a normalized trade record from any data source.

    This is the contract that all adapters produce. The loader receives
    these records and upserts them to swap_trades with data_source set
    to the adapter's name.
    """

    def __init__(self, data: Dict[str, Any]):
        """Initialize a trade record from a dict (typically from a parser).

        Args:
            data: Dict containing trade fields matching swap_trades schema
                  (dissemination_id, regulator, asset_class, etc.)
        """
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        """Export record as a dict for database insertion."""
        return self.data.copy()

    def __repr__(self) -> str:
        return f"TradeRecord({self.data.get('dissemination_id')})"


class DataSourceAdapter(ABC):
    """Abstract base class for swap trade data source adapters.

    Implementations (e.g., DTCCAdapter, CMEAdapter) wrap source-specific
    logic (authentication, API calls, file parsing, timestamp normalization)
    and present trades as a uniform stream via fetch_trades().

    The adapter's name (get_name()) is recorded in swap_trades.data_source
    to allow filtering trades by origin.
    """

    @abstractmethod
    def get_name(self) -> str:
        """Return the adapter's name (e.g., 'DTCC', 'CME', 'OTC').

        This name is recorded in swap_trades.data_source for every trade
        this adapter produces, enabling multi-source audits and filtering.
        """
        pass

    @abstractmethod
    def get_schema_version(self) -> int:
        """Return the adapter's schema version for this trade format.

        Allows tracking backwards-incompatible changes in how the adapter
        normalizes data. When an adapter's normalization changes (e.g., new
        timestamp format, fields added/removed), increment this version.

        Returns:
            Positive integer (e.g., 1, 2, 3). Start at 1; never use 0.
        """
        pass

    @abstractmethod
    def fetch_trades(
        self,
        date_range: Optional[tuple[date, date]] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[TradeRecord]:
        """Fetch trades from this source for the given date range and filters.

        Args:
            date_range: Optional tuple (start_date, end_date) inclusive.
                       If None, fetch all available data (use with caution).
            filters: Optional dict of filter params (source-specific).
                    Common keys: regulator (e.g., 'SEC', 'CFTC'),
                    asset_class (e.g., 'EQ', 'IR').
                    Adapters may accept additional filters.

        Returns:
            List of TradeRecord objects, in any order. Empty list if no
            trades match the criteria.

        Raises:
            Exception: On network errors, parse failures, auth issues.
                      The caller (db_loader) is responsible for logging,
                      state management, and recovery.
        """
        pass


# Type alias for convenience
DataSourceAdapterClass = type[DataSourceAdapter]
