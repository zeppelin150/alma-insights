"""Stage 1 — load the source material we are updating the card FROM.

Primary source (first non-empty wins): raw ``source_text`` > the doc attached to
``asana_task_gid`` > a ``source_doc_ref``. Then any ``reference_refs`` the user
points at (treated as authoritative point-of-truth material).
"""

from __future__ import annotations

from typing import Any, Callable

from .attachment import fetch_attachment_text, pick_attachment
from .models import ContentUpdateRequest, SourceBundle, SourceDoc
from .refs import resolve_ref


def load_source(conn, request: ContentUpdateRequest, *,
                asana_client: Any | None = None,
                guru_client: Any | None = None,
                http_get: Callable[[str], str] | None = None,
                drive_reader: Any | None = None) -> SourceBundle:
    """Return the primary SourceDoc + resolved reference docs (or an error)."""
    primary = _primary(conn, request, asana_client, guru_client, http_get, drive_reader)
    if primary is None or not primary.text.strip():
        return SourceBundle(ok=False, error="no_source_text")

    references: list[SourceDoc] = []
    for ref in request.reference_refs:
        doc = resolve_ref(conn, ref, guru_client=guru_client,
                          drive_reader=drive_reader, http_get=http_get)
        if doc and doc.text.strip():
            references.append(doc)
    return SourceBundle(ok=True, primary=primary, references=references)


def _primary(conn, request, asana_client, guru_client, http_get, drive_reader) -> SourceDoc | None:
    if request.source_text:
        return SourceDoc("inline", request.source_title or "Source", request.source_text)
    if request.asana_task_gid:
        return _from_asana(request, asana_client, http_get, drive_reader)
    if request.source_doc_ref:
        return resolve_ref(conn, request.source_doc_ref, guru_client=guru_client,
                           drive_reader=drive_reader, http_get=http_get,
                           title_override=request.source_title)
    return None


def _from_asana(request, asana_client, http_get, drive_reader) -> SourceDoc | None:
    if asana_client is None:
        return None
    attachments = asana_client.list_attachments(request.asana_task_gid)
    chosen = pick_attachment(attachments, request.attachment_selector)
    if not chosen:
        return None
    full = asana_client.get_attachment(chosen["gid"])
    text = fetch_attachment_text(full, http_get=http_get, drive_reader=drive_reader)
    if not text.strip():
        return None
    title = request.source_title or full.get("name") or chosen.get("name") or "Attached document"
    return SourceDoc(ref=f"asana_attachment:{chosen['gid']}", title=title, text=text)
