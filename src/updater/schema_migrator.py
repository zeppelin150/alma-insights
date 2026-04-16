"""
Alma Insights — Schema Migrator (Phase 2)

Applies pending SQL migration files to the SQLite database.  Each migration
runs in its own transaction and is recorded in a ``schema_migrations`` table
so it is never re-applied.

Migration files live in ``migrations/`` at the project root and are named
``NNN_description.sql`` (e.g. ``001_initial_baseline.sql``).

Usage (called from ``db_manager.initialize()``):
    from src.updater.schema_migrator import SchemaMigrator
    SchemaMigrator().migrate(conn)
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path

logger = logging.getLogger("alma.migrator")

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"

# Matches: ALTER TABLE <table> ADD COLUMN <col> <type...>; with optional comments
_ALTER_ADD_COL_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+([^;]*?)\s*;[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)


class SchemaMigrator:
    """Apply numbered .sql migrations that haven't been run yet."""

    def __init__(self, migrations_dir: Path | None = None) -> None:
        self._dir = migrations_dir or _MIGRATIONS_DIR

    # ── public ──────────────────────────────────────────────

    def migrate(self, conn: sqlite3.Connection) -> list[str]:
        """Run all pending migrations.  Returns list of applied filenames."""
        self._ensure_tracking_table(conn)
        applied = self._applied_set(conn)
        pending = self.pending(conn)

        results: list[str] = []
        for mig_path in pending:
            name = mig_path.name
            if name in applied:
                continue
            try:
                sql = mig_path.read_text(encoding="utf-8").strip()
                if sql:
                    # Apply ALTER TABLE ADD COLUMN idempotently (skip if exists),
                    # then run the remaining SQL via executescript.
                    cleaned = self._apply_alters_idempotent(sql, conn)
                    if cleaned.strip():
                        conn.executescript(cleaned)
                # Run Python post-hook if one exists
                self._run_post_hook(name, conn)
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (?)",
                    (name,),
                )
                conn.commit()
                results.append(name)
                logger.info("Applied migration: %s", name)
            except Exception as exc:
                logger.error("Migration %s failed: %s", name, exc)
                # Don't apply further migrations after a failure
                raise RuntimeError(f"Migration {name} failed: {exc}") from exc

        if results:
            logger.info("Applied %d migration(s): %s", len(results), results)
        return results

    @staticmethod
    def _apply_alters_idempotent(sql: str, conn: sqlite3.Connection) -> str:
        """Extract ALTER TABLE ADD COLUMN from SQL, apply only if missing.

        SQLite has no IF NOT EXISTS for ALTER TABLE ADD COLUMN, so we
        check PRAGMA table_info before each ADD. Statements that would
        fail on duplicate columns are applied safely; the rest of the
        SQL is returned for executescript.
        """
        alters = _ALTER_ADD_COL_RE.findall(sql)
        for table, col, col_type in alters:
            clean_type = col_type.split("--")[0].strip()
            existing = {
                r[1] for r in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if col not in existing:
                stmt = f"ALTER TABLE {table} ADD COLUMN {col} {clean_type}"
                conn.execute(stmt)
                logger.info("Added column %s.%s (%s)", table, col, clean_type)
            else:
                logger.info("Column %s.%s already exists — skipped", table, col)
        return _ALTER_ADD_COL_RE.sub("", sql)

    def current_version(self, conn: sqlite3.Connection) -> int:
        """Return the highest applied migration number, or 0."""
        self._ensure_tracking_table(conn)
        applied = self._applied_set(conn)
        if not applied:
            return 0
        # Extract leading digits from filenames
        numbers = []
        for name in applied:
            prefix = name.split("_", 1)[0]
            try:
                numbers.append(int(prefix))
            except ValueError:
                pass
        return max(numbers) if numbers else 0

    def pending(self, conn: sqlite3.Connection) -> list[Path]:
        """Return list of migration files not yet applied, sorted by name."""
        if not self._dir.is_dir():
            return []
        self._ensure_tracking_table(conn)
        applied = self._applied_set(conn)
        files = sorted(self._dir.glob("*.sql"))
        return [f for f in files if f.name not in applied]

    # ── internal ────────────────────────────────────────────

    @staticmethod
    def _ensure_tracking_table(conn: sqlite3.Connection):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename    TEXT PRIMARY KEY,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    @staticmethod
    def _applied_set(conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
        return {row[0] for row in rows}

    # ── Python post-hooks (keyed by migration filename) ────

    _POST_HOOKS: dict[str, str] = {
        "009_chat_data_layer.sql": "_posthook_009_extract_json_messages",
    }

    def _run_post_hook(self, filename: str, conn: sqlite3.Connection) -> None:
        hook_name = self._POST_HOOKS.get(filename)
        if hook_name:
            getattr(self, hook_name)(conn)

    @staticmethod
    def _posthook_009_extract_json_messages(conn: sqlite3.Connection) -> None:
        """Extract existing JSON message blobs into chat_messages rows."""
        # Check if messages column exists on chat_sessions
        cols = {
            row[1] if isinstance(row, tuple) else row["name"]
            for row in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()
        }
        if "messages" not in cols:
            return

        rows = conn.execute(
            "SELECT session_id, messages FROM chat_sessions WHERE messages IS NOT NULL"
        ).fetchall()

        migrated = 0
        for row in rows:
            session_id = row[0] if isinstance(row, tuple) else row["session_id"]
            raw = row[1] if isinstance(row, tuple) else row["messages"]
            if not raw:
                continue
            try:
                messages = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            for ordinal, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "user")
                content = msg.get("content", "")
                timestamp = msg.get("timestamp")
                tool_calls = msg.get("tool_calls")
                conn.execute(
                    """INSERT OR IGNORE INTO chat_messages
                       (message_id, session_id, ordinal, role, content,
                        created_at, tool_calls)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        session_id,
                        ordinal,
                        role,
                        content,
                        timestamp,
                        json.dumps(tool_calls) if tool_calls else None,
                    ),
                )
                migrated += 1

        if migrated:
            logger.info("Migrated %d messages from JSON blobs to chat_messages", migrated)
