"""Filter dict validation and sanitization.

Ensures filter dicts are well-formed before SQL generation.
Returns cleaned filters plus a list of warnings for ignored keys.
"""

from __future__ import annotations

import re
from datetime import date

from src.data.filter_engine.column_map import COLUMN_MAP

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Filter keys that accept list values
_LIST_KEYS = {"trc_codes", "friction_types", "sub_patterns", "theme_ids"}

# Filter keys that accept scalar string values
_SCALAR_KEYS = {"sentiment", "anomaly_flag", "keyword"}

# Filter keys that accept date strings
_DATE_KEYS = {"date_start", "date_end"}

# Filter keys that accept integer values
_INT_KEYS = {"dataset_id"}

# Filter keys that accept dict values
_DICT_KEYS = {"entities"}


def validate_filters(filters: dict) -> tuple[dict, list[str]]:
    """Validate and clean a filter dict.

    Returns:
        (cleaned_filters, warnings) where warnings lists any ignored keys.
    """
    if not isinstance(filters, dict):
        return {}, ["filters must be a dict"]

    cleaned: dict = {}
    warnings: list[str] = []

    for key, value in filters.items():
        if key not in COLUMN_MAP:
            warnings.append(f"Unknown filter key ignored: {key!r}")
            continue

        if value is None:
            continue

        if key in _LIST_KEYS:
            validated = _validate_list(key, value)
            if validated is not None:
                cleaned[key] = validated
            else:
                warnings.append(f"Invalid list value for {key!r}, ignored")

        elif key in _DATE_KEYS:
            validated = sanitize_date(value)
            if validated is not None:
                cleaned[key] = validated
            else:
                warnings.append(f"Invalid date for {key!r}: {value!r}")

        elif key in _SCALAR_KEYS:
            if isinstance(value, str) and value.strip():
                cleaned[key] = value.strip()
            else:
                warnings.append(f"Invalid scalar for {key!r}, ignored")

        elif key in _INT_KEYS:
            if isinstance(value, int):
                cleaned[key] = value
            elif isinstance(value, str) and value.isdigit():
                cleaned[key] = int(value)
            else:
                warnings.append(f"Invalid int for {key!r}: {value!r}")

        elif key in _DICT_KEYS:
            if isinstance(value, dict) and value:
                cleaned[key] = value
            else:
                warnings.append(f"Invalid dict for {key!r}, ignored")

    # Cross-field validation: date_start must be <= date_end
    err = _validate_date_range(cleaned)
    if err:
        warnings.append(err)
        cleaned.pop("date_start", None)
        cleaned.pop("date_end", None)

    return cleaned, warnings


def sanitize_date(date_str: str) -> str | None:
    """Validate and normalize a date string to ISO format (YYYY-MM-DD)."""
    if not isinstance(date_str, str):
        return None
    stripped = date_str.strip()
    if not _ISO_DATE_RE.match(stripped):
        return None
    try:
        date.fromisoformat(stripped)
    except ValueError:
        return None
    return stripped


def _validate_list(key: str, value) -> list[str] | None:
    """Validate a list filter value, returning cleaned list or None."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        cleaned = [str(v).strip() for v in value if v is not None]
        return cleaned if cleaned else None
    return None


def _validate_date_range(filters: dict) -> str | None:
    """Check date_start <= date_end if both present."""
    start = filters.get("date_start")
    end = filters.get("date_end")
    if start and end and start > end:
        return f"date_start ({start}) > date_end ({end}), both removed"
    return None
