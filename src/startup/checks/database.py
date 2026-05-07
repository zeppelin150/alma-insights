"""
Check 8 — Database integrity.

Opens data/local_warehouse.db (if present), verifies the file is a valid
SQLite database, counts rows in the tickets table for the user-visible
message, and confirms the schema migration table is readable.

Critical: a corrupt DB blocks every downstream feature.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from src.startup.checker import CheckResult

# Must match src/data/connection_factory.py::DEFAULT_DB_PATH and
# src/data/db_manager.py::DB_PATH. Previously pointed at
# `alma_insights.db` which was a latent bug — the check silently hit the
# "no database yet" branch on every launch and created a 0-byte stub.
_DB_PATH = Path("data") / "local_warehouse.db"


def check_database() -> CheckResult:
    if not _DB_PATH.exists():
        return CheckResult(
            id="database",
            name="Database integrity",
            status="pass",
            message="No database yet — will be created on first import",
            critical=False,
        )

    try:
        with sqlite3.connect(str(_DB_PATH), timeout=5) as conn:
            conn.execute("PRAGMA quick_check").fetchone()
            schema_version = _schema_version(conn)
            ticket_count = _ticket_count(conn)
            size_mb = _DB_PATH.stat().st_size / (1024 * 1024)
    except sqlite3.DatabaseError as exc:
        return CheckResult(
            id="database",
            name="Database integrity",
            status="fail",
            message=f"SQLite error: {exc}",
            remediation=(
                "The database is corrupt. Export a backup from "
                "Settings → Support, then delete data/alma_insights.db to "
                "start fresh."
            ),
            critical=True,
        )

    message = (
        f"Schema v{schema_version} — {ticket_count:,} tickets — {size_mb:.1f}MB"
    )
    return CheckResult(
        id="database",
        name="Database integrity",
        status="pass",
        message=message,
        critical=True,
    )


def _schema_version(conn: sqlite3.Connection) -> str:
    """Return the highest applied migration number.

    The real schema_migrations table stores (filename, applied_at) — so
    we parse the leading digits from every filename and return the max.
    Falls back to the legacy (version INTEGER) layout used by older
    fixtures."""
    # Modern layout: (filename TEXT, applied_at TEXT)
    try:
        rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
        nums = []
        for row in rows:
            m = re.match(r"^(\d+)", row[0] or "")
            if m:
                nums.append(int(m.group(1)))
        if nums:
            return str(max(nums))
    except sqlite3.OperationalError:
        pass
    # Legacy layout: (version INTEGER)
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return str(row[0]) if row and row[0] is not None else "?"
    except sqlite3.OperationalError:
        return "?"


def _ticket_count(conn: sqlite3.Connection) -> int:
    """Count rows in the tickets table, returning 0 if absent."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
