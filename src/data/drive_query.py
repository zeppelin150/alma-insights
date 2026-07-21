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


def build_live_drive_client():
    """Construct the live Drive READ client (service account) when Drive read is
    configured, else None.

    The chat-tool handlers (``handle_search_google_drive`` etc.) call this and
    inject the result into ``query_business_drive``'s ``live_client`` seam.
    ``query_business_drive`` itself stays injection-only on purpose, so its unit
    tests never read settings or touch the network — the settings/DriveReader
    dependency lives here, at the production entry point, not in the data path.
    """
    try:
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()
        return reader if reader.is_configured() else None
    except Exception:  # noqa: BLE001 — any build failure → caller falls back
        return None


def query_business_drive(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    live_client=None,
    folder_id: str | None = None,
) -> dict:
    """Search the business Drive for documents matching `query`.

    Prefers the live Drive client when one is supplied; otherwise searches the
    locally-indexed mirror. ``folder_id`` (live path only) scopes the search to
    one Drive folder's direct children.
    """
    if live_client is not None:
        try:
            kwargs = {"limit": limit}
            if folder_id:
                kwargs["folder_id"] = folder_id
            results = live_client.search_files(query, **kwargs)
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
