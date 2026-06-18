"""Phase 0 — Renn's tool channel on the Claude/Bedrock route.

The UAT failure: with enablement.provider='claude', Renn had ZERO tools because
the page built a per-message Claude client and never wired the chat-tool MCP
config onto it (the Gemini warm-bridge path does, via set_mcp_config). Claude
refuses text-injected tool results, so without native MCP it has no tools at all.

These tests pin the wiring: the Claude route must receive
set_mcp_config(chat-tools) so `claude -p` runs the tool loop natively, mirroring
Gemini; the client is kept warm; switching providers tears the other down; and a
build failure degrades gracefully instead of crashing the chat.
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


class _FakeClaude:
    def __init__(self):
        self.model = "sonnet"
        self.mcp = None
        self.shut = False

    def set_mcp_config(self, cfg):
        self.mcp = cfg

    def shutdown(self):
        self.shut = True


class _FakeBridge:
    def __init__(self, *a, **k):
        self.mcp = None
        self.shut = False

    def set_mcp_config(self, cfg):
        self.mcp = cfg

    def shutdown(self):
        self.shut = True


def _page(db, demo=False):
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(db, demo=demo)
    page._engine = MagicMock()      # isolate the wiring from a real ACP boot
    return page


def _route(monkeypatch, provider):
    monkeypatch.setattr(
        "src.gemini.client_factory.resolve_provider_for_task",
        lambda task: provider,
    )


def test_claude_provider_wires_mcp_tools(qapp, empty_db, monkeypatch):
    _route(monkeypatch, "claude")
    fake = _FakeClaude()
    monkeypatch.setattr(
        "src.gemini.client_factory.build_client_for_task", lambda task: fake)

    page = _page(empty_db)
    page._prepare_provider()

    # The MCP chat-tool server was wired onto the Claude client...
    assert fake.mcp is not None
    assert any(s.get("name") == "alma-chat-tools" for s in fake.mcp)
    # ...and the engine uses it as the warm client (not None → no tools).
    page._engine.set_client.assert_called_with(fake)
    assert page._claude_client is fake


def test_claude_client_is_reused_warm(qapp, empty_db, monkeypatch):
    _route(monkeypatch, "claude")
    built = []

    def build(task):
        c = _FakeClaude()
        built.append(c)
        return c

    monkeypatch.setattr("src.gemini.client_factory.build_client_for_task", build)
    page = _page(empty_db)
    page._prepare_provider()
    page._prepare_provider()        # second send must reuse, not rebuild
    assert len(built) == 1
    assert page._claude_client is built[0]


def test_switch_to_gemini_tears_down_claude(qapp, empty_db, monkeypatch):
    _route(monkeypatch, "claude")
    fake = _FakeClaude()
    monkeypatch.setattr(
        "src.gemini.client_factory.build_client_for_task", lambda t: fake)
    monkeypatch.setattr(
        "src.agents.report_bridge_client.ReportBridgeClient",
        lambda *a, **k: _FakeBridge())

    page = _page(empty_db)
    page._prepare_provider()        # claude
    assert page._claude_client is fake

    _route(monkeypatch, "gemini")
    page._prepare_provider()        # switch providers
    assert fake.shut is True        # claude client shut down
    assert page._claude_client is None
    assert page._warm_bridge is not None   # gemini bridge now warm


def test_claude_wiring_failure_falls_back(qapp, empty_db, monkeypatch):
    _route(monkeypatch, "claude")

    def boom(task):
        raise RuntimeError("no creds")

    monkeypatch.setattr("src.gemini.client_factory.build_client_for_task", boom)
    page = _page(empty_db)
    page._prepare_provider()        # must not raise
    assert page._claude_client is None
    page._engine.set_client.assert_called_with(None)
