"""TaskWebController + TaskBridge contract tests (no WebEngine).

Everything a page script can invoke is treated as untrusted: forged/stale
ids, malformed dates, unknown attachment gids and unregistered urls must all
be safe no-ops; writes relay ONLY through the injected host lane behind a
single-winner inflight claim that the host's reopen loop (show_task) clears.
Registered in tests/test_web_guardrails.py::GATED_SLOT_TESTS.
"""

import json
from datetime import datetime

import pytest
from PySide6.QtCore import QCoreApplication

from src.services.task_web import TaskWebController
from src.ui.web.task_bridge import TaskBridge


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


TASK = {
    "task_id": "t1", "source": "asana", "source_ref": "900100",
    "title": "BCBSMA copay update", "status": "in_progress",
    "assignee": "Jordan Avery", "due_iso": "2026-09-01",
    "due_date": "2026-09-01",
    "source_url": "https://app.asana.com/0/1/900100",
    "subtasks": [
        {"text": "Draft card", "done": False, "gid": "sub900",
         "assignee": "", "due": ""},
        {"text": "Local-only step", "done": False, "gid": "",
         "assignee": "", "due": ""},
    ],
    "extras": {
        "fetched_at": "2026-08-10T11:55:00+00:00",
        "attachments": [{"gid": "a1", "name": "thread", "host": "slack"}],
        "stories": [
            {"gid": "s1", "text": "see https://doc.example/z",
             "created_at": "2026-07-02T12:00:00Z", "author": "Dana",
             "subtype": "comment_added"},
        ],
        "task_fields": {"start_on": "2026-08-15"},
    },
}


def _controller(**kw):
    calls = {"write": [], "refresh": [], "urls": []}
    defaults = dict(
        write_fn=lambda lane, tid, *a: calls["write"].append((lane, tid) + a),
        refresh_fn=lambda tid, gid: calls["refresh"].append((tid, gid)),
        open_url_fn=lambda u: calls["urls"].append(u),
        now_fn=lambda: datetime(2026, 8, 10),
    )
    defaults.update(kw)
    ctrl = TaskWebController(**defaults)
    seen = {"data": [], "status": [], "resolved": []}
    ctrl.task_data.connect(lambda j: seen["data"].append(json.loads(j)))
    ctrl.status_text.connect(lambda t: seen["status"].append(t))
    ctrl.action_resolved.connect(lambda j: seen["resolved"].append(json.loads(j)))
    return ctrl, calls, seen


def _shown(**kw):
    ctrl, calls, seen = _controller(**kw)
    ctrl.show_task(dict(TASK))
    return ctrl, calls, seen


# ── show_task push + capabilities ────────────────────────────────────────

def test_show_task_pushes_viewmodel_with_capabilities():
    ctrl, _calls, seen = _shown()
    assert len(seen["data"]) == 1
    vm = seen["data"][0]
    assert vm["task_id"] == "t1"
    assert vm["capabilities"] == {"complete": True, "due": True,
                                  "comment": True, "subtask": True,
                                  "description": True, "refresh": True}


def test_capabilities_fail_closed_without_injected_lanes():
    ctrl, _c, seen = _controller(write_fn=None, refresh_fn=None)
    ctrl.show_task(dict(TASK))
    caps = seen["data"][0]["capabilities"]
    assert set(caps.values()) == {False}


def test_non_asana_task_gets_no_asana_capabilities():
    ctrl, _c, seen = _controller()
    ctrl.show_task({"task_id": "d1", "source": "drive", "title": "Doc"})
    caps = seen["data"][0]["capabilities"]
    assert caps["complete"] is False and caps["refresh"] is False
    assert caps["subtask"] is True     # local checklist lane still works


def test_refresh_replays_and_is_silent_before_any_feed():
    ctrl, _c, seen = _shown()
    ctrl.js_refresh()
    assert len(seen["data"]) == 2 and seen["data"][0] == seen["data"][1]
    ctrl2, _c2, seen2 = _controller()
    ctrl2.js_refresh()
    assert seen2["data"] == []


# ── write gate: current-id, single-winner, release-on-reopen ─────────────

def test_toggle_complete_relays_the_writeback_lane():
    ctrl, calls, seen = _shown()
    ctrl.js_toggle_complete("t1", True)
    assert calls["write"] == [("set_completed_in_asana", "t1", True)]
    assert seen["resolved"][-1]["ok"] is True


