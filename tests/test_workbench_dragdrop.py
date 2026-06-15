"""Bug fix — drag-and-drop a doc loads it as a draft (esp. demo mode).

Repro for: dropping a document did nothing because the drop zone lacked
dragMoveEvent (Windows rejects the drop) and the handler was a stub that
never ingested the file.
"""

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestDropTargets:
    def test_dropzone_handles_drag_move(self, qapp):
        # dragMoveEvent must be OVERRIDDEN (QWidget has a no-op base), else
        # Windows rejects the drop after dragEnter accepts.
        from src.ui.pages.enablement.workbench import _DropZone
        assert "dragMoveEvent" in _DropZone.__dict__

    def test_workbench_accepts_drops(self, qapp):
        from src.ui.pages.enablement.workbench import WorkbenchPage
        wb = WorkbenchPage()
        assert wb.acceptDrops()

    def test_workbench_drop_emits_load_file(self, qapp, tmp_path):
        from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
        from PySide6.QtGui import QDropEvent
        from src.ui.pages.enablement.workbench import WorkbenchPage
        wb = WorkbenchPage()
        f = tmp_path / "d.txt"
        f.write_text("hi")
        got = []
        wb.load_file_requested.connect(got.append)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(f))])
        ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime,
                        Qt.LeftButton, Qt.NoModifier, QEvent.Drop)
        wb.dropEvent(ev)
        import os
        # QUrl.toLocalFile() returns forward slashes on Windows — normalize.
        assert got and os.path.normpath(got[0]) == os.path.normpath(str(f))


class TestIngest:
    def test_read_local_text(self, tmp_path):
        from src.ui.pages.enablement import EnablementPage
        f = tmp_path / "n.md"
        f.write_text("# Title\n\nbody text here")
        assert "body text here" in EnablementPage._read_local_text(str(f))

    def test_read_docx(self, tmp_path):
        import zipfile
        from src.ui.pages.enablement import EnablementPage
        p = tmp_path / "report.docx"
        doc_xml = (
            '<?xml version="1.0"?><w:document xmlns:w="ns"><w:body>'
            '<w:p><w:r><w:t>Hello from</w:t></w:r>'
            '<w:r><w:t xml:space="preserve"> a docx</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Second &amp; final line</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("word/document.xml", doc_xml)
        text = EnablementPage._read_local_text(str(p))
        assert "Hello from a docx" in text
        assert "Second & final line" in text
        assert "can't be read" not in text   # not the stub fallback

    def test_dropped_file_creates_draft_demo(self, qapp, empty_db, tmp_path):
        from src.data import enablement_store as store
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        f = tmp_path / "pricing.md"
        f.write_text("# Pricing\n\nTier B moves to usage-based billing on Aug 1.")
        before = len(store.list_drafts(page._conn(), status="pending"))
        res = page._ingest_local_file(page._conn(), str(f), demo=True)
        assert res["ok"] and res["draft_id"]
        assert res["chars"] > 0
        after = store.list_drafts(page._conn(), status="pending")
        assert len(after) == before + 1
