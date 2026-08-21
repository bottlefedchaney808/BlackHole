# Production-Grade Structured Logging Guide

## Overview

The logging system provides production-ready observability for the FinancialDevelopment platform with:

- **Structured JSON Logging**: All logs are emitted as JSON for container-friendly aggregation
- **Metrics Collection**: Track request latency, query execution time, upsert batches, error rates, and cache hit rates
- **Operation Tracking**: Automatic duration measurement and context tracking for operations
- **Exception Handling**: Structured error logging with full stack traces and context
- **Container-Ready**: JSON output to stdout with optional file rotation (daily or 100MB)

## Architecture

### Components

1. **JSONFormatter** (`shared/logging.py`)
   - Formats all log records as JSON
   - Includes timestamp, level, module, message, and context
   - Captures exception info with full traceback

2. **MetricsCollector** (`shared/logging.py`)
   - Thread-safe metrics collection
   - Tracks: request latency (p50, p95, p99), query execution time by type, upsert batch size/duration, error rates, cache hit rates

3. **LogContext** (`shared/logging.py`)
   - Context manager for operation tracking
   - Automatically measures operation duration
   - Tracks rows affected and operation status
   - Logs start and completion with context

4. **setup_logging()** (`shared/logging.py`)
   - Initializes logger with JSON formatter
   - Optional file rotation (daily or 100MB)
   - Returns configured logger instance

## Usage

### Basic Setup

```python
from shared.logging import setup_logging, get_metrics, log_operation, LogContext

# Initialize logger (typically done at module level)
logger = setup_logging(
    name='my_module',
    level=logging.INFO,
    use_json=True,
)

# Log a message
logger.info("Processing started", extra={
    'batch_id': '12345',
    'source': 'DTCC',
})
```

### Operation Tracking with LogContext

```python
# Track an operation with automatic duration measurement
with LogContext('batch_process', metadata={'total_batches': 100}) as ctx:
    # Do work
    process_batch()
    
    # Record rows affected
    ctx.rows_affected = 1000
```

### Log Operation Context Manager

```python
# Simpler context manager for common cases
with log_operation('query_execution', metadata={'query_type': 'upsert'}):
    result = execute_query()
```

### Metrics Collection

```python
from shared.logging import get_metrics

metrics = get_metrics()

# Record request latency
start = time.time()
# ... do work ...
duration = time.time() - start
metrics.add_request_duration(duration)

# Record query execution time by type
metrics.add_query_execution('select', duration_sec)
metrics.add_query_execution('upsert', duration_sec)

# Track upsert batches
metrics.add_upsert_batch(batch_size=1000, duration=2.5)

# Count errors
metrics.add_error('ValueError')

# Cache statistics
metrics.add_cache_hit()
metrics.add_cache_miss()

# Export metrics
stats = metrics.to_dict()
# Returns dict with percentiles, query stats by type, upsert stats, error counts, cache rate
```

## Integration in Codebase

### orchestrator.py

```python
from shared.logging import setup_logging, log_operation

logger = setup_logging('orchestrator', level=logging.INFO, use_json=True)

# Logs operation start/end with duration
with log_operation('run_suite', metadata={'suite': name}):
    result = run_suite(name, context)
```

**Metrics Recorded:**
- Suite execution duration (p50, p95, p99)
- Suite results tracking

### backfill.py

```python
from shared.logging import log_operation, get_metrics

# Each entry processed records metrics
with log_operation('backfill_entry', metadata={'regulator': reg, 'asset': asset}):
    records = parse_swap_zip(...)
    result = loader.upsert_trades(records)
    metrics = get_metrics()
    metrics.add_upsert_batch(len(records), duration)
```

**Metrics Recorded:**
- Batch processing duration
- Records upserted per batch
- Regulator/asset breakdown

### db_loader.py

```python
from shared.logging import log_operation, get_metrics

# Upsert operations tracked
with log_operation('upsert_trades', metadata={'batch_size': len(records)}):
    result = upsert_trades(records)
    metrics = get_metrics()
    metrics.add_upsert_batch(len(records), duration)
```

**Metrics Recorded:**
- Upsert batch size and duration
- Transaction atomicity confirmation
- Conflict resolution counts

### swaps_query.py

```python
from shared.logging import get_metrics

# Query execution tracked
start = time.time()
result = execute_query()
duration = time.time() - start
metrics = get_metrics()
metrics.add_query_execution('query_by_upi', duration)

logger.info("Query completed", extra={
    'upi': upi,
    'result_count': len(result),
    'duration_sec': duration,
})
```

**Metrics Recorded:**
- Query execution time by type
- Result set sizes
- Query failures with error type

### dashboard/app.py

```python
from shared.logging import setup_logging, get_metrics

logger = setup_logging('dashboard', level=logging.INFO, use_json=True)

# Request/response logging
logger.info("Request received", extra={
    'endpoint': path,
    'method': method,
})

# Rate limit hits
logger.warning("Rate limit exceeded", extra={
    'client_ip': ip,
    'limit': '60/minute',
})
```

**Metrics Recorded:**
- Request latency by endpoint
- Auth events
- Rate limit violations

## JSON Log Output Format

### Standard Log Entry