def test_forged_or_stale_ids_never_write():
    ctrl, calls, seen = _shown()
    for bad in ("nope", "", None, "t2"):
        ctrl.js_toggle_complete(bad, True)
        ctrl.js_set_due(bad, "2026-09-02")
        ctrl.js_post_comment(bad, "hi")
        ctrl.js_add_subtask(bad, "hi")
    assert calls["write"] == []
    assert all(r["ok"] is False for r in seen["resolved"])


def test_single_winner_while_inflight_then_released_by_reopen():
    ctrl, calls, seen = _shown()
    ctrl.js_toggle_complete("t1", True)
    ctrl.js_post_comment("t1", "second while first inflight")
    assert len(calls["write"]) == 1
    assert seen["resolved"][-1]["error"] == "busy"
    ctrl.show_task(dict(TASK))            # the host reopen loop
    ctrl.js_post_comment("t1", "after reopen")
    assert calls["write"][-1] == ("post_comment_to_asana", "t1", "after reopen")


def test_write_dispatch_failure_releases_the_claim():
    def boom(*_a):
        raise RuntimeError("db locked")
    ctrl, calls, seen = _shown(write_fn=boom)
    ctrl.js_toggle_complete("t1", True)
    assert seen["resolved"][-1]["error"] == "dispatch_failed"
    ctrl.js_post_comment("t1", "still allowed")   # claim was released
    assert seen["resolved"][-1]["error"] == "dispatch_failed"


def test_set_due_validates_full_iso_and_change():
    ctrl, calls, seen = _shown()
    for bad in ("2026-9-2", "not-a-date", "javascript:x", "", None,
                "2026-09-021"):
        ctrl.js_set_due("t1", bad)
    assert calls["write"] == []
    ctrl.js_set_due("t1", "2026-09-01")           # unchanged
    assert calls["write"] == []
    assert seen["resolved"][-1]["error"] == "unchanged"
    ctrl.js_set_due("t1", "2026-09-15")
    assert calls["write"] == [("update_due_in_asana", "t1", "2026-09-15")]


def test_comment_and_subtask_require_text_and_cap_at_6000():
    ctrl, calls, _seen = _shown()
    ctrl.js_post_comment("t1", "   ")
    ctrl.js_add_subtask("t1", "")
    assert calls["write"] == []
    ctrl.js_post_comment("t1", "x" * 9000)
    assert len(calls["write"][0][2]) == 6000


def test_toggle_subtask_relays_served_gids_only():
    ctrl, calls, seen = _shown()
    ctrl.js_toggle_subtask("t1", "forged", True)
    ctrl.js_toggle_subtask("t1", "", True)
    ctrl.js_toggle_subtask("t1", None, True)
    assert calls["write"] == []
    assert seen["resolved"][-1]["error"] == "unknown_subtask"
    ctrl.js_toggle_subtask("t1", "sub900", True)
    assert calls["write"] == [
        ("set_subtask_completed_in_asana", "t1", "sub900", True)]
    assert seen["resolved"][-1]["ok"] is True


def test_toggle_subtask_respects_capability_and_current_id():
    ctrl, calls, seen = _shown(write_fn=None)
    ctrl.js_toggle_subtask("t1", "sub900", True)
    assert seen["resolved"][-1]["error"] == "not_allowed"
    ctrl2, calls2, seen2 = _shown()
    ctrl2.js_toggle_subtask("t2", "sub900", True)
    assert calls2["write"] == []
    assert seen2["resolved"][-1]["ok"] is False


def test_open_subtask_navigates_served_gids_only():
    opened = []
    ctrl, _c, _s2 = _shown(open_subtask_fn=lambda tid, gid: opened.append((tid, gid)))
    ctrl.js_open_subtask("t1", "forged")
    ctrl.js_open_subtask("t2", "sub900")     # stale task id
    assert opened == []
    ctrl.js_open_subtask("t1", "sub900")
    assert opened == [("t1", "sub900")]


def test_open_subtask_without_fn_or_broken_fn_is_silent():
    ctrl, _c, _s2 = _shown()
    ctrl.js_open_subtask("t1", "sub900")     # no fn injected

    def boom(*_a):
        raise RuntimeError("nope")
    ctrl2, _c2, _s3 = _shown(open_subtask_fn=boom)
    ctrl2.js_open_subtask("t1", "sub900")    # must not raise


