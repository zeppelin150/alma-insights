"""Phase 5c — style-guide library: switch active, delete, Renn tool, settings UI."""

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
    state: dict = {}
    monkeypatch.setattr("src.data.settings_manager.get_section",
                        lambda n, d=None: state.get(n, d if d is not None else {}))
    monkeypatch.setattr("src.data.settings_manager.set_section",
                        lambda n, v: state.__setitem__(n, dict(v)))
    return state


def test_set_active_and_delete_promotes(empty_db, fake_settings):
    from src.data import enablement_store as store
    a = store.set_style_guide(empty_db.conn, "A", name="A", doc_id="sg-a")
    b = store.set_style_guide(empty_db.conn, "B", name="B", doc_id="sg-b")  # active=b
    assert store.get_style_guide(empty_db.conn) == "B"
    assert store.set_active_style_guide(empty_db.conn, a)
    assert store.get_style_guide(empty_db.conn) == "A"
    assert store.delete_style_guide(empty_db.conn, a)              # delete the active one
    guides = store.list_style_guides(empty_db.conn)
    assert [g["doc_id"] for g in guides] == [b]
    assert store.get_style_guide(empty_db.conn) == "B"            # promoted to remaining


def test_set_active_missing_returns_false(empty_db, fake_settings):
    from src.data import enablement_store as store
    assert store.set_active_style_guide(empty_db.conn, "nope") is False


def test_set_active_style_guide_tool(empty_db, fake_settings):
    from src.data import enablement_store as store
    from src.data.chat_tools.enablement_tools import _set_active_style_guide_impl
    store.set_style_guide(empty_db.conn, "A", name="A", doc_id="sg-a")
    store.set_style_guide(empty_db.conn, "B", name="B", doc_id="sg-b")
    assert _set_active_style_guide_impl(empty_db.conn, "sg-a")["ok"]
    assert store.get_style_guide(empty_db.conn) == "A"
    assert _set_active_style_guide_impl(empty_db.conn, "missing")["ok"] is False


def test_tool_registered_both_paths():
    from src.data.chat_tools import registry
    registry._ensure_registered()
    from src.llm import claude_tools as CT
    assert "set_active_style_guide" in registry._CHAT_TOOLS
    assert "set_active_style_guide" in CT._DISPATCH
    assert "set_active_style_guide" in {s["name"] for s in CT.TOOL_DEFINITIONS}


def test_settings_library_renders_and_emits(qapp, fake_settings):
    from src.ui.pages.enablement.settings import SettingsPage
    s = SettingsPage()
    s.set_style_guides([
        {"doc_id": "sg-a", "name": "[STYLE-GUIDE] Brand Voice", "chars": 120, "active": True},
        {"doc_id": "sg-b", "name": "[STYLE-GUIDE] Ops Guide", "chars": 88, "active": False}])
    acts, dels = [], []
    s.style_guide_activate.connect(acts.append)
    s.style_guide_delete.connect(dels.append)
    btns = s.findChildren(QPushButton)
    next(b for b in btns if b.text() == "Make active").click()   # only the inactive row has it
    next(b for b in btns if b.text() == "Delete").click()
    assert acts == ["sg-b"]
    assert dels and dels[0] in ("sg-a", "sg-b")
    for i in range(s._tabs.count()):
        if "Style" in s._tabs.tabText(i):
            s._tabs.setCurrentIndex(i)
            break
    s.resize(640, 500)
    out = os.path.join(_ART, "settings_style_guide_library.png")
    assert s.grab().save(out) and os.path.getsize(out) > 0
