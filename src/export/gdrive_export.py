"""
Alma Insights — Google Drive Export (Pass 3.0)
Upload reports to Google Drive via service account credentials.
Optional dependency — graceful failure if google-api-python-client not installed.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)


class GoogleDriveExporter:
    """Upload Markdown reports to Google Drive.

    Requires:
      - google-api-python-client
      - google-auth
      - A service account JSON credentials file
      - A target folder ID (shared with the service account email)
    """

    def __init__(self, credentials_path: str = "", folder_id: str = "") -> None:
        self._credentials_path = credentials_path
        self._folder_id = folder_id
        self._service = None

    def is_configured(self) -> bool:
        """Check if credentials and folder are set."""
        return (
            bool(self._credentials_path)
            and bool(self._folder_id)
            and Path(self._credentials_path).is_file()
        )

    def _build_service(self):
        """Lazily build the Drive API service."""
        if self._service:
            return self._service

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            SCOPES = ["https://www.googleapis.com/auth/drive.file"]
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_path, scopes=SCOPES
            )
            self._service = build("drive", "v3", credentials=creds)
            return self._service
        except ImportError:
            raise ImportError(
                "Google Drive export requires: pip install google-api-python-client google-auth"
            )
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

        result = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()

        file_id = result.get("id", "")
        log.info(f"Uploaded to Drive: {filename} -> {file_id}")
        return file_id

    def test_connection(self) -> tuple[bool, str]:
        """Test the Drive connection by listing files in the folder.

        Returns (success: bool, message: str)
        """
        if not self.is_configured():
            return False, "Credentials path or folder ID not set"

        try:
            service = self._build_service()
            results = service.files().list(
                q=f"'{self._folder_id}' in parents",
                pageSize=1,
                fields="files(id, name)",
            ).execute()
            count = len(results.get("files", []))
            return True, f"Connected — folder contains {count}+ files"
        except ImportError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Connection failed: {e}"
