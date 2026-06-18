"""Phase 3 — style-guide seamless upload + searchable identifier + Renn tools.

Verifies the [STYLE-GUIDE] name identifier (so Renn can find guides), multi-guide
listing with an active flag, the Settings Upload control + the page 'upload' action
(the previously-missing case), and the list/get style-guide chat tools.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

pytestmark = pytest.mark.ui

_ART = os.path.join(tempfile.gettempdir(), "alma_render")
os.makedirs(_ART, exist_ok=True)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fake_settings(monkeypatch):
    """In-memory settings so the style-guide pointer persists within a test."""
    state: dict = {}

    def get_section(name, default=None):
        return state.get(name, default if default is not None else {})

    def set_section(name, value):
        state[name] = dict(value)

    monkeypatch.setattr("src.data.settings_manager.get_section", get_section)
    monkeypatch.setattr("src.data.settings_manager.set_section", set_section)
    return state


def test_set_style_guide_prepends_identifier(empty_db, fake_settings):
    from src.data import enablement_store as store
    did = store.set_style_guide(empty_db.conn, "Tone: plain.", name="Brand Voice")
    doc = store.get_document(empty_db.conn, did)
    assert doc["name"].startswith("[STYLE-GUIDE]")
    assert "Brand Voice" in doc["name"]
    assert store.get_style_guide(empty_db.conn) == "Tone: plain."   # text unchanged


def test_search_documents_finds_style_guide_by_tag(empty_db, fake_settings):
    from src.data import enablement_store as store
    store.set_style_guide(empty_db.conn, "Use numbered steps.", name="Ops Guide")
    hits = store.search_documents(empty_db.conn, "[STYLE-GUIDE]")
    assert any("[STYLE-GUIDE]" in h["name"] for h in hits)


def test_list_style_guides_flags_active(empty_db, fake_settings):
    from src.data import enablement_store as store
    store.set_style_guide(empty_db.conn, "A", name="Guide A", doc_id="style-guide-a")
    store.set_style_guide(empty_db.conn, "B", name="Guide B", doc_id="style-guide-b")
    guides = {g["name"]: g for g in store.list_style_guides(empty_db.conn)}
    assert len(guides) == 2
    assert guides["[STYLE-GUIDE] Guide B"]["active"] is True     # latest = active
    assert guides["[STYLE-GUIDE] Guide A"]["active"] is False


def test_upload_handler_stores_active_guide(qapp, empty_db, fake_settings, tmp_path, monkeypatch):
    """The previously-missing 'upload' action: file dialog → read → store + activate."""
    from PySide6.QtWidgets import QFileDialog
    from src.ui.pages.enablement import EnablementPage
    from src.data import enablement_store as store

    f = tmp_path / "Brand Voice.md"
    f.write_text("# Brand Voice\n\nTone: warm, concise. End with an FAQ.", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(f), "")))

    page = EnablementPage(empty_db, demo=False)
    page._on_style_guide_action("upload")

    text = store.get_style_guide(empty_db.conn)
    assert "Tone: warm, concise" in text
    guides = store.list_style_guides(empty_db.conn)
    assert any(g["active"] and "Brand Voice" in g["name"] for g in guides)


def test_style_guide_chat_tools(empty_db, fake_settings):
    from src.data import enablement_store as store
    from src.data.chat_tools.enablement_tools import (
        _list_style_guides_impl, _get_style_guide_impl)
    store.set_style_guide(empty_db.conn, "Plain language only.", name="Voice")
    lst = _list_style_guides_impl(empty_db.conn)
    assert lst["ok"] and lst["count"] >= 1
    got = _get_style_guide_impl(empty_db.conn)
    assert got["has_style_guide"] and "Plain language only." in got["style_guide"]


def test_settings_style_guide_has_upload_button(qapp):
    from src.ui.pages.enablement.settings import SettingsPage
    s = SettingsPage()
    labels = {b.text() for b in s.findChildren(QPushButton)}
    assert {"Paste…", "Upload…", "From Drive…", "Clear"}.issubset(labels)
    s.resize(560, 760)
    out = os.path.join(_ART, "settings_style_guide.png")
    assert s.grab().save(out) and os.path.getsize(out) > 0


def test_style_guide_tools_registered():
    from src.data.chat_tools import registry
    registry._ensure_registered()
    from src.llm import claude_tools as CT
    names = {s["name"] for s in CT.TOOL_DEFINITIONS}
    for t in ("list_style_guides", "get_style_guide"):
        assert t in registry._CHAT_TOOLS
        assert t in CT._DISPATCH and t in names
