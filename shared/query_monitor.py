"""Query performance monitoring with slow query detection and EXPLAIN analysis.

Features:
  * @monitor_query decorator for automatic query timing and logging
  * Configurable slow query threshold (default 1s)
  * EXPLAIN QUERY PLAN analysis for slow queries
  * Query fingerprinting to normalize and group similar queries
  * Slow query history tracking (in-memory with configurable max size)
  * Index suggestions based on EXPLAIN analysis
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from shared.logging import setup_logging

# Setup structured JSON logging
logger = setup_logging(
    name='query_monitor',
    level=logging.INFO,
    use_json=True,
)

F = TypeVar('F', bound=Callable[..., Any])


# ── Query Fingerprinting ───────────────────────────────────────────────────

def fingerprint_query(query: str) -> str:
    """Generate a fingerprint for a query by normalizing values.

    Replaces numeric literals and strings with placeholders to group similar
    queries together for trend analysis.

    Example:
        "SELECT * FROM swap_trades WHERE upi = 'ABC123'"
        -> fingerprint includes normalized version without specific value
    """
    # Normalize whitespace
    normalized = re.sub(r'\s+', ' ', query.strip())

    # Replace string literals with ?
    normalized = re.sub(r"'[^']*'", "'?'", normalized)

    # Replace numeric literals with ?
    normalized = re.sub(r'\b\d+\b', '?', normalized)

    # Create hash of normalized query
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def extract_table_names(query: str) -> List[str]:
    """Extract table names from a SQL query (simple regex approach)."""
    # Match FROM, JOIN, UPDATE, DELETE, INSERT INTO
    pattern = r'(?:FROM|JOIN|INTO|UPDATE|DELETE FROM)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
    matches = re.findall(pattern, query, re.IGNORECASE)
    return list(set([m.lower() for m in matches]))


def extract_columns_referenced(query: str) -> List[str]:
    """Extract column names from WHERE clauses (basic extraction)."""
    # Match column = ? or column > ? patterns in WHERE clause
    where_match = re.search(r'WHERE\s+(.+?)(?:GROUP|ORDER|LIMIT|$)', query, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return []

    where_clause = where_match.group(1)
    # Simple pattern: identifier = or > or <
    pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*[=><]'
    matches = re.findall(pattern, where_clause)
    return list(set([m.lower() for m in matches]))


# ── Query Plan Analysis ────────────────────────────────────────────────────

@dataclass
class QueryPlanNode:
    """Represents a node in SQLite's EXPLAIN QUERY PLAN output."""

    addr: int
    opcode: str
    p1: int
    p2: int
    p3: int
    p4: str
    p5: int
    comment: str

    def is_full_scan(self) -> bool:
        """Check if this node represents a full table scan."""
        return self.opcode in ('OpenRead', 'OpenWrite') and 'SCAN' in self.comment

    def is_index_scan(self) -> bool:
        """Check if this node represents an index scan."""
        return 'SEEK' in self.comment or 'INDEX' in self.comment


def parse_explain_query_plan(plan_output: List[Tuple]) -> List[QueryPlanNode]:
    """Parse EXPLAIN QUERY PLAN output into structured nodes."""
    nodes = []
    for row in plan_output:
        # SQLite EXPLAIN output: addr, opcode, p1, p2, p3, p4, p5, comment
        if len(row) >= 8:
            node = QueryPlanNode(
                addr=row[0],
                opcode=row[1],
                p1=row[2],
                p2=row[3],
                p3=row[4],
                p4=row[5] or '',
                p5=row[6],
                comment=row[7] or '',
            )
            nodes.append(node)
    return nodes


def analyze_query_plan(plan_nodes: List[QueryPlanNode]) -> Dict[str, Any]:
    """Analyze query plan for performance issues.

    Returns dict with:
      - full_scans: count of full table scans
      - index_scans: count of index scans
      - issues: list of performance issues found
      - suggestions: list of optimization suggestions
    """
    analysis = {
        'full_scans': 0,
        'index_scans': 0,
        'issues': [],
        'suggestions': [],
    }

    for node in plan_nodes:
        if node.is_full_scan():
            analysis['full_scans'] += 1
            # Extract table name from comment
            if 'TABLE' in node.comment:
                table_match = re.search(r'TABLE\s+(\w+)', node.comment)
                if table_match:
                    table = table_match.group(1)
                    analysis['issues'].append(
                        f'Full table scan on {table}'
                    )

        if node.is_index_scan():
            analysis['index_scans'] += 1

    # Generate suggestions based on issues
    if analysis['full_scans'] > 0:
        analysis['suggestions'].append(
            'Consider creating indexes on columns used in WHERE clauses'
        )

    if analysis['index_scans'] == 0 and analysis['full_scans'] > 0:
        analysis['suggestions'].append(
            'No index scans detected; query may not use indexes effectively'
        )

    return analysis


