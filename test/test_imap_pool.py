"""Tests for IMAP Connection Pool."""

import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import imap_pool


def _make_conf():
    return {
        "host": "imap.example.com",
        "user": "user@example.com",
        "password": "pass",
        "oauth2_token": None,
        "oauth2": None,
    }


def _make_mock_conn():
    conn = MagicMock()
    conn.noop.return_value = ("OK", [])
    conn.logout.return_value = ("OK", [])
    return conn


class TestConnectionPool:
    def test_borrow_returns_connection(self):
        conf = _make_conf()
        mock_conn = _make_mock_conn()

        with patch("imap_session.ensure_connection", return_value=mock_conn):
            pool = imap_pool.ConnectionPool(conf, max_size=2)
            with pool.connection() as conn:
                assert conn is mock_conn
            pool.shutdown()

    def test_return_makes_connection_available(self):
        conf = _make_conf()
        mock_conn = _make_mock_conn()

        with patch("imap_session.ensure_connection", return_value=mock_conn) as mock_ensure:
            pool = imap_pool.ConnectionPool(conf, max_size=2)

            # First borrow creates a new connection
            with pool.connection() as conn:
                assert conn is mock_conn

            # Second borrow should reuse the returned connection
            with pool.connection() as conn:
                assert conn is mock_conn

            # ensure_connection is called each time (health-check), but
            # on the second call it gets the idle connection passed in
            assert mock_ensure.call_count == 2
            # First call: conn=None (no idle), second call: conn=mock_conn (from idle)
            assert mock_ensure.call_args_list[0][0][0] is None
            assert mock_ensure.call_args_list[1][0][0] is mock_conn

            pool.shutdown()

    def test_discard_on_exception(self):
        conf = _make_conf()
        mock_conn = _make_mock_conn()

        with patch("imap_session.ensure_connection", return_value=mock_conn):
            pool = imap_pool.ConnectionPool(conf, max_size=2)

            with pytest.raises(ValueError):
                with pool.connection() as conn:
                    raise ValueError("test error")

            # Connection should have been logged out (discarded)
            mock_conn.logout.assert_called_once()

            # Pool should still be usable (slot freed)
            mock_conn2 = _make_mock_conn()
            with patch("imap_session.ensure_connection", return_value=mock_conn2):
                with pool.connection() as conn:
                    assert conn is mock_conn2

            pool.shutdown()

    def test_health_check_recreates_unhealthy(self):
        conf = _make_conf()
        old_conn = _make_mock_conn()
        new_conn = _make_mock_conn()

        call_count = [0]

        def mock_ensure(conn, _conf):
            call_count[0] += 1
            if call_count[0] == 1:
                return old_conn  # First borrow: create
            # Second borrow: old_conn fails health check, return new
            return new_conn

        with patch("imap_session.ensure_connection", side_effect=mock_ensure):
            pool = imap_pool.ConnectionPool(conf, max_size=2)

            with pool.connection() as conn:
                assert conn is old_conn

            # Second borrow: ensure_connection gets old_conn, returns new_conn
            with pool.connection() as conn:
                assert conn is new_conn

            pool.shutdown()

    def test_semaphore_blocks_when_exhausted(self):
        conf = _make_conf()
        conns = [_make_mock_conn() for _ in range(2)]
        conn_idx = [0]

        def mock_ensure(conn, _conf):
            if conn is not None:
                return conn
            idx = conn_idx[0]
            conn_idx[0] += 1
            return conns[idx]

        with patch("imap_session.ensure_connection", side_effect=mock_ensure):
            pool = imap_pool.ConnectionPool(conf, max_size=1)
            blocked = threading.Event()
            acquired = threading.Event()

            def hold_connection():
                with pool.connection():
                    acquired.set()
                    blocked.wait(timeout=5)

            t = threading.Thread(target=hold_connection)
            t.start()
            acquired.wait(timeout=5)

            # Try to borrow — should block because pool size is 1
            got_conn = threading.Event()

            def try_borrow():
                with pool.connection():
                    got_conn.set()

            t2 = threading.Thread(target=try_borrow)
            t2.start()

            # Should NOT get connection yet
            assert not got_conn.wait(timeout=0.3)

            # Release first connection
            blocked.set()
            t.join(timeout=5)

            # Now second thread should get through
            assert got_conn.wait(timeout=5)
            t2.join(timeout=5)

            pool.shutdown()

    def test_shutdown_logs_out_idle(self):
        conf = _make_conf()
        mock_conn = _make_mock_conn()

        with patch("imap_session.ensure_connection", return_value=mock_conn):
            pool = imap_pool.ConnectionPool(conf, max_size=2)

            with pool.connection():
                pass  # Return connection to idle

            pool.shutdown()
            mock_conn.logout.assert_called_once()

    def test_borrow_after_shutdown_raises(self):
        conf = _make_conf()
        pool = imap_pool.ConnectionPool(conf, max_size=2)
        pool.shutdown()

        with pytest.raises(RuntimeError, match="shut down"):
            with pool.connection():
                pass

    def test_connection_returned_after_shutdown_is_logged_out(self):
        conf = _make_conf()
        mock_conn = _make_mock_conn()
        barrier = threading.Barrier(2, timeout=5)

        with patch("imap_session.ensure_connection", return_value=mock_conn):
            pool = imap_pool.ConnectionPool(conf, max_size=2)

            def use_then_return():
                with pool.connection():
                    barrier.wait()  # sync: ensure shutdown runs while conn is borrowed
                    time.sleep(0.1)  # give shutdown time to set _closed

            t = threading.Thread(target=use_then_return)
            t.start()
            barrier.wait()  # wait until worker is inside context
            pool.shutdown()  # sets _closed=True, drains idle (empty)
            t.join(timeout=5)

            # Connection was borrowed during shutdown; on return it should be logged out
            mock_conn.logout.assert_called()

    def test_concurrent_borrow_return(self):
        conf = _make_conf()
        num_workers = 4

        def mock_ensure(conn, _conf):
            if conn is not None:
                return conn
            return _make_mock_conn()

        with patch("imap_session.ensure_connection", side_effect=mock_ensure):
            pool = imap_pool.ConnectionPool(conf, max_size=num_workers)
            results = []
            errors = []

            def worker(i):
                try:
                    with pool.connection() as conn:
                        assert conn is not None
                        time.sleep(0.01)
                    results.append(i)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_workers * 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert len(errors) == 0, f"Errors: {errors}"
            assert len(results) == num_workers * 2

            pool.shutdown()

    def test_failed_connection_creation_raises(self):
        conf = _make_conf()

        with patch("imap_session.ensure_connection", return_value=None):
            pool = imap_pool.ConnectionPool(conf, max_size=2)

            with pytest.raises(RuntimeError, match="Failed to establish"):
                with pool.connection():
                    pass

            pool.shutdown()
