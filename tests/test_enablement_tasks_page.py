"""TasksPage (enablement Plan tab task list) — expanded-row action wiring.

The expanded row's "Open in Workbench ›" button rendered since the tab was
built but was connected to NOTHING (tasks.py created the QPushButton and laid
it out without a single `.connect`), so clicking it did nothing — reported
2026-08-10. The drilldown TaskDetailPanel's sibling button has always been
wired (open_in_workbench → page.py tab switch); this locks the list-row
button to the same contract.

Run: python -m pytest tests/test_enablement_tasks_page.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QPushButton

from src.ui.pages.enablement.tasks import TasksPage


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _workbench_button(page: TasksPage) -> QPushButton:
    """The expanded row's button (row 0 renders expanded by default)."""
    btns = [b for b in page.findChildren(QPushButton)
            if b.text() == "Open in Workbench ›"]
    assert btns, "expanded row should carry the Open in Workbench button"
    return btns[0]


def test_open_in_workbench_click_emits_the_signal(qapp):
    page = TasksPage()
    fired = []
    page.open_workbench.connect(lambda: fired.append(1))
    _workbench_button(page).click()
    assert fired == [1]


def test_the_page_routes_the_signal_to_the_workbench_tab():
    """Structural: the enablement page must consume the signal the same way
    it consumes TaskDetailPanel.open_in_workbench (page.py's existing
    setCurrentWidget(_workbench_tab) route)."""
    src = (Path(__file__).resolve().parents[1] / "src" / "ui" / "pages"
           / "enablement" / "page.py").read_text(encoding="utf-8")
    assert "self.tasks.open_workbench.connect" in src
