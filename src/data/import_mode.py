"""Import mode definitions for CSV and Lightdash ingestion."""

from enum import Enum


class ImportMode(Enum):
    """Data ingestion mode for CSV and Lightdash imports.

    INCREMENTAL skips tickets already in the database (dedup by ticket_id).
    FULL_REFRESH wipes existing data and reloads from scratch — accessible
    only from Settings to prevent accidental data loss.
    """

    INCREMENTAL = "incremental"      # Default: skip existing ticket_ids
    FULL_REFRESH = "full_refresh"    # Nuclear: wipe and reload (Settings only)


def mode_from_ui_text(text: str) -> ImportMode:
    """Convert UI display text to ImportMode enum."""
    if text == "Full Refresh":
        return ImportMode.FULL_REFRESH
    return ImportMode.INCREMENTAL
