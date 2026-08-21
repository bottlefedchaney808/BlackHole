# Cross-Source UPI and Instrument Identifier Generalization

## Overview

This guide documents the instrument identifier generalization system that enables unified handling of financial instruments across multiple data sources (DTCC, CME, OTC). The system allows:

- **Normalized instrument representation** across sources
- **Bidirectional identifier mapping** (e.g., DTCC UPI ↔ CME contract code)
- **Multi-source queries and aggregation** via a unified schema
- **Extensible resolver architecture** for new data sources

## Architecture

### Core Modules

#### 1. `shared/identifiers.py` - Identifier Resolution

Central module providing:

- **`IdentifierType` enum**: Supported identifier types across sources
  - DTCC: `DTCC_UPI`, `DTCC_FISN`
  - CME: `CME_CONTRACT_CODE`, `CME_PRODUCT_ID`
  - OTC: `OTC_CUSIP`, `OTC_ISIN`
  - Generic: `CUSIP`, `ISIN`, `LEI`
  - Internal: `NORMALIZED_ID`

- **`InstrumentIdentifier` class**: Represents a single identifier from a source
  ```python
  identifier = InstrumentIdentifier(
      identifier_type=IdentifierType.DTCC_UPI,
      identifier_value="123ABC",
      source="DTCC",
      metadata={"tenor": "5Y", "asset_class": "interest_rate"}
  )
  ```

- **`Instrument` class**: Normalized instrument with multiple identifiers
  ```python
  instrument = Instrument(
      normalized_id="INSTR_001",
      name="5Y Interest Rate Swap",
      instrument_type="swap",
      asset_class="interest_rate",
      maturity_date="2031-07-29"
  )
  instrument.add_identifier(dtcc_identifier)
  instrument.add_identifier(cme_identifier)
  
  # Query by type
  upi = instrument.get_identifier(IdentifierType.DTCC_UPI)
  cme_code = instrument.get_identifier(IdentifierType.CME_CONTRACT_CODE)
  ```

- **`IdentifierResolver` interface**: Abstract base for identifier resolution
  - `resolve(identifier)`: Resolve a single identifier to Instrument
  - `resolve_by_id(value, type, source)`: Convenience method
  - `map_identifier(source, target_type)`: Cross-source mapping
  - `get_all_identifiers(instrument)`: Grouped retrieval by type

- **`LocalIdentifierResolver`**: Reference implementation
  - In-memory mapping store (suitable for development/testing)
  - Supports instrument merging for consolidation
  - `merge_instruments(primary_id, secondary_id)`: Consolidate identifiers

- **`IdentifierResolverFactory`**: Service locator for resolvers
  ```python
  resolver = IdentifierResolverFactory.get_resolver("default")
  available = IdentifierResolverFactory.list_resolvers()
  ```

#### 2. `adapters/cme_adapter.py` - CME Futures

Reference implementation for CME data:

- **Data source**: CME futures and swaps (SOFR, Euribor, Treasury, etc.)
- **Identifiers**: CME contract codes, product IDs
- **Integration**: Builds CME identifiers and resolves to Instruments
- **Mock mode**: Generates synthetic trades for testing

```python
from adapters.cme_adapter import CMEAdapter

adapter = CMEAdapter(mock_mode=True)
trades = adapter.fetch_trades(filters={"product_code": "SR3"})
instrument = adapter.resolve_instrument("SR3")
contract_info = adapter.get_contract_info("SR3")
```

#### 3. `adapters/otc_adapter.py` - OTC Swaps

Reference implementation for OTC swap data:

- **Data source**: OTC swaps (interest rate, currency, equity)
- **Identifiers**: CUSIP, ISIN
- **Additional data**: Counterparty LEI, collateral info
- **Integration**: Builds OTC identifiers and resolves to Instruments
- **Mock mode**: Generates synthetic trades for testing

```python
from adapters.otc_adapter import OTCAdapter

adapter = OTCAdapter(mock_mode=True)
trades = adapter.fetch_trades(filters={"cusip": "037833100"})
instrument = adapter.resolve_instrument("037833100")
swap_info = adapter.get_swap_info("037833100")
adapter.set_collateral_data("037833100", "cash", 50_000_000.0, "USD")
```

### Database Schema Changes

#### Migration: `003_add_instrument_identifiers.sql`

Adds columns to `swap_trades` table for cross-source tracking:

```sql
ALTER TABLE swap_trades ADD COLUMN source_identifier TEXT;
ALTER TABLE swap_trades ADD COLUMN source_identifier_type TEXT;
ALTER TABLE swap_trades ADD COLUMN normalized_instrument_id TEXT;
ALTER TABLE swap_trades ADD COLUMN instrument_type TEXT;

CREATE INDEX idx_swap_trades_source_identifier ON swap_trades(source_identifier, source_identifier_type);
CREATE INDEX idx_swap_trades_normalized_instrument_id ON swap_trades(normalized_instrument_id);
CREATE INDEX idx_swap_trades_instrument_type ON swap_trades(instrument_type);
```

