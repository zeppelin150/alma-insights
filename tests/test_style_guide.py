"""Style guide — storage round trip + injection into card-gen and revise
prompts. Settings access is faked so tests never touch settings.yaml."""

import pytest

from src.data import enablement_store as store


@pytest.fixture()
def fake_settings(monkeypatch):
    """Dict-backed settings_manager for the style-guide pointer."""
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
        assert store.get_style_guide(conn) == ""
        doc_id = store.set_style_guide(conn, "Tone: plain language.")
        assert fake_settings["enablement"]["style_guide_doc_id"] == doc_id
        assert store.get_style_guide(conn) == "Tone: plain language."

    def test_replace_updates_same_doc(self, empty_db, fake_settings):
        conn = empty_db.conn
        a = store.set_style_guide(conn, "v1")
        b = store.set_style_guide(conn, "v2")
        assert a == b
        assert store.get_style_guide(conn) == "v2"

    def test_clear(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_style_guide(conn, "rules")
        store.clear_style_guide()
        assert store.get_style_guide(conn) == ""

    def test_block_wraps_text(self, empty_db, fake_settings):
        conn = empty_db.conn
        assert store.style_guide_block(conn) == ""
        store.set_style_guide(conn, "Use numbered steps.")
        block = store.style_guide_block(conn)
        assert "STYLE GUIDE START" in block
        assert "Use numbered steps." in block


class TestInjection:
    def _doc(self, conn):
        return store.save_document(
            conn, source="drive", name="Doc.gdoc", full_text="Body text."
        )

    def test_card_gen_includes_block_when_set(self, empty_db, fake_settings):
        conn = empty_db.conn
        store.set_style_guide(conn, "Always end with an FAQ.")
        llm = StubLLM()
        store.draft_card_from_document(conn, self._doc(conn), llm)
        assert "Always end with an FAQ." in llm.prompts[0]
        assert "{style_guide}" not in llm.prompts[0]

    def test_card_gen_clean_when_unset(self, empty_db, fake_settings):
        conn = empty_db.conn
        llm = StubLLM()
        store.draft_card_from_document(conn, self._doc(conn), llm)
        assert "STYLE GUIDE START" not in llm.prompts[0]
        assert "{style_guide}" not in llm.prompts[0]

    def test_revise_includes_block(self, empty_db, fake_settings, monkeypatch):
        conn = empty_db.conn
        store.set_style_guide(conn, "Bullet lists over prose.")
        draft_id = store.save_card_draft(conn, title="T", content="Body")
        llm = StubLLM()
        import src.gemini.client_factory as cf
        monkeypatch.setattr(cf, "build_client_for_task", lambda task: llm)
        from src.data.chat_tools.enablement_tools import _revise_draft_impl
        res = _revise_draft_impl(conn, draft_id, "tighten it")
        assert res["ok"]
        assert "Bullet lists over prose." in llm.prompts[0]
        assert "tighten it" in llm.prompts[0]
