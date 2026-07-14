"""
Alma Insights — Google Drive Export (Pass 3.0)
Upload reports to Google Drive via service account credentials.
Optional dependency — graceful failure if google-api-python-client not installed.
"""

from __future__ import annotations

import os
import logging
import threading
import time
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

# ── WS2-M1: the Drive WRITE throttle/backoff chokepoint ──────────────
# Drive sustains only ~2-3 writes/sec per user and 403s with
# (user)RateLimitExceeded beyond that; NOTHING in the repo had backoff before
# this. EVERY Drive write from ANY workstream (KB sync/ingest, artifact
# uploads) must call _throttled_execute — never request.execute() directly.
_WRITE_LOCK = threading.Lock()
_MIN_WRITE_INTERVAL_S = 0.5
_MAX_RETRIES = 5
_last_write_ts = 0.0


def _is_rate_error(exc) -> bool:
    text = str(exc)
    return ("rateLimitExceeded" in text or "userRateLimitExceeded" in text
            or "429" in text or "500" in text or "502" in text or "503" in text)


def _throttled_execute(request):
    """Pace + retry a Drive WRITE request: min-interval spacing under a lock,
    exponential backoff with jitter-free doubling on rate/5xx errors (max 5
    tries). Reads don't need this — writes are the scarce quota."""
    global _last_write_ts
    delay = 1.0
    for attempt in range(_MAX_RETRIES):
        with _WRITE_LOCK:
            wait = _MIN_WRITE_INTERVAL_S - (time.monotonic() - _last_write_ts)
            if wait > 0:
                time.sleep(wait)
            _last_write_ts = time.monotonic()
        try:
            return request.execute()
        except Exception as exc:  # noqa: BLE001 — googleapiclient HttpError etc.
            if attempt >= _MAX_RETRIES - 1 or not _is_rate_error(exc):
                raise
            log.warning("Drive write throttled (%s) — retry in %.1fs", exc, delay)
            time.sleep(delay)
            delay = min(delay * 2, 16)


