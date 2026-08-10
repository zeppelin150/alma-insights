"""Phase 2 — Enablement Workbench write-tools on both chat paths.

The load-bearing concern is transaction safety: a tool handler must call at most
one atomic() store/task function, and the dispatch path must never leave the
connection mid-transaction (a dangling transaction would make the next atomic()
raise "cannot be nested"). Each test runs several write-tools on the SAME
connection and asserts conn.in_transaction is False after each one AND that the
writes persisted — together that proves no nesting/leak. Also checks Claude-path
parity via execute_tool.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.data import enablement_store as S
from src.data import enablement_tasks as T
from src.data.chat_tools.registry import dispatch_tool
from src.llm.claude_tools import execute_tool


class _DB:
    """Minimal stand-in for DatabaseManager: the Claude handlers read .conn."""
    def __init__(self, conn):
        self.conn = conn


def _call(conn, name, args):
    return json.loads(dispatch_tool(name, args, conn))


# ── Gemini/registry path: no nesting, no transaction leak ────────────

def test_create_add_toggle_no_transaction_leak(empty_db):
    conn = empty_db.conn

    out = _call(conn, "create_task", {"title": "Ship SSO card", "source": "drive", "kind": "card_review"})
    assert out["ok"] and out["task_id"]
    assert conn.in_transaction is False
    tid = out["task_id"]

    sub = _call(conn, "add_subtask", {"task_id": tid, "text": "draft the card"})
    assert sub["ok"]
    assert conn.in_transaction is False

    tog = _call(conn, "toggle_subtask", {"task_id": tid, "subtask_id": sub["subtask_id"], "done": True})
    assert tog["ok"]
    assert conn.in_transaction is False

    # Three sequential write-tools on one connection succeeded → no nesting; and
    # the data actually persisted.
    subs = T.list_subtasks(conn, tid)
    assert subs and subs[0]["done"] == 1


def test_draft_subtasks_bulk_and_list(empty_db):
    conn = empty_db.conn
    tid = _call(conn, "create_task", {"title": "Rollout plan"})["task_id"]
    out = _call(conn, "draft_subtasks", {"task_id": tid, "items": ["a", "b", "c"]})
    assert out["ok"] and out["added"] == 3
    assert conn.in_transaction is False
    assert len(T.list_subtasks(conn, tid)) == 3
    listed = _call(conn, "list_tasks", {})
    assert listed["count"] >= 1


def test_update_scratchpad_and_task(empty_db):
    conn = empty_db.conn
    tid = _call(conn, "create_task", {"title": "Notes task"})["task_id"]
    assert _call(conn, "update_scratchpad", {"task_id": tid, "text": "confirm regions"})["ok"]
    assert _call(conn, "update_task", {"task_id": tid, "status": "done", "priority": "high"})["ok"]
    assert conn.in_transaction is False
    task = T.get_task(conn, tid)
    assert task["status"] == "done" and task["priority"] == "high"
    assert "confirm regions" in task["scratchpad"]


def test_revise_draft_calls_llm_and_updates(empty_db):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="SSO", content="old body")
    fake = MagicMock()
    fake.generate.return_value = "TITLE: SSO (v2)\n---\nTighter intro.\nMore details."
    with patch("src.gemini.client_factory.build_client_for_task", return_value=fake):
        out = _call(conn, "revise_draft", {"draft_id": did, "instruction": "tighten the intro"})
    assert out["ok"]
    fake.generate.assert_called_once()
    d = S.get_draft(conn, did)
    assert d["title"] == "SSO (v2)"
    assert "Tighter intro" in d["content"]
    assert conn.in_transaction is False


def test_push_guru_draft_with_creds(empty_db, mock_guru_client):
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="SSO Guide", content="body")
    S.approve_draft(conn, did, approved_by="reviewer")  # M5: sign-off gate before push
    guru_mock = MagicMock()
    guru_mock.load_credentials.return_value = ("ops@alma.com", "tok")
    guru_mock.return_value = mock_guru_client
    with patch("src.data.guru_client.GuruClient", guru_mock), \
         patch("src.data.settings_manager.get_section",
               return_value={"demo_mode": False}):
        out = _call(conn, "push_guru_draft", {"draft_id": did, "collection_id": "coll-1"})
    assert out["ok"] and out["status"] == "pushed"
    mock_guru_client.create_card.assert_called_once()
    assert conn.in_transaction is False
    assert S.get_draft(conn, did)["status"] == "pushed"


def test_create_card_draft_from_scratch_then_push(empty_db, mock_guru_client):
    """The MCP from-scratch flow: create_card_draft -> push_guru_draft."""
    conn = empty_db.conn
    out = _call(conn, "create_card_draft",
                {"title": "VS Code authored card", "content": "# Body\nWritten by Claude."})
    assert out["ok"] and out["status"] == "pending"
    assert conn.in_transaction is False
    d = S.get_draft(conn, out["draft_id"])
    assert d["title"] == "VS Code authored card"

    S.approve_draft(conn, out["draft_id"], approved_by="reviewer")  # M5: sign-off gate
    guru_mock = MagicMock()
    guru_mock.load_credentials.return_value = ("ops@alma.com", "tok")
    guru_mock.return_value = mock_guru_client
    with patch("src.data.guru_client.GuruClient", guru_mock), \
         patch("src.data.settings_manager.get_section",
               return_value={"demo_mode": False}):
        pub = _call(conn, "push_guru_draft", {"draft_id": out["draft_id"], "collection_id": "coll-1"})
    assert pub["ok"] and pub["status"] == "pushed"
    mock_guru_client.create_card.assert_called_once()


def test_create_card_draft_requires_title_and_content(empty_db):
    assert _call(empty_db.conn, "create_card_draft", {"title": "x"})["ok"] is False
    assert _call(empty_db.conn, "create_card_draft", {"content": "y"})["ok"] is False


def test_push_guru_draft_defaults_collection_from_settings(empty_db, mock_guru_client):
    """A chat-initiated push without a collection_id falls back to the operator's
    configured publish target (Renn rarely knows the Guru collection id)."""
    conn = empty_db.conn
    did = S.save_card_draft(conn, title="Fallback card", content="body")
    S.approve_draft(conn, did, approved_by="reviewer")  # M5: sign-off gate before push
    guru_mock = MagicMock()
    guru_mock.load_credentials.return_value = ("ops@alma.com", "tok")
    guru_mock.return_value = mock_guru_client
    with patch("src.data.guru_client.GuruClient", guru_mock), \
         patch("src.data.settings_manager.get_section",
               return_value={"demo_mode": False,
                             "guru": {"publish_collection_id": "coll-cfg"}}):
        out = _call(conn, "push_guru_draft", {"draft_id": did})
    assert out["ok"]
    args, _ = mock_guru_client.create_card.call_args
    assert args[0] == "coll-cfg"


def test_run_monitor_now_degrades_gracefully(empty_db):
    """Until the monitors land (Phases 4/5) the tool reports them unavailable —
    never raises, never leaks a transaction."""
    conn = empty_db.conn
    out = _call(conn, "run_monitor_now", {})
    assert "errors" in out
    assert conn.in_transaction is False


# ── Claude path parity ───────────────────────────────────────────────

def test_claude_path_create_and_list(empty_db):
    db = _DB(empty_db.conn)
    out = json.loads(execute_tool("create_task", {"title": "Claude task", "source": "manual"}, db))
    assert out["ok"] and out["task_id"]
    listed = json.loads(execute_tool("list_tasks", {}, db))
    assert any(t["title"] == "Claude task" for t in listed["tasks"])