def test_update_description_gates_and_caps():
    ctrl, calls, seen = _shown()
    ctrl.js_update_description("forged", "new body")
    assert calls["write"] == []
    ctrl.js_update_description("t1", "x" * 70000)
    lane, tid, md = calls["write"][0]
    assert lane == "update_description_in_asana" and tid == "t1"
    assert len(md) == 60000
    ctrl.show_task(dict(TASK))               # release the claim
    ctrl.js_update_description("t1", "")     # clearing is legal
    assert calls["write"][-1] == ("update_description_in_asana", "t1", "")
    assert seen["resolved"][-1]["ok"] is True


def test_update_description_requires_capability():
    ctrl, calls, seen = _shown(write_fn=None)
    ctrl.js_update_description("t1", "body")
    assert seen["resolved"][-1]["error"] == "not_allowed"


# ── the description LANE (asana_writeback) ───────────────────────────────

class _FakeAsanaRich:
    def __init__(self, fail=False, remote_modified="2026-08-10T13:00:00Z"):
        self.puts = []
        self.fail = fail
        self.remote_modified = remote_modified
        self.api_key = "k"

    def get_task(self, gid, **_kw):
        return {"gid": gid, "modified_at": self.remote_modified}

    def update_task(self, gid, **fields):
        self.puts.append((gid, fields))
        if self.fail:
            raise RuntimeError("HTTP 400")
        return {"gid": gid, "modified_at": "2026-08-10T14:00:00Z"}


