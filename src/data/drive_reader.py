"""Google Drive READ client (drive.readonly) for the enablement Drive monitor.

Reuses the service-account auth shape from src/export/gdrive_export.py but with
the broader READ scope and read methods: list files changed in a watched folder,
and export a document's plain text. The uploader keeps its narrow drive.file
scope — the two clients stay separate; we never widen one to do the other's job.

Going live needs drive.readonly + the folders shared to the service-account email
(an org/admin step). Until enablement.drive.read_enabled is set with a valid
credentials file, is_configured() is False and the monitor skips.

Optional deps: google-api-python-client + google-auth (+ pypdf / python-docx for
PDF/DOCX text). Degrades gracefully if absent (the gdrive_export precedent).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("alma.drive_reader")

_READONLY_SCOPE = ["https://www.googleapis.com/auth/drive.readonly"]
_GOOGLE_DOC = "application/vnd.google-apps.document"


class DriveReader:
    """Read-only Drive client: list changed files + export text + search."""

    def __init__(self, credentials_path: str = "", auth_type: str = "service_account"):
        self._credentials_path = credentials_path
        self._auth_type = auth_type
        self._service = None

    @classmethod
    def from_settings(cls) -> "DriveReader":
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive") or {}
        return cls(drive.get("credentials_path", ""),
                   auth_type=drive.get("auth_type", "service_account"))

    def is_configured(self) -> bool:
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive") or {}
        if not drive.get("read_enabled"):
            return False
        if self._auth_type == "oauth_user":
            # Disable-on-launch: only "configured" once the user has
            # explicitly reconnected this session, so the background monitor
            # never resumes Google calls on its own at boot.
            from src.data import google_oauth
            return google_oauth.is_active()
        return bool(self._credentials_path) and Path(self._credentials_path).is_file()

    def _build_service(self):
        if self._service:
            return self._service
        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise ImportError(
                "Drive read requires: pip install google-api-python-client google-auth")
        if self._auth_type == "oauth_user":
            from src.data import google_oauth
            creds = google_oauth.load_active_credentials()
            if creds is None:
                raise RuntimeError(
                    "Google account not connected this session — open Settings "
                    "and click Reconnect to authorize Drive access.")
        else:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_path, scopes=_READONLY_SCOPE)
        self._service = build("drive", "v3", credentials=creds)
        return self._service

    def test_connection(self) -> tuple[bool, str]:
        if self._auth_type == "oauth_user":
            from src.data import google_oauth
            if not google_oauth.is_active():
                return False, "Google account not connected this session"
        elif not self._credentials_path or not Path(self._credentials_path).is_file():
            return False, "Service-account credentials path not set"
        try:
            self._build_service().files().list(pageSize=1, fields="files(id)").execute()
            return True, "Connected (drive.readonly)"
        except ImportError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"Connection failed: {e}"

    def list_changed_files(self, folder_id: str, *, modified_after: str | None = None,
                           recursive: bool = True, mime_types: list[str] | None = None) -> list[dict]:
        """Files in a folder changed since modified_after (incremental watermark)."""
        svc = self._build_service()
        clauses = [f"'{folder_id}' in parents", "trashed = false",
                   "mimeType != 'application/vnd.google-apps.folder'"]
        if modified_after:
            clauses.append(f"modifiedTime > '{modified_after}'")
        if mime_types:
            clauses.append("(" + " or ".join(f"mimeType = '{m}'" for m in mime_types) + ")")
        q = " and ".join(clauses)
        files: list[dict] = []
        page_token = None
        while True:
            resp = svc.files().list(
                q=q, pageSize=100, pageToken=page_token, orderBy="modifiedTime",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
            ).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def get_file(self, file_id: str) -> dict:
        """Metadata for a single file (the import-by-URL Workbench path)."""
        svc = self._build_service()
        f = svc.files().get(
            fileId=file_id,
            fields="id, name, mimeType, webViewLink, modifiedTime",
        ).execute()
        return {
            "id": f.get("id"), "name": f.get("name"),
            "mime_type": f.get("mimeType"), "url": f.get("webViewLink"),
            "modified_time": f.get("modifiedTime"),
        }

    def export_text(self, file_id: str, mime_type: str) -> str:
        """Plain text of a doc: native Google Docs via export, others via download."""
        svc = self._build_service()
        if mime_type == _GOOGLE_DOC:
            data = svc.files().export(fileId=file_id, mimeType="text/plain").execute()
            return data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        raw = svc.files().get_media(fileId=file_id).execute()
        if not isinstance(raw, (bytes, bytearray)):
            return str(raw)
        return _extract_text(raw, mime_type)

    def search_files(self, query: str, limit: int = 20) -> list[dict]:
        """Satisfies drive_query.query_business_drive's live_client.search_files seam."""
        svc = self._build_service()
        q = f"fullText contains '{query}' and trashed = false" if query else "trashed = false"
        resp = svc.files().list(
            q=q, pageSize=limit, fields="files(id, name, mimeType, webViewLink)").execute()
        return [{"name": f.get("name"), "id": f.get("id"),
                 "url": f.get("webViewLink"), "mime_type": f.get("mimeType")}
                for f in resp.get("files", [])]


def _extract_text(raw: bytes, mime_type: str) -> str:
    mt = mime_type or ""
    try:
        if "pdf" in mt:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        if "word" in mt or "officedocument" in mt:
            import io
            import docx
            doc = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs)
        return raw.decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001 — extraction is best-effort
        logger.debug("text extract failed (%s): %s", mime_type, exc)
        return ""
