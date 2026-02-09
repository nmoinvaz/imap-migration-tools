"""
IMAP Connection Pool

Thread-safe pool for borrowing and returning IMAP connections.
Workers borrow a connection, use it, and return it. Broken connections
are discarded automatically via the context manager.
"""

import collections
import threading
from contextlib import contextmanager

import imap_common
import imap_session


class ConnectionPool:
    """Thread-safe IMAP connection pool.

    Connections are created via imap_common.get_imap_connection_from_conf and
    health-checked via imap_session.ensure_connection on each borrow.

    Args:
        conf: Shared mutable config dict (OAuth2 token updates propagate).
        max_size: Maximum concurrent connections (match max_workers).
        log_fn: Optional logging function (default: imap_common.safe_print).
    """

    def __init__(self, conf, max_size, log_fn=None):
        self.conf = conf
        self._max_size = max_size
        self._log_fn = log_fn or imap_common.safe_print
        self._semaphore = threading.Semaphore(max_size)
        self._idle = collections.deque()
        self._lock = threading.Lock()
        self._closed = False

    @contextmanager
    def connection(self):
        """Borrow a connection. Returns on normal exit, discards on exception."""
        self._semaphore.acquire()
        conn = None
        try:
            with self._lock:
                if self._closed:
                    self._semaphore.release()
                    raise RuntimeError("ConnectionPool is shut down")
                if self._idle:
                    conn = self._idle.pop()

            # Health-check or create
            conn = imap_session.ensure_connection(conn, self.conf)
            if conn is None:
                raise RuntimeError("Failed to establish IMAP connection")

            yield conn

        except BaseException:
            # Discard broken connection on any exception
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
            self._semaphore.release()
            raise
        else:
            # Return healthy connection to pool
            with self._lock:
                if not self._closed:
                    self._idle.append(conn)
                else:
                    try:
                        conn.logout()
                    except Exception:
                        pass
            self._semaphore.release()

    def shutdown(self):
        """Logout all idle connections and mark pool as closed."""
        with self._lock:
            self._closed = True
            while self._idle:
                conn = self._idle.pop()
                try:
                    conn.logout()
                except Exception:
                    pass
