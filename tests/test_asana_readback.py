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
    # list_subtasks pages via _get_raw, so the stub has to sit there too.
    c._get_raw = lambda path, params=None: calls.append((path, params)) or {"data": []}
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
    # WS1-M2: returns the local task_id (truthy) instead of a bare True.
    assert am._reconcile_existing_task(conn, fake, task) == tid
    t = et.get_task(conn, tid)
    assert t["due_date"] == "2026-09-09"
    assert t["status"] == "done"
    assert t["assignee"] == "Ada"


def test_reconcile_returns_false_for_untracked(empty_db):
    from src.data import asana_monitor as am
    # WS1-M2: returns None (falsy) for untracked gids instead of a bare False.
    assert am._reconcile_existing_task(empty_db.conn, FakeAsana(), {"gid": "nope"}) is None


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


_SUB = {"gid": "sa1", "name": "Draft the FAQ", "completed": False,
        "assignee_gid": "u77", "assignee_name": "Ada Lovelace",
        "due_on": "2026-09-01", "modified_at": "2026-08-01T00:00:00Z",
        "permalink_url": "https://app.asana.com/0/1/sa1", "notes": "Body"}


def test_assigned_subtask_promoted_to_task(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g10")
    fake = FakeAsana(subtasks={"g10": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g10", board_source_id="asana:111")
    row = conn.execute(
        "SELECT * FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()
    assert row is not None
    d = dict(row)
    assert d["parent_task_ref"] == "g10"
    assert d["title"] == "Draft the FAQ"
    assert d["due_date"] == "2026-09-01"
    assert d["assignee"] == "Ada Lovelace" and d["assignee_gid"] == "u77"
    assert d["source_url"] == "https://app.asana.com/0/1/sa1"
    assert d["description"] == "Body"
    # attribution goes through task_board_links, like any polled task
    assert et.task_board_ids(conn, d["task_id"]) == ["asana:111"]
    # the parent's checklist mirror is untouched by promotion
    assert len(et.list_subtasks(conn, tid)) == 1


def test_promoted_subtask_no_duplicate_on_repoll(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g11")
    fake = FakeAsana(subtasks={"g11": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g11", board_source_id="asana:111")
    am._pull_subtasks(conn, fake, tid, "g11", board_source_id="asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()[0] == 1
    assert len(et.list_subtasks(conn, tid)) == 1


def test_promoted_subtask_reconciles_due_assignee_and_done(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g12")
    fake = FakeAsana(subtasks={"g12": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g12", board_source_id="asana:111")
    fake._subtasks["g12"][0].update(
        {"due_on": "2026-09-15", "completed": True,
         "assignee_gid": "u88", "assignee_name": "Grace Hopper"})
    am._pull_subtasks(conn, fake, tid, "g12", board_source_id="asana:111")
    row = dict(conn.execute(
        "SELECT * FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone())
    assert row["due_date"] == "2026-09-15"
    assert row["assignee"] == "Grace Hopper" and row["assignee_gid"] == "u88"
    assert row["status"] == "done"


def test_completed_subtask_is_not_promoted(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g13")
    done_sub = dict(_SUB, completed=True)
    fake = FakeAsana(subtasks={"g13": [done_sub]})
    am._pull_subtasks(conn, fake, tid, "g13", board_source_id="asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()[0] == 0
    # still mirrored on the parent's checklist, as done
    subs = et.list_subtasks(conn, tid)
    assert len(subs) == 1 and subs[0]["done"] == 1


def test_unassigned_subtask_stays_checklist_only(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g14")
    fake = FakeAsana(subtasks={"g14": [
        {"gid": "sb1", "name": "No assignee", "completed": False}]})
    am._pull_subtasks(conn, fake, tid, "g14", board_source_id="asana:111")
    assert conn.execute(
        "SELECT COUNT(*) FROM enablement_tasks WHERE source='asana' AND source_ref='sb1'"
    ).fetchone()[0] == 0
    assert len(et.list_subtasks(conn, tid)) == 1


def test_list_tasks_carries_parent_title_for_promoted_rows(empty_db):
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g15")
    fake = FakeAsana(subtasks={"g15": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g15", board_source_id="asana:111")
    by_ref = {t.get("source_ref"): t for t in et.list_tasks(conn)}
    assert by_ref["sa1"]["parent_task_ref"] == "g15"
    assert by_ref["sa1"]["parent_title"] == "Parent"
    assert by_ref["g15"]["parent_task_ref"] is None
    assert by_ref["g15"]["parent_title"] is None


def test_migration_056_applied_and_rerun_is_noop(empty_db):
    conn = empty_db.conn
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(enablement_tasks)").fetchall()}
    assert "parent_task_ref" in cols
    applied = {r[0] for r in conn.execute(
        "SELECT filename FROM schema_migrations").fetchall()}
    assert "056_subtask_promotion.sql" in applied
    from src.updater.schema_migrator import SchemaMigrator
    assert SchemaMigrator().migrate(conn) == []


def test_unassignment_clears_assignee_and_still_syncs(empty_db):
    """A promoted subtask whose Asana assignee is REMOVED must keep
    reconciling: assignee columns clear to empty, due/done still sync."""
    from src.data import asana_monitor as am
    conn = empty_db.conn
    from src.data import enablement_tasks as et
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g20")
    fake = FakeAsana(subtasks={"g20": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g20", board_source_id="asana:111")
    fake._subtasks["g20"][0].update(
        {"assignee_gid": "", "assignee_name": "",
         "due_on": "2026-09-20", "completed": True})
    am._pull_subtasks(conn, fake, tid, "g20", board_source_id="asana:111")
    row = dict(conn.execute(
        "SELECT * FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone())
    assert row["assignee"] == "" and row["assignee_gid"] == ""
    assert row["due_date"] == "2026-09-20"
    assert row["status"] == "done"


def test_vanished_subtask_dismissed_not_deleted(empty_db):
    """A subtask deleted in Asana emits no board event; the authoritative
    list_subtasks diff must dismiss the promoted row — never DELETE it."""
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g21")
    fake = FakeAsana(subtasks={"g21": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g21", board_source_id="asana:111")
    fake._subtasks["g21"] = []
    am._pull_subtasks(conn, fake, tid, "g21", board_source_id="asana:111")
    rows = conn.execute(
        "SELECT status FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchall()
    assert len(rows) == 1                      # dismissed, not deleted
    assert rows[0][0] == "dismissed"
    # decision: the checklist-mirror row is retained (never a DELETE), same
    # discipline as the task row — it just stops syncing.
    assert len(et.list_subtasks(conn, tid)) == 1


def test_truncated_listing_skips_dismissal(empty_db):
    """DELETION-FAILS-CLOSED: a listing that could be silently truncated
    (>= one full Asana page) must not dismiss anything this cycle."""
    from src.data import asana_monitor as am, enablement_tasks as et
    from src.data.asana_client import _PAGE_SIZE
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g22")
    fake = FakeAsana(subtasks={"g22": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g22", board_source_id="asana:111")
    # Next poll returns a full page NOT containing sa1 — indistinguishable
    # from a mid-walk truncation, so the dismissal pass must be skipped.
    fake._subtasks["g22"] = [
        {"gid": f"t{i}", "name": f"n{i}", "completed": False}
        for i in range(_PAGE_SIZE)]
    am._pull_subtasks(conn, fake, tid, "g22", board_source_id="asana:111")
    row = conn.execute(
        "SELECT status FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()
    assert row[0] != "dismissed"


def test_failed_listing_skips_dismissal(empty_db):
    """A listing that dies mid-call proves nothing about deletions —
    the exception propagates and no dismissal happens."""
    import pytest
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g23")
    fake = FakeAsana(subtasks={"g23": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g23", board_source_id="asana:111")

    def boom(task_gid, **kw):
        raise RuntimeError("429 mid-page")

    fake.list_subtasks = boom
    with pytest.raises(RuntimeError):
        am._pull_subtasks(conn, fake, tid, "g23", board_source_id="asana:111")
    row = conn.execute(
        "SELECT status FROM enablement_tasks WHERE source='asana' AND source_ref='sa1'"
    ).fetchone()
    assert row[0] != "dismissed"


def test_parent_dismissal_cascades_to_promoted_children(empty_db):
    """Deleting the parent task in Asana dismisses its promoted subtask rows
    too (their deletion emits no event of its own). Done children keep their
    done state; nothing is DELETEd."""
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g24")
    done_child = dict(_SUB, gid="sa2", name="Done child")
    fake = FakeAsana(subtasks={"g24": [dict(_SUB), done_child]})
    am._pull_subtasks(conn, fake, tid, "g24", board_source_id="asana:111")
    child_done = conn.execute(
        "SELECT task_id FROM enablement_tasks WHERE source_ref='sa2'"
    ).fetchone()[0]
    et.update_task(conn, child_done, status="done")
    am._dismiss_by_gid(conn, "g24")
    by_ref = {r[0]: r[1] for r in conn.execute(
        "SELECT source_ref, status FROM enablement_tasks WHERE source='asana'"
    ).fetchall()}
    assert by_ref["g24"] == "dismissed"
    assert by_ref["sa1"] == "dismissed"        # open child cascades
    assert by_ref["sa2"] == "done"             # done child keeps done
    assert len(by_ref) == 3                    # nothing deleted


def test_description_synced_on_repoll(empty_db):
    """Asana notes are source-of-truth for the body on subtasks too, matching
    the parent-task reconcile convention."""
    from src.data import asana_monitor as am, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="Parent",
                         source_ref="g25")
    fake = FakeAsana(subtasks={"g25": [dict(_SUB)]})
    am._pull_subtasks(conn, fake, tid, "g25", board_source_id="asana:111")
    fake._subtasks["g25"][0]["notes"] = "Edited in Asana"
    am._pull_subtasks(conn, fake, tid, "g25", board_source_id="asana:111")
    row = conn.execute(
        "SELECT description FROM enablement_tasks WHERE source_ref='sa1'"
    ).fetchone()
    assert row[0] == "Edited in Asana"


def test_update_task_tool_cannot_set_parent_task_ref(empty_db):
    """parent_task_ref is promotion lineage owned by asana_monitor — the chat
    tool lane must strip it so a model turn can't fake or orphan a subtask."""
    from src.data import enablement_tasks as et
    from src.data.chat_tools.enablement_tools import handle_update_task
    conn = empty_db.conn
    tid = et.create_task(conn, source="asana", kind="request", title="T",
                         source_ref="g26")
    res = handle_update_task(
        conn, {"task_id": tid, "parent_task_ref": "evil", "priority": "high"}, {})
    assert res["ok"] is True
    t = et.get_task(conn, tid)
    assert t["parent_task_ref"] is None
    assert t["priority"] == "high"
    # parent_task_ref alone → nothing updatable left after the strip
    res2 = handle_update_task(conn, {"task_id": tid, "parent_task_ref": "evil"}, {})
    assert res2["ok"] is False and res2["error"] == "no_updatable_fields"
    assert et.get_task(conn, tid)["parent_task_ref"] is None


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
