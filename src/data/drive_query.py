"""Query the business Google Drive (or its local mirror) for documents.

Backs the chat ``query_business_drive`` tool. Today it searches the local
mirror in ``enablement_documents`` (populated from the business Drive by the
Drive monitor). When a ``drive.readonly`` client is configured — gated on the
org read-scope step (see ~/.claude/plans/guru-enablement-redesign.md §7) — it
queries Drive directly. A ``live_client`` can be injected for tests.
"""

from __future__ import annotations

import sqlite3

from src.data import enablement_store as store


def is_live_drive_configured() -> bool:
    """True when live drive.readonly access has been enabled in settings."""
    try:
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive", {}) or {}
        return bool(drive.get("read_enabled"))
    except Exception:
        return False


def query_business_drive(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    live_client=None,
) -> dict:
    """Search the business Drive for documents matching `query`.

    Prefers the live Drive client when one is supplied/configured; otherwise
    searches the locally-indexed mirror so the chat still works before the
    org read-scope lands.
    """
    if live_client is not None:
        try:
            results = live_client.search_files(query, limit=limit)
            return {"mode": "live", "configured": True,
                    "results": list(results), "count": len(results)}
        except Exception as exc:  # noqa: BLE001 — surface as a failed query
            return {"mode": "live", "configured": True, "error": str(exc), "results": []}

    docs = [d for d in store.search_documents(conn, query, limit=limit)
            if d.get("source") == "drive"]
    return {
        "mode": "local_index",
        "configured": is_live_drive_configured(),
        "results": docs,
        "count": len(docs),
        "note": ("Searched the locally-indexed mirror of the business Drive "
                 "(documents pulled by the Drive monitor). Live drive.readonly "
                 "access is not configured yet."),
    }
