"""High-performance SQLite connection pool for concurrent data ingestion.

Provides thread-safe connection pooling with health checks, automatic cleanup,
and WAL mode optimization for readers/writers contention avoidance.
"""
import sqlite3
import threading
import time
import logging
from queue import Queue, Empty, Full
from contextlib import contextmanager
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PooledConnection:
    """Wrapper around sqlite3.Connection with health tracking."""

    def __init__(self, path: str, connection_id: int):
        self.path = path
        self.connection_id = connection_id
        self.conn: Optional[sqlite3.Connection] = None
        self.created_at = time.time()
        self.last_used_at = self.created_at
        self.health_check_at = self.created_at
        self.is_healthy = False
        self._initialize()

    def _initialize(self):
        """Create connection and configure for optimal throughput."""
        try:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            self.conn.row_factory = sqlite3.Row

            # Configure for high-throughput writes + concurrent reads
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")  # fsync only at commit
            self.conn.execute("PRAGMA cache_size=-64000;")  # 64MB cache
            self.conn.execute("PRAGMA temp_store=MEMORY;")
            self.conn.execute("PRAGMA query_only=FALSE;")
            self.conn.commit()

            self.is_healthy = True
            logger.debug(f"Initialized connection {self.connection_id} to {self.path}")
        except Exception as e:
            logger.error(f"Failed to initialize connection {self.connection_id}: {e}")
            self.is_healthy = False
            raise

    def health_check(self) -> bool:
        """Verify connection is still responsive."""
        if not self.is_healthy or self.conn is None:
            return False

        try:
            # Quick ping query
            self.conn.execute("SELECT 1;")
            self.health_check_at = time.time()
            return True
        except sqlite3.DatabaseError as e:
            logger.warning(f"Health check failed for connection {self.connection_id}: {e}")
            self.is_healthy = False
            return False

    def close(self):
        """Close the underlying connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception as e:
                logger.warning(f"Error closing connection {self.connection_id}: {e}")
            finally:
                self.conn = None
                self.is_healthy = False

    def get_age_seconds(self) -> float:
        """Age in seconds since connection creation."""
        return time.time() - self.created_at

    def get_idle_seconds(self) -> float:
        """Seconds since last use."""
        return time.time() - self.last_used_at


class ConnectionPool:
    """Thread-safe SQLite connection pool optimized for high-throughput ingestion.

    Features:
      - Configurable pool size (default 5, max 20)
      - Connection health checks (ping, age)
      - Automatic cleanup of stale connections
      - Timeout-aware connection acquisition
      - WAL mode for concurrent reads/writes
      - Thread-safe via Queue

    Example:
        pool = ConnectionPool('swaps.db', pool_size=10)
        try:
            with pool.get_connection_context() as conn:
                cur = conn.cursor()
                cur.execute("INSERT INTO ...")
                conn.commit()
        finally:
            pool.close()
    """

    def __init__(self, db_path: str, pool_size: int = 5, max_pool_size: int = 20,
                 stale_connection_timeout: int = 3600, health_check_interval: int = 60):
        """
        Initialize the connection pool.

        Args:
            db_path: Path to SQLite database file
            pool_size: Initial number of connections to create (default 5)
            max_pool_size: Maximum connections allowed (default 20)
            stale_connection_timeout: Seconds before closing idle connections (default 3600)
            health_check_interval: Seconds between health checks (default 60)
        """
        if pool_size < 1 or pool_size > max_pool_size:
            raise ValueError(f"pool_size must be 1-{max_pool_size}, got {pool_size}")

        self.db_path = db_path
        self.pool_size = pool_size
        self.max_pool_size = max_pool_size
        self.stale_connection_timeout = stale_connection_timeout
        self.health_check_interval = health_check_interval

        # Thread-safe queue for available connections
        self.available_connections: Queue = Queue(maxsize=max_pool_size)
        self.all_connections: Dict[int, PooledConnection] = {}
        self.borrowed_connections: Dict[int, PooledConnection] = {}  # Track active checkouts
        self.lock = threading.RLock()
        self.connection_counter = 0

        # Statistics
        self.stats = {
            "acquired": 0,
            "released": 0,
            "timeouts": 0,
            "health_checks": 0,
            "cleanup": 0,
        }
        self.stats_lock = threading.Lock()

        # Initialize pool with initial connections
        self._initialize_pool()

        logger.info(f"ConnectionPool initialized: path={db_path}, size={pool_size}, max={max_pool_size}")

    def _initialize_pool(self):
        """Create initial pool connections."""
        with self.lock:
            for _ in range(self.pool_size):
                try:
                    conn_id = self._get_next_connection_id()
                    pooled = PooledConnection(self.db_path, conn_id)
                    self.all_connections[conn_id] = pooled
                    self.available_connections.put(pooled, block=False)
                except Exception as e:
                    logger.error(f"Failed to initialize pool connection: {e}")
                    break

    def _get_next_connection_id(self) -> int:
        """Get next connection ID (thread-safe)."""
        self.connection_counter += 1
        return self.connection_counter

    def get_connection(self, timeout: float = 5.0) -> PooledConnection:
        """
        Acquire a connection from the pool.

        Creates a new connection if pool is not full and none available.
        Waits up to 'timeout' seconds if pool is exhausted.

        Args:
            timeout: Seconds to wait for available connection (default 5.0)

        Returns:
            PooledConnection ready for use

        Raises:
            TimeoutError: If unable to acquire connection within timeout
        """
        start_time = time.time()

        # Try to get an available connection from queue
        try:
            pooled = self.available_connections.get(timeout=timeout)
            with self.stats_lock:
                self.stats["acquired"] += 1
            pooled.last_used_at = time.time()
            return pooled
        except Empty:
            pass

        # Queue is empty; try to create a new connection if under limit
        with self.lock:
            if len(self.all_connections) < self.max_pool_size:
                try:
                    conn_id = self._get_next_connection_id()
                    pooled = PooledConnection(self.db_path, conn_id)
                    self.all_connections[conn_id] = pooled
                    with self.stats_lock:
                        self.stats["acquired"] += 1
                    pooled.last_used_at = time.time()
                    return pooled
                except Exception as e:
                    logger.error(f"Failed to create new connection: {e}")

        # Could not get or create connection; fail with timeout
        with self.stats_lock:
            self.stats["timeouts"] += 1
        elapsed = time.time() - start_time
        raise TimeoutError(
            f"Could not acquire connection after {elapsed:.1f}s "
            f"(pool_size={len(self.all_connections)}, max={self.max_pool_size})"
        )

    def return_connection(self, pooled: PooledConnection):
        """
        Return a connection to the pool.

        Args:
            pooled: PooledConnection to return
        """
        if pooled is None:
            return

        try:
            # Check health before returning
            if pooled.health_check():
                self.available_connections.put(pooled, block=False)
                with self.stats_lock:
                    self.stats["released"] += 1
            else:
                # Connection unhealthy; close and remove from pool
                logger.warning(f"Connection {pooled.connection_id} failed health check; removing")
                with self.lock:
                    if pooled.connection_id in self.all_connections:
                        del self.all_connections[pooled.connection_id]
                pooled.close()
        except Full:
            # Queue is full (shouldn't happen with proper pool_size); close conn
            logger.warning(f"Could not return connection {pooled.connection_id} (queue full); closing")
            pooled.close()
            with self.lock:
                if pooled.connection_id in self.all_connections:
                    del self.all_connections[pooled.connection_id]

    def cleanup_stale_connections(self):
        """Remove connections idle longer than stale_connection_timeout."""
        with self.lock:
            current_time = time.time()
            stale_ids = []

            for conn_id, pooled in self.all_connections.items():
                idle_seconds = pooled.get_idle_seconds()
                if idle_seconds > self.stale_connection_timeout:
                    stale_ids.append(conn_id)
                    logger.debug(f"Marking connection {conn_id} stale (idle {idle_seconds:.0f}s)")

            # Remove stale connections from tracking
            for conn_id in stale_ids:
                pooled = self.all_connections.pop(conn_id, None)
                if pooled:
                    pooled.close()
                    with self.stats_lock:
                        self.stats["cleanup"] += 1

        # Refill pool to desired size if we dropped below
        with self.lock:
            current_size = len(self.all_connections)
            if current_size < self.pool_size:
                for _ in range(self.pool_size - current_size):
                    try:
                        conn_id = self._get_next_connection_id()
                        pooled = PooledConnection(self.db_path, conn_id)
                        self.all_connections[conn_id] = pooled
                        self.available_connections.put(pooled, block=False)
                    except Exception as e:
                        logger.error(f"Failed to refill pool: {e}")
                        break

    def health_check_all(self):
        """Verify all connections in pool are healthy."""
        with self.lock:
            unhealthy_ids = []
            for conn_id, pooled in self.all_connections.items():
                if not pooled.health_check():
                    unhealthy_ids.append(conn_id)

            # Remove unhealthy connections
            for conn_id in unhealthy_ids:
                pooled = self.all_connections.pop(conn_id, None)
                if pooled:
                    pooled.close()

            if unhealthy_ids:
                with self.stats_lock:
                    self.stats["health_checks"] += 1
                logger.warning(f"Removed {len(unhealthy_ids)} unhealthy connections")

    def close(self):
        """Close all connections and shut down the pool."""
        with self.lock:
            for pooled in self.all_connections.values():
                try:
                    pooled.close()
                except Exception as e:
                    logger.error(f"Error closing connection {pooled.connection_id}: {e}")

            self.all_connections.clear()

            # Drain the queue
            while not self.available_connections.empty():
                try:
                    self.available_connections.get_nowait()
                except Empty:
                    break

        logger.info(f"ConnectionPool closed: {self.get_stats()}")

    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        with self.lock:
            with self.stats_lock:
                return {
                    "db_path": self.db_path,
                    "pool_size": len(self.all_connections),
                    "max_pool_size": self.max_pool_size,
                    "available": self.available_connections.qsize(),
                    **self.stats,
                }

    @contextmanager
    def get_connection_context(self, timeout: float = 5.0):
        """
        Context manager for acquiring and automatically returning connections.

        Usage:
            with pool.get_connection_context() as conn:
                cur = conn.cursor()
                cur.execute("SELECT ...")
        """
        pooled = self.get_connection(timeout=timeout)
        try:
            yield pooled.conn
        finally:
            self.return_connection(pooled)


# Global pool instance (optional; callers can create their own)
_default_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def init_pool(db_path: str, pool_size: int = 5, **kwargs) -> ConnectionPool:
    """Initialize and return the global connection pool."""
    global _default_pool
    with _pool_lock:
        if _default_pool is not None:
            logger.warning("Global pool already initialized; closing and reinitializing")
            _default_pool.close()
        _default_pool = ConnectionPool(db_path, pool_size=pool_size, **kwargs)
        return _default_pool


def get_pool() -> Optional[ConnectionPool]:
    """Get the global connection pool (or None if not initialized)."""
    return _default_pool


def close_pool():
    """Close the global connection pool."""
    global _default_pool
    with _pool_lock:
        if _default_pool:
            _default_pool.close()
            _default_pool = None
