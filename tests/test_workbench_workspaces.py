"""Phase 4 — Workbench multi-workspace + task search.

The 'pending drafts' strip becomes up to MAX_WORKSPACES toggle-able workspace
chips (each closable); a '+ Find a task' button opens the task-search dialog
(search_drafts) to choose what to work on. Renn stays shared + workspace-aware
via the existing active_draft_id.
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


def _wb():
    from src.ui.pages.enablement.workbench import WorkbenchPage
    w = WorkbenchPage()
    w.set_pending_drafts([])      # clear the sample drafts
    return w


def test_open_workspace_caps_at_four(qapp):
    w = _wb()
    for i in range(1, 7):
        w.open_workspace({"id": i, "title": f"Draft {i}", "source": "drive"})
    assert [d["id"] for d in w._current_drafts] == [3, 4, 5, 6]   # oldest dropped
    assert w.active_draft_id == 6
    assert w._count_badge.text() == "4"
    assert len(w._chip_widgets) == 4


def test_open_workspace_dedups_and_activates(qapp):
    w = _wb()
    w.open_workspace({"id": 1, "title": "A", "source": "drive"})
    w.open_workspace({"id": 2, "title": "B", "source": "drive"})
    w.open_workspace({"id": 1, "title": "A", "source": "drive"})   # re-open existing
    assert [d["id"] for d in w._current_drafts] == [1, 2]
    assert w.active_draft_id == 1


def test_set_pending_drafts_caps_display(qapp):
    w = _wb()
    w.set_pending_drafts([{"id": i, "title": f"d{i}", "source": "drive"} for i in range(6)])
    assert len(w._chip_widgets) == 4
    assert w._count_badge.text() == "4"


def test_chip_close_emits_workspace_closed(qapp):
    w = _wb()
    w.open_workspace({"id": 7, "title": "Closable", "source": "drive"})
    closed = []
    w.workspace_closed.connect(closed.append)
    chip = w._chip_widgets[0]
    x = next(b for b in chip.findChildren(QPushButton) if b.text() == "×")
    x.click()
    assert closed == [7]


def test_find_button_emits(qapp):
    w = _wb()
    seen = []
    w.find_task_requested.connect(lambda: seen.append(True))
    next(b for b in w.findChildren(QPushButton) if b.text() == "+ Find a task").click()
    assert seen == [True]


def test_workspaces_strip_renders(qapp):
    w = _wb()
    for i in range(1, 4):
        w.open_workspace({"id": i, "title": f"Card {i}", "source": "drive"})
    w.resize(940, 220)
    out = os.path.join(_ART, "workbench_workspaces.png")
    assert w.grab().save(out) and os.path.getsize(out) > 0


# ── task-search dialog + page wiring ─────────────────────────────────

def test_task_search_dialog_filters_and_picks(qapp, empty_db):
    from src.data import enablement_store as store
    from src.ui.pages.enablement.task_search import TaskSearchDialog
    store.save_card_draft(empty_db.conn, title="Aetna rate change", content="x")
    store.save_card_draft(empty_db.conn, title="Okta SSO setup", content="y")
    dlg = TaskSearchDialog(empty_db.conn)
    assert dlg.results_count() == 2
    dlg._search.setText("Okta")
    assert dlg.results_count() == 1
    dlg._pick(dlg._list.item(0))
    assert dlg.selected_draft_id is not None


def test_page_find_task_opens_workspace(qapp, empty_db, monkeypatch):
    from src.ui.pages.enablement import EnablementPage
    from src.data import enablement_store as store
    import src.ui.pages.enablement.task_search as ts
    did = store.save_card_draft(empty_db.conn, title="Searched draft", content="body")
    page = EnablementPage(empty_db, demo=False)
    page.workbench.set_pending_drafts([])

    class _FakeDlg:
        def __init__(self, conn, parent=None):
            self.selected_draft_id = did

        def exec(self):
            return True

    monkeypatch.setattr(ts, "TaskSearchDialog", _FakeDlg)
    page._open_task_search()
    assert did in [d["id"] for d in page.workbench._current_drafts]
    assert page.workbench.active_draft_id == did


def test_page_workspace_close_removes_chip(qapp, empty_db):
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(empty_db, demo=False)
    page.workbench.set_pending_drafts(
        [{"id": 1, "title": "A", "source": "drive"},
         {"id": 2, "title": "B", "source": "drive"}], active_id=1)
    page._drafts = {}
    page._on_workspace_closed(1)
    assert [d["id"] for d in page.workbench._current_drafts] == [2]
    assert page.workbench.active_draft_id == 2