This enables:
- **`source_identifier`**: Non-DTCC identifier (CME code, OTC CUSIP)
- **`source_identifier_type`**: Type of source identifier
- **`normalized_instrument_id`**: Cross-source unique instrument ID
- **`instrument_type`**: Standardized instrument category

Existing `upi` column remains as DTCC primary identifier.

## Usage Patterns

### 1. Resolve a Single Identifier

```python
from shared.identifiers import IdentifierType, LocalIdentifierResolver, InstrumentIdentifier

resolver = LocalIdentifierResolver()

# Resolve DTCC UPI
dtcc_ident = InstrumentIdentifier(
    identifier_type=IdentifierType.DTCC_UPI,
    identifier_value="123ABC",
    source="DTCC"
)
instrument = resolver.resolve(dtcc_ident)
print(f"Normalized ID: {instrument.normalized_id}")
```

### 2. Map Between Identifier Types

```python
# Add CME mapping to the same instrument
cme_ident = InstrumentIdentifier(
    identifier_type=IdentifierType.CME_CONTRACT_CODE,
    identifier_value="SR3",
    source="CME"
)
instrument.add_identifier(cme_ident)
resolver.add_mapping(instrument.normalized_id, cme_ident)

# Map from DTCC to CME
cme_code = resolver.map_identifier(dtcc_ident, IdentifierType.CME_CONTRACT_CODE)
print(f"CME Code: {cme_code}")  # Output: SR3
```

### 3. Cross-Source Queries

```python
import sqlite3

conn = sqlite3.connect("swaps.db")

# Query trades grouped by normalized instrument ID
query = """
    SELECT
        normalized_instrument_id,
        COUNT(*) as trade_count,
        data_source,
        instrument_type
    FROM swap_trades
    WHERE normalized_instrument_id IS NOT NULL
    GROUP BY normalized_instrument_id, data_source
    ORDER BY trade_count DESC;
"""

for row in conn.execute(query):
    norm_id, count, source, instr_type = row
    print(f"{norm_id}: {count} trades from {source} (type: {instr_type})")
```

### 4. Using CME and OTC Adapters

```python
from adapters.cme_adapter import CMEAdapter
from adapters.otc_adapter import OTCAdapter
from shared.data_source import TradeRecord

# Fetch CME futures
cme = CMEAdapter(mock_mode=True)
cme_trades = cme.fetch_trades(filters={"product_code": "SR3"})

# Fetch OTC swaps
otc = OTCAdapter(mock_mode=True)
otc_trades = otc.fetch_trades(filters={"swap_type": "interest_rate"})

# Both return TradeRecord objects compatible with db_loader
from db_loader import SwapsLoader

loader = SwapsLoader()

# Load CME trades
loader.upsert_trades([t.to_dict() for t in cme_trades], data_source=cme)

# Load OTC trades
loader.upsert_trades([t.to_dict() for t in otc_trades], data_source=otc)
```

### 5. Vol_Suite Integration

The `Vol_Suite/instrument_resolver.py` module provides integration with Vol_Suite:

```python
from Vol_Suite.instrument_resolver import (
    Vol_SuiteInstrumentNormalizer,
    get_resolver_from_args
)

# In Vol_Suite main():
parser.add_argument(
    "--instrument-resolver", default=None,
    help="Name of IdentifierResolver for cross-source normalization"
)
args = parser.parse_args(argv)
resolver = get_resolver_from_args(args.instrument_resolver)
normalizer = Vol_SuiteInstrumentNormalizer(resolver)

# Later, after vol_result is produced:
vol_result = normalizer.add_instrument_identifiers_to_result(vol_result)
```

The normalizer adds an `_instruments` section to `vol_result.json`:

```json
{
  "vol_surface": {...},
  "_instruments": {
    "focus": {
      "ticker": "GOOG",
      "normalized_id": "DTCC_UPI_XYZ",
      "identifiers": {
        "DTCC_UPI": ["XYZ"],
        "CME_CONTRACT_CODE": ["SR3"]
      }
    },
    "index": {...}
  }
}
```

## Testing

Run the comprehensive test suite:

```bash
pytest tests/test_identifiers.py -v
```

Tests cover:
- Identifier creation and validation
- Instrument construction and queries
- Resolver operations (resolve, map, merge)
- CME adapter mock trades
- OTC adapter mock trades
- Cross-source mapping scenarios
- Factory and registry patterns

Example test scenarios:
- Mapping DTCC UPI to CME contract code
- Creating instruments with multiple identifiers from different sources
- Merging duplicate instruments
- Fetching filtered trades from CME and OTC

## Production Implementation

For production deployments:

### 1. Implement Custom Resolvers

Extend `IdentifierResolver` for production data sources:

