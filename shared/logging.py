"""Production-grade structured logging for observability.

Features:
  * JSONFormatter for all logs (timestamp, level, module, message, context)
  * LogContext manager for operation tracking with automatic duration measurement
  * Metrics collection: request duration, query count, rows affected
  * Structured exception logging with stack trace + context
  * Stdout JSON output (container-friendly)
  * Optional file rotation (daily, 100MB limit)
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import local
from typing import Any, Dict, List, Optional


# ── Global Metrics Storage ─────────────────────────────────────────────────

_thread_local = local()


class MetricsCollector:
    """Thread-safe metrics collection for observability."""

    def __init__(self):
        self.request_durations: List[float] = []
        self.query_execution_times: List[tuple] = []  # (query_type, duration)
        self.upsert_batches: List[tuple] = []  # (batch_size, duration)
        self.error_count: Dict[str, int] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    def add_request_duration(self, duration: float) -> None:
        """Record request latency in seconds."""
        self.request_durations.append(duration)

    def add_query_execution(self, query_type: str, duration: float) -> None:
        """Record query execution time by type."""
        self.query_execution_times.append((query_type, duration))

    def add_upsert_batch(self, batch_size: int, duration: float) -> None:
        """Record upsert batch size and duration."""
        self.upsert_batches.append((batch_size, duration))

    def add_error(self, error_type: str) -> None:
        """Count error by type."""
        self.error_count[error_type] = self.error_count.get(error_type, 0) + 1

    def add_cache_hit(self) -> None:
        """Increment cache hit counter."""
        self.cache_hits += 1

    def add_cache_miss(self) -> None:
        """Increment cache miss counter."""
        self.cache_misses += 1

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dict."""
        if not self.request_durations:
            p50, p95, p99 = None, None, None
        else:
            sorted_durations = sorted(self.request_durations)
            n = len(sorted_durations)
            # Use 0-based indexing with proper percentile calculation
            p50_idx = min(int(n * 0.50), n - 1)
            p95_idx = min(int(n * 0.95), n - 1)
            p99_idx = min(int(n * 0.99), n - 1)
            p50 = sorted_durations[p50_idx] if p50_idx >= 0 else None
            p95 = sorted_durations[p95_idx] if p95_idx >= 0 else None
            p99 = sorted_durations[p99_idx] if p99_idx >= 0 else None

        query_by_type: Dict[str, Dict[str, Any]] = {}
        for query_type, duration in self.query_execution_times:
            if query_type not in query_by_type:
                query_by_type[query_type] = {'durations': [], 'count': 0}
            query_by_type[query_type]['durations'].append(duration)
            query_by_type[query_type]['count'] += 1

        for query_type in query_by_type:
            durations = query_by_type[query_type]['durations']
            sorted_durations = sorted(durations)
            query_by_type[query_type]['avg'] = sum(durations) / len(durations)
            query_by_type[query_type]['min'] = min(durations)
            query_by_type[query_type]['max'] = max(durations)

        cache_hit_rate = None
        if self.cache_hits + self.cache_misses > 0:
            cache_hit_rate = self.cache_hits / (self.cache_hits + self.cache_misses)

        return {
            'request_latency': {
                'count': len(self.request_durations),
                'p50_sec': p50,
                'p95_sec': p95,
                'p99_sec': p99,
            } if self.request_durations else None,
            'query_execution': query_by_type if query_by_type else None,
            'upsert_batches': {
                'count': len(self.upsert_batches),
                'batches': self.upsert_batches,
            } if self.upsert_batches else None,
            'error_rates': self.error_count if self.error_count else None,
            'cache': {
                'hits': self.cache_hits,
                'misses': self.cache_misses,
                'hit_rate': cache_hit_rate,
            } if (self.cache_hits + self.cache_misses) > 0 else None,
        }

    def reset(self) -> None:
        """Clear all metrics."""
        self.request_durations.clear()
        self.query_execution_times.clear()
        self.upsert_batches.clear()
        self.error_count.clear()
        self.cache_hits = 0
        self.cache_misses = 0


def get_metrics() -> MetricsCollector:
    """Get thread-local metrics collector."""
    if not hasattr(_thread_local, 'metrics'):
        _thread_local.metrics = MetricsCollector()
    return _thread_local.metrics


# ── LogContext Manager ─────────────────────────────────────────────────────

@dataclass
class LogContextData:
    """Structured context for operation tracking."""

    operation_id: str
    operation_type: str
    timestamp_start: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_sec: Optional[float] = None
    status: str = 'in_progress'
    rows_affected: Optional[int] = None
    error: Optional[Dict[str, Any]] = None


