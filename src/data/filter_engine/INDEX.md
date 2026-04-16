# Filter Engine

Shared query primitive for all chat tools, reports, and session-scoped queries.

## Public API

```python
from src.data.filter_engine import build_filter_query, validate_filters

sql, params = build_filter_query(
    filters={"trc_codes": ["Billing"], "date_start": "2026-01-01"},
    select_columns=["ticket_id", "trc_code", "friction_type"],
    base_table="ticket_index",
    limit=50,
    order_by="ticket_created_date DESC"
)
```

## Filter Dict Contract

```python
{
    "trc_codes": ["Billing", "Claims"],       # list[str] - IN clause
    "date_start": "2026-01-01",               # str ISO date - >= comparison
    "date_end": "2026-03-31",                 # str ISO date - <= comparison
    "friction_types": ["incorrect_charge"],    # list[str] - IN clause
    "sub_patterns": ["duplicate_billing"],     # list[str] - IN clause
    "sentiment": "negative",                  # str - exact match
    "anomaly_flag": "critical",               # str - exact match
    "keyword": "duplicate",                   # str - FTS5 MATCH or LIKE fallback
    "entities": {"payer": "BlueCross"},        # dict - JSON contains match
    "theme_ids": ["theme_abc"],               # list[str] - JOIN ticket_theme_tags
    "dataset_id": 3                           # int - exact match
}
```

All keys are optional. Empty dict returns full table (no WHERE clause).

## Column Map Reference

See `column_map.py` for table-aware column resolution.

## Module Files

| File | Purpose |
|------|---------|
| `core.py` | `build_filter_query()` — main entry point |
| `column_map.py` | Table-aware column mapping registry |
| `join_builder.py` | Cross-table JOIN generation |
| `fts_handler.py` | FTS5 keyword integration |
| `validators.py` | Filter dict validation + sanitization |
