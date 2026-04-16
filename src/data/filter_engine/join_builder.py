"""Cross-table JOIN generation for the filter engine.

Builds JOIN clauses when filters span multiple tables, using
ticket_id as the universal join key.
"""

from __future__ import annotations

from src.data.filter_engine.column_map import TABLE_ALIASES

# All joins go through ticket_id
_JOIN_KEY = "ticket_id"

# JOIN templates keyed by (left_table, right_table)
_JOIN_TEMPLATES: dict[tuple[str, str], str] = {
    ("ticket_index", "conversations"): (
        "JOIN conversations {ra} ON {la}.ticket_id = {ra}.ticket_id"
    ),
    ("ticket_index", "ticket_theme_tags"): (
        "JOIN ticket_theme_tags {ra} ON {la}.ticket_id = {ra}.ticket_id"
    ),
    ("conversations", "ticket_index"): (
        "JOIN ticket_index {ra} ON {la}.ticket_id = {ra}.ticket_id"
    ),
    ("conversations", "ticket_theme_tags"): (
        "JOIN ticket_theme_tags {ra} ON {la}.ticket_id = {ra}.ticket_id"
    ),
}


def build_join_clause(tables: set[str], base_table: str) -> str:
    """Build JOIN clauses for all tables that aren't the base table.

    Args:
        tables: Set of table names that need to be included.
        base_table: The FROM table (already in the query).

    Returns:
        JOIN clause string (may be empty if no joins needed).
    """
    extra = tables - {base_table}
    if not extra:
        return ""

    base_alias = TABLE_ALIASES.get(base_table, base_table)
    parts: list[str] = []

    for table in sorted(extra):
        right_alias = TABLE_ALIASES.get(table, table)
        template = _JOIN_TEMPLATES.get((base_table, table))
        if template is None:
            raise ValueError(
                f"No JOIN path from {base_table!r} to {table!r}"
            )
        parts.append(template.format(la=base_alias, ra=right_alias))

    return "\n".join(parts)


def get_join_key(table_a: str, table_b: str) -> str:
    """Return the join column between two tables."""
    if (table_a, table_b) in _JOIN_TEMPLATES:
        return _JOIN_KEY
    if (table_b, table_a) in _JOIN_TEMPLATES:
        return _JOIN_KEY
    raise ValueError(f"No join path between {table_a!r} and {table_b!r}")
