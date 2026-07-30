"""Data source adapters for swap trade ingestion.

This package contains pluggable adapters for different swap data sources
(DTCC, CME, OTC, etc.). Each adapter implements the DataSourceAdapter interface
defined in shared/data_source.py.

To add a new source:

1. Create <source_lower>_adapter.py with a class <Source>Adapter
2. Implement get_name(), get_schema_version(), and fetch_trades()
3. Enable via DATA_SOURCES env var (e.g., DATA_SOURCES=DTCC,CME,OTC)

The orchestrator discovers enabled sources and calls their adapters in parallel,
aggregating results before passing to Vol_Suite.
"""

__all__ = []
