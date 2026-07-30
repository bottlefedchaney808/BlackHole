"""Graceful shutdown handler for long-running processes.

Provides SIGINT/SIGTERM handling with state preservation for backfill and scheduler.
"""

import signal
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Manages graceful shutdown for long-running processes.

    Usage:
        shutdown = GracefulShutdown()
        while not shutdown.is_requested():
            # Do work
            pass
        # Gracefully exit (state already updated)
    """

    def __init__(self, on_shutdown: Optional[Callable[[], None]] = None):
        """Initialize graceful shutdown handler.

        Args:
            on_shutdown: Optional callback to invoke when shutdown is requested.
        """
        self._shutdown_requested = False
        self._on_shutdown = on_shutdown

        # Register signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        logger.info("Graceful shutdown handler registered. Press Ctrl+C to exit cleanly.")

    def _handle_signal(self, signum, frame):
        """Handle SIGINT/SIGTERM signals."""
        if signum == signal.SIGINT:
            sig_name = "SIGINT (Ctrl+C)"
        elif signum == signal.SIGTERM:
            sig_name = "SIGTERM"
        else:
            sig_name = f"signal {signum}"

        logger.info(f"Received {sig_name}. Graceful shutdown initiated.")
        self._shutdown_requested = True

        if self._on_shutdown:
            self._on_shutdown()

    def is_requested(self) -> bool:
        """Check if shutdown has been requested.

        Returns:
            True if user pressed Ctrl+C or SIGTERM was sent, False otherwise.
        """
        return self._shutdown_requested

    def request_shutdown(self):
        """Manually request shutdown (for testing)."""
        self._shutdown_requested = True
        if self._on_shutdown:
            self._on_shutdown()

    def reset(self):
        """Reset shutdown flag (for testing/restart scenarios)."""
        self._shutdown_requested = False
