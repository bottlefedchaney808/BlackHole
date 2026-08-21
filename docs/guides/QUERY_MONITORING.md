# Query Performance Monitoring System

## Overview

The query monitoring system provides real-time visibility into database query performance with automatic detection of slow queries, EXPLAIN QUERY PLAN analysis, and performance optimization recommendations.

## Features

### 1. **Automatic Query Timing**
- Captures execution time for all monitored queries
- Threshold-based detection (default 1s)
- Structured logging with query context

### 2. **Query Fingerprinting**
- Normalizes similar queries to group them together
- Removes specific values (strings, numbers) to identify patterns
- Enables trend analysis across repeated queries

### 3. **EXPLAIN QUERY PLAN Analysis**
- Automatic EXPLAIN analysis for slow queries
- Identifies full table scans vs. index scans
- Detects inefficient query patterns

### 4. **Performance Metrics**
- Per-query statistics: count, avg/min/max duration, total rows
- Aggregated performance summary
- Query history (last 100 slow queries)

### 5. **Index Suggestions**
- Recommends indexes based on full table scans
- Suggests composite indexes for complex queries
- Performance health dashboard

## Architecture

### Core Components

```
shared/query_monitor.py
├── QueryMonitor: Main monitoring class
├── SlowQueryRecord: Data structure for slow queries
├── QueryPlanNode: Parsed EXPLAIN output
├── fingerprint_query(): Normalize and hash queries
├── extract_table_names(): Parse table references
├── parse_explain_query_plan(): Parse EXPLAIN output
├── analyze_query_plan(): Identify performance issues
└── monitor_query(): Decorator for automatic monitoring
```

### Integration Points

**swaps_query.py**
- All query methods instrumented with monitoring
- Manual timing and EXPLAIN capture
- Error tracking for failed queries

**dashboard/app.py**
- GET `/metrics/queries` - Top 10 slowest queries
- GET `/metrics/slow-queries` - Last 100 slow query records
- GET `/metrics/health` - Performance health summary

## Usage

### Basic Usage

```python
from shared.query_monitor import get_query_monitor

# Get monitor instance
monitor = get_query_monitor()

# Access slow query history
slow_queries = monitor.get_slow_queries(limit=50)
for query in slow_queries:
    print(f"{query['query_fingerprint']}: {query['duration_sec']:.2f}s")

# Get performance statistics
stats = monitor.get_top_slow_queries(limit=10)
for stat in stats:
    print(f"{stat['query_example']}: avg {stat['avg_duration_sec']:.2f}s")

# Get health status
health = monitor.get_query_stats()
```

### Query Monitoring in SwapsQuery

```python
from swaps_query import SwapsQuery

q = SwapsQuery()

# All queries are automatically monitored
results = q.query_by_upi('ABC123', days_back=30)
summary = q.get_upi_summary('ABC123')
top_products = q.top_notional_products()
```

### Dashboard API

**Get top slowest queries:**
```bash
curl http://localhost:8000/metrics/queries?limit=10
```

Response:
```json
{
  "top_slow_queries": [
    {
      "fingerprint": "abc123def45",
      "query_example": "SELECT * FROM swap_trades WHERE upi = ?",
      "avg_duration_sec": 2.345,
      "max_duration_sec": 5.123,
      "execution_count": 45,
      "total_rows": 123456,
      "last_execution": "2024-01-15T10:30:00Z"
    }
  ],
  "count": 10
}
```

**Get recent slow query executions:**
```bash
curl http://localhost:8000/metrics/slow-queries?limit=100
```

Response:
```json
{
  "slow_queries": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "query_fingerprint": "abc123def45",
      "query_text": "SELECT * FROM swap_trades WHERE upi = ?",
      "duration_sec": 2.345,
      "row_count": 5000,
      "tables": ["swap_trades"],
      "plan_analysis": {
        "full_scans": 1,
        "index_scans": 0,
        "issues": ["Full table scan on swap_trades"],
        "suggestions": ["Create indexes on columns used in WHERE clauses"]
      }
    }
  ],
  "count": 100,
  "threshold_sec": 1.0
}
```

**Get performance health summary:**
```bash
curl http://localhost:8000/metrics/health
```

Response:
```json
{
  "status": "healthy",
  "total_slow_queries": 12,
  "avg_duration_sec": 1.234,
  "max_duration_sec": 5.123,
  "full_table_scans": 2,
  "alerts": [
    "Query exceeding 5s detected (max 5.123s)"
  ],
  "index_suggestions": [
    "Create indexes on frequently scanned columns (WHERE clauses)"
  ],
  "threshold_sec": 1.0
}
```

