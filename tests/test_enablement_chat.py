"""Phase 3 — live Renn chat wiring (ChatEngine).

These exercise the wiring without booting the real ACP bridge or a model: the
engine is built at construction (lightweight), the context provider binds the
active draft, dispatch reaches the engine, and an engine response refreshes the
views. The actual model-in-the-loop is a manual live smoke (Gemini CLI).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from src.data import enablement_sim as SIM

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _page(db, demo=False):
    from src.ui.pages.enablement import EnablementPage
    return EnablementPage(db, demo=demo)


def test_engine_built_for_enablement_chat(qapp, empty_db):
    page = _page(empty_db)
    assert page._engine is not None
    assert page._engine._task_type == "enablement_chat"


def test_chat_context_includes_active_draft(qapp, empty_db):
    SIM.run_simulation(empty_db.conn, publish=False)
    page = _page(empty_db)
    did = next(iter(page._drafts))
    page.workbench.set_active_draft(did)
    ctx = page._chat_context("anything", [])
    assert "active draft id" in ctx.lower()
    assert str(did) in ctx
    assert "search_local_documents" in ctx


def test_dispatch_sends_to_engine(qapp, empty_db):
    page = _page(empty_db)
    page._engine = MagicMock(is_busy=False)
    # Claude path → wire a native-MCP Claude client so Renn actually HAS tools.
    # (UAT fix: this used to be set_client(None) = zero tools on Bedrock.)
    # Building the client is subprocess-free; the CLI boots lazily on generate().
    with patch("src.gemini.client_factory.resolve_provider_for_task", return_value="claude"):
        page._on_chat("hello renn")
    page._engine.send.assert_called_once_with("hello renn")
    client = page._engine.set_client.call_args[0][0]
    assert client is not None                       # not the old no-tools None
    assert page._claude_client is client
    assert any(s.get("name") == "alma-chat-tools"
               for s in (getattr(client, "_mcp_config", None) or []))


def test_engine_response_refreshes_views(qapp, empty_db):
    SIM.run_simulation(empty_db.conn, publish=False)
    page = _page(empty_db)
    page._load_live = MagicMock()
    page._on_engine_response("Updated the draft and re-rendered it.")
    page._load_live.assert_called_once()


def test_busy_engine_does_not_resend(qapp, empty_db):
    page = _page(empty_db)
    page._engine = MagicMock(is_busy=True)
    page._on_chat("are you there")
    page._engine.send.assert_not_called()
