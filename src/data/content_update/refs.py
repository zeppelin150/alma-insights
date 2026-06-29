"""Resolve a string reference to a SourceDoc.

A reference may be a local file path, a stored ``enablement_documents`` doc_id,
a Google Drive id/URL, or a Guru card id/URL. Each branch is a small helper so
the dispatcher stays flat. Returns None when the ref cannot be resolved.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .models import SourceDoc


def resolve_ref(conn, ref: str, *, guru_client: Any | None = None,
                drive_reader: Any | None = None,
                http_get: Callable[[str], str] | None = None,
                title_override: str | None = None) -> SourceDoc | None:
    """Resolve ``ref`` to a SourceDoc (file > guru card > stored doc > Drive)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if _is_guru_ref(ref):
        return _from_guru(guru_client, ref, title_override)
    if os.path.exists(ref):
        return _from_file(ref, title_override)
    stored = _from_stored(conn, ref, title_override)
    if stored:
        return stored
    if drive_reader is not None and _looks_like_drive(ref):
        return _from_drive(conn, drive_reader, ref, title_override)
    return None


def _is_guru_ref(ref: str) -> bool:
    return "getguru.com/card/" in ref.lower()


def _looks_like_drive(ref: str) -> bool:
    low = ref.lower()
    return "drive.google" in low or "docs.google" in low


def _from_guru(guru_client, ref: str, title_override: str | None) -> SourceDoc | None:
    if guru_client is None:
        return None
    from src.data.enablement_store import parse_guru_card_ref
    from src.data.html_markdown import html_to_markdown
    card_id = parse_guru_card_ref(ref)
    try:
        card = guru_client.get_card(card_id)
    except Exception:  # noqa: BLE001 — unresolved reference, skip it
        return None
    if not card or not card.get("id"):
        return None
    return SourceDoc(
        ref=f"guru:{card['id']}",
        title=title_override or card.get("title", "") or "Guru card",
        text=html_to_markdown(card.get("content", "")),
    )


def _from_file(ref: str, title_override: str | None) -> SourceDoc | None:
    try:
        with open(ref, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    return SourceDoc(ref=f"file:{ref}", title=title_override or os.path.basename(ref), text=text)


def _from_stored(conn, ref: str, title_override: str | None) -> SourceDoc | None:
    from src.data.enablement_store import get_document
    doc = get_document(conn, ref)
    if not doc:
        return None
    return SourceDoc(
        ref=f"doc:{ref}",
        title=title_override or doc.get("name", "") or "Document",
        text=doc.get("full_text", "") or "",
    )


def _from_drive(conn, drive_reader, ref: str, title_override: str | None) -> SourceDoc | None:
    from src.data.enablement_store import get_document, import_drive_doc
    res = import_drive_doc(conn, drive_reader, ref)
    if not res.get("ok"):
        return None
    doc = get_document(conn, res["doc_id"])
    if not doc:
        return None
    return SourceDoc(
        ref=f"drive:{res['doc_id']}",
        title=title_override or doc.get("name", "") or "Drive document",
        text=doc.get("full_text", "") or "",
    )
