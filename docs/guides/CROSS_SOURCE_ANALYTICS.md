# Cross-Source Analytics

This system enables unified analytics over swap trades from multiple data sources (DTCC, CME, OTC, etc.).

## Architecture

### Components

1. **Data Source Adapters** (`adapters/`)
   - Pluggable modules implementing `shared/data_source.py::DataSourceAdapter`
   - Each adapter is responsible for:
     - Authentication to its data source (API keys, credentials)
     - Fetching raw trades over a date range
     - Normalizing trades to the standard `swap_trades` schema
     - Handling errors and retries

2. **Multi-Source Orchestrator** (`orchestrator.py`)
   - `discover_adapters()`: Read enabled sources from `DATA_SOURCES` env var
   - `run_unified_sources()`: Parallel ingest from all sources, aggregate, then run unified suite

3. **Unified Query Builder** (`shared/query_builder.py`)
   - `CrossSourceQueryBuilder`: Query interface abstracting `data_source` column
   - Supports:
     - Filtering trades by source(s)
     - Aggregations (SUM, COUNT) across sources
     - Time-series queries with per-source breakdown
     - Instrument resolution across sources

4. **Dashboard Endpoints** (`dashboard/app.py`)
   - `/trades?source=DTCC,CME`: Get trades filtered by source
   - `/instruments/{upi}?resolve_cross_source=true`: Find UPI across sources
   - `/analytics/cross-source-notional?source=...`: Aggregate notional by source
   - `/analytics/timeseries?source=...`: Daily time-series per source

5. **Vol_Suite Context Extension** (`Vol_Suite/suite_context.py`)
   - Added `data_sources` field to suite context
   - Vol_Suite and child suites can read `context.data_sources` to filter dealer positioning by source

### Database Schema

The existing `swap_trades` table includes a `data_source` column (added in `db_loader.py`):

```
swap_trades:
  dissemination_id (PK)
  data_source TEXT  <- indicates source: DTCC, CME, OTC, etc.
  ... other columns (unchanged)
```

No schema migration needed; the column already exists.

## Setup

### 1. Enable Data Sources

Set the `DATA_SOURCES` environment variable to a comma-separated list:

```bash
# Single source (default)
export DATA_SOURCES=DTCC

# Multiple sources
export DATA_SOURCES=DTCC,CME,OTC
```

### 2. Implement Adapters

Create adapter modules in `adapters/` following the pattern in `adapters/cme_adapter.py`:

```python
from shared.data_source import DataSourceAdapter, TradeRecord

class CMEAdapter(DataSourceAdapter):
    def get_name(self) -> str:
        return "CME"
    
    def get_schema_version(self) -> int:
        return 1
    
    def fetch_trades(self, date_range=None, filters=None):
        # 1. Fetch raw trades from CME API
        # 2. Normalize to swap_trades schema
        # 3. Return list of TradeRecord objects
        pass
```

See `adapters/cme_adapter.py` and `adapters/otc_adapter.py` for complete templates.

### 3. Deploy

Follow the deployment guide in `DEPLOY.md` under "Multi-source analytics".

## Usage

### Command Line

Run a unified analysis over multiple sources:

```bash
# Orchestrator discovers sources from DATA_SOURCES env var
DATA_SOURCES=DTCC,CME python orchestrator.py \
    --unified --ticker MSFT --target-years 0.25

# Or use run_unified_sources() from Python:
from orchestrator import run_unified_sources

result = run_unified_sources(
    tickers=['MSFT', 'NVDA'],
    target_years=0.25,
    sources=['DTCC', 'CME'],  # Override env var
)

print(f"Total trades: {result['total_trades_ingested']}")
print(f"By source: {result['trades_by_source']}")
print(f"Unified result: {result['unified_result']['status']}")
```

### Dashboard

Once the dashboard is running:

```bash
# Get trades from DTCC and CME
curl 'http://localhost:8000/trades?source=DTCC,CME&days_back=30'

# Find a UPI across all sources
curl 'http://localhost:8000/instruments/MSFT_EQ_001?resolve_cross_source=true'

# Aggregate notional across sources
curl 'http://localhost:8000/analytics/cross-source-notional?source=DTCC,CME'

# Get time-series per source
curl 'http://localhost:8000/analytics/timeseries?source=DTCC,CME&days_back=90'
```

