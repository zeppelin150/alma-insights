"""P7 Renn tools — registry dispatch + Claude-path parity for
import_guru_card / get_guru_analytics / create_task_from_comment."""

import pytest

from src.data.chat_tools import registry


@pytest.fixture()
def seeded_analytics(empty_db):
    from src.data.enablement_sim import seed_demo_analytics
    seed_demo_analytics(empty_db.conn)
    return empty_db


class TestRegistryDispatch:
    def test_tools_registered_on_both_surfaces(self):
        tools = registry.get_tool_registry()
        for name in ("import_guru_card", "get_guru_analytics",
                     "create_task_from_comment"):
            assert name in tools, f"{name} missing from the chat registry"
        import importlib
        import sys
        sys.modules.pop("src.mcp.chat_mcp_server", None)
        srv = importlib.import_module("src.mcp.chat_mcp_server")
        names = {t["name"] for t in srv.TOOL_SCHEMAS}
        for name in ("import_guru_card", "get_guru_analytics",
                     "create_task_from_comment"):
            assert name in names
        from src.llm.claude_tools import _DISPATCH
        for name in ("import_guru_card", "get_guru_analytics",
                     "create_task_from_comment"):
            assert name in _DISPATCH

    def test_get_guru_analytics_metrics(self, seeded_analytics):
        from src.data.chat_tools.enablement_tools import _get_guru_analytics_impl
        conn = seeded_analytics.conn
        top = _get_guru_analytics_impl(conn, "top_cards", 30)
        assert top["ok"] and top["rows"]
        ver = _get_guru_analytics_impl(conn, "verification")
        assert ver["ok"] and ver["kpis"]["queue_total"] == 3
        com = _get_guru_analytics_impl(conn, "comments")
        assert com["ok"] and len(com["rows"]) == 4
        due = _get_guru_analytics_impl(conn, "due_cards")
        assert due["ok"] and due["rows"]
        bad = _get_guru_analytics_impl(conn, "bogus")
        assert not bad["ok"] and "valid" in bad

    def test_create_task_from_comment_impl(self, seeded_analytics):
        from src.data.chat_tools.enablement_tools import (
            _create_task_from_comment_impl,
        )
        conn = seeded_analytics.conn
        res = _create_task_from_comment_impl(conn, "demo-comment-1")
        assert res["ok"]
        again = _create_task_from_comment_impl(conn, "demo-comment-1")
        assert again["task_id"] == res["task_id"]

    def test_import_guru_card_uses_credentials(self, empty_db, monkeypatch):
        from src.data.guru_client import GuruClient
        monkeypatch.setattr(GuruClient, "load_credentials",
                            staticmethod(lambda: ("e@x.com", "tok")))
        monkeypatch.setattr(
            GuruClient, "get_card",
            lambda self, cid: {"id": cid, "title": "Stub Card",
                               "content": "<h2>Body</h2>"},
        )
        from src.data.chat_tools.enablement_tools import _import_guru_card_impl
        res = _import_guru_card_impl(empty_db.conn, "card-99")
        assert res["ok"] and res["card_id"] == "card-99"
        from src.data import enablement_store as store
        draft = store.get_draft(empty_db.conn, res["draft_id"])
        assert draft["card_id"] == "card-99"

    def test_import_guru_card_without_credentials(self, empty_db, monkeypatch):
        from src.data.guru_client import GuruClient
        monkeypatch.setattr(GuruClient, "load_credentials",
                            staticmethod(lambda: ("", "")))
        from src.data.chat_tools.enablement_tools import _import_guru_card_impl
        res = _import_guru_card_impl(empty_db.conn, "card-99")
        assert not res["ok"] and res["error"] == "guru_not_connected"
