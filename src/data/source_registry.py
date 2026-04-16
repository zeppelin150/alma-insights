"""
Alma Insights — Source Registry
CRUD operations for data sources. Each source gets its own set of tables
(tickets, conversations, comments, FTS) identified by a table_prefix.
"""

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger("alma.source_registry")

# Valid characters for table prefix (used in SQL table names)
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")

# ── Source Type Templates ──────────────────────
# Default column mappings and config for known source types.
SOURCE_TYPE_TEMPLATES = {
    "zendesk": {
        "conversation_structure": "row_per_comment",
        "column_mapping": {
            "ticket_id": "ticket_id",
            "subject": "subject",
            "trc_code": "trc_code",
            "status": "status",
            "csat_score": "csat_score",
            "created_at": "created_at",
            "comment_body": "comment_body",
            "author_role": "author_role",
        },
    },
    "kodif": {
        "conversation_structure": "self_contained",
        "column_mapping": {
            "conversation_id": "ticket_id",
            "chat_id": "ticket_id",
            "subject": "subject",
            "category": "trc_code",
            "status": "status",
            "csat_score": "csat_score",
            "created_at": "created_at",
            "conversation_body": "full_thread",
            "chat_body": "full_thread",
        },
    },
    "custom": {
        "conversation_structure": "row_per_comment",
        "column_mapping": {},
    },
}


def get_source_template(source_type: str) -> dict:
    """Return the default template for a source type."""
    return SOURCE_TYPE_TEMPLATES.get(source_type, SOURCE_TYPE_TEMPLATES["custom"])


class SourceRegistry:
    """Manages registered data sources and their table mappings."""

    def __init__(self, conn):
        self.conn = conn

    # ── CRUD ──────────────────────────────────────────────

    def create_source(self, source_id: str, source_name: str, source_type: str,
                      table_prefix: str, column_mapping: dict = None) -> dict:
        """Register a new data source and create its tables.

        Args:
            source_id: Unique identifier (e.g., 'zendesk_provider_group')
            source_name: Display name (e.g., 'RCM Support Tickets')
            source_type: Source type ('zendesk', 'kodif', 'custom')
            table_prefix: SQL-safe prefix for table names
            column_mapping: Optional dict mapping source columns to internal schema

        Returns:
            The created source record as a dict.

        Raises:
            ValueError: If source_id already exists or table_prefix is invalid.
        """
        if self.get_source(source_id):
            raise ValueError(f"Source '{source_id}' already exists")

        table_prefix = self._validate_prefix(table_prefix)

        # Check prefix uniqueness
        existing = self.conn.execute(
            "SELECT source_id FROM source_registry WHERE table_prefix = ?",
            (table_prefix,)
        ).fetchone()
        if existing:
            raise ValueError(f"Table prefix '{table_prefix}' already in use by '{existing[0]}'")

        now = datetime.now(timezone.utc).isoformat()
        mapping_json = json.dumps(column_mapping) if column_mapping else None

        self.conn.execute(
            """INSERT INTO source_registry
               (source_id, source_name, source_type, table_prefix, column_mapping, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, source_name, source_type, table_prefix, mapping_json, now),
        )
        self.conn.commit()

        # Create per-source tables
        from src.data.schema_builder import create_source_tables
        create_source_tables(self.conn, table_prefix)

        logger.info("Registered source: %s (%s) with prefix '%s'", source_id, source_type, table_prefix)
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> dict | None:
        """Look up a source by ID. Returns dict or None."""
        row = self.conn.execute(
            "SELECT * FROM source_registry WHERE source_id = ?", (source_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_sources(self) -> list[dict]:
        """Return all registered sources."""
        rows = self.conn.execute(
            "SELECT * FROM source_registry ORDER BY is_default DESC, source_name"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_default_source(self) -> dict | None:
        """Return the default source (is_default=1)."""
        row = self.conn.execute(
            "SELECT * FROM source_registry WHERE is_default = 1"
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def update_source(self, source_id: str, **kwargs):
        """Update source metadata (name, column_mapping). Does not affect data."""
        allowed = {"source_name", "column_mapping", "last_import_at", "ticket_count"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        if "column_mapping" in updates and isinstance(updates["column_mapping"], dict):
            updates["column_mapping"] = json.dumps(updates["column_mapping"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [source_id]
        self.conn.execute(
            f"UPDATE source_registry SET {set_clause} WHERE source_id = ?",  # noqa: S608
            values,
        )
        self.conn.commit()

    def delete_source(self, source_id: str, force: bool = False):
        """Delete a source registration. Refuses if data exists unless force=True."""
        source = self.get_source(source_id)
        if not source:
            raise ValueError(f"Source '{source_id}' not found")
        if source["is_default"]:
            raise ValueError("Cannot delete the default source")

        if not force:
            prefix = source["table_prefix"]
            count = self.conn.execute(
                f"SELECT COUNT(*) FROM [{prefix}_tickets]"  # noqa: S608
            ).fetchone()[0]
            if count > 0:
                raise ValueError(
                    f"Source '{source_id}' has {count} tickets. Use force=True to delete."
                )

        self.conn.execute("DELETE FROM source_registry WHERE source_id = ?", (source_id,))
        self.conn.commit()
        logger.info("Deleted source: %s", source_id)

    # ── Table Name Helpers ────────────────────────────────

    def get_table_name(self, source_id: str, table_type: str) -> str:
        """Get the table name for a source + type.

        Args:
            source_id: Source identifier
            table_type: One of 'tickets', 'conversations', 'comments', 'fts'

        Returns:
            Full table name (e.g., 'zendesk_default_conversations')
        """
        source = self.get_source(source_id)
        if not source:
            raise ValueError(f"Source '{source_id}' not found")
        return f"{source['table_prefix']}_{table_type}"

    def get_all_table_names(self, table_type: str) -> list[str]:
        """Get table names for a type across ALL sources."""
        sources = self.list_sources()
        return [f"{s['table_prefix']}_{table_type}" for s in sources]

    # ── Internal ──────────────────────────────────────────

    def _validate_prefix(self, prefix: str) -> str:
        """Validate and sanitize a table prefix."""
        prefix = prefix.lower().strip()
        if not _PREFIX_RE.match(prefix):
            raise ValueError(
                f"Invalid table prefix '{prefix}': must be 3-50 lowercase alphanumeric + underscores, "
                "starting with a letter"
            )
        return prefix

    def _row_to_dict(self, row) -> dict:
        """Convert a sqlite3.Row or tuple to dict."""
        if row is None:
            return None
        cols = [d[0] for d in self.conn.execute("PRAGMA table_info(source_registry)").fetchall()]
        # Use column index mapping
        col_names = ["source_id", "source_name", "source_type", "table_prefix",
                     "column_mapping", "created_at", "is_default", "ticket_count",
                     "last_import_at"]
        d = {}
        for i, name in enumerate(col_names):
            d[name] = row[i] if i < len(row) else None

        # Parse JSON fields
        if d.get("column_mapping"):
            try:
                d["column_mapping"] = json.loads(d["column_mapping"])
            except (json.JSONDecodeError, TypeError):
                pass

        return d
