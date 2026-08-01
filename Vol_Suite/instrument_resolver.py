"""Integration of instrument identifier resolution with Vol_Suite.

Provides a helper module for Vol_Suite to use IdentifierResolver for
normalizing UPIs across multiple data sources and tracking instrument
identifiers in the vol_result.json output.

This module can be imported by volatility_suite.py to add:
  - --instrument-resolver flag support
  - Automatic identifier normalization in analysis pipeline
  - Cross-source instrument tracking in vol_result.json
"""

import sys
import os
from typing import Optional, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.identifiers import (
    IdentifierResolver, IdentifierResolverFactory, LocalIdentifierResolver,
    InstrumentIdentifier, IdentifierType
)


class Vol_SuiteInstrumentNormalizer:
    """Helper class for normalizing instrument identifiers within Vol_Suite.

    Integrates with the existing vol_surface and gamma_records processing
    to track and normalize identifiers across sources.
    """

    def __init__(self, resolver: Optional[IdentifierResolver] = None):
        """Initialize the normalizer.

        Args:
            resolver: Optional IdentifierResolver instance. If None, uses the
                     default resolver from IdentifierResolverFactory.
        """
        self.resolver = resolver or IdentifierResolverFactory.get_resolver("default")
        if self.resolver is None:
            self.resolver = LocalIdentifierResolver()

    def normalize_instrument_in_vol_surface(self, vol_surface: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize instrument identifiers in a vol_surface dict.

        Updates the vol_surface with normalized instrument IDs if the focus
        ticker or index ticker are known to have cross-source identifiers.

        Args:
            vol_surface: The vol_surface dict from vol_result.json

        Returns:
            Updated vol_surface with normalized_instrument_id fields added
            (if identifiers were resolved).
        """
        result = vol_surface.copy()

        # Normalize focus ticker if present
        if "focus_ticker" in vol_surface and vol_surface["focus_ticker"]:
            focus_normalized = self.resolver.resolve_by_id(
                vol_surface["focus_ticker"],
                IdentifierType.NORMALIZED_ID,
                source=None
            )
            if focus_normalized:
                result["focus_instrument_normalized_id"] = focus_normalized.normalized_id

        # Normalize index ticker if present
        if "index_ticker" in vol_surface and vol_surface["index_ticker"]:
            index_normalized = self.resolver.resolve_by_id(
                vol_surface["index_ticker"],
                IdentifierType.NORMALIZED_ID,
                source=None
            )
            if index_normalized:
                result["index_instrument_normalized_id"] = index_normalized.normalized_id

        return result

    def add_instrument_identifiers_to_result(self, vol_result: Dict[str, Any]) -> Dict[str, Any]:
        """Add instrument identifier information to a vol_result.json payload.

        Enriches the vol_result with identifier mappings for the focus/index
        tickers, useful for multi-source dashboard integration.

        Args:
            vol_result: The complete vol_result.json dict

        Returns:
            Updated vol_result with _instruments block added.
        """
        result = vol_result.copy()

        # Build an instruments section with identifier mappings
        instruments = {}

        # Add focus ticker identifiers if available
        if result.get("vol_surface", {}).get("focus_ticker"):
            focus_ticker = result["vol_surface"]["focus_ticker"]
            focus_instr = self.resolver.resolve_by_id(
                focus_ticker,
                IdentifierType.NORMALIZED_ID,
                source=None
            )
            if focus_instr:
                instruments["focus"] = {
                    "ticker": focus_ticker,
                    "normalized_id": focus_instr.normalized_id,
                    "identifiers": self._serialize_identifiers(focus_instr),
                }

        # Add index ticker identifiers if available
        if result.get("vol_surface", {}).get("index_ticker"):
            index_ticker = result["vol_surface"]["index_ticker"]
            index_instr = self.resolver.resolve_by_id(
                index_ticker,
                IdentifierType.NORMALIZED_ID,
                source=None
            )
            if index_instr:
                instruments["index"] = {
                    "ticker": index_ticker,
                    "normalized_id": index_instr.normalized_id,
                    "identifiers": self._serialize_identifiers(index_instr),
                }

        if instruments:
            result["_instruments"] = instruments

        return result

    def _serialize_identifiers(self, instrument) -> Dict[str, Any]:
        """Serialize an Instrument's identifiers for JSON output."""
        result = {}
        for ident_type, identifiers in self.resolver.get_all_identifiers(instrument).items():
            result[ident_type.value] = identifiers
        return result

    def resolve_ticker_to_identifiers(self, ticker: str, source: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Resolve a ticker to its known identifiers across sources.

        Useful for dashboard queries where a user provides a ticker but needs
        to look up data from multiple sources (DTCC, CME, OTC).

        Args:
            ticker: Ticker or symbol to resolve
            source: Optional source hint (e.g., "DTCC")

        Returns:
            Dict with normalized_id and identifier_map, or None if not found.
        """
        instrument = self.resolver.resolve_by_id(ticker, IdentifierType.NORMALIZED_ID, source)
        if not instrument:
            return None

        return {
            "ticker": ticker,
            "normalized_id": instrument.normalized_id,
            "identifiers": self._serialize_identifiers(instrument),
            "name": instrument.name,
            "instrument_type": instrument.instrument_type,
            "asset_class": instrument.asset_class,
        }


def get_resolver_from_args(instrument_resolver_name: Optional[str]) -> IdentifierResolver:
    """Get an IdentifierResolver instance by name.

    Called by volatility_suite.py to handle the --instrument-resolver flag.

    Args:
        instrument_resolver_name: Name of resolver (e.g., "default", "local", "custom")

    Returns:
        IdentifierResolver instance, or default if name not found.

    Raises:
        ValueError: If the specified resolver name doesn't exist.
    """
    if not instrument_resolver_name:
        return IdentifierResolverFactory.get_resolver("default") or LocalIdentifierResolver()

    resolver = IdentifierResolverFactory.get_resolver(instrument_resolver_name)
    if not resolver:
        available = IdentifierResolverFactory.list_resolvers()
        raise ValueError(
            f"Unknown instrument resolver: {instrument_resolver_name}. "
            f"Available: {', '.join(available)}"
        )

    return resolver


# Example usage in volatility_suite.py (conceptual; add to main()):
#
#   parser.add_argument(
#       "--instrument-resolver", default=None,
#       help="Name of IdentifierResolver to use for cross-source normalization "
#            "(default: 'default'). Resolvers: " + ", ".join(IdentifierResolverFactory.list_resolvers()))
#
#   args = parser.parse_args(argv)
#   resolver = get_resolver_from_args(args.instrument_resolver)
#   normalizer = Vol_SuiteInstrumentNormalizer(resolver)
#
#   # Later, after _run_core_analysis produces vol_result:
#   vol_result = normalizer.add_instrument_identifiers_to_result(vol_result)
#   vol_surface = normalizer.normalize_instrument_in_vol_surface(vol_result["vol_surface"])
