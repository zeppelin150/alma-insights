"""Phase 6 — EnablementMonitor orchestration.

The orchestrator composes the Asana + Drive monitors behind one ``changed``
signal and a manual ``scan_all()``. scan_all runs each connector's module-level
poll_once once and refreshes; sub-monitor background activity also folds into
``changed``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _monitor(db_path=":memory:"):
    from src.data.enablement_monitor import EnablementMonitor
    db = MagicMock()
    db.db_path = db_path
    return EnablementMonitor(db)


def test_composes_sub_monitors(qapp):
    mon = _monitor()
    assert mon.asana.source_name == "asana"
    assert mon.drive.source_name == "drive"


def test_scan_all_worker_polls_both_and_refreshes(qapp, monkeypatch):
    from src.data import asana_monitor, drive_monitor
    monkeypatch.setattr(asana_monitor, "poll_once", MagicMock(return_value=["t1"]))
    monkeypatch.setattr(drive_monitor, "poll_once", MagicMock(return_value={"documents": []}))

    mon = _monitor()
    fired = []
    mon.changed.connect(lambda: fired.append(1))
    mon._scan_all_worker()                       # synchronous core of scan_all()

    asana_monitor.poll_once.assert_called_once()
    drive_monitor.poll_once.assert_called_once()
    assert fired == [1]


def test_sub_monitor_activity_folds_into_changed(qapp):
    mon = _monitor()
    fired = []
    mon.changed.connect(lambda: fired.append(1))
    mon.asana.tasks_created.emit(["t1"])
    mon.drive.documents_indexed.emit(["d1"])
    assert fired == [1, 1]
