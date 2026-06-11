"""Phase 5 — live Drive ingest (mock-tested; live when the SA share lands).

A FakeDriveReader stands in for the drive.readonly client so the whole pipeline
(list → export text → save_document → draft_card_from_document → card_review
task) is exercised headlessly. Idempotency, the skip-when-unconfigured path, the
export_text mime routing, and monitor construction are covered too.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.data import enablement_sim as SIM
from src.data import enablement_sources as sources
from src.data import enablement_store as S
from src.data import enablement_tasks as T
from src.data.drive_monitor import poll_once


class FakeDriveReader:
    def __init__(self, files, texts, *, honor_watermark=True):
        self._files = files
        self._texts = texts
        self._honor = honor_watermark

    def is_configured(self):
        return True

    def list_changed_files(self, folder_id, *, modified_after=None, recursive=True, mime_types=None):
        if self._honor and modified_after:
            return [f for f in self._files if (f.get("modifiedTime", "") > modified_after)]
        return list(self._files)

    def export_text(self, file_id, mime_type):
        return self._texts.get(file_id, "")


_FILES = [
    {"id": "f1", "name": "SSO Setup.gdoc", "mimeType": "application/vnd.google-apps.document",
     "modifiedTime": "2026-06-09T10:00:00Z", "webViewLink": "https://docs/f1"},
    {"id": "f2", "name": "Returns Policy.pdf", "mimeType": "application/pdf",
     "modifiedTime": "2026-06-09T11:00:00Z", "webViewLink": "https://docs/f2"},
]
_TEXTS = {"f1": "Provider SSO self-serve launches June 24, 2026.",
          "f2": "Returns window extended to 30 days, effective July 1, 2026."}


def _add_folder(conn):
    sources.add_source(conn, source_type="drive", source_id="drive:folder1",
                       display_name="Product Docs", config={"folder_id": "folder1", "recursive": True})


def test_poll_indexes_drafts_and_tasks(empty_db):
    conn = empty_db.conn
    _add_folder(conn)
    reader = FakeDriveReader(_FILES, _TEXTS)
    out = poll_once(conn, reader=reader, llm_client=SIM._StubLLM())

    assert len(out["documents"]) == 2
    assert len(out["drafts"]) == 2
    assert len(out["tasks"]) == 2
    # documents stored with full text
    assert "SSO" in S.get_document(conn, "f1")["full_text"]
    # each task is a card_review linked to its draft
    tasks = T.list_tasks(conn, source="drive")
    assert len(tasks) == 2
    assert all(t["kind"] == "card_review" for t in tasks)
    assert all(t["draft_id"] for t in tasks)
    # drafts are pending (review-then-publish; Drive never auto-pushes)
    assert all(d["status"] == "pending" for d in S.list_drafts(conn))


def test_repoll_is_idempotent(empty_db):
    conn = empty_db.conn
    _add_folder(conn)
    # reader that ignores the watermark → returns the same files every time
    reader = FakeDriveReader(_FILES, _TEXTS, honor_watermark=False)
    poll_once(conn, reader=reader, llm_client=SIM._StubLLM())
    poll_once(conn, reader=reader, llm_client=SIM._StubLLM())
    assert len(S.list_documents(conn)) == 2
    assert len(T.list_tasks(conn, source="drive")) == 2


def test_watermark_advances(empty_db):
    conn = empty_db.conn
    _add_folder(conn)
    reader = FakeDriveReader(_FILES, _TEXTS)        # honors the watermark
    poll_once(conn, reader=reader, llm_client=SIM._StubLLM())
    # cursor advanced to the newest modifiedTime → a second poll sees nothing new
    out2 = poll_once(conn, reader=reader, llm_client=SIM._StubLLM())
    assert out2["documents"] == []


def test_no_sources_is_noop(empty_db):
    out = poll_once(empty_db.conn, reader=FakeDriveReader(_FILES, _TEXTS), llm_client=SIM._StubLLM())
    assert out == {"documents": [], "drafts": [], "tasks": []}


def test_skipped_when_unconfigured(empty_db, monkeypatch):
    conn = empty_db.conn
    _add_folder(conn)
    fake = MagicMock()
    fake.is_configured.return_value = False
    from src.data import drive_reader
    monkeypatch.setattr(drive_reader.DriveReader, "from_settings", staticmethod(lambda: fake))
    out = poll_once(conn)                            # reader=None → built from settings → not configured
    assert out.get("skipped") is True


def test_list_changed_files_excludes_subfolders():
    """The Drive query must filter out folder objects — otherwise a subfolder
    becomes a junk document + draft + task."""
    from src.data.drive_reader import DriveReader
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc
    svc.files().list().execute.return_value = {"files": []}
    reader.list_changed_files("folder1")
    q = svc.files().list.call_args.kwargs.get("q", "")
    assert "mimeType != 'application/vnd.google-apps.folder'" in q
    assert "'folder1' in parents" in q


def test_export_text_mime_routing():
    from src.data.drive_reader import DriveReader
    reader = DriveReader("creds.json")
    svc = MagicMock()
    reader._service = svc
    # Google Doc → export(text/plain)
    svc.files().export().execute.return_value = b"hello"
    assert reader.export_text("f1", "application/vnd.google-apps.document") == "hello"
    export_kwargs = svc.files().export.call_args
    assert export_kwargs.kwargs.get("mimeType") == "text/plain"
    # PDF → get_media
    svc.files().get_media().execute.return_value = b"%PDF binary"
    reader.export_text("f2", "application/pdf")
    assert svc.files().get_media.called


@pytest.mark.ui
def test_monitor_constructs():
    from PySide6.QtWidgets import QApplication
    from src.data.drive_monitor import DriveMonitor
    _ = QApplication.instance() or QApplication([])
    mon = DriveMonitor(MagicMock())
    assert mon.source_name == "drive"
    assert mon.status == "paused"
