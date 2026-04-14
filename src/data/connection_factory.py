"""Centralized SQLite connection factory.

Every database connection in Alma Insights MUST go through
get_connection(). Direct sqlite3.connect() calls are banned.

Phase 1 (2026-04-14): WAL tuning added to bound WAL growth under scan load.
`wal_autocheckpoint` caps the WAL at ~1000 pages between checkpoints;
`write_sync_mode=NORMAL` is a 3-5x write-throughput win that's still safe under
WAL (readers still see FULL sync). Both are settings-controlled so an operator
can dial them back if needed. A startup health check checkpoints the WAL if the
file has grown beyond `wal_health_max_mb`.

Public API:
    get_connection(db_path=None, readonly=False, *, for_writer=False)
    atomic(conn)
    wal_health_check(db_path) -> dict

Module deps: stdlib only (sqlite3, pathlib, os, logging)
Module dependents: every src/data/* module, agents, services, UI
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("alma.connection_factory")

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_DB_PATH = DB_DIR / "local_warehouse.db"

_BUSY_TIMEOUT_MS = 30_000  # 30s retry on SQLITE_BUSY

# Defaults mirror what settings_manager would return if no override is present.
# Defined as module-level constants so tests can monkeypatch without pulling
# settings_manager into the import graph.
_DEFAULT_WAL_AUTOCHECKPOINT_PAGES = 1000
_DEFAULT_WRITE_SYNC_MODE = "NORMAL"   # writers. Readers always use FULL.
_DEFAULT_READ_SYNC_MODE = "FULL"
_DEFAULT_WAL_HEALTH_MAX_MB = 50


def _load_wal_settings() -> dict[str, Any]:
    """Read WAL tuning settings via settings_manager with safe defaults.

    We import lazily so that the factory remains usable in contexts where
    settings_manager isn't initialized yet (e.g. tooling scripts, migrations
    running before the app is fully up).

    Returns:
        dict with keys: wal_autocheckpoint (int), write_sync_mode (str),
        read_sync_mode (str), wal_health_max_mb (int).
    """
    try:
        from src.data.settings_manager import get_section   # noqa: PLC0415
        db_cfg = get_section("database", {}) or {}
    except Exception:   # noqa: BLE001 — settings_manager unavailable, fall back
        db_cfg = {}
    return {
        "wal_autocheckpoint": int(db_cfg.get("wal_autocheckpoint", _DEFAULT_WAL_AUTOCHECKPOINT_PAGES)),
        "write_sync_mode": str(db_cfg.get("write_sync_mode", _DEFAULT_WRITE_SYNC_MODE)).upper(),
        "read_sync_mode": str(db_cfg.get("read_sync_mode", _DEFAULT_READ_SYNC_MODE)).upper(),
        "wal_health_max_mb": int(db_cfg.get("wal_health_max_mb", _DEFAULT_WAL_HEALTH_MAX_MB)),
    }


def _valid_sync_mode(mode: str) -> str:
    """Clamp sync mode to a SQLite-accepted value, fallback FULL if invalid."""
    mode_upper = mode.upper()
    if mode_upper in {"OFF", "NORMAL", "FULL", "EXTRA"}:
        return mode_upper
    logger.warning("Invalid sync mode %r, defaulting to FULL", mode)
    return "FULL"


def get_connection(
    db_path: str | Path | None = None,
    readonly: bool = False,
    *,
    for_writer: bool = False,
) -> sqlite3.Connection:
    """Create a properly configured SQLite connection.

    Guarantees:
      - WAL journal mode (concurrent readers + one writer)
      - busy_timeout = 30s (retries instead of immediate SQLITE_BUSY)
      - foreign_keys = ON (enforces referential integrity)
      - row_factory = sqlite3.Row (dict-like access)
      - wal_autocheckpoint tuned to bound WAL growth during scans
      - synchronous = NORMAL for writer connections, FULL for readers
        (settings-controlled; defaults are safe under WAL)

    Args:
        db_path: Path to database. Defaults to local_warehouse.db.
        readonly: If True, opens in read-only mode (URI file:...?mode=ro).
        for_writer: If True, uses the NORMAL sync mode tuned for throughput
            (3-5x faster INSERTs; still safe under WAL). Scan orchestrator,
            ingestion, and ticket_index_writer should pass True. UI reads and
            one-off queries should pass False (the default).

    Returns:
        Configured sqlite3.Connection.

    Raises:
        sqlite3.OperationalError: if the path cannot be opened.
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH

    if readonly:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))

    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")

    cfg = _load_wal_settings()
    sync_target = cfg["write_sync_mode"] if (for_writer and not readonly) else cfg["read_sync_mode"]
    conn.execute(f"PRAGMA synchronous = {_valid_sync_mode(sync_target)}")

    # wal_autocheckpoint only matters on write paths but setting it on a RO
    # connection is a no-op at worst — keep it symmetric for simplicity.
    conn.execute(f"PRAGMA wal_autocheckpoint = {cfg['wal_autocheckpoint']}")

    return conn


