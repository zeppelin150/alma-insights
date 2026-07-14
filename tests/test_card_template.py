"""Card/article template — storage round trip, injection into card-gen and
revise prompts, and the Style Guide tab's rendered preview + inline editor.
Settings access is faked so tests never touch settings.yaml."""

import pytest

from src.data import enablement_store as store


@pytest.fixture()
def fake_settings(monkeypatch):
    """Dict-backed settings_manager for the guide pointers."""
    state = {}

    def get_section(name, default=None):
        return state.get(name, default if default is not None else {})

    def set_section(name, value):
        state[name] = dict(value)
        return True

    import src.data.settings_manager as sm
    monkeypatch.setattr(sm, "get_section", get_section)
    monkeypatch.setattr(sm, "set_section", set_section)
    return state


class StubLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return "TITLE: Stub Card\n---\nStub body."


class TestStorage:
    def test_round_trip(self, empty_db, fake_settings):
        conn = empty_db.conn
        assert store.get_card_template(conn) == ""
        doc_id = store.set_card_template(conn, "## Article Title\n[Insert]")
        assert fake_settings["enablement"]["card_template_doc_id"] == doc_id
        assert store.get_card_template(conn) == "## Article Title\n[Insert]"

    def test_replace_updates_same_doc(self, empty_db, fake_settings):
        conn = empty_db.conn
        a = store.set_card_template(conn, "v1")
        b = store.set_card_template(conn, "v2")
        assert a == b
        assert store.get_card_template(conn) == "v2"

    def test_clear(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_card_template(conn, "skeleton")
        store.clear_card_template()
        assert store.get_card_template(conn) == ""

    def test_block_wraps_text(self, empty_db, fake_settings):
        conn = empty_db.conn
        assert store.card_template_block(conn) == ""
        store.set_card_template(conn, "## Still Need Help?")
        block = store.card_template_block(conn)
        assert "TEMPLATE START" in block
        assert "## Still Need Help?" in block
        assert "EXACTLY" in block

    def test_span_markup_stripped(self, empty_db, fake_settings):
        """doc_reader keeps Word heading colors as spans — guides drop them."""
        conn = empty_db.conn
        store.set_card_template(
            conn, '## <span style="color:#2f3ba2">**Body**</span>\ntext')
        assert store.get_card_template(conn) == "## **Body**\ntext"

    def test_independent_of_style_guide(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_style_guide(conn, "tone rules")
        store.set_card_template(conn, "heading skeleton")
        assert store.get_style_guide(conn) == "tone rules"
        assert store.get_card_template(conn) == "heading skeleton"
        store.clear_card_template()
        assert store.get_style_guide(conn) == "tone rules"

    def test_update_text_in_place_keeps_name(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_card_template(conn, "v1", name="Unified Article Template",
                                doc_id="card-template-unified")
        store.update_card_template_text(conn, "v2")
        docs = store.list_card_templates(conn)
        assert len(docs) == 1
        assert "Unified Article Template" in docs[0]["name"]
        assert store.get_card_template(conn) == "v2"

    def test_update_style_guide_text_keeps_name(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_style_guide(conn, "v1", name="Alma tone",
                              doc_id="style-guide-alma-tone")
        store.update_style_guide_text(conn, "v2")
        docs = store.list_style_guides(conn)
        assert len(docs) == 1
        assert "Alma tone" in docs[0]["name"]
        assert store.get_style_guide(conn) == "v2"

    def test_library_activate_delete(self, empty_db, fake_settings):
        conn = empty_db.conn
        a = store.set_card_template(conn, "A", name="a", doc_id="card-template-a")
        b = store.set_card_template(conn, "B", name="b", doc_id="card-template-b")
        assert store.get_card_template(conn) == "B"
        assert store.set_active_card_template(conn, a)
        assert store.get_card_template(conn) == "A"
        assert store.delete_card_template(conn, a)
        # deleting the active one promotes the newest remaining
        assert store.get_card_template(conn) == "B"
        assert store.delete_card_template(conn, b)
        assert store.get_card_template(conn) == ""


class TestInjection:
    def _doc(self, conn):
        return store.save_document(
            conn, source="drive", name="Doc.gdoc", full_text="Body text."
        )

    def test_card_gen_includes_template_when_set(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_card_template(conn, "## FAQs (SEO + AI Readiness)")
        llm = StubLLM()
        store.draft_card_from_document(conn, self._doc(conn), llm)
        assert "## FAQs (SEO + AI Readiness)" in llm.prompts[0]
        assert "TEMPLATE START" in llm.prompts[0]

    def test_card_gen_includes_both_blocks(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_style_guide(conn, "Always end with an FAQ.")
        store.set_card_template(conn, "## Still Need Help?")
        llm = StubLLM()
        store.draft_card_from_document(conn, self._doc(conn), llm)
        assert "Always end with an FAQ." in llm.prompts[0]
        assert "## Still Need Help?" in llm.prompts[0]

    def test_card_gen_clean_when_unset(self, empty_db, fake_settings):
        conn = empty_db.conn
        llm = StubLLM()
        store.draft_card_from_document(conn, self._doc(conn), llm)
        assert "TEMPLATE START" not in llm.prompts[0]

    def test_revise_includes_template(self, empty_db, fake_settings, monkeypatch):
        conn = empty_db.conn
        store.set_card_template(conn, "## Article Title")
        draft_id = store.save_card_draft(conn, title="T", content="Body")
        llm = StubLLM()
        import src.gemini.client_factory as cf
        monkeypatch.setattr(cf, "build_client_for_task", lambda task: llm)
        from src.data.chat_tools.enablement_tools import _revise_draft_impl
        res = _revise_draft_impl(conn, draft_id, "tighten it")
        assert res["ok"]
        assert "## Article Title" in llm.prompts[0]


@pytest.mark.ui
class TestSettingsPreview:
    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_content_preview_show_hide(self, qapp):
        from src.ui.pages.enablement.settings import SettingsPage
        s = SettingsPage()
        assert s._sg_section._preview.isHidden()
        s.set_style_guide_content("## Heading\nSome **bold** rules.")
        assert not s._sg_section._preview.isHidden()
        assert "Heading" in s._sg_section._view.toPlainText()
        s.set_style_guide_content("")
        assert s._sg_section._preview.isHidden()

    def test_edit_save_emits_and_rerenders(self, qapp):
        from src.ui.pages.enablement.settings import SettingsPage
        s = SettingsPage()
        s.set_card_template_content("## Old")
        sec = s._ct_section
        got = []
        s.card_template_saved.connect(got.append)
        sec._begin_edit()
        assert sec.in_edit_mode()
        sec._editor.setPlainText("## New heading")
        sec._save_edit()
        assert got == ["## New heading"]
        assert not sec.in_edit_mode()
        assert "New heading" in sec._view.toPlainText()

    def test_cancel_restores_original(self, qapp):
        from src.ui.pages.enablement.settings import SettingsPage
        s = SettingsPage()
        s.set_style_guide_content("original")
        sec = s._sg_section
        sec._begin_edit()
        sec._editor.setPlainText("scribbles")
        sec._cancel_edit()
        assert not sec.in_edit_mode()
        assert sec.content() == "original"

    def test_library_rows_strip_both_tags(self, qapp):
        from src.ui.pages.enablement.settings import SettingsPage
        s = SettingsPage()
        s.set_card_templates([
            {"doc_id": "t1", "name": "[CARD-TEMPLATE] Unified", "chars": 10,
             "active": True},
        ])
        assert s._ct_section._library.count() == 1

    def test_action_signals_forward(self, qapp):
        from src.ui.pages.enablement.settings import SettingsPage
        s = SettingsPage()
        got = []
        s.card_template_action.connect(got.append)
        s._ct_section.action.emit("upload_folder")
        assert got == ["upload_folder"]


@pytest.mark.ui
class TestPageDemoTemplate:
    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        yield QApplication.instance() or QApplication([])

    def test_demo_card_template_action(self, qapp, empty_db, fake_settings):
        """Demo drive action seeds the bundled Unified Support Center
        template and the generator block picks it up."""
        from src.ui.pages.enablement import EnablementPage
        page = EnablementPage(empty_db, demo=True)
        page._on_card_template_action("drive")
        text = store.get_card_template(page._conn())
        assert "Unified Support Center/Guru Article Template" in text
        assert "Still Need Help?" in text
        block = store.card_template_block(page._conn())
        assert "TEMPLATE START" in block
