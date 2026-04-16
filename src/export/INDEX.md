# src/export/

> Optional Google Drive export functionality for uploading generated Markdown reports via service account credentials.

## Module Index

### gdrive_export.py
> Uploads Markdown reports to a Google Drive folder using a service account; gracefully fails if google-api-python-client is not installed.

**Public API:**
- `GoogleDriveExporter.__init__(self, credentials_path: str = "", folder_id: str = "")` — Initialize with service account credentials path and target folder ID.
- `GoogleDriveExporter.is_configured(self)` — Check if credentials file and folder ID are set.
- `GoogleDriveExporter.upload_report(self, filename, content, mime_type: str = "text/markdown")` — Upload a report file to the configured Drive folder; returns the file ID.
- `GoogleDriveExporter.test_connection(self)` — Test the Drive connection by listing files in the folder; returns (success: bool, message: str).

**Depends on:** (none -- only stdlib and optional `google-api-python-client`, `google-auth`)
**Depended by:** `src.ui.pages.ai_reports`, `src.ui.pages.settings_page`, `src.data.smart_pipeline`

---
