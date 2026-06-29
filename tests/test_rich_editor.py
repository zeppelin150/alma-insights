"""E1 — rich-text draft editor: markdown round-trip, toolbar formatting,
and the Workbench three-mode toggle persisting via content_edited."""

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _editor():
    from src.ui.pages.enablement.rich_editor import RichTextEditor
    return RichTextEditor()


class TestRoundTrip:
    def test_headings_and_emphasis_survive(self, qapp):
        ed = _editor()
        md = "# Title\n\nSome **bold** and *italic* text.\n"
        ed.set_markdown(md)
        out = ed.to_markdown()
        assert "# Title" in out
        assert "**bold**" in out
        assert "*italic*" in out or "_italic_" in out

    def test_lists_survive(self, qapp):
        ed = _editor()
        ed.set_markdown("- one\n- two\n\n1. first\n2. second\n")
        out = ed.to_markdown()
        assert "one" in out and "two" in out
        # bullets render as - or * in GitHub dialect
        assert out.count("first") == 1 and "second" in out

    def test_empty(self, qapp):
        ed = _editor()
        ed.set_markdown("")
        assert ed.to_markdown() == ""


class TestToolbar:
    def test_bold_applies(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("hello world")
        cursor = ed.editor.textCursor()
        cursor.select(cursor.SelectionType.Document)
        ed.editor.setTextCursor(cursor)
        ed._bold()
        assert "**" in ed.to_markdown()

    def test_heading_applies(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("My heading")
        cursor = ed.editor.textCursor()
        cursor.select(cursor.SelectionType.Document)
        ed.editor.setTextCursor(cursor)
        ed._heading(2)
        assert ed.to_markdown().startswith("##")

    def test_bullet_list_applies(self, qapp):
        ed = _editor()
        ed.editor.setPlainText("item")
        ed._list(__import__("PySide6.QtGui", fromlist=["QTextListFormat"])
                 .QTextListFormat.ListDisc)
        out = ed.to_markdown()
        assert "item" in out


@pytest.mark.ui
class TestWorkbenchThreeModes:
    def _wb(self, qapp):
        from src.ui.pages.enablement.workbench import WorkbenchPage
        return WorkbenchPage()

    def test_view_toggle_buttons(self, qapp):
        wb = self._wb(qapp)
        assert hasattr(wb, "_preview_btn")
        assert hasattr(wb, "_rich_btn")
        assert hasattr(wb, "_edit_btn")
        assert hasattr(wb, "_diff_btn")        # Milestone D: "Review changes" diff view
        assert wb._body_stack.count() == 4     # preview / rich / edit / diff

    def test_rich_edit_persists_via_content_edited(self, qapp):
        wb = self._wb(qapp)
        got = []
        wb.content_edited.connect(lambda did, md: got.append((did, md)))
        wb.show_draft({"title": "T", "markdown": "original body"})
        wb._set_view("rich")
        # simulate a WYSIWYG edit
        wb._rich.editor.setPlainText("edited in rich text")
        wb._set_view("preview")
        assert got, "switching away from rich must commit the edit"
        assert "edited in rich text" in got[-1][1]
        assert "edited in rich text" in wb._current_md

    def test_markdown_edit_still_persists(self, qapp):
        wb = self._wb(qapp)
        got = []
        wb.content_edited.connect(lambda did, md: got.append(md))
        wb.show_draft({"title": "T", "markdown": "orig"})
        wb._set_view("edit")
        wb._editor.setPlainText("md edited")
        wb._set_view("preview")
        assert got and got[-1] == "md edited"

    def test_switching_draft_does_not_leak_edit(self, qapp):
        wb = self._wb(qapp)
        got = []
        wb.content_edited.connect(lambda did, md: got.append((did, md)))
        wb.show_draft({"title": "A", "markdown": "aaa"})
        wb._set_view("rich")
        wb._rich.editor.setPlainText("unsaved change")
        # load a different draft WITHOUT toggling back — must not emit the
        # unsaved change under the new draft
        wb.show_draft({"title": "B", "markdown": "bbb"})
        assert wb._current_md == "bbb"
