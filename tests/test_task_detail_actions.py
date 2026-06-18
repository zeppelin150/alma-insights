"""Phase 2 UI — TaskDetailPanel Asana write-back actions.

Renders the panel offscreen (saving a PNG artifact for visual review) and asserts
the interactive controls populate from the task and emit the right signals, and
that the page wires those signals to the asana_writeback runner.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

pytestmark = pytest.mark.ui

_ART = os.path.join(tempfile.gettempdir(), "alma_render")
os.makedirs(_ART, exist_ok=True)

ASANA_TASK = {
    "task_id": "t1",
    "title": "Aetna rate-change escalation",
    "source": "asana",
    "priority": "high",
    "assignee": "Renn Ops",
    "due": "Jul 15",
    "due_iso": "2026-07-15",
    "subtasks": [("Confirm affected payer list", True),
                 ("Draft member comms", False)],
    "scratch": "Waiting on payer ops confirmation before publishing.",
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _panel(task):
    from src.ui.pages.enablement.task_detail import TaskDetailPanel
    p = TaskDetailPanel(task)
    p.resize(440, 760)
    return p


def test_asana_panel_renders_interactive_controls(qapp):
    p = _panel(ASANA_TASK)
    edits = p.findChildren(QLineEdit)
    assert len(edits) == 3                       # due + subtask + comment
    assert p._due_edit.text() == "2026-07-15"    # due prefilled from due_iso
    labels = {b.text() for b in p.findChildren(QPushButton)}
    assert {"Update due", "Add", "Comment", "Open in Workbench ›"}.issubset(labels)
    out = os.path.join(_ART, "task_detail_asana.png")
    assert p.grab().save(out) and os.path.getsize(out) > 0


def test_non_asana_panel_hides_comment(qapp):
    p = _panel(dict(ASANA_TASK, source="drive", title="Drive doc task"))
    assert len(p.findChildren(QLineEdit)) == 2    # due + subtask only
    assert not hasattr(p, "_comment_edit")
    out = os.path.join(_ART, "task_detail_drive.png")
    assert p.grab().save(out) and os.path.getsize(out) > 0


def test_subtask_input_emits_and_clears(qapp):
    p = _panel(ASANA_TASK)
    seen = []
    p.subtask_added.connect(seen.append)
    p._sub_edit.setText("Notify the helpline team")
    p._emit_subtask()
    assert seen == ["Notify the helpline team"]
    assert p._sub_edit.text() == ""               # cleared after emit


def test_due_and_comment_buttons_emit(qapp):
    p = _panel(ASANA_TASK)
    dues, comments = [], []
    p.due_changed.connect(dues.append)
    p.comment_posted.connect(comments.append)
    p._due_edit.setText("2026-08-01")
    next(b for b in p.findChildren(QPushButton) if b.text() == "Update due").click()
    p._comment_edit.setText("Pushed the rollout date a week.")
    next(b for b in p.findChildren(QPushButton) if b.text() == "Comment").click()
    assert dues == ["2026-08-01"]
    assert comments == ["Pushed the rollout date a week."]


def test_page_wires_panel_signals_to_writeback(qapp, empty_db):
    from src.ui.pages.enablement import EnablementPage
    from src.data import enablement_tasks as et
    page = EnablementPage(empty_db, demo=False)
    tid = et.create_task(empty_db.conn, source="asana", kind="request",
                         title="T", source_ref="999")

    class _Drill:
        def __init__(self):
            self.widget = None

        def show_widget(self, *a):
            self.widget = a[-1]

    page._drilldown = _Drill()
    calls = []
    page._run_task_writeback = lambda *a: calls.append(a)
    page._show_task_detail({"task_id": tid, "title": "T", "source": "asana",
                            "due_iso": "", "subtasks": [], "scratch": ""})
    panel = page._drilldown.widget
    panel.subtask_added.emit("Step A")
    panel.comment_posted.emit("nice work")
    panel.due_changed.emit("2026-09-01")
    assert ("create_subtask_in_asana", tid, "Step A") in calls
    assert ("post_comment_to_asana", tid, "nice work") in calls
    assert ("update_due_in_asana", tid, "2026-09-01") in calls
