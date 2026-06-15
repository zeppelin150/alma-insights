"""Guru-parity rich-text controls + expand/focus overlay.

Locks in the empirically-verified markdown round-trip for every NEW
formatting control (strikethrough, inline code, code block, blockquote,
checklist, table, divider, image) and the Workbench expand overlay's
load → commit → collapse path.
"""

import pytest
from PySide6.QtGui import QTextBlockFormat
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _editor():
    from src.ui.pages.enablement.rich_editor import RichTextEditor
    return RichTextEditor()


def _select_all(ed):
    cursor = ed.editor.textCursor()
    cursor.select(cursor.SelectionType.Document)
    ed.editor.setTextCursor(cursor)


class TestNewControlsRoundTrip:
    def test_strikethrough(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("struck")
        _select_all(ed)
        ed._strike()
        assert "~~struck~~" in ed.to_markdown()

    def test_inline_code(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("mono")
        _select_all(ed)
        ed._inline_code()
        assert "`mono`" in ed.to_markdown()

    def test_code_block(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("int x = 1;")
        _select_all(ed)
        ed._code_block()
        out = ed.to_markdown()
        assert "```" in out and "int x = 1;" in out

    def test_blockquote(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("quoted line")
        _select_all(ed)
        ed._blockquote()
        assert "> quoted line" in ed.to_markdown()

    def test_task_list(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("todo item")
        _select_all(ed)
        ed._task_list()
        assert "- [ ] todo item" in ed.to_markdown()

    def test_table(self, qapp, monkeypatch):
        ed = _editor()
        from PySide6.QtWidgets import QInputDialog
        vals = iter([(2, True), (2, True)])
        monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: next(vals))
        ed._table()
        out = ed.to_markdown()
        # Header-seeded table emits a valid GFM grid with a delimiter row.
        assert "Column 1" in out and "Column 2" in out
        assert "|-" in out.replace(" ", "")

    def test_horizontal_rule(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("above")
        cursor = ed.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        ed.editor.setTextCursor(cursor)
        ed._hr()
        ed.editor.textCursor().insertText("below")   # HR between content
        out = ed.to_markdown()
        assert "---" in out or "- - -" in out

    def test_image(self, qapp, monkeypatch):
        ed = _editor()
        from PySide6.QtWidgets import QInputDialog
        vals = iter([("https://x.test/a.png", True), ("diagram", True)])
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: next(vals))
        ed._image()
        assert "![diagram](https://x.test/a.png)" in ed.to_markdown()


class TestQtApiContract:
    """The checklist control depends on this exact PySide6 API; fail loudly
    if a future bump changes it."""

    def test_setmarker_is_the_api(self):
        bf = QTextBlockFormat()
        assert hasattr(bf, "setMarker")
        assert not hasattr(bf, "setMarkerType")
        assert hasattr(QTextBlockFormat.MarkerType, "Unchecked")


class TestToolbarBreadth:
    def test_has_all_guru_representable_controls(self, qapp):
        ed = _editor()
        for handler in ("_bold", "_italic", "_underline", "_strike",
                        "_heading", "_list", "_task_list", "_inline_code",
                        "_code_block", "_blockquote", "_table", "_hr",
                        "_link", "_image", "_clear"):
            assert callable(getattr(ed, handler)), handler


class TestExpandOverlay:
    def _overlay(self, parent=None):
        from src.ui.pages.enablement.expand_overlay import ExpandOverlay
        return ExpandOverlay(parent)

    def test_starts_hidden(self, qapp):
        ov = self._overlay()
        assert ov.isHidden()

    def test_load_seeds_both_editors(self, qapp):
        ov = self._overlay()
        ov.load("# Hi\n\nbody text", 7, "Title")
        assert "Hi" in ov._rich.to_markdown()
        assert "body text" in ov._source.toPlainText()
        assert ov._title.text() == "Title"

    def test_collapse_emits_committed_and_hides(self, qapp):
        ov = self._overlay()
        got = []
        ov.committed.connect(lambda did, md, html: got.append((did, md)))
        ov.load("orig", 9, "T")
        ov._set_mode("markdown")
        ov._source.setPlainText("edited in focus mode")
        ov._collapse()
        assert got and got[0][0] == 9
        assert "edited in focus mode" in got[0][1]
        assert ov.isHidden()

    def test_read_only_hint(self, qapp):
        ov = self._overlay()
        ov.load("x", 1, "T", read_only=True)
        assert ov._ro_hint.isVisible() or ov._read_only

    def test_resize_tracks_parent(self, qapp):
        from PySide6.QtWidgets import QWidget
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QResizeEvent
        parent = QWidget()
        parent.resize(800, 600)
        parent.show()
        ov = self._overlay(parent)
        ov.load("x", 1, "T")
        ov.present()
        qapp.processEvents()
        parent.resize(1000, 700)
        ov.eventFilter(parent, QResizeEvent(QSize(1000, 700), QSize(800, 600)))
        assert ov.width() == 1000 and ov.height() == 700


class TestWorkbenchExpand:
    def _wb(self):
        from src.ui.pages.enablement.workbench import WorkbenchPage
        return WorkbenchPage()

    def test_expand_button_present(self, qapp):
        wb = self._wb()
        assert hasattr(wb, "_expand_btn")

    def test_open_expand_builds_and_loads_overlay(self, qapp):
        from PySide6.QtWidgets import QWidget
        wb = self._wb()
        host = QWidget()
        wb.set_overlay_host(host)
        wb.show_draft({"title": "SSO", "markdown": "draft body"})
        wb._open_expand()
        assert wb._overlay is not None
        assert "draft body" in wb._overlay._source.toPlainText()

    def test_overlay_commit_persists_via_content_edited(self, qapp):
        from PySide6.QtWidgets import QWidget
        wb = self._wb()
        wb.set_overlay_host(QWidget())
        got = []
        wb.content_edited.connect(lambda did, md: got.append((did, md)))
        wb.show_draft({"title": "T", "markdown": "before"})
        wb._open_expand()
        wb._on_expand_committed(wb._active_draft_id, "after focus edit")
        assert got and got[-1][1] == "after focus edit"
        assert wb._current_md == "after focus edit"

    def test_overlay_commit_no_change_does_not_emit(self, qapp):
        from PySide6.QtWidgets import QWidget
        wb = self._wb()
        wb.set_overlay_host(QWidget())
        wb.show_draft({"title": "T", "markdown": "same"})
        got = []
        wb.content_edited.connect(lambda did, md: got.append(md))
        wb._on_expand_committed(wb._active_draft_id, "same")
        assert not got
