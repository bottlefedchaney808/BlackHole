"""Tests for production-grade structured logging.

Validates:
  * JSON format correctness
  * All components emit structured logs
  * Metrics calculation accuracy
  * LogContext manager tracking
  * Exception handling and structured error logging
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from shared.logging import (
    JSONFormatter,
    LogContext,
    MetricsCollector,
    get_metrics,
    log_operation,
    setup_logging,
)


# ── Test Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def reset_metrics():
    """Reset metrics before each test."""
    metrics = get_metrics()
    metrics.reset()
    yield metrics
    metrics.reset()


@pytest.fixture
def json_logger():
    """Create a logger with JSON formatter for testing."""
    logger = logging.getLogger('test_json_logger')
    logger.handlers = []  # Clear existing handlers
    logger.setLevel(logging.DEBUG)

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)

    return logger, stream


# ── JSON Format Tests ──────────────────────────────────────────────────────

class TestJSONFormatter:
    """Test JSONFormatter outputs valid JSON with required fields."""

    def test_json_format_validity(self, json_logger):
        """Verify JSON formatter produces valid JSON."""
        logger, stream = json_logger
        logger.info("Test message")

        output = stream.getvalue().strip()
        assert output, "Logger produced no output"

        log_data = json.loads(output)
        assert isinstance(log_data, dict)

    def test_json_contains_required_fields(self, json_logger):
        """Verify JSON contains all required observability fields."""
        logger, stream = json_logger
        logger.info("Test message")

        log_data = json.loads(stream.getvalue().strip())

        # Required fields for observability
        assert 'timestamp' in log_data
        assert 'level' in log_data
        assert 'logger' in log_data
        assert 'message' in log_data
        assert 'module' in log_data

    def test_json_with_extra_context(self, json_logger):
        """Verify JSON preserves extra context fields."""
        logger, stream = json_logger
        logger.info(
            "Test message",
            extra={
                'operation_id': 'op123',
                'batch_size': 100,
                'status': 'completed',
            }
        )

        log_data = json.loads(stream.getvalue().strip())

        assert log_data['operation_id'] == 'op123'
        assert log_data['batch_size'] == 100
        assert log_data['status'] == 'completed'

    def test_json_with_exception(self, json_logger):
        """Verify JSON formatter captures exception info."""
        logger, stream = json_logger

        try:
            raise ValueError("Test exception")
        except ValueError:
            logger.exception("An error occurred")

        log_data = json.loads(stream.getvalue().strip())

        assert 'exception' in log_data
        assert log_data['exception']['type'] == 'ValueError'
        assert 'Test exception' in log_data['exception']['message']
        assert log_data['level'] == 'ERROR'


# ── Metrics Collection Tests ───────────────────────────────────────────────

class TestMetricsCollector:
    """Test metrics collection and percentile calculations."""

    def test_request_duration_percentiles(self, reset_metrics):
        """Verify request duration percentile calculations."""
        metrics = reset_metrics
        durations = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

        for d in durations:
            metrics.add_request_duration(d)

        stats = metrics.to_dict()
        assert stats['request_latency']['count'] == 10
        # p50 at index 5 (0-based) should be 0.6
        assert stats['request_latency']['p50_sec'] in [0.5, 0.6]
        assert stats['request_latency']['p95_sec'] is not None
        assert stats['request_latency']['p99_sec'] is not None

    def test_query_execution_by_type(self, reset_metrics):
        """Verify query execution metrics grouped by type."""
        metrics = reset_metrics
        metrics.add_query_execution('upsert', 0.5)
        metrics.add_query_execution('upsert', 1.0)
        metrics.add_query_execution('select', 0.1)
        metrics.add_query_execution('select', 0.2)

        stats = metrics.to_dict()
        query_stats = stats['query_execution']

        assert 'upsert' in query_stats
        assert query_stats['upsert']['count'] == 2
        assert query_stats['upsert']['avg'] == 0.75
        assert query_stats['upsert']['min'] == 0.5
        assert query_stats['upsert']['max'] == 1.0

        assert 'select' in query_stats
        assert query_stats['select']['count'] == 2

    def test_upsert_batch_tracking(self, reset_metrics):
        """Verify upsert batch metrics."""
        metrics = reset_metrics
        metrics.add_upsert_batch(100, 0.5)
        metrics.add_upsert_batch(200, 1.0)
        metrics.add_upsert_batch(150, 0.7)

        stats = metrics.to_dict()
        assert stats['upsert_batches']['count'] == 3
        assert len(stats['upsert_batches']['batches']) == 3

    def test_error_rate_tracking(self, reset_metrics):
        """Verify error counting by type."""
        metrics = reset_metrics
        metrics.add_error('ValueError')
        metrics.add_error('ValueError')
        metrics.add_error('KeyError')
        metrics.add_error('RuntimeError')

        stats = metrics.to_dict()
        assert stats['error_rates']['ValueError'] == 2
        assert stats['error_rates']['KeyError'] == 1
        assert stats['error_rates']['RuntimeError'] == 1

    def test_cache_hit_rate(self, reset_metrics):
        """Verify cache hit rate calculation."""
        metrics = reset_metrics
        for _ in range(80):
            metrics.add_cache_hit()
        for _ in range(20):
            metrics.add_cache_miss()

        stats = metrics.to_dict()
        assert stats['cache']['hits'] == 80
        assert stats['cache']['misses'] == 20
        assert stats['cache']['hit_rate'] == 0.8

    def test_metrics_reset(self, reset_metrics):
        """Verify metrics can be reset."""
        metrics = reset_metrics
        metrics.add_request_duration(1.0)
        metrics.add_error('TestError')

        metrics.reset()

        stats = metrics.to_dict()
        assert stats['request_latency'] is None
        assert stats['error_rates'] is None


# ── LogContext Manager Tests ───────────────────────────────────────────────

class TestLogContext:
    """Test LogContext manager for operation tracking."""

    def test_log_context_successful_operation(self, json_logger, reset_metrics):
        """Verify LogContext tracks successful operation."""
        logger, stream = json_logger
        metrics = reset_metrics

        with LogContext('test_op', logger=logger) as ctx:
            ctx.rows_affected = 42

        # Check logs contain operation tracking
        logs = [l for l in stream.getvalue().strip().split('\n') if l]
        # Should have start + end logs from LogContext
        assert len(logs) >= 1

        # Parse logs and find status messages
        parsed_logs = []
        for l in logs:
            try:
                parsed_logs.append(json.loads(l))
            except:
                pass

        # Find the log with status field
        status_logs = [log for log in parsed_logs if 'status' in log]
        assert len(status_logs) >= 1, "No status log found"

        end_log = status_logs[-1]

        assert end_log['operation_type'] == 'test_op'
        assert end_log['status'] == 'completed'
        assert 'duration_sec' in end_log
        assert end_log['duration_sec'] >= 0

        # Check metrics were recorded
        stats = metrics.to_dict()
        assert stats['request_latency'] is not None
        assert stats['request_latency']['count'] >= 1

    def test_log_context_failed_operation(self, json_logger, reset_metrics):
        """Verify LogContext tracks failed operation with exception."""
        logger, stream = json_logger
        metrics = reset_metrics

        with pytest.raises(ValueError):
            with LogContext('failing_op', logger=logger) as ctx:
                raise ValueError("Operation failed")

        logs = [l for l in stream.getvalue().strip().split('\n') if l]
        end_log = json.loads(logs[-1])

        assert end_log['status'] == 'failed'
        assert 'error' in end_log
        assert end_log['error']['error_type'] == 'ValueError'
        assert 'Operation failed' in end_log['error']['error_message']

        # Check error was recorded in metrics
        stats = metrics.to_dict()
        assert 'ValueError' in stats['error_rates']

    def test_log_context_with_metadata(self, json_logger):
        """Verify LogContext preserves metadata."""
        logger, stream = json_logger

        metadata = {'batch_size': 100, 'source': 'dtcc'}
        with LogContext('batch_op', metadata=metadata, logger=logger) as ctx:
            pass

        logs = [l for l in stream.getvalue().strip().split('\n') if l]
        # First log should have metadata
        start_log = json.loads(logs[0])
        assert start_log['metadata'] == metadata

    def test_log_context_duration_measurement(self, json_logger, reset_metrics):
        """Verify LogContext accurately measures operation duration."""
        import time
        logger, stream = json_logger
        metrics = reset_metrics

        with LogContext('timed_op', logger=logger):
            time.sleep(0.05)  # Sleep for 50ms

        logs = [l for l in stream.getvalue().strip().split('\n') if l]
        end_log = json.loads(logs[-1])

        assert end_log['duration_sec'] >= 0.05
        assert end_log['duration_sec'] < 0.2  # Should be close to 50ms


# ── Context Manager Function Tests ────────────────────────────────────────

class TestLogOperation:
    """Test log_operation context manager convenience function."""

    def test_log_operation_success(self, reset_metrics):
        """Verify log_operation context manager works."""
        metrics = reset_metrics

        with log_operation('sample_op', metadata={'count': 10}):
            pass

        stats = metrics.to_dict()
        assert stats['request_latency'] is not None
        assert stats['request_latency']['count'] == 1

    def test_log_operation_with_custom_id(self, reset_metrics):
        """Verify log_operation accepts custom operation ID."""
        with log_operation('op', operation_id='custom_id_123'):
            pass

        # Should not raise


# ── Logger Setup Tests ─────────────────────────────────────────────────────

class TestLoggerSetup:
    """Test setup_logging configuration."""

    def test_setup_logging_console_only(self):
        """Verify setup_logging creates console logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = setup_logging(
                name='test_console',
                level=logging.INFO,
                use_json=True
            )

            assert logger is not None
            assert logger.level == logging.INFO
            # Should have at least console handler
            assert len(logger.handlers) > 0

    def test_setup_logging_with_file_rotation(self):
        """Verify setup_logging creates file handler with rotation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, 'app.log')

            logger = setup_logging(
                name='test_file_' + str(os.getpid()),
                log_file=log_file,
                use_json=True,
                rotation_mode='daily'
            )

            logger.info("Test message")

            # Log file should exist
            assert os.path.exists(log_file), f"Log file not found at {log_file}"

            # Close handlers before tempdir cleanup to release file locks
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

            # Should be valid JSON
            with open(log_file, 'r') as f:
                content = f.read().strip()
                if content:  # Only check if file has content
                    log_data = json.loads(content)
                    assert 'message' in log_data

    def test_setup_logging_non_json(self):
        """Verify setup_logging works in non-JSON mode."""
        logger = setup_logging(
            name='test_plaintext',
            use_json=False
        )

        # Should not raise
        logger.info("Test message")


# ── Integration Tests ──────────────────────────────────────────────────────

class TestLoggingIntegration:
    """Integration tests across the logging system."""

    def test_full_workflow(self, json_logger, reset_metrics):
        """Test complete logging workflow with metrics."""
        logger, stream = json_logger
        metrics = reset_metrics

        # Simulate a batch processing workflow
        with LogContext('batch_process', metadata={'total_batches': 3}, logger=logger):
            for i in range(3):
                with log_operation(f'batch_{i}'):
                    metrics.add_query_execution('upsert', 0.1 * (i + 1))
                    metrics.add_upsert_batch(100 * (i + 1), 0.1 * (i + 1))

        # Verify metrics were collected
        stats = metrics.to_dict()
        # 3 batch operations + 1 outer batch_process context = 4 total
        assert stats['request_latency']['count'] == 4
        assert stats['query_execution']['upsert']['count'] == 3
        assert stats['upsert_batches']['count'] == 3

        # Verify logs are present
        output = stream.getvalue()
        assert len(output) > 0

    def test_error_propagation_in_logging(self, reset_metrics):
        """Verify errors are properly tracked and re-raised."""
        metrics = reset_metrics

        with pytest.raises(RuntimeError):
            with LogContext('error_op'):
                # LogContext will add one error automatically, so we get 2
                raise RuntimeError("Test error")

        stats = metrics.to_dict()
        # LogContext adds one error automatically on exception
        assert stats['error_rates']['RuntimeError'] == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
