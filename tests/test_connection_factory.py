"""Tests for centralized SQLite connection factory."""

import sqlite3
import pytest
from src.data.connection_factory import get_connection, atomic


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    c.commit()
    yield c
    c.close()


class TestGetConnection:
    def test_wal_mode(self, db_path):
        conn = get_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_busy_timeout_set(self, db_path):
        conn = get_connection(db_path)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 30_000
        conn.close()

    def test_synchronous_full(self, db_path):
        conn = get_connection(db_path)
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 2  # FULL
        conn.close()

    def test_foreign_keys_on(self, db_path):
        conn = get_connection(db_path)
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
        conn.close()

    def test_row_factory_is_row(self, db_path):
        conn = get_connection(db_path)
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_readonly_mode(self, db_path):
        # Create db first so readonly can open it
        w = get_connection(db_path)
        w.execute("CREATE TABLE t (id INTEGER)")
        w.commit()
        w.close()

        ro = get_connection(db_path, readonly=True)
        rows = ro.execute("SELECT * FROM t").fetchall()
        assert rows == []
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES (1)")
        ro.close()


class TestAtomic:
    def test_commits_on_success(self, conn):
        with atomic(conn):
            conn.execute("INSERT INTO t VALUES (1, 'a')")
            conn.execute("INSERT INTO t VALUES (2, 'b')")

        rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert rows == 2

    def test_rolls_back_on_exception(self, conn):
        conn.execute("INSERT INTO t VALUES (99, 'pre')")
        conn.commit()

        with pytest.raises(ValueError):
            with atomic(conn):
                conn.execute("INSERT INTO t VALUES (1, 'a')")
                raise ValueError("boom")

        rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        assert rows == 1  # only the pre-existing row

    def test_nested_atomic_raises(self, conn):
        with atomic(conn):
            conn.execute("INSERT INTO t VALUES (1, 'a')")
            with pytest.raises(RuntimeError, match="cannot be nested"):
                with atomic(conn):
                    pass


class TestWALTuning:
    """Phase 1: WAL tuning settings from migration 016 / connection_factory refresh."""

    def test_wal_settings_enforced(self, db_path):
        """Every connection gets WAL + busy_timeout + wal_autocheckpoint set."""
        conn = get_connection(db_path)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
        # Default autocheckpoint is 1000 pages unless settings override
        autocp = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert autocp == 1000
        conn.close()

    def test_writer_gets_normal_sync(self, db_path):
        """for_writer=True downgrades synchronous to NORMAL (sqlite code 1)."""
        conn = get_connection(db_path, for_writer=True)
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 1  # NORMAL
        conn.close()

    def test_reader_stays_full_sync(self, db_path):
        """Default (for_writer=False) keeps synchronous=FULL (sqlite code 2)."""
        conn = get_connection(db_path, for_writer=False)
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        conn.close()

    def test_wal_health_check_no_wal_yet(self, db_path, tmp_path):
        """wal_health_check returns 'none' when WAL file doesn't exist."""
        from src.data.connection_factory import wal_health_check
        result = wal_health_check(db_path)
        assert result["action_taken"] == "none"
        assert result["wal_size_bytes"] == 0

    def test_wal_health_check_below_threshold(self, db_path):
        """Small WAL (present but tiny) produces 'none' action."""
        from src.data.connection_factory import wal_health_check
        conn = get_connection(db_path)
        conn.execute("CREATE TABLE tst (id INTEGER)")
        conn.execute("INSERT INTO tst VALUES (1)")
        conn.commit()
        conn.close()
        result = wal_health_check(db_path)
        assert result["action_taken"] == "none"
        # WAL threshold default = 50MB; tiny WAL should be well under
        assert result["wal_size_mb"] < 50
