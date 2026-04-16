"""Table-aware column mapping registry for the filter engine.

Maps abstract filter keys to concrete {table: column} pairs so the
filter engine can resolve which column to use depending on which
base table or JOIN context is active.
"""

from __future__ import annotations

# filter_key -> {table_alias: column_name}
# Each key maps to one or more tables that carry that data.
COLUMN_MAP: dict[str, dict[str, str]] = {
    "trc_codes": {
        "ticket_index": "trc_code",
        "conversations": "trc_code",
    },
    "date_start": {
        "ticket_index": "ticket_created_date",
        "conversations": "created_at",
    },
    "date_end": {
        "ticket_index": "ticket_created_date",
        "conversations": "created_at",
    },
    "friction_types": {
        "ticket_index": "friction_type",
    },
    "sub_patterns": {
        "ticket_index": "sub_pattern",
    },
    "sentiment": {
        "ticket_index": "sentiment_polarity",
    },
    "anomaly_flag": {
        "ticket_index": "anomaly_flag",
    },
    "keyword": {
        "conversations": "conversations_fts",
    },
    "entities": {
        "ticket_index": "entities_json",
    },
    "theme_ids": {
        "ticket_theme_tags": "theme_id",
    },
    "dataset_id": {
        "ticket_index": "dataset_id",
        "conversations": "dataset_id",
    },
    "csat_score": {
        "conversations": "csat_score",
    },
}

# Tables that can serve as base tables for queries
VALID_BASE_TABLES = {"ticket_index", "conversations", "tickets"}

# Default table alias prefixes (short, for readable SQL)
TABLE_ALIASES: dict[str, str] = {
    "ticket_index": "ti",
    "conversations": "c",
    "tickets": "t",
    "ticket_theme_tags": "ttt",
}


def resolve_column(filter_key: str, table: str) -> str:
    """Look up the concrete column for a filter key on a given table.

    Raises KeyError if the filter key is unknown or doesn't apply to the table.
    """
    mapping = COLUMN_MAP.get(filter_key)
    if mapping is None:
        raise KeyError(f"Unknown filter key: {filter_key!r}")
    col = mapping.get(table)
    if col is None:
        raise KeyError(
            f"Filter {filter_key!r} not available on table {table!r}"
        )
    return col


def infer_tables(filters: dict) -> set[str]:
    """Return the set of tables a filter dict touches."""
    tables: set[str] = set()
    for key in filters:
        mapping = COLUMN_MAP.get(key)
        if mapping:
            tables.update(mapping.keys())
    return tables


def best_table_for_filter(filter_key: str, base_table: str) -> str:
    """Pick the best table for a filter key, preferring the base table."""
    mapping = COLUMN_MAP.get(filter_key, {})
    if base_table in mapping:
        return base_table
    # Return first available table
    for table in mapping:
        return table
    raise KeyError(f"No table found for filter key: {filter_key!r}")
