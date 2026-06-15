"""HTML-payload Guru publish — the dual markdown+HTML content model.

Covers the Qt-HTML cleaner, content_html dual-write, the regression guard
that publish sends HTML (not raw markdown) into Guru's HTML content field,
and migration 031.
"""

import pytest

from src.data import enablement_store as store
from src.data.html_markdown import markdown_to_html, qt_html_to_clean_html


class _StubGuru:
    def __init__(self):
        self.created, self.updated = [], []

    def update_card(self, card_id, content, title=None):
        self.updated.append({"card_id": card_id, "content": content, "title": title})
        return {"id": card_id}

    def create_card(self, collection_id, title, content):
        self.created.append({"collection_id": collection_id, "title": title,
                             "content": content})
        return {"id": "new-card-123"}


class TestQtHtmlCleaner:
    def test_strips_wrapper_keeps_color(self):
        raw = ('<!DOCTYPE HTML><html><head><meta charset="utf-8">'
               '<style>p{margin:0}</style></head>'
               '<body style="font-family:Sans;font-size:9pt">'
               '<p style="-qt-block-indent:0;margin-top:0px">'
               '<span style="color:#cc0000;background-color:#fff2a8;font-family:X">hi</span>'
               '</p></body></html>')
        out = qt_html_to_clean_html(raw)
        assert "<!DOCTYPE" not in out and "<body" not in out and "<style" not in out
        assert "-qt-" not in out and "font-family" not in out
        assert "color:#cc0000" in out
        assert "background-color:#fff2a8" in out

    def test_promotes_align_attr_to_text_align(self):
        out = qt_html_to_clean_html('<p align="center" style="margin:0">x</p>')
        assert "text-align:center" in out
        assert 'align="center"' not in out

    def test_drops_style_and_script_content(self):
        out = qt_html_to_clean_html(
            '<style>p{x:y}</style><p>keep</p><script>alert(1)</script>')
        assert "keep" in out and "alert" not in out and "x:y" not in out

    def test_empty(self):
        assert qt_html_to_clean_html("") == ""


class TestDualWrite:
    def test_save_card_draft_persists_content_html(self, empty_db):
        conn = empty_db.conn
        did = store.save_card_draft(conn, title="T", content="# md",
                                    content_html="<p>rich</p>")
        d = store.get_draft(conn, did)
        assert d["content"] == "# md"
        assert d["content_html"] == "<p>rich</p>"

    def test_update_draft_content_sets_html(self, empty_db):
        conn = empty_db.conn
        did = store.save_card_draft(conn, title="T", content="orig")
        store.update_draft_content(conn, did, content="new md", content_html="<p>c</p>")
        assert store.get_draft(conn, did)["content_html"] == "<p>c</p>"

    def test_markdown_edit_clears_stale_html(self, empty_db):
        conn = empty_db.conn
        did = store.save_card_draft(conn, title="T", content="md",
                                    content_html="<p>old</p>")
        # markdown-only edit (content_html defaults None) clears stale HTML so
        # publish re-derives a fresh body from the new markdown.
        store.update_draft_content(conn, did, content="new md")
        assert store.get_draft(conn, did)["content_html"] is None


class TestPublishSendsHtml:
    def test_rich_draft_sends_stored_html(self, empty_db):
        conn = empty_db.conn
        rich = '<p><span style="color:#c00">x</span></p>'
        did = store.save_card_draft(conn, title="T", content="# Heading",
                                    content_html=rich)
        g = _StubGuru()
        res = store.publish_draft(conn, did, guru_client=g, collection_id="col1")
        assert res["ok"]
        assert g.created and g.created[0]["content"] == rich

    def test_markdown_only_draft_derives_html(self, empty_db):
        conn = empty_db.conn
        md = "## Steps\n\n- one\n- two"
        did = store.save_card_draft(conn, title="T", content=md)
        g = _StubGuru()
        store.publish_draft(conn, did, guru_client=g, collection_id="col1")
        sent = g.created[0]["content"]
        # HTML, not raw markdown — the regression guard for the bug
        assert "<h2>" in sent and "<li>" in sent
        assert "## Steps" not in sent
        assert sent == markdown_to_html(md)

    def test_update_branch_sends_html(self, empty_db):
        conn = empty_db.conn
        did = store.save_card_draft(conn, title="T", content="# H")
        store.set_draft_card_id(conn, did, "card-xyz")
        g = _StubGuru()
        store.publish_draft(conn, did, guru_client=g)
        assert g.updated and "<h1>" in g.updated[0]["content"]
        assert g.updated[0]["card_id"] == "card-xyz"

    def test_demo_no_client_marks_pushed(self, empty_db):
        conn = empty_db.conn
        did = store.save_card_draft(conn, title="T", content="# H")
        res = store.publish_draft(conn, did, guru_client=None)
        assert res["ok"] and store.get_draft(conn, did)["status"] == "pushed"


class TestMigration:
    def test_content_html_column_exists(self, empty_db):
        cols = {r[1] for r in empty_db.conn.execute(
            "PRAGMA table_info(guru_content_drafts)").fetchall()}
        assert "content_html" in cols


@pytest.mark.ui
class TestEditorCleanHtml:
    def test_to_clean_html_carries_color(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QTextCharFormat, QColor
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        ed.editor.setPlainText("hello")
        cur = ed.editor.textCursor()
        cur.select(cur.SelectionType.Document)
        ed.editor.setTextCursor(cur)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#cc0000"))
        ed.editor.mergeCurrentCharFormat(fmt)
        out = ed.to_clean_html()
        assert "color:#cc0000" in out
        assert "<!DOCTYPE" not in out and "-qt-" not in out

    def test_workbench_rich_edit_captures_html(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.workbench import WorkbenchPage
        wb = WorkbenchPage()
        wb.show_draft({"title": "T", "markdown": "plain body"})
        wb._set_view("rich")
        wb._rich.editor.setPlainText("rich body now")
        wb._set_view("preview")          # commits the rich edit
        assert wb.current_html() is not None
        assert "rich body now" in wb.current_html()

    def test_markdown_edit_leaves_html_none(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.workbench import WorkbenchPage
        wb = WorkbenchPage()
        wb.show_draft({"title": "T", "markdown": "body"})
        wb._set_view("edit")
        wb._editor.setPlainText("# markdown edit")
        wb._set_view("preview")
        assert wb.current_html() is None      # markdown-only → publish derives
