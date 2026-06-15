"""Guru-native blocks — emitters + directive expansion (callout / collapsible
/ card-link) into Guru's ghq-card-content__* markup."""

import pytest

from src.data import guru_blocks as gb
from src.data.html_markdown import markdown_to_html


class TestEmitters:
    def test_callout_section_and_color(self):
        out = gb.callout_html("<p>hi</p>", "warning")
        assert out.startswith("<section")
        assert 'class="ghq-card-content__callout"' in out
        assert 'data-ghq-card-content-type="CALLOUT"' in out
        assert 'data-ghq-color="yellow"' in out
        assert "#ffc20042" in out
        assert "<p>hi</p>" in out

    def test_callout_variant_aliases(self):
        assert 'data-ghq-color="green"' in gb.callout_html("x", "success")
        assert 'data-ghq-color="green"' in gb.callout_html("x", "resolved")
        assert 'data-ghq-color="red"' in gb.callout_html("x", "danger")
        assert 'data-ghq-color="blue"' in gb.callout_html("x", "note")

    def test_collapsible_details_summary(self):
        out = gb.collapsible_html("Title here", "<p>body</p>")
        assert out.startswith("<details")
        assert 'data-ghq-card-content-type="COLLAPSIBLE"' in out
        assert "<summary" in out and 'data-ghq-card-content-type="COLLAPSIBLE_SUMMARY"' in out
        assert "Title here" in out and "<p>body</p>" in out

    def test_card_link_anchor(self):
        out = gb.card_link_html("ce1b45a4-uuid", "SSO card")
        assert 'class="ghq-card-content__guru-card"' in out
        assert 'data-ghq-card-content-type="GURU_CARD"' in out
        assert 'data-ghq-guru-card-id="ce1b45a4-uuid"' in out
        assert ">SSO card</a>" in out

    def test_callout_escapes_nothing_in_body(self):
        # body is already-rendered HTML; it is inserted verbatim
        assert "<strong>x</strong>" in gb.callout_html("<strong>x</strong>", "note")


class TestExpandMarkdownPath:
    def test_callout_from_blockquote(self):
        out = gb.expand_blocks(markdown_to_html("> [!WARNING]\n> Rollout June 24."))
        assert 'class="ghq-card-content__callout"' in out
        assert 'data-ghq-color="yellow"' in out
        assert "Rollout June 24." in out
        assert "[!WARNING]" not in out

    def test_collapsible_from_directive(self):
        md = gb.collapsible_directive("More info", "the hidden body")
        out = gb.expand_blocks(markdown_to_html(md))
        assert "<details" in out and 'data-ghq-card-content-type="COLLAPSIBLE"' in out
        assert "More info" in out and "the hidden body" in out
        assert "::: details" not in out

    def test_card_link_token(self):
        out = gb.expand_blocks(markdown_to_html("See [[guru:abc123|the SSO card]]."))
        assert 'data-ghq-card-content-type="GURU_CARD"' in out
        assert "the SSO card" in out
        assert "[[guru:" not in out


class TestExpandIdempotentAndSafe:
    def test_idempotent(self):
        once = gb.expand_blocks(markdown_to_html("> [!NOTE]\n> x"))
        assert gb.expand_blocks(once) == once

    def test_no_directives_passthrough(self):
        html = "<h2>Steps</h2><p>plain body</p>"
        assert gb.expand_blocks(html) == html

    def test_empty(self):
        assert gb.expand_blocks("") == ""
        assert gb.expand_blocks(None) == ""


@pytest.mark.ui
class TestRichEditorPath:
    def test_rich_callout_p_form(self):
        # Qt models a blockquote as an indented <p>, so the rich-editor callout
        # comes through as <p>[!TYPE]<br/>body</p> — must still expand.
        from PySide6.QtWidgets import QApplication, QTextEdit
        QApplication.instance() or QApplication([])
        from src.data.html_markdown import qt_html_to_clean_html
        e = QTextEdit()
        e.textCursor().insertHtml("<blockquote>[!SUCCESS]<br>Done.</blockquote>")
        out = gb.expand_blocks(qt_html_to_clean_html(e.document().toHtml()))
        assert 'data-ghq-color="green"' in out and "Done." in out

    def test_insert_menu_present(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.ui.pages.enablement.rich_editor import RichTextEditor
        ed = RichTextEditor()
        assert hasattr(ed, "_insert_menu")
        labels = {a.text() for a in ed._insert_menu.actions()}
        assert "Collapsible section" in labels


class TestPublishExpands:
    def test_publish_sends_native_blocks(self, empty_db):
        from src.data import enablement_store as store

        class _G:
            def __init__(self): self.created = []
            def create_card(self, c, t, content):
                self.created.append(content); return {"id": "x"}
            def update_card(self, *a, **k): return {"id": "x"}

        conn = empty_db.conn
        did = store.save_card_draft(
            conn, title="T",
            content="Intro.\n\n> [!DANGER]\n> Do not skip this.\n")
        g = _G()
        store.publish_draft(conn, did, guru_client=g, collection_id="c1")
        sent = g.created[0]
        assert 'data-ghq-card-content-type="CALLOUT"' in sent
        assert 'data-ghq-color="red"' in sent