class GoogleDriveExporter:
    """Upload Markdown reports to Google Drive.

    Requires:
      - google-api-python-client
      - google-auth
      - A service account JSON credentials file
      - A target folder ID (shared with the service account email)
    """

    def __init__(self, credentials_path: str = "", folder_id: str = "",
                 auth_type: str = "service_account") -> None:
        self._credentials_path = credentials_path
        self._folder_id = folder_id
        self._auth_type = auth_type
        self._service = None

    @classmethod
    def from_settings(cls, folder_id: str = "") -> "GoogleDriveExporter":
        from src.data.settings_manager import get_section
        drive = (get_section("enablement", {}) or {}).get("drive") or {}
        return cls(drive.get("credentials_path", ""), folder_id,
                   auth_type=drive.get("auth_type", "service_account"))

    def is_configured(self) -> bool:
        """Check if credentials and folder are set."""
        if not self._folder_id:
            return False
        if self._auth_type == "oauth_user":
            from src.data import google_oauth
            return google_oauth.is_active()  # disable-on-launch
        return (
            bool(self._credentials_path)
            and Path(self._credentials_path).is_file()
        )

    def _build_service(self):
        """Lazily build the Drive API service."""
        if self._service:
            return self._service

        try:
            from googleapiclient.discovery import build
        except ImportError:
            raise ImportError(
                "Google Drive export requires: pip install google-api-python-client google-auth"
            )
        try:
            if self._auth_type == "oauth_user":
                from src.data import google_oauth
                creds = google_oauth.load_active_credentials()
                if creds is None:
                    raise RuntimeError("Google account not connected this session")
            else:
                from google.oauth2 import service_account
                SCOPES = ["https://www.googleapis.com/auth/drive.file"]
                creds = service_account.Credentials.from_service_account_file(
                    self._credentials_path, scopes=SCOPES
                )
            self._service = build("drive", "v3", credentials=creds)
            return self._service
        except Exception as e:
            raise RuntimeError(f"Failed to build Drive service: {e}")

    def upload_report(self, filename: str, content: str, mime_type: str = "text/markdown") -> str:
        """Upload a report file to the configured Drive folder.

        Returns the file ID of the uploaded file.
        """
        if not self.is_configured():
            raise ValueError("Google Drive export is not configured")

        service = self._build_service()

        file_metadata = {
            "name": filename,
            "parents": [self._folder_id],
        }

        from io import BytesIO
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(
            BytesIO(content.encode("utf-8")),
            mimetype=mime_type,
            resumable=False,
        )

        result = _throttled_execute(service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink",
        ))

        file_id = result.get("id", "")
        log.info(f"Uploaded to Drive: {filename} -> {file_id}")
        return file_id

    # ── WS2-M1 write surface (create/update/bytes; explicit parents) ──

    def create_folder(self, name: str, parent_id: str | None = None,
                      *, app_properties: dict | None = None) -> dict:
        """Create a Drive folder; returns {id, name, webViewLink}. parent_id
        None → My Drive root (pure drive.file, zero extra grants)."""
        service = self._build_service()
        body: dict = {"name": name,
                      "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            body["parents"] = [parent_id]
        if app_properties:
            body["appProperties"] = dict(app_properties)
        return _throttled_execute(service.files().create(
            body=body, fields="id, name, webViewLink"))

    def update_file(self, file_id: str, content: str,
                    mime_type: str = "text/markdown") -> dict:
        """Replace a file's content; returns {id, modifiedTime} — the caller
        records modifiedTime for echo suppression (its own write must not
        re-ingest on the next pull)."""
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseUpload
        service = self._build_service()
        media = MediaIoBaseUpload(BytesIO(content.encode("utf-8")),
                                  mimetype=mime_type, resumable=False)
        return _throttled_execute(service.files().update(
            fileId=file_id, media_body=media, fields="id, modifiedTime"))

    def upload_file(self, filename: str, data: bytes,
                    mime_type: str, folder_id: str | None = None,
                    *, app_properties: dict | None = None) -> dict:
        """Upload arbitrary bytes (e.g. a .pptx) into a folder; returns
        {id, name, webViewLink}. WS3-M7's Confirm-gated artifact upload rides
        this — it must NEVER build its own unthrottled create call."""
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseUpload
        service = self._build_service()
        body: dict = {"name": filename}
        parent = folder_id or self._folder_id
        if parent:
            body["parents"] = [parent]
        if app_properties:
            body["appProperties"] = dict(app_properties)
        media = MediaIoBaseUpload(BytesIO(data), mimetype=mime_type,
                                  resumable=False)
        return _throttled_execute(service.files().create(
            body=body, media_body=media, fields="id, name, webViewLink"))

    def get_file_meta(self, file_id: str,
                      fields: str = "id, name, trashed, modifiedTime, "
                                    "capabilities/canAddChildren") -> dict:
        """files().get with the lifecycle fields the KB verification needs —
        DriveReader's get_file omits ``trashed``, which is exactly the field a
        bootstrap re-verify must see (writes into the trash succeed!)."""
        service = self._build_service()
        return service.files().get(fileId=file_id, fields=fields).execute()

    def find_child_by_app_property(self, folder_id: str, key: str,
                                   value: str) -> dict | None:
        """First non-trashed child of ``folder_id`` carrying appProperties
        {key: value} — the write-idempotency probe (crash between create and
        mirror-commit must not duplicate the card on retry)."""
        service = self._build_service()
        esc_f = str(folder_id).replace("\\", "\\\\").replace("'", "\\'")
        esc_k = str(key).replace("\\", "\\\\").replace("'", "\\'")
        esc_v = str(value).replace("\\", "\\\\").replace("'", "\\'")
        res = service.files().list(
            q=(f"'{esc_f}' in parents and trashed=false and "
               f"appProperties has {{ key='{esc_k}' and value='{esc_v}' }}"),
            pageSize=1, fields="files(id, name, modifiedTime)").execute()
        files = res.get("files") or []
        return files[0] if files else None

    def test_connection(self) -> tuple[bool, str]:
        """Test the Drive connection by listing files in the folder.

        Returns (success: bool, message: str)
        """
        if not self.is_configured():
            return False, "Credentials path or folder ID not set"

        try:
            service = self._build_service()
            esc = str(self._folder_id).replace("\\", "\\\\").replace("'", "\\'")
            results = service.files().list(
                q=f"'{esc}' in parents",
                pageSize=1,
                fields="files(id, name)",
            ).execute()
            count = len(results.get("files", []))
            return True, f"Connected — folder contains {count}+ files"
        except ImportError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Connection failed: {e}"
