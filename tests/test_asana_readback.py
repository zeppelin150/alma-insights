"""Phase 5a — Asana two-way read-back.

On poll, Asana-side changes (due date / completion / assignee / subtasks) flow
back into the local task we already track; existing tasks are reconciled in place
rather than re-created; subtask mirroring is idempotent (matched by gid).
"""

from __future__ import annotations


class FakeAsana:
    def __init__(self, tasks=None, subtasks=None):
        self._tasks = tasks or []
        self._subtasks = subtasks or {}
        self.api_key = "tok"

    def list_tasks(self, project_gid, modified_since=None, **kw):
        return list(self._tasks)

    def list_subtasks(self, task_gid, **kw):
        return [dict(s) for s in self._subtasks.get(task_gid, [])]

    def get_task(self, gid, **kw):
        return next((dict(t) for t in self._tasks if t["gid"] == gid), {})


def test_client_read_requests():
    from src.data.asana_client import AsanaClient
    c = AsanaClient("tok")
    calls = []
    c._get = lambda path, params=None: calls.append((path, params)) or {}
    c.get_task("100")
    assert calls[-1][0] == "/tasks/100"
    c.list_subtasks("100")
    assert calls[-1][0] == "/tasks/100/subtasks"


def test_reconcile_updates_due_status_assignee(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="T", source_ref="g1")
    fake = FakeAsana(subtasks={"g1": []})
    task = {"gid": "g1", "due_on": "2026-09-09", "completed": True,
            "assignee": {"name": "Ada"}}
    assert am._reconcile_existing_task(conn, fake, task) is True
    t = et.get_task(conn, tid)
    assert t["due_date"] == "2026-09-09"
    assert t["status"] == "done"
    assert t["assignee"] == "Ada"


def test_reconcile_returns_false_for_untracked(empty_db):
    from src.data import asana_monitor as am
    assert am._reconcile_existing_task(empty_db.conn, FakeAsana(), {"gid": "nope"}) is False


def test_pull_subtasks_adds_then_syncs_no_dup(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="T", source_ref="g2")
    fake = FakeAsana(subtasks={"g2": [
        {"gid": "s1", "name": "Step one", "completed": False},
        {"gid": "s2", "name": "Step two", "completed": True}]})
    am._pull_subtasks(conn, fake, tid, "g2")
    subs = et.list_subtasks(conn, tid)
    assert len(subs) == 2
    by_gid = {s["asana_subtask_gid"]: s for s in subs}
    assert by_gid["s1"]["done"] == 0 and by_gid["s2"]["done"] == 1
    # s1 completed in Asana → re-pull syncs done, never duplicates
    fake._subtasks["g2"][0]["completed"] = True
    am._pull_subtasks(conn, fake, tid, "g2")
    subs2 = et.list_subtasks(conn, tid)
    assert len(subs2) == 2
    assert {s["asana_subtask_gid"]: s["done"] for s in subs2} == {"s1": 1, "s2": 1}


def test_poll_reconciles_existing_not_recreate(empty_db, monkeypatch):
    from src.data import asana_monitor as am, enablement_tasks as et, asana_setup
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Old", source_ref="g3")
    fake = FakeAsana(
        tasks=[{"gid": "g3", "due_on": "2026-10-01", "completed": False,
                "modified_at": "2026-09-01T00:00:00Z"}],
        subtasks={"g3": [{"gid": "sx", "name": "From Asana", "completed": False}]})
    board = {"source_id": "b1",
             "config": {"project_gid": "p1", "indicators": [], "mappings": {}}}
    monkeypatch.setattr(asana_setup, "get_asana_config", lambda c: [board])
    created = am.poll_once(conn, client=fake)
    assert created == []                                  # reconciled, not re-created
    t = et.get_task(conn, tid)
    assert t["due_date"] == "2026-10-01"                  # pulled back from Asana
    assert len(et.list_subtasks(conn, tid)) == 1          # Asana subtask mirrored
