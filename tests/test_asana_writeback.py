"""Phase 2 — Asana write-back (two-way sync).

Covers the AsanaClient POST/PUT methods, the Qt-free asana_writeback helpers
(subtask create / comment / due-date, with local-only + graceful-failure paths),
the migration 032 link column, and the mapped-projects-only poll guard.
"""

from __future__ import annotations

from src.data.asana_client import AsanaClient


class _Rec:
    def __init__(self, ret):
        self.calls = []
        self.ret = ret

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        return self.ret


def test_create_subtask_builds_request():
    c = AsanaClient("tok")
    c._send = _Rec({"gid": "sub1"})
    out = c.create_subtask("100", "Do X", due_on="2026-07-01")
    assert out == {"gid": "sub1"}
    assert c._send.calls[0] == ("POST", "/tasks/100/subtasks",
                                {"name": "Do X", "due_on": "2026-07-01"})


def test_add_comment_builds_request():
    c = AsanaClient("tok")
    c._send = _Rec({"gid": "story1"})
    c.add_comment("100", "hi there")
    assert c._send.calls[0] == ("POST", "/tasks/100/stories", {"text": "hi there"})


def test_update_due_date_builds_request_and_clears():
    c = AsanaClient("tok")
    c._send = _Rec({"gid": "100"})
    c.update_due_date("100", "2026-07-02")
    assert c._send.calls[0] == ("PUT", "/tasks/100", {"due_on": "2026-07-02"})
    c.update_due_date("100", None)
    assert c._send.calls[1] == ("PUT", "/tasks/100", {"due_on": None})


# ── write-back helpers ───────────────────────────────────────────────

class FakeAsana:
    def __init__(self):
        self.subtasks, self.comments, self.dues = [], [], []

    def create_subtask(self, parent, name, **kw):
        self.subtasks.append((parent, name))
        return {"gid": "asub-1"}

    def add_comment(self, gid, text):
        self.comments.append((gid, text))
        return {"gid": "story-1"}

    def update_due_date(self, gid, due):
        self.dues.append((gid, due))
        return {"gid": gid}


def _asana_task(conn, gid="111"):
    from src.data import enablement_tasks as et
    return et.create_task(conn, source="asana", kind="request", title="T", source_ref=gid)


def test_create_subtask_syncs_to_asana_and_persists_gid(empty_db):
    from src.data import asana_writeback as awb
    conn = empty_db.conn
    tid = _asana_task(conn, "111")
    fa = FakeAsana()
    res = awb.create_subtask_in_asana(conn, tid, "Step 1", client=fa)
    assert res["ok"] and res["synced"] and res["asana_subtask_gid"] == "asub-1"
    assert fa.subtasks == [("111", "Step 1")]
    # migration 032 column persisted the link
    row = conn.execute(
        "SELECT asana_subtask_gid FROM enablement_subtasks WHERE subtask_id = ?",
        (res["subtask_id"],),
    ).fetchone()
    assert row[0] == "asub-1"


def test_create_subtask_local_only_for_non_asana(empty_db):
    from src.data import asana_writeback as awb, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="manual", kind="request", title="M")
    fa = FakeAsana()
    res = awb.create_subtask_in_asana(conn, tid, "Step", client=fa)
    assert res["ok"] and res["synced"] is False
    assert fa.subtasks == []                       # nothing pushed to Asana
    assert len(et.list_subtasks(conn, tid)) == 1   # but saved locally


def test_post_comment_to_asana(empty_db):
    from src.data import asana_writeback as awb
    conn = empty_db.conn
    tid = _asana_task(conn, "222")
    fa = FakeAsana()
    res = awb.post_comment_to_asana(conn, tid, "Looks good", client=fa)
    assert res["ok"] and res["story_gid"] == "story-1"
    assert fa.comments == [("222", "Looks good")]


def test_post_comment_non_asana_errors(empty_db):
    from src.data import asana_writeback as awb, enablement_tasks as et
    conn = empty_db.conn
    tid = et.create_task(conn, source="drive", kind="request", title="D")
    res = awb.post_comment_to_asana(conn, tid, "x", client=FakeAsana())
    assert not res["ok"] and res["error"] == "not_an_asana_task"


def test_update_due_syncs_and_updates_local(empty_db):
    from src.data import asana_writeback as awb, enablement_tasks as et
    conn = empty_db.conn
    tid = _asana_task(conn, "333")
    fa = FakeAsana()
    res = awb.update_due_in_asana(conn, tid, "2026-08-01", client=fa)
    assert res["ok"] and res["synced"]
    assert fa.dues == [("333", "2026-08-01")]
    assert et.get_task(conn, tid)["due_date"] == "2026-08-01"   # local also updated


def test_writeback_api_failure_is_graceful(empty_db):
    from src.data import asana_writeback as awb, enablement_tasks as et
    conn = empty_db.conn
    tid = _asana_task(conn, "444")

    class Boom:
        def create_subtask(self, *a, **k):
            raise RuntimeError("429 rate limited")

    res = awb.create_subtask_in_asana(conn, tid, "S", client=Boom())
    assert res["ok"] and res["synced"] is False and "429" in res["error"]
    assert len(et.list_subtasks(conn, tid)) == 1   # local subtask survived


def test_poll_board_skips_unmapped_project(empty_db):
    from src.data import asana_monitor as am
    results = {"created": [], "updated": []}
    am._poll_board(empty_db.conn, object(), {"source_id": "s1", "config": {}}, results)
    assert results == {"created": [], "updated": []}


def test_writeback_tools_registered():
    """WS1-M6: the three un-gated write tools are RETIRED from every model
    surface; the single Confirm-gated request_asana_task_update replaces them."""
    from src.data.chat_tools import registry
    registry._ensure_registered()
    retired = ("create_asana_subtask", "post_asana_comment", "update_asana_due_date")
    for t in retired:
        assert t not in registry._CHAT_TOOLS
    assert "request_asana_task_update" in registry._CHAT_TOOLS
    from src.llm import claude_tools as CT
    names = {s["name"] for s in CT.TOOL_DEFINITIONS}
    for t in retired:
        assert t not in CT._DISPATCH and t not in names
    assert "request_asana_task_update" in CT._DISPATCH
    assert "request_asana_task_update" in names
    # The MCP surface steers retired names instead of a generic unknown-tool.
    from src.mcp import chat_mcp_server as MCP
    for t in retired:
        assert t not in MCP._MCP_ALLOWED_TOOLS
        assert "request_asana_task_update" in MCP._RETIRED_TOOL_HINTS[t]