# ── Slow Query Data Structure ──────────────────────────────────────────────

@dataclass
class SlowQueryRecord:
    """Record of a slow query execution."""

    timestamp: str
    query_fingerprint: str
    query_text: str
    duration_sec: float
    row_count: int
    error: Optional[str] = None
    explain_plan: Optional[List[QueryPlanNode]] = None
    plan_analysis: Optional[Dict[str, Any]] = None
    tables: List[str] = field(default_factory=list)
    params: Optional[tuple] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, excluding unpickleable objects."""
        d = asdict(self)
        # Remove QueryPlanNode objects, replace with dicts
        if d['explain_plan']:
            d['explain_plan'] = [
                {
                    'addr': n.addr,
                    'opcode': n.opcode,
                    'p1': n.p1,
                    'p2': n.p2,
                    'p3': n.p3,
                    'p4': n.p4,
                    'p5': n.p5,
                    'comment': n.comment,
                }
                for n in d['explain_plan']
            ]
        # Remove params tuple (may contain sensitive data)
        d['params'] = None
        return d


class QueryMonitor:
    """Thread-safe query performance monitor with slow query tracking."""

    def __init__(self, slow_query_threshold_sec: float = 1.0, max_history: int = 100):
        """Initialize query monitor.

        Args:
            slow_query_threshold_sec: Queries slower than this are logged (default 1s)
            max_history: Max slow queries to keep in history
        """
        self.slow_query_threshold_sec = slow_query_threshold_sec
        self.max_history = max_history
        self._lock = threading.Lock()

        # In-memory slow query history
        self.slow_queries: List[SlowQueryRecord] = []

        # Aggregated stats by fingerprint
        self.query_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'count': 0,
            'total_duration': 0.0,
            'avg_duration': 0.0,
            'max_duration': 0.0,
            'min_duration': float('inf'),
            'total_rows': 0,
            'last_execution': None,
        })

    def get_slow_queries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get most recent slow queries."""
        with self._lock:
            # Return most recent queries first
            return [q.to_dict() for q in self.slow_queries[-limit:]][::-1]

    def get_query_stats(self) -> Dict[str, Any]:
        """Get aggregated query statistics."""
        with self._lock:
            # Sort by average duration (slowest first)
            sorted_stats = sorted(
                self.query_stats.items(),
                key=lambda x: x[1]['avg_duration'],
                reverse=True,
            )
            return {
                fingerprint: stats
                for fingerprint, stats in sorted_stats[:10]  # Top 10 slowest
            }

    def get_top_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top slowest queries by average duration."""
        stats = self.get_query_stats()
        result = []
        for fingerprint, stats_dict in list(stats.items())[:limit]:
            # Find a recent example of this query
            with self._lock:
                example = next(
                    (q for q in reversed(self.slow_queries) if q.query_fingerprint == fingerprint),
                    None,
                )

            if example:
                result.append({
                    'fingerprint': fingerprint,
                    'query_example': example.query_text[:200],
                    'avg_duration_sec': stats_dict['avg_duration'],
                    'max_duration_sec': stats_dict['max_duration'],
                    'execution_count': stats_dict['count'],
                    'total_rows': stats_dict['total_rows'],
                    'last_execution': stats_dict['last_execution'],
                })
        return result

    def record_slow_query(self, record: SlowQueryRecord) -> None:
        """Record a slow query execution."""
        with self._lock:
            # Add to history
            self.slow_queries.append(record)

            # Trim history if needed
            if len(self.slow_queries) > self.max_history:
                self.slow_queries = self.slow_queries[-self.max_history:]

            # Update aggregated stats
            fingerprint = record.query_fingerprint
            stats = self.query_stats[fingerprint]
            stats['count'] += 1
            stats['total_duration'] += record.duration_sec
            stats['avg_duration'] = stats['total_duration'] / stats['count']
            stats['max_duration'] = max(stats['max_duration'], record.duration_sec)
            stats['min_duration'] = min(stats['min_duration'], record.duration_sec)
            stats['total_rows'] += record.row_count
            stats['last_execution'] = record.timestamp

    def reset(self) -> None:
        """Clear all monitoring data."""
        with self._lock:
            self.slow_queries.clear()
            self.query_stats.clear()


# Global monitor instance
_monitor = QueryMonitor()


def get_query_monitor() -> QueryMonitor:
    """Get the global query monitor instance."""
    return _monitor


# ── Decorator for Automatic Query Monitoring ──────────────────────────────

def monitor_query(
    get_connection: Callable[[], sqlite3.Connection],
    threshold_sec: float = 1.0,
    capture_explain: bool = True,
) -> Callable[[F], F]:
    """Decorator for monitoring query execution time and performance.

    Usage:
        @monitor_query(get_connection=lambda: sqlite3.connect('swaps.db'))
        def my_query(self, sql, params):
            # execute query
            pass

    Args:
        get_connection: Callable that returns a sqlite3 connection
        threshold_sec: Log slow queries above this threshold (default 1.0s)
        capture_explain: Whether to capture EXPLAIN QUERY PLAN for slow queries

    Returns:
        Decorated function that monitors query performance
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            start_time = time.time()
            result = None
            error = None
            row_count = 0

            try:
                result = func(*args, **kwargs)

                # Count rows in result
                if isinstance(result, list):
                    row_count = len(result)
                elif hasattr(result, '__len__'):
                    try:
                        row_count = len(result)
                    except TypeError:
                        row_count = 0

                return result
            except Exception as e:
                error = f'{type(e).__name__}: {str(e)}'
                raise
            finally:
                duration = time.time() - start_time

                # Extract query info from function arguments
                query = None
                params = None

                # Try to get query from args/kwargs
                if len(args) > 1:
                    query = args[1]  # Usually first arg after self
                if 'query' in kwargs:
                    query = kwargs['query']
                if 'sql' in kwargs:
                    query = kwargs['sql']

                if len(args) > 2:
                    params = args[2]
                if 'params' in kwargs:
                    params = kwargs['params']

                # Only monitor if we have a query
                if query:
                    # Check if query exceeds threshold
                    if duration >= threshold_sec or error:
                        try:
                            fingerprint = fingerprint_query(query)
                            tables = extract_table_names(query)

                            # Capture EXPLAIN QUERY PLAN if requested
                            explain_plan = None
                            plan_analysis = None

                            if capture_explain and not error:
                                try:
                                    conn = get_connection()
                                    cur = conn.cursor()
                                    explain_query = f'EXPLAIN QUERY PLAN {query}'
                                    cur.execute(explain_query, params or ())
                                    plan_output = cur.fetchall()
                                    explain_plan = parse_explain_query_plan(plan_output)
                                    plan_analysis = analyze_query_plan(explain_plan)
                                    cur.close()
                                    conn.close()
                                except Exception as e:
                                    logger.warning(
                                        'Failed to capture EXPLAIN plan',
                                        extra={'error': str(e)}
                                    )

                            # Create slow query record
                            record = SlowQueryRecord(
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                query_fingerprint=fingerprint,
                                query_text=query[:500],  # Truncate very long queries
                                duration_sec=duration,
                                row_count=row_count,
                                error=error,
                                explain_plan=explain_plan,
                                plan_analysis=plan_analysis,
                                tables=tables,
                                params=params,
                            )

                            # Log the slow query
                            monitor = get_query_monitor()
                            monitor.record_slow_query(record)

                            # Log to structured logger
                            log_level = logging.WARNING if duration >= threshold_sec else logging.INFO
                            log_msg = f'Slow query detected ({duration:.2f}s)' if duration >= threshold_sec else 'Query executed'

                            logger.log(
                                log_level,
                                log_msg,
                                extra={
                                    'query_fingerprint': fingerprint,
                                    'query_text': query[:200],
                                    'duration_sec': duration,
                                    'row_count': row_count,
                                    'tables': tables,
                                    'threshold_sec': threshold_sec,
                                    'exceeded_threshold': duration >= threshold_sec,
                                    'error': error,
                                    'plan_analysis': plan_analysis,
                                }
                            )
                        except Exception as e:
                            logger.error(
                                'Error monitoring query',
                                extra={'error': str(e)},
                                exc_info=True,
                            )

        return wrapper  # type: ignore

    return decorator


__all__ = [
    'QueryMonitor',
    'SlowQueryRecord',
    'QueryPlanNode',
    'fingerprint_query',
    'extract_table_names',
    'extract_columns_referenced',
    'parse_explain_query_plan',
    'analyze_query_plan',
    'monitor_query',
    'get_query_monitor',
]
