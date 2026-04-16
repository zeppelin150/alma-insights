"""Core filter query builder — main entry point for the filter engine.

Composes column_map, join_builder, fts_handler, and validators into
a single `build_filter_query()` call that returns (SQL, params).
"""

from __future__ import annotations

import logging
import time

from src.data.filter_engine.column_map import (
    COLUMN_MAP,
    TABLE_ALIASES,
    VALID_BASE_TABLES,
    best_table_for_filter,
    resolve_column,
)
from src.data.filter_engine.fts_handler import build_fts_clause
from src.data.filter_engine.join_builder import build_join_clause
from src.data.filter_engine.validators import validate_filters

logger = logging.getLogger(__name__)

# Date columns that need SUBSTR for safe comparison (see danger_zones.md)
_DATE_COLUMNS = {"ticket_created_date", "created_at"}


def build_filter_query(
    filters: dict,
    select_columns: list[str],
    base_table: str = "ticket_index",
    limit: int | None = None,
    order_by: str | None = None,
) -> tuple[str, list]:
    """Convert a filter dict into (SQL query string, parameter list).

    Args:
        filters: Filter dict (see INDEX.md for contract).
        select_columns: Columns to SELECT (use table aliases: ti.ticket_id).
        base_table: Primary table for FROM clause.
        limit: Optional LIMIT value.
        order_by: Optional ORDER BY clause (e.g. "ti.ticket_created_date DESC").

    Returns:
        (sql_string, params_list) ready for cursor.execute().
    """
    start = time.perf_counter()

    if base_table not in VALID_BASE_TABLES:
        raise ValueError(f"Invalid base table: {base_table!r}")

    cleaned, warnings = validate_filters(filters)
    for w in warnings:
        logger.warning("Filter validation: %s", w)

    base_alias = TABLE_ALIASES.get(base_table, base_table)
    tables_needed = _collect_tables(cleaned, base_table)
    join_clause = build_join_clause(tables_needed, base_table)

    where_parts, params = _build_where_clauses(
        cleaned, base_table, base_alias
    )

    sql = _assemble_sql(
        select_columns, base_table, base_alias,
        join_clause, where_parts, order_by, limit,
    )

    elapsed = (time.perf_counter() - start) * 1000
    logger.debug(
        "build_filter_query: tables=%s joins=%d clauses=%d elapsed=%.1fms",
        tables_needed, len(tables_needed) - 1, len(where_parts), elapsed,
    )
    return sql, params


def _collect_tables(filters: dict, base_table: str) -> set[str]:
    """Determine which tables are needed based on filter keys."""
    tables = {base_table}
    for key in filters:
        try:
            table = best_table_for_filter(key, base_table)
            tables.add(table)
        except KeyError:
            pass
    return tables


def _build_where_clauses(
    filters: dict, base_table: str, base_alias: str
) -> tuple[list[str], list]:
    """Build WHERE clause fragments and params from cleaned filters."""
    parts: list[str] = []
    params: list = []

    for key, value in filters.items():
        if key == "keyword":
            clause, p = build_fts_clause(value, base_alias, base_table)
            parts.append(clause)
            params.extend(p)
            continue

        if key == "entities":
            _add_entity_clauses(value, base_alias, parts, params)
            continue

        if key == "theme_ids":
            _add_theme_clauses(value, parts, params)
            continue

        _add_standard_clause(key, value, base_table, parts, params)

    return parts, params


def _add_standard_clause(
    key: str, value, base_table: str,
    parts: list[str], params: list,
) -> None:
    """Add a standard WHERE clause for a filter key."""
    try:
        table = best_table_for_filter(key, base_table)
    except KeyError:
        return

    alias = TABLE_ALIASES.get(table, table)
    col = resolve_column(key, table)

    if key in ("date_start", "date_end"):
        _add_date_clause(key, value, alias, col, parts, params)
    elif isinstance(value, list):
        _add_in_clause(alias, col, value, parts, params)
    else:
        parts.append(f"{alias}.{col} = ?")
        params.append(value)


def _add_date_clause(
    key: str, value: str, alias: str, col: str,
    parts: list[str], params: list,
) -> None:
    """Add a date comparison clause using SUBSTR for safety."""
    # SUBSTR date comparison per danger_zones.md
    date_expr = f"SUBSTR({alias}.{col}, 1, 10)"
    if key == "date_start":
        parts.append(f"{date_expr} >= ?")
    else:
        parts.append(f"{date_expr} <= ?")
    params.append(value)


def _add_in_clause(
    alias: str, col: str, values: list,
    parts: list[str], params: list,
) -> None:
    """Add an IN (...) clause for list-valued filters."""
    placeholders = ", ".join("?" for _ in values)
    parts.append(f"{alias}.{col} IN ({placeholders})")
    params.extend(values)


def _add_entity_clauses(
    entities: dict, base_alias: str,
    parts: list[str], params: list,
) -> None:
    """Add JSON-based entity matching clauses."""
    alias = base_alias
    for entity_type, entity_value in entities.items():
        # Use json_extract for entity matching within entities_json
        parts.append(
            f"json_extract({alias}.entities_json, '$.{entity_type}') = ?"
        )
        params.append(entity_value)


def _add_theme_clauses(
    theme_ids: list[str], parts: list[str], params: list,
) -> None:
    """Add theme_id filter via ticket_theme_tags join."""
    alias = TABLE_ALIASES.get("ticket_theme_tags", "ttt")
    placeholders = ", ".join("?" for _ in theme_ids)
    parts.append(f"{alias}.theme_id IN ({placeholders})")
    params.extend(theme_ids)


def _assemble_sql(
    select_columns: list[str],
    base_table: str,
    base_alias: str,
    join_clause: str,
    where_parts: list[str],
    order_by: str | None,
    limit: int | None,
) -> str:
    """Assemble the final SQL string from components."""
    select_str = ", ".join(select_columns)
    sql = f"SELECT {select_str}\nFROM {base_table} {base_alias}"

    if join_clause:
        sql += f"\n{join_clause}"

    if where_parts:
        sql += "\nWHERE " + "\n  AND ".join(where_parts)

    if order_by:
        sql += f"\nORDER BY {order_by}"

    if limit is not None:
        sql += f"\nLIMIT {limit}"

    return sql