```python
class DatabaseIdentifierResolver(IdentifierResolver):
    """Production resolver backed by a real database."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def resolve(self, identifier: InstrumentIdentifier) -> Optional[Instrument]:
        # Query database for identifier and related mappings
        pass
    
    def map_identifier(self, source, target_type):
        # Query cross-reference table
        pass
```

### 2. Implement Real Data Adapters

Update CME and OTC adapters to call real APIs:

```python
class CMEAdapter(DataSourceAdapter):
    def fetch_trades(self, date_range, filters):
        # Replace mock_mode with real CME DataMine API calls
        # Handle authentication, pagination, rate-limiting
        pass
```

### 3. Populate Cross-Reference Data

Load identifier mappings from reference data services:

```python
resolver = DatabaseIdentifierResolver("swaps.db")

# Load DTCC ↔ CME mappings from Bloomberg/Refinitiv
mappings = load_reference_data("dtcc_cme_mapping.csv")
for dtcc_upi, cme_code in mappings:
    dtcc_ident = InstrumentIdentifier(
        IdentifierType.DTCC_UPI, dtcc_upi, "DTCC"
    )
    instrument = resolver.resolve(dtcc_ident)
    cme_ident = InstrumentIdentifier(
        IdentifierType.CME_CONTRACT_CODE, cme_code, "CME"
    )
    instrument.add_identifier(cme_ident)
    resolver.persist(instrument)
```

### 4. Dashboard Integration

Enable the dashboard to accept multiple identifier types:

```python
# dashboard/app.py
@app.get("/instruments/{identifier_value}")
def get_instrument(identifier_value: str, identifier_type: str = "DTCC_UPI"):
    resolver = IdentifierResolverFactory.get_resolver("default")
    ident_type = IdentifierType[identifier_type]
    instrument = resolver.resolve_by_id(identifier_value, ident_type)
    
    if not instrument:
        return {"error": "Instrument not found"}
    
    return {
        "normalized_id": instrument.normalized_id,
        "identifiers": resolver.get_all_identifiers(instrument),
        "metadata": instrument.metadata
    }
```

## Extensibility

### Adding a New Data Source

1. Create adapter in `adapters/new_source_adapter.py`:
   ```python
   class NewSourceAdapter(DataSourceAdapter):
       def fetch_trades(self, date_range, filters):
           # Fetch from new source
           pass
   ```

2. Define new IdentifierType if needed:
   ```python
   class IdentifierType(Enum):
       NEW_SOURCE_ID = "NEW_SOURCE_ID"
   ```

3. Create resolver that understands the new source:
   ```python
   resolver = LocalIdentifierResolver()
   IdentifierResolverFactory.register_resolver("new_source", resolver)
   ```

4. Update tests to cover the new adapter.

### Custom Resolver Implementation

1. Extend `IdentifierResolver`:
   ```python
   class CustomResolver(IdentifierResolver):
       def resolve(self, identifier):
           # Custom resolution logic
           pass
   ```

2. Register with factory:
   ```python
   IdentifierResolverFactory.register_resolver("custom", CustomResolver())
   ```

3. Use in Vol_Suite or adapters:
   ```python
   resolver = IdentifierResolverFactory.get_resolver("custom")
   ```

## Migration Path

For existing deployments with DTCC-only data:

1. Apply `003_add_instrument_identifiers.sql` migration
   ```bash
   python setup_db.py --migrate
   ```

2. Existing DTCC trades remain unchanged; new columns are NULL
   ```sql
   SELECT COUNT(*) FROM swap_trades WHERE source_identifier IS NULL;  -- existing trades
   ```

3. Add CME/OTC adapters to `db_loader.py` ingestion pipeline
   ```python
   loaders = [DTCCAdapter(), CMEAdapter(), OTCAdapter()]
   ```

4. Populate `normalized_instrument_id` via batch resolver:
   ```python
   resolver = DatabaseIdentifierResolver(db_path)
   
   for trade_row in conn.execute("SELECT * FROM swap_trades WHERE normalized_instrument_id IS NULL"):
       # Determine source from data_source column
       # Resolve via appropriate identifier type
       # Update row with normalized_instrument_id
   ```

5. Update Vol_Suite to use resolver (backward compatible; defaults to None)
   ```bash
   python Vol_Suite/volatility_suite.py --instrument-resolver default
   ```

## API Reference

See `shared/identifiers.py` for complete documentation of:
- `IdentifierType` enum values
- `InstrumentIdentifier` construction and methods
- `Instrument` queries and metadata
- `IdentifierResolver` interface contract
- `LocalIdentifierResolver` implementation details
- `IdentifierResolverFactory` registry

## Summary

The cross-source UPI generalization system provides:

✓ **Unified instrument representation** across DTCC, CME, OTC  
✓ **Extensible resolver architecture** for custom implementations  
✓ **Database schema** supporting multi-source queries  
✓ **Reference adapters** for CME and OTC data  
✓ **Vol_Suite integration** with normalized identifiers  
✓ **Comprehensive test coverage** for validation  

This enables dashboards and analytics to seamlessly query instruments regardless of source.