### Python API

Use `CrossSourceQueryBuilder` for custom queries:

```python
from shared.query_builder import CrossSourceQueryBuilder

builder = CrossSourceQueryBuilder('/path/to/swaps.db')

# Get all trades from DTCC and CME over the last 30 days
trades = builder.query_by_sources(['DTCC', 'CME'], days_back=30)

# Aggregate notional by source
by_source = builder.aggregate_notional_by_source(['DTCC', 'CME'])
total = builder.aggregate_notional_cross_source(['DTCC', 'CME'])

# Time-series query
timeseries = builder.timeseries_by_source(['DTCC', 'CME'], start_date=..., end_date=...)

# Resolve a UPI across sources
msft_trades = builder.resolve_instrument_across_sources('MSFT_EQ_001')
```

## Testing

Run the test suite:

```bash
python -m pytest tests/test_cross_source.py -v

# Specific test
python -m pytest tests/test_cross_source.py::TestMultiSourceBackfill::test_ingest_from_mock_adapters -v
```

Tests include:
- Multi-source data backfill
- Cross-source aggregations
- Dashboard endpoint filtering
- Vol_Suite context with sources

## Adding a New Source

### Step 1: Create Adapter Module

File: `adapters/<source_lower>_adapter.py`

```python
from shared.data_source import DataSourceAdapter, TradeRecord

class MySourceAdapter(DataSourceAdapter):
    def get_name(self) -> str:
        return "MYSOURCE"
    
    def get_schema_version(self) -> int:
        return 1
    
    def fetch_trades(self, date_range=None, filters=None):
        # Implementation here
        trades = [...]  # List of TradeRecord
        return trades
```

### Step 2: Enable in Environment

```bash
export DATA_SOURCES=DTCC,MYSOURCE
```

### Step 3: Test

The orchestrator will automatically discover and use the adapter:

```bash
python orchestrator.py --unified --ticker MSFT --target-years 0.25
```

## Monitoring

### Adapter Health

Check which sources are available and enabled:

```bash
curl http://localhost:8000/health
# Returns: "available_data_sources": ["DTCC", "CME"]
```

### Ingestion Stats

Query cross-source summary:

```python
from shared.query_builder import get_cross_source_summary

summary = get_cross_source_summary(
    '/path/to/swaps.db',
    sources=['DTCC', 'CME'],
    days_back=30
)

print(f"Total notional: ${summary['total_notional']:,.0f}")
print(f"By source: {summary['notional_by_source']}")
print(f"Trade counts: {summary['trade_counts_by_source']}")
```

## Troubleshooting

### Adapter Not Discovered

1. Check `DATA_SOURCES` env var is set
2. Verify adapter file exists: `adapters/<source_lower>_adapter.py`
3. Verify class name matches: `<Source>Adapter` (e.g., `CMEAdapter`)
4. Check logs for import errors

### No Trades Ingested

1. Verify adapter's `fetch_trades()` is being called
2. Check adapter returns `List[TradeRecord]`
3. Verify trades are being normalized to swap_trades schema
4. Check database for `data_source` values

### Dashboard Endpoints Return Empty

1. Verify trades exist in database: `SELECT COUNT(*) FROM swap_trades;`
2. Verify `data_source` column has values: `SELECT DISTINCT data_source FROM swap_trades;`
3. Check query filters: `?source=DTCC,CME` should match values in database
4. Check date range: `?days_back=30` might exclude older trades

## Performance Notes

- **Parallel Ingestion**: Sources are ingested sequentially in `run_unified_sources()` for simplicity; can be parallelized with threading/multiprocessing
- **Query Aggregations**: Use database indexes on `data_source`, `effective_date`, and `upi` for better performance
- **Time-Series**: Pre-aggregate daily data if querying frequently over long periods

## Future Enhancements

- Parallel adapter ingestion (threading/multiprocessing)
- Adapter health checks and monitoring
- Source-specific error recovery strategies
- Data reconciliation between sources (duplicate detection)
- Per-source dealer positioning in Vol_Suite
- Cross-source gamma record aggregation