def wal_health_check(db_path: str | Path | None = None) -> dict[str, Any]:
    """Report WAL file size; if above threshold, force a PASSIVE checkpoint.

    Called by app startup (main.py) and by the scan orchestrator before a scan.
    PASSIVE is the mildest form — it yields to active writers rather than
    blocking them. For aggressive reclamation during idle, use TRUNCATE.

    Args:
        db_path: Path to database. Defaults to local_warehouse.db.

    Returns:
        dict: {wal_size_bytes, wal_size_mb, threshold_mb, action_taken,
               checkpointed_pages, log_pages}. action_taken is one of
               'none', 'checkpoint_passive', 'checkpoint_failed'.
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    wal_path = path.with_name(path.name + "-wal")
    cfg = _load_wal_settings()
    threshold_bytes = cfg["wal_health_max_mb"] * 1024 * 1024

    result: dict[str, Any] = {
        "wal_size_bytes": 0,
        "wal_size_mb": 0.0,
        "threshold_mb": cfg["wal_health_max_mb"],
        "action_taken": "none",
        "checkpointed_pages": 0,
        "log_pages": 0,
    }

    if not wal_path.exists():
        return result

    try:
        size = os.path.getsize(wal_path)
    except OSError as exc:
        logger.warning("Could not stat WAL at %s: %s", wal_path, exc)
        return result

    result["wal_size_bytes"] = size
    result["wal_size_mb"] = round(size / (1024 * 1024), 2)

    if size <= threshold_bytes:
        return result

    # Above threshold — checkpoint passively to reclaim.
    try:
        conn = get_connection(path)
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        # Returns (busy, log, checkpointed) per sqlite docs
        if row:
            result["log_pages"] = int(row[1]) if row[1] is not None else 0
            result["checkpointed_pages"] = int(row[2]) if row[2] is not None else 0
        conn.close()
        result["action_taken"] = "checkpoint_passive"
        logger.info(
            "WAL checkpoint: size=%.2fMB > threshold=%dMB, checkpointed %d pages",
            result["wal_size_mb"], cfg["wal_health_max_mb"], result["checkpointed_pages"],
        )
    except sqlite3.Error as exc:
        logger.error("WAL checkpoint failed: %s", exc)
        result["action_taken"] = "checkpoint_failed"

    return result


@contextmanager
def atomic(conn: sqlite3.Connection):
    """Transaction context manager with automatic rollback.

    Usage:
        with atomic(conn):
            conn.execute("DELETE FROM ...")
            conn.execute("INSERT INTO ...")
        # commits on success, rolls back on any exception

    CRITICAL: Use this for ALL multi-step mutations.
    Never call conn.commit() directly inside an atomic() block.
    """
    if conn.in_transaction:
        raise RuntimeError(
            "atomic() cannot be nested — SQLite does not support "
            "reliable nested transactions across threads. "
            "Refactor to use a single atomic() block."
        )

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
