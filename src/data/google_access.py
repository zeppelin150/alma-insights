"""Is Google Drive access usable RIGHT NOW? — the one auth_type-aware answer.

``google_oauth.is_active()`` answers a NARROWER question: has the operator
reconnected the per-user OAuth session THIS launch. On the service-account
path that flag is structurally always False — ``_active`` starts None and is
set only by an explicit :func:`google_oauth.reconnect` — so using it as a
proxy for "Google is connected" reports a perfectly good service-account
credential as permanently disconnected. Callers that mean "can we reach
Drive" must branch on ``enablement.drive.auth_type``, the shape already used
by ``DriveReader.is_configured`` and ``GoogleDriveExporter.is_configured``.

Safe in the MCP tool subprocess: the OAuth branch is only taken for
``auth_type == "oauth_user"``, and ``google_oauth`` refuses under
``ALMA_MCP_MODE`` — that RuntimeError is caught here and reported as
"not ready" rather than escaping into a worker tick.
"""

from __future__ import annotations


def google_access_ready() -> bool:
    """True when Drive access can run right now, under EITHER auth_type.

    Delegates to :meth:`DriveReader.is_configured` — the existing correct
    branch — so the app keeps exactly ONE definition of "connected".
    Never raises; never performs network I/O.
    """
    try:
        from src.data.drive_reader import DriveReader
        return bool(DriveReader.from_settings().is_configured())
    except Exception:  # noqa: BLE001 — MCP refusal, bad settings, missing google libs
        return False


def service_account_email() -> str:
    """The configured service account's ``client_email`` — "" on any other setup.

    Deliberately surfaced in UI: it is the address the operator must share
    folders TO, and not having it in front of them is the single biggest reason
    an empty Drive looks like a broken one. This is the ONE place allowed to
    open the key file, and it reads ``client_email`` ONLY — the file also holds
    a private key that must never be touched, logged, or rendered.
    Never raises; never performs network I/O.
    """
    try:
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive") or {}
        if (drive.get("auth_type") or "service_account") != "service_account":
            return ""
        path = drive.get("credentials_path") or ""
        if not path:
            return ""
        import json
        with open(path, encoding="utf-8") as fh:
            return str(json.load(fh).get("client_email", "") or "")
    except Exception:  # noqa: BLE001 — a missing/garbled key file just means no email
        return ""