class LogContext:
    """Context manager for operation tracking with automatic duration measurement."""

    def __init__(self, operation_type: str, operation_id: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None):
        """Initialize log context.

        Args:
            operation_type: Type of operation (e.g., 'suite_run', 'upsert', 'query')
            operation_id: Optional unique operation ID (auto-generated if not provided)
            metadata: Optional dict of contextual data
            logger: Optional logger instance (uses root logger if not provided)
        """
        import uuid
        self.operation_id = operation_id or str(uuid.uuid4())[:8]
        self.operation_type = operation_type
        self.metadata = metadata or {}
        self.start_time = time.time()
        self.context_data = LogContextData(
            operation_id=self.operation_id,
            operation_type=operation_type,
            timestamp_start=datetime.now(timezone.utc).isoformat(),
            metadata=self.metadata,
        )
        self.logger = logger or logging.getLogger('financial_development')

    def __enter__(self) -> LogContextData:
        """Enter context and log operation start."""
        self.logger.info(
            f"Operation {self.operation_type} started",
            extra={
                'operation_id': self.operation_id,
                'operation_type': self.operation_type,
                'metadata': self.metadata,
            }
        )
        return self.context_data

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context, log operation end with duration and result."""
        duration = time.time() - self.start_time
        self.context_data.duration_sec = duration

        if exc_type is not None:
            self.context_data.status = 'failed'
            self.context_data.error = {
                'error_type': exc_type.__name__,
                'error_message': str(exc_val),
                'traceback': traceback.format_exc(),
            }
            self.logger.error(
                f"Operation {self.operation_type} failed after {duration:.2f}s",
                extra={
                    'operation_id': self.operation_id,
                    'operation_type': self.operation_type,
                    'duration_sec': duration,
                    'status': 'failed',
                    'error': self.context_data.error,
                }
            )
            get_metrics().add_error(exc_type.__name__)
        else:
            self.context_data.status = 'completed'
            self.logger.info(
                f"Operation {self.operation_type} completed in {duration:.2f}s",
                extra={
                    'operation_id': self.operation_id,
                    'operation_type': self.operation_type,
                    'duration_sec': duration,
                    'status': 'completed',
                    'rows_affected': self.context_data.rows_affected,
                }
            )
            get_metrics().add_request_duration(duration)

        return False  # Re-raise exceptions


@contextmanager
def log_operation(operation_type: str, operation_id: Optional[str] = None,
                  metadata: Optional[Dict[str, Any]] = None):
    """Context manager for structured operation logging.

    Usage:
        with log_operation('upsert', metadata={'batch_size': 100}):
            # do work
            pass
    """
    context = LogContext(operation_type, operation_id, metadata)
    with context as ctx:
        yield ctx


# ── JSON Formatter ─────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging with timestamps, level, module, message, and context."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_dict = {
            'timestamp': datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        # Add module and line number for debugging
        if record.module:
            log_dict['module'] = record.module
        if record.lineno:
            log_dict['line'] = record.lineno

        # Add function name if available
        if record.funcName:
            log_dict['function'] = record.funcName

        # Add any extra fields passed in the LogRecord
        if hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                # Skip standard logging fields
                if key not in (
                    'name', 'msg', 'args', 'created', 'filename', 'funcName',
                    'levelname', 'levelno', 'lineno', 'module', 'msecs',
                    'message', 'pathname', 'process', 'processName', 'relativeCreated',
                    'thread', 'threadName', 'exc_info', 'exc_text', 'stack_info',
                    'getMessage',
                ):
                    log_dict[key] = value

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_dict['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_dict)


# ── Logger Setup ───────────────────────────────────────────────────────────

def setup_logging(
    name: str = 'financial_development',
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    use_json: bool = True,
    rotation_mode: str = 'daily',
) -> logging.Logger:
    """Configure structured logging with JSON output.

    Args:
        name: Logger name
        level: Logging level (default INFO)
        log_file: Optional file path for log output. If provided, enables file rotation.
        use_json: Whether to use JSON format (default True)
        rotation_mode: 'daily' for daily rotation, or 'size' for 100MB rotation

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    if use_json:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                '%(asctime)s %(levelname)s [%(module)s:%(lineno)d] %(message)s'
            )
        )

    logger.addHandler(console_handler)

    # File handler with rotation (if log_file provided)
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        if rotation_mode == 'daily':
            file_handler = logging.handlers.TimedRotatingFileHandler(
                log_file,
                when='midnight',
                interval=1,
                backupCount=7,  # Keep 7 days of logs
                encoding='utf-8',
            )
        else:  # 'size'
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=100 * 1024 * 1024,  # 100MB
                backupCount=5,
                encoding='utf-8',
            )

        file_handler.setLevel(level)
        if use_json:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(
                logging.Formatter(
                    '%(asctime)s %(levelname)s [%(module)s:%(lineno)d] %(message)s'
                )
            )

        logger.addHandler(file_handler)

    return logger


# ── Convenience Exports ────────────────────────────────────────────────────

__all__ = [
    'JSONFormatter',
    'LogContext',
    'LogContextData',
    'MetricsCollector',
    'get_metrics',
    'log_operation',
    'setup_logging',
]
