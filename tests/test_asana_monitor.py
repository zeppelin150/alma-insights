"""Phase 4 — live Asana sync (poll a board → auto-create enablement tasks).

poll_once is the shared core (Qt monitor + run_monitor_now tool + tests). These
drive it with a mock AsanaClient: one task whose indicator field is set to the
trigger value (→ created, with mapped priority/assignee) and one that isn't
(→ skipped); re-polling must not duplicate.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.data import asana_setup
from src.data import enablement_tasks as T
from src.data.asana_monitor import poll_once


def _configure_board(conn):
    asana_setup.set_asana_board_config(
        conn,
        project_gid="proj1", project_name="Enablement Requests",
        indicator_field_gid="team_gid", indicator_field_name="Assigned Team",
        indicator_value_gid="enab_gid", indicator_value_name="Enablement",
        priority_field_gid="urg_gid", assignee_field_gid="ppl_gid",
    )


def _matching_task():
    return {
        "gid": "task1", "name": "SSO rollout request", "due_on": "2026-06-20",
        "permalink_url": "https://app.asana.com/0/1/task1", "completed": False,
        "modified_at": "2026-06-09T10:00:00.000Z",
        "assignee": {"name": "Fallback Person"},
        "custom_fields": [
            {"gid": "team_gid", "name": "Assigned Team",
             "enum_value": {"gid": "enab_gid", "name": "Enablement"}},
            {"gid": "urg_gid", "name": "Urgency",
             "enum_value": {"gid": "high_gid", "name": "High"}, "display_value": "High"},
            {"gid": "ppl_gid", "name": "Assigned People",
             "people_value": [{"gid": "u1", "name": "J. Rivera"}]},
        ],
    }


def _non_matching_task():
    return {
        "gid": "task2", "name": "Support ticket", "completed": False,
        "modified_at": "2026-06-09T11:00:00.000Z",
        "custom_fields": [
            {"gid": "team_gid", "name": "Assigned Team",
             "enum_value": {"gid": "support_gid", "name": "Support"}},
        ],
    }


def _client(tasks):
    c = MagicMock()
    c.api_key = "key"
    c.list_tasks.return_value = tasks
    return c


def test_indicator_match_creates_one_task(empty_db):
    conn = empty_db.conn
    _configure_board(conn)
    client = _client([_matching_task(), _non_matching_task()])

    created = poll_once(conn, client=client)
    assert len(created) == 1

    tasks = T.list_tasks(conn, source="asana")
    assert len(tasks) == 1
    t = tasks[0]
    assert t["title"] == "SSO rollout request"
    assert t["priority"] == "high"           # mapped from Urgency=High
    assert t["assignee"] == "J. Rivera"       # resolved from the people field
    assert t["due_date"] == "2026-06-20"
    assert t["source_ref"] == "task1"


def test_assignee_gid_cached_in_asana_users(empty_db):
    conn = empty_db.conn
    _configure_board(conn)
    poll_once(conn, client=_client([_matching_task()]))
    row = conn.execute("SELECT name FROM asana_users WHERE gid='u1'").fetchone()
    assert row and row[0] == "J. Rivera"


def test_repoll_does_not_duplicate(empty_db):
    conn = empty_db.conn
    _configure_board(conn)
    client = _client([_matching_task(), _non_matching_task()])
    poll_once(conn, client=client)
    poll_once(conn, client=client)            # same board item again
    assert len(T.list_tasks(conn, source="asana")) == 1


def test_no_boards_configured_is_noop(empty_db):
    assert poll_once(empty_db.conn, client=_client([_matching_task()])) == []


def test_falls_back_to_task_assignee(empty_db):
    """No people custom-field on the task → use the Asana assignee name."""
    conn = empty_db.conn
    _configure_board(conn)
    task = _matching_task()
    task["custom_fields"] = [c for c in task["custom_fields"] if c["gid"] != "ppl_gid"]
    poll_once(conn, client=_client([task]))
    assert T.list_tasks(conn, source="asana")[0]["assignee"] == "Fallback Person"


@pytest.mark.ui
def test_monitor_constructs():
    from PySide6.QtWidgets import QApplication
    from src.data.asana_monitor import AsanaMonitor
    _ = QApplication.instance() or QApplication([])
    mon = AsanaMonitor(MagicMock())
    assert mon.source_name == "asana"
    assert mon.status == "paused"