```json
{
  "timestamp": "2026-07-30T12:34:56.789012+00:00",
  "level": "INFO",
  "logger": "orchestrator",
  "message": "Operation suite_run started",
  "module": "orchestrator",
  "line": 567,
  "function": "run_suite",
  "operation_id": "abc123",
  "operation_type": "suite_run",
  "metadata": {
    "suite": "vol",
    "timeout": 1800
  }
}
```

### Operation Completion Log

```json
{
  "timestamp": "2026-07-30T12:35:12.456789+00:00",
  "level": "INFO",
  "logger": "orchestrator",
  "message": "Operation suite_run completed in 15.67s",
  "operation_id": "abc123",
  "operation_type": "suite_run",
  "duration_sec": 15.67,
  "status": "completed",
  "rows_affected": 1000
}
```

### Error Log with Exception

```json
{
  "timestamp": "2026-07-30T12:35:15.123456+00:00",
  "level": "ERROR",
  "logger": "db_loader",
  "message": "Upsert failed: database locked",
  "exception": {
    "type": "sqlite3.OperationalError",
    "message": "database is locked",
    "traceback": ["Traceback (most recent call last):", "  File...", "..."]
  },
  "batch_size": 1000,
  "error_type": "OperationalError"
}
```

## Metrics Export Format

```python
metrics.to_dict()  # Returns:
{
  "request_latency": {
    "count": 250,
    "p50_sec": 0.045,
    "p95_sec": 0.185,
    "p99_sec": 0.450,
  },
  "query_execution": {
    "upsert": {
      "count": 100,
      "avg": 1.23,
      "min": 0.5,
      "max": 3.2,
    },
    "select": {
      "count": 150,
      "avg": 0.34,
      "min": 0.1,
      "max": 1.5,
    },
  },
  "upsert_batches": {
    "count": 25,
    "batches": [[1000, 2.5], [800, 1.9], ...],
  },
  "error_rates": {
    "ValueError": 3,
    "KeyError": 1,
    "OperationalError": 5,
  },
  "cache": {
    "hits": 8000,
    "misses": 2000,
    "hit_rate": 0.8,
  },
}
```

## Configuration

### Log Level

```python
logger = setup_logging(
    name='my_module',
    level=logging.DEBUG,  # INFO, WARNING, ERROR, CRITICAL
)
```

### File Output with Rotation

```python
# Daily rotation (keeps 7 days)
logger = setup_logging(
    name='my_module',
    log_file='/var/log/app.log',
    rotation_mode='daily',  # or 'size' for 100MB rotation
)

# Size-based rotation (100MB, keeps 5 backup files)
logger = setup_logging(
    name='my_module',
    log_file='/var/log/app.log',
    rotation_mode='size',
)
```

### Non-JSON Output

```python
# Plain text format for development
logger = setup_logging(
    name='my_module',
    use_json=False,
)
```

## Testing

All logging features are tested in `test_logging.py`:

```bash
python -m pytest test_logging.py -v

# 21 tests covering:
# - JSON format validity
# - Required field presence
# - Extra context preservation
# - Exception handling
# - Percentile calculations
# - Query execution metrics
# - Upsert batch tracking
# - Error rate tracking
# - Cache hit rate calculation
# - LogContext manager tracking
# - Logger setup and configuration
# - Full integration workflows
```

## Best Practices

1. **Initialize Early**: Set up logging at module import time
2. **Use Extra Context**: Pass relevant fields as `extra` dict for structured querying
3. **Track Operations**: Wrap important operations with `LogContext` or `log_operation`
4. **Record Metrics**: Call `get_metrics()` to record performance data
5. **Error Context**: Always log error type and relevant context before raising
6. **Container Ready**: JSON output integrates with ELK, Datadog, Splunk, CloudWatch

## Log Aggregation Integration

### CloudWatch (AWS)
```python
# JSON logs automatically parsed as structured events
logger = setup_logging(
    name='app',
    log_file='/dev/stdout',  # or write to agent
    use_json=True,
)
```

### ELK Stack
```python
# Each JSON log becomes a document
# Fields available for filtering and aggregation:
# - timestamp, level, logger, module, message
# - operation_id, operation_type, duration_sec
# - batch_size, query_type, error_type
# - rows_affected, status, metadata
```

### Datadog
```python
# JSON structured logs with tags/attributes
# Query examples:
# - @duration_sec:[0.5 TO 2.0]  # Request latency
# - @query_type:upsert @status:completed
# - @error_type:*  # All errors
```

## Performance Impact

- **Metrics Collection**: O(1) per event, thread-safe
- **JSON Serialization**: < 1ms per log entry
- **File I/O**: Asynchronous (buffered by logging module)
- **Memory**: ~100KB for typical metrics collection

## Troubleshooting

### Logs not appearing
- Check logger name matches module
- Verify logging level (default INFO)
- Check file permissions for log files

### High disk usage
- Use `rotation_mode='size'` for 100MB limit
- Configure `backupCount` in setup_logging if modifying source
- Archive old logs regularly

### Performance concerns
- Disable JSON in high-throughput scenarios: `use_json=False`
- Reduce logging verbosity if CPU-bound
- Batch metrics exports if necessary

## Future Enhancements

- Distributed tracing with trace IDs
- Log sampling for high-volume endpoints
- Custom filters for sensitive data
- Async log shipping to external services
- Metrics export to Prometheus
