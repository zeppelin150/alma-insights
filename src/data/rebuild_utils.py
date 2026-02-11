"""
Alma Insights — Rebuild Utilities
Shared logic for timestamp parsing, NULL placement, chronological sorting,
and role normalization. Used by both CSV and API ingestion paths.
"""

from datetime import datetime, timedelta
from typing import Optional, List


def parse_timestamp(ts_raw) -> Optional[datetime]:
    """Parse a raw timestamp string into datetime. Returns None if unparsable."""
    if ts_raw is None or ts_raw == "" or str(ts_raw).lower() in ("null", "none", "nan"):
        return None

    ts_str = str(ts_raw).strip()

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    return None


def normalize_role(raw_role: str) -> str:
    """Normalize author role string → 'customer' | 'agent' | 'bot'."""
    r = raw_role.strip().lower() if isinstance(raw_role, str) else ""
    if r == "end-user":
        return "customer"
    elif r == "agent":
        return "agent"
    elif r in ("", "null", "none"):
        return "bot"
    return r or "bot"


def sort_events_chronologically(events: List[dict]) -> List[dict]:
    """
    Sort a list of comment/event dicts chronologically with NULL timestamp handling.

    Each event dict must have:
        - "ts_raw": raw timestamp string (can be None/empty)
        - "order":  receipt/row order index (int)

    Returns the same list, sorted in place, with added keys:
        - "ts_parsed": datetime (real or synthetic)
        - "is_synthetic": bool

    Timestamp rules:
        - NULL/unparsable → placed at START of conversation
        - Multiple NULLs ordered by receipt order
        - Synthetic timestamps: min_valid_ts - (n_nulls - idx) seconds
        - If NO valid timestamps: baseline = 2020-01-01 + receipt order
    """
    # Parse all timestamps
    for evt in events:
        evt["ts_parsed"] = parse_timestamp(evt.get("ts_raw"))
        evt["is_synthetic"] = False

    has_valid = [e for e in events if e["ts_parsed"] is not None]
    has_null = [e for e in events if e["ts_parsed"] is None]

    if has_valid:
        min_ts = min(e["ts_parsed"] for e in has_valid)
    else:
        # No valid timestamps at all — use epoch baseline
        min_ts = datetime(2020, 1, 1)

    # Assign synthetic timestamps to NULLs — before min_ts, in receipt order
    has_null.sort(key=lambda e: e["order"])
    n_nulls = len(has_null)
    for idx, evt in enumerate(has_null):
        evt["ts_parsed"] = min_ts - timedelta(seconds=(n_nulls - idx))
        evt["is_synthetic"] = True

    # Sort: timestamp ASC, then receipt order ASC
    events.sort(key=lambda e: (e["ts_parsed"], e["order"]))

    return events