## Configuration

### Adjust Slow Query Threshold

```python
from shared.query_monitor import get_query_monitor

monitor = get_query_monitor()
# Note: Threshold is set at monitor creation time, not changeable after
# To use a different threshold, create a new monitor:
from shared.query_monitor import QueryMonitor
custom_monitor = QueryMonitor(slow_query_threshold_sec=2.0)
```

### Limit History Size

```python
from shared.query_monitor import QueryMonitor

# Keep only 50 most recent slow queries
monitor = QueryMonitor(max_history=50)
```

## Query Plan Analysis

The system automatically captures and analyzes EXPLAIN QUERY PLAN output for slow queries:

### Example Analysis

For a query like:
```sql
SELECT * FROM swap_trades WHERE upi = ?
```

The system will:
1. Execute `EXPLAIN QUERY PLAN SELECT * FROM swap_trades WHERE upi = ?`
2. Parse the output to identify:
   - Full table scans (inefficient)
   - Index scans (efficient)
   - Query plan steps

3. Generate suggestions:
   - "Create index on `upi` column"
   - "Use indexed column for filtering"

### Interpreting Query Plans

**Full Scan (Inefficient):**
```
SCAN TABLE swap_trades
```
- Table is scanned sequentially
- Recommendation: Create index on WHERE clause columns

**Index Seek (Efficient):**
```
SEEK INDEX idx_upi
```
- Index is used for fast lookup
- No additional optimization needed

## Monitoring Workflow

### For Developers

1. **Enable monitoring** - Automatically applied to all SwapsQuery methods
2. **Run your application** - Queries are tracked as they execute
3. **Review dashboard** - Check `/metrics/health` for performance status
4. **Identify slow queries** - Use `/metrics/queries` to find problems
5. **Optimize** - Add indexes based on suggestions
6. **Verify** - Check that duration decreases after optimization

### For Database Administrators

1. **Monitor health** - Set up alerts for status != "healthy"
2. **Review suggestions** - Check `/metrics/health` for index recommendations
3. **Implement indexes** - Create suggested indexes
4. **Validate** - Re-run analysis to confirm improvements
5. **Track trends** - Monitor average query durations over time

## Performance Impact

The monitoring system is designed to be low-overhead:

- **Query fingerprinting**: ~1ms per query (regex normalization)
- **EXPLAIN analysis**: ~5-10ms per slow query (cached in history)
- **Memory usage**: ~1KB per slow query record (last 100 = 100KB)
- **Logging**: Structured JSON output, minimal I/O overhead

## Troubleshooting

### Queries Not Being Tracked

- Check that SwapsQuery methods are being called
- Verify query duration exceeds threshold (1s default)
- Monitor.get_slow_queries() returns empty list if no slow queries

### EXPLAIN Analysis Fails

- Monitored in logs as warnings
- Continues query execution despite EXPLAIN failure
- Check database permissions for EXPLAIN

### High Memory Usage

- Reduce max_history size: `QueryMonitor(max_history=50)`
- Clear history periodically: `monitor.reset()`

## Testing

Run the test suite:

```bash
python -m pytest test_query_monitor.py -v
```

Tests cover:
- Query fingerprinting and normalization
- Table/column extraction
- EXPLAIN QUERY PLAN parsing
- Slow query detection and recording
- Performance metrics aggregation
- Dashboard endpoints
- Performance accuracy

## Future Enhancements

Potential improvements:

1. **Persistent Storage**: Save slow queries to database for historical analysis
2. **Alerting**: Send notifications for performance degradation
3. **Automated Indexing**: Recommend and optionally create indexes
4. **Query Optimization**: Suggest query rewrites
5. **Cost Estimation**: Estimate index cost vs. query performance benefit
6. **Distributed Tracing**: Track queries across multiple systems
7. **Machine Learning**: Detect anomalies in query performance

## Related Files

- `shared/query_monitor.py` - Core monitoring implementation
- `swaps_query.py` - Query methods with monitoring integration
- `dashboard/app.py` - Performance monitoring API endpoints
- `test_query_monitor.py` - Comprehensive test suite
- `shared/logging.py` - Structured logging foundation

## References

- [SQLite EXPLAIN QUERY PLAN](https://www.sqlite.org/eqp.html)
- [Database Index Best Practices](https://use-the-index-luke.com/)
- [Query Optimization](https://en.wikipedia.org/wiki/Query_optimization)
