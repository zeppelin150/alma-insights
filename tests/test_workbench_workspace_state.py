"""Phase 5b — per-workspace edit state preserved across chip switches + Ctrl+N."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _wb():
    from src.ui.pages.enablement.workbench import WorkbenchPage
    w = WorkbenchPage()
    w.set_pending_drafts([])
    return w


def test_switch_preserves_unsaved_edit_and_view(qapp):
    w = _wb()
    c1 = {"title": "One", "markdown": "original one", "source": "drive"}
    c2 = {"title": "Two", "markdown": "original two", "source": "drive"}
    w.open_workspace({"id": 1, "title": "One", "source": "drive"})
    w.show_draft(c1)
    w._set_view("edit")
    w._editor.setPlainText("EDITED one")
    w.open_workspace({"id": 2, "title": "Two", "source": "drive"})   # snapshots ws1
    w.show_draft(c2)
    assert w._current_md == "original two"
    w.switch_workspace(1, c1)
    assert w._current_md == "EDITED one"            # unsaved edit preserved
    assert w._current_view_mode() == "edit"         # view mode preserved


def test_ctrl_n_switches_to_nth_workspace(qapp):
    w = _wb()
    for i in (1, 2, 3):
        w.open_workspace({"id": i, "title": f"C{i}", "source": "drive"})
    picked = []
    w.draft_selected.connect(picked.append)
    w._switch_to_index(0)    # Ctrl+1
    w._switch_to_index(2)    # Ctrl+3
    assert picked == [1, 3]
    w._switch_to_index(9)    # out of range → no emit
    assert picked == [1, 3]


def test_closing_workspace_prunes_state(qapp):
    w = _wb()
    c = {"title": "X", "markdown": "body", "source": "drive"}
    w.open_workspace({"id": 1, "title": "X", "source": "drive"})
    w.show_draft(c)
    w._set_view("edit")
    w._editor.setPlainText("edited")
    w.open_workspace({"id": 2, "title": "Y", "source": "drive"})     # snapshots ws1
    assert 1 in w._ws_state
    w.set_pending_drafts([{"id": 2, "title": "Y", "source": "drive"}], active_id=2)
    assert 1 not in w._ws_state                     # pruned on close
