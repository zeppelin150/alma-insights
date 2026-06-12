"""E4 — PptxPage view + EnablementPage integration (offscreen, demo)."""

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestOutlineText:
    def test_round_trip(self):
        from src.ui.pages.enablement.pptx_tab import outline_to_text, text_to_outline
        outline = {"title": "Deck", "slides": [
            {"title": "One", "bullets": ["a", "b"]},
            {"title": "Two", "bullets": ["c"]}]}
        text = outline_to_text(outline)
        back = text_to_outline("Deck", text)
        assert back["slides"][0]["title"] == "One"
        assert back["slides"][0]["bullets"] == ["a", "b"]
        assert back["slides"][1]["bullets"] == ["c"]

    def test_bullets_without_slide_header(self):
        from src.ui.pages.enablement.pptx_tab import text_to_outline
        out = text_to_outline("D", "- orphan bullet")
        assert out["slides"][0]["bullets"] == ["orphan bullet"]


class TestPage:
    def _page(self):
        from src.ui.pages.enablement.pptx_tab import PptxPage
        return PptxPage()

    def test_set_decks_renders(self, qapp):
        p = self._page()
        p.set_decks([{"id": 1, "title": "SSO", "slide_count": 3, "status": "pending"}])
        assert p._list.count() == 1

    def test_show_deck_fills_editor(self, qapp):
        p = self._page()
        p.show_deck({"id": 7, "title": "SSO", "status": "pending",
                     "outline": {"title": "SSO", "slides": [
                         {"title": "Intro", "bullets": ["hi"]}]}})
        assert p._title.text() == "SSO"
        assert "# Intro" in p._outline.toPlainText()
        assert p._save_btn.isEnabled()

    def test_model_topic_signal(self, qapp):
        p = self._page()
        got = []
        p.model_topic_requested.connect(got.append)
        p._topic.setText("Onboarding 101")
        p._on_model_topic()
        assert got == ["Onboarding 101"]

    def test_save_emits_outline(self, qapp):
        p = self._page()
        got = []
        p.outline_saved.connect(lambda did, t, o: got.append((did, t, o)))
        p.show_deck({"id": 5, "title": "D", "status": "pending",
                     "outline": {"title": "D", "slides": []}})
        p._outline.setPlainText("# Slide A\n- point")
        p._on_save()
        assert got and got[0][0] == 5
        assert got[0][2]["slides"][0]["title"] == "Slide A"

    def test_export_signal(self, qapp):
        p = self._page()
        got = []
        p.export_requested.connect(got.append)
        p.show_deck({"id": 9, "title": "D", "status": "pending", "outline": {}})
        p._on_export()
        assert got == [9]


class TestIntegration:
    def test_enablement_page_has_powerpoint_tab(self, qapp, empty_db):
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        assert "powerpoint" in page._tab_widgets
        page.select_tab("powerpoint")
        assert page.tabs.currentWidget() is page._tab_widgets["powerpoint"]
        # demo seeded decks on load
        assert page.pptx._list.count() >= 2

    def test_deck_select_and_export_end_to_end(self, qapp, empty_db, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        from src.data import pptx_store
        decks = pptx_store.list_decks(page._conn())
        deck_id = decks[0]["id"]
        page._on_pptx_deck_selected(deck_id)
        assert page.pptx._title.text()
        out = tmp_path / "out.pptx"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(out), "PowerPoint (*.pptx)"))
        page._on_pptx_export(deck_id)
        assert out.exists()
        assert pptx_store.get_deck(page._conn(), deck_id)["status"] == "exported"
