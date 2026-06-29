"""Resolve an Asana attachment to plain text.

Uploaded files (``host == "asana"``) carry a pre-signed ``download_url`` we can
GET directly. Google-Drive-hosted attachments are resolved through the injected
Drive reader. Anything else falls back to a best-effort GET of ``download_url``.
"""

from __future__ import annotations

from typing import Any, Callable

_DRIVE_DOC_MIME = "application/vnd.google-apps.document"


def pick_attachment(attachments: list[dict], selector: str | None) -> dict | None:
    """Choose the target attachment by name substring, else the first one."""
    if not attachments:
        return None
    if selector:
        needle = selector.lower()
        for att in attachments:
            if needle in (att.get("name") or "").lower():
                return att
    return attachments[0]


def fetch_attachment_text(attachment: dict, *,
                          http_get: Callable[[str], str] | None,
                          drive_reader: Any | None = None) -> str:
    """Return the attachment's text, or "" when it cannot be resolved."""
    host = (attachment.get("host") or "").lower()
    url = attachment.get("download_url") or ""

    if host in ("google_drive", "gdrive"):
        # Drive-hosted: only the Drive reader can resolve it. NEVER fall back to
        # GETting the Drive URL — that returns a login/interstitial HTML page
        # which would be silently accepted as "source text" and drafted into a
        # card. Force a typed no-source failure instead.
        if drive_reader is None:
            return ""
        file_id = _drive_file_id(attachment)
        if not file_id:
            return ""
        try:
            return drive_reader.export_text(file_id, _DRIVE_DOC_MIME) or ""
        except Exception:  # noqa: BLE001 — surface as no-text, not a crash
            return ""

    if url and http_get is not None:
        return http_get(url) or ""
    return ""


def _drive_file_id(attachment: dict) -> str:
    from src.data.enablement_store import parse_drive_file_ref
    ref = attachment.get("view_url") or attachment.get("download_url") or ""
    if not ref:
        return ""
    file_id = parse_drive_file_ref(ref)
    # parse_drive_file_ref returns the raw ref unchanged when it finds no id.
    # Reject anything still URL-shaped so we never hand a URL to the Drive
    # reader as a "file id" (which would raise unhandled).
    return "" if "://" in file_id else file_id
