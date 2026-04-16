"""FTS5 keyword integration for the filter engine.

Generates FTS5 MATCH clauses for per-source FTS virtual tables,
with a LIKE fallback when the FTS table may not be populated.

Uses warehouse query layer to route to the correct per-source tables.
Falls back to legacy shared tables (conversations_fts, conversations) when
source_registry is not available.
"""

from __future__ import annotations


def _get_fts_table_name(conn=None) -> str:
    """Get the FTS table name (per-source or legacy)."""
    if conn is None:
        return "conversations_fts"
    try:
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(conn, SourceRegistry(conn))
        tables = wq._get_tables("fts")
        return tables[0] if tables else "conversations_fts"
    except Exception:
        return "conversations_fts"


def _get_conv_table_name(conn=None) -> str:
    """Get the conversations table name (per-source or legacy)."""
    if conn is None:
        return "conversations"
    try:
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        wq = WarehouseQuery(conn, SourceRegistry(conn))
        tables = wq._get_tables("conversations")
        return tables[0] if tables else "conversations"
    except Exception:
        return "conversations"


def build_fts_clause(
    keyword: str, base_alias: str, base_table: str, conn=None
) -> tuple[str, list]:
    """Build an FTS5 MATCH clause or LIKE fallback.

    Returns (sql_fragment, params) to be AND-ed into the WHERE clause.
    """
    safe_keyword = _sanitize_fts_query(keyword)
    fts_table = _get_fts_table_name(conn)

    clause = (
        f"{base_alias}.ticket_id IN ("
        f"SELECT ticket_id FROM [{fts_table}] "
        f"WHERE [{fts_table}] MATCH ?"
        ")"
    )
    return clause, [safe_keyword]


def build_like_fallback(
    keyword: str, base_alias: str, base_table: str, conn=None
) -> tuple[str, list]:
    """LIKE-based fallback when FTS5 is unavailable."""
    like_param = f"%{keyword}%"
    conv_table = _get_conv_table_name(conn)

    if base_table in ("conversations", conv_table):
        clause = (
            f"({base_alias}.full_thread LIKE ? "
            f"OR {base_alias}.subject LIKE ?)"
        )
        return clause, [like_param, like_param]

    clause = (
        f"{base_alias}.ticket_id IN ("
        f"SELECT ticket_id FROM [{conv_table}] "
        "WHERE full_thread LIKE ? OR subject LIKE ?"
        ")"
    )
    return clause, [like_param, like_param]


def _sanitize_fts_query(keyword: str) -> str:
    """Escape FTS5 special characters for safe MATCH queries."""
    # FTS5 special chars: * " ( ) : ^
    # Wrap bare terms in double quotes for exact phrase matching
    stripped = keyword.strip()
    if not stripped:
        return '""'
    # If user already quoted, pass through
    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped
    # Escape internal double quotes and wrap
    escaped = stripped.replace('"', '""')
    return f'"{escaped}"'