def test_description_lane_remote_first_then_local(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    client = _FakeAsanaRich()
    res = awb.update_description_in_asana(conn, tid, "**new** body",
                                          client=client)
    assert res == {"ok": True, "synced": True}
    gid, fields = client.puts[0]
    assert gid == "900100"
    assert fields["html_notes"].startswith("<body>")
    assert "<strong>new</strong>" in fields["html_notes"]
    assert (et.get_task(conn, tid) or {}).get("description") == "**new** body"


def test_description_lane_put_failure_changes_nothing_locally(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    before = (et.get_task(conn, tid) or {}).get("description")
    res = awb.update_description_in_asana(conn, tid, "won't land",
                                          client=_FakeAsanaRich(fail=True))
    assert res["ok"] is False
    assert (et.get_task(conn, tid) or {}).get("description") == before


def test_description_lane_cas_conflict_blocks_then_refresh_unblocks(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    et.update_task(conn, tid, remote_modified_at="2026-08-01T00:00:00Z")
    client = _FakeAsanaRich(remote_modified="2026-08-09T00:00:00Z")
    res = awb.update_description_in_asana(conn, tid, "clobber", client=client)
    assert res.get("conflict") is True
    assert client.puts == []
    # The documented remedy: the operator refreshes (the panel's ↻ restamps
    # the anchor to the observed remote), reviews, and the retry proceeds.
    et.update_task(conn, tid, remote_modified_at="2026-08-09T00:00:00Z")
    res2 = awb.update_description_in_asana(conn, tid, "seen and retried",
                                           client=client)
    assert res2 == {"ok": True, "synced": True}


def test_panel_refresh_restamps_the_cas_anchor():
    """Promoted subtask rows drift (subtask-local changes emit no board
    event), so the panel's explicit refresh MUST restamp — without it,
    every CAS write on a drifted promoted row conflicts forever."""
    import inspect
    from src.ui.pages.enablement.page import EnablementPage
    src = inspect.getsource(EnablementPage._run_task_refresh)
    assert 'remote_modified_at=payload["modified_at"]' in src


# ── the subtask completion LANE (asana_writeback) ────────────────────────

class _FakeAsana:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.api_key = "k"

    def update_task(self, gid, **fields):
        self.calls.append((gid, fields))
        if self.fail:
            raise RuntimeError("HTTP 500")
        return {"gid": gid, "modified_at": "2026-08-10T13:00:00Z"}


def _seed_subtask(conn):
    from src.data import enablement_tasks as et
    tid = et.create_task(conn, source="asana", kind="request",
                         title="Parent", source_ref="900100")
    sid = et.add_subtask(conn, tid, "Draft card")
    conn.execute(
        "UPDATE enablement_subtasks SET asana_subtask_gid = 'sub900' "
        "WHERE subtask_id = ?", (sid,))
    conn.commit()
    return tid, sid


def test_subtask_lane_flips_local_and_remote(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    client = _FakeAsana()
    res = awb.set_subtask_completed_in_asana(conn, tid, "sub900", True,
                                            client=client)
    assert res == {"ok": True, "synced": True, "done": True}
    assert client.calls == [("sub900", {"completed": True})]
    subs = et.list_subtasks(conn, tid)
    assert subs[0]["done"] == 1


def test_subtask_lane_reverts_local_flip_on_api_failure(empty_db):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    res = awb.set_subtask_completed_in_asana(conn, tid, "sub900", True,
                                            client=_FakeAsana(fail=True))
    assert res["ok"] is False and res["reverted"] is True
    assert et.list_subtasks(conn, tid)[0]["done"] == 0


def test_subtask_lane_unknown_gid_and_missing_gid_refuse(empty_db):
    from src.data import asana_writeback as awb
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    assert awb.set_subtask_completed_in_asana(
        conn, tid, "nope", True, client=_FakeAsana())["error"] == "subtask_not_found"
    assert awb.set_subtask_completed_in_asana(
        conn, tid, "", True, client=_FakeAsana())["error"] == "subtask_gid_required"


def test_subtask_lane_degrades_local_only_without_client(empty_db, monkeypatch):
    from src.data import asana_writeback as awb
    from src.data import enablement_tasks as et
    conn = empty_db.conn
    tid, _sid = _seed_subtask(conn)
    monkeypatch.setattr(awb, "_client", lambda c: None)
    res = awb.set_subtask_completed_in_asana(conn, tid, "sub900", True)
    assert res["ok"] is True and res["synced"] is False
    assert et.list_subtasks(conn, tid)[0]["done"] == 1


# ── url + attachment gates ───────────────────────────────────────────────

def test_open_url_only_registry_members():
    ctrl, calls, _seen = _shown()
    ctrl.js_open_url("https://evil.example/steal")
    ctrl.js_open_url("")
    ctrl.js_open_url(None)
    assert calls["urls"] == []
    ctrl.js_open_url("https://app.asana.com/0/1/900100")   # the permalink
    ctrl.js_open_url("https://doc.example/z")              # a comment link
    assert calls["urls"] == ["https://app.asana.com/0/1/900100",
                             "https://doc.example/z"]


def test_open_url_with_broken_opener_never_raises():
    def boom(_u):
        raise RuntimeError("no browser")
    ctrl, _c, _s = _shown(open_url_fn=boom)
    ctrl.js_open_url("https://app.asana.com/0/1/900100")


class _FakeClient:
    def __init__(self, url="https://app.example/view", error=None):
        self.asked = []
        self._url = url
        self._error = error

    def get_attachment(self, gid):
        self.asked.append(gid)
        if self._error:
            raise RuntimeError(self._error)
        return {"gid": gid, "view_url": self._url, "download_url": ""}


def _drain(ctrl, app):
    for t in ctrl._att_threads:
        t.join(timeout=5)
    for _ in range(3):
        app.processEvents()


def test_open_attachment_resolves_and_opens_http_only(monkeypatch, _qt_app):
    opened = []
    import src.services.task_web as tw
    monkeypatch.setattr(tw.webbrowser, "open", lambda u: opened.append(u))
    client = _FakeClient()
    ctrl, _c, seen = _shown(client_factory=lambda: client)
    ctrl.js_open_attachment("a1")
    _drain(ctrl, _qt_app)
    assert client.asked == ["a1"]
    assert opened == ["https://app.example/view"]
    assert seen["resolved"][-1] == {"action": "open_attachment",
                                    "task_id": "a1", "ok": True, "error": ""}


def test_open_attachment_refuses_non_web_urls(monkeypatch, _qt_app):
    opened = []
    import src.services.task_web as tw
    monkeypatch.setattr(tw.webbrowser, "open", lambda u: opened.append(u))
    ctrl, _c, seen = _shown(
        client_factory=lambda: _FakeClient(url="file:///C:/evil.exe"))
    ctrl.js_open_attachment("a1")
    _drain(ctrl, _qt_app)
    assert opened == []
    assert seen["resolved"][-1]["error"] == "non_web_url"
    assert any("Refused" in s for s in seen["status"])


def test_open_attachment_unknown_gid_is_silent():
    client = _FakeClient()
    ctrl, _c, seen = _shown(client_factory=lambda: client)
    ctrl.js_open_attachment("forged")
    ctrl.js_open_attachment("")
    assert client.asked == []
    assert all(r["ok"] is False for r in seen["resolved"])


def test_open_attachment_resolve_error_surfaces_status(monkeypatch, _qt_app):
    ctrl, _c, seen = _shown(
        client_factory=lambda: _FakeClient(error="HTTP 401"))
    ctrl.js_open_attachment("a1")
    _drain(ctrl, _qt_app)
    assert seen["resolved"][-1]["error"] == "resolve_failed"
    assert any("HTTP 401" in s for s in seen["status"])


def test_action_outcomes_surface_in_panel_status():
    """The phantom-save lesson: a CAS conflict or API failure whose only
    trace is the app status line reads as a silently swallowed save. The
    host relays writeback OUTCOMES into the panel's own status signal."""
    ctrl, _c, seen = _shown()
    ctrl.notify_action_outcome({"ok": False, "conflict": True})
    assert seen["status"][-1] == "Task changed in Asana — refreshed. Please retry."
    ctrl.notify_action_outcome({"ok": False, "error": "HTTP 400"})
    assert seen["status"][-1] == "Asana action failed: HTTP 400"
    ctrl.notify_action_outcome({"ok": True, "synced": True})
    assert seen["status"][-1] == "Synced to Asana."
    ctrl.notify_action_outcome({"ok": True, "synced": False, "note": "not linked"})
    assert seen["status"][-1] == "Saved locally — not linked"
    ctrl.notify_action_outcome({"ok": True, "refreshed": True})
    assert seen["status"][-1] == "Refreshed from Asana."
    ctrl.notify_action_outcome("garbage")     # never raises
    ctrl.notify_action_outcome(None)


# ── refreshTask ──────────────────────────────────────────────────────────

def test_refresh_task_relays_current_id_only():
    ctrl, calls, seen = _shown()
    ctrl.js_refresh_task("forged")
    assert calls["refresh"] == []
    ctrl.js_refresh_task("t1")
    assert calls["refresh"] == [("t1", "900100")]
    assert seen["resolved"][-1]["ok"] is True


# ── bridge: pure relay ───────────────────────────────────────────────────

def test_bridge_relays_signals_and_slots():
    ctrl, calls, _seen = _controller()
    bridge = TaskBridge(
        data_signal=ctrl.task_data, status_signal=ctrl.status_text,
        resolved_signal=ctrl.action_resolved,
        refresh_fn=ctrl.js_refresh, refresh_task_fn=ctrl.js_refresh_task,
        complete_fn=ctrl.js_toggle_complete, due_fn=ctrl.js_set_due,
        comment_fn=ctrl.js_post_comment, subtask_fn=ctrl.js_add_subtask,
        attachment_fn=ctrl.js_open_attachment, url_fn=ctrl.js_open_url)
    got = {"data": [], "resolved": []}
    bridge.taskData.connect(lambda j: got["data"].append(json.loads(j)))
    bridge.actionResolved.connect(lambda j: got["resolved"].append(json.loads(j)))
    ctrl.show_task(dict(TASK))
    assert len(got["data"]) == 1
    bridge.toggleComplete("t1", True)
    assert calls["write"] == [("set_completed_in_asana", "t1", True)]
    assert got["resolved"][-1]["ok"] is True
    bridge.refresh()
    assert len(got["data"]) == 2
    assert bridge.ping() == "pong"


def test_bridge_with_nothing_injected_is_inert():
    bridge = TaskBridge()
    bridge.refresh()
    bridge.refreshTask("t1")
    bridge.toggleComplete("t1", True)
    bridge.setDue("t1", "2026-09-15")
    bridge.postComment("t1", "x")
    bridge.addSubtask("t1", "x")
    bridge.openAttachment("a1")
    bridge.openUrl("https://x.example/")
    assert bridge.ping() == "pong"


def test_bridge_swallows_raising_callables():
    def boom(*_a):
        raise RuntimeError("nope")
    bridge = TaskBridge(refresh_fn=boom, refresh_task_fn=boom,
                        complete_fn=boom, due_fn=boom, comment_fn=boom,
                        subtask_fn=boom, attachment_fn=boom, url_fn=boom)
    bridge.refresh()
    bridge.refreshTask("x")
    bridge.toggleComplete("x", False)
    bridge.setDue("x", "2026-01-01")
    bridge.postComment("x", "y")
    bridge.addSubtask("x", "y")
    bridge.openAttachment("x")
    bridge.openUrl("x")   # none of these may raise


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
