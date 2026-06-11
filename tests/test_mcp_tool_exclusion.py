"""Mode-gated MCP tool exclusion (ALMA_MCP_EXCLUDE_TOOLS).

In enablement mode the host strips `semantic_search` from the chat MCP
server so the torch/Qwen3 embedding stack can never load. Covers the
server-side schema/dispatch filter, the handler belt-and-braces guard,
and the env injection in EnablementPage._build_mcp_config.
"""

import importlib
import sys
from types import SimpleNamespace

import pytest


def _load_server(monkeypatch, exclude):
    """Import a fresh chat_mcp_server with the exclusion env applied."""
    if exclude is None:
        monkeypatch.delenv("ALMA_MCP_EXCLUDE_TOOLS", raising=False)
    else:
        monkeypatch.setenv("ALMA_MCP_EXCLUDE_TOOLS", exclude)
    sys.modules.pop("src.mcp.chat_mcp_server", None)
    return importlib.import_module("src.mcp.chat_mcp_server")


@pytest.fixture(autouse=True)
def _fresh_server_module():
    """Never leave a filtered module cached for other test files."""
    yield
    sys.modules.pop("src.mcp.chat_mcp_server", None)


class TestServerFilter:
    def test_exclusion_removes_schema_and_dispatch(self, monkeypatch):
        srv = _load_server(monkeypatch, "semantic_search")
        names = {t["name"] for t in srv.TOOL_SCHEMAS}
        assert "semantic_search" not in names
        assert "semantic_search" not in srv._MCP_ALLOWED_TOOLS
        result = srv._execute_tool("semantic_search", {"query": "x"})
        assert "error" in result

    def test_no_exclusion_keeps_tool(self, monkeypatch):
        srv = _load_server(monkeypatch, None)
        names = {t["name"] for t in srv.TOOL_SCHEMAS}
        assert "semantic_search" in names
        assert "revise_draft" in names

    def test_csv_exclusions(self, monkeypatch):
        srv = _load_server(monkeypatch, "semantic_search, query_stats")
        names = {t["name"] for t in srv.TOOL_SCHEMAS}
        assert "semantic_search" not in names
        assert "query_stats" not in names
        assert "list_tickets" in names


class TestHandlerGuard:
    def test_handler_short_circuits(self, monkeypatch):
        monkeypatch.setenv("ALMA_MCP_EXCLUDE_TOOLS", "semantic_search")
        from src.data.chat_tools.semantic_tools import handle_semantic_search
        result = handle_semantic_search(None, {"query": "x"}, {})
        assert "disabled" in result.get("error", "")

    def test_handler_normal_without_env(self, monkeypatch):
        monkeypatch.delenv("ALMA_MCP_EXCLUDE_TOOLS", raising=False)
        from src.data.chat_tools.semantic_tools import handle_semantic_search
        # No query → the ordinary validation error, not the mode error
        result = handle_semantic_search(None, {}, {})
        assert result.get("error") == "query is required"


class TestMcpConfigEnv:
    def test_enablement_mode_injects_exclusion(self):
        from src.ui import app_modes
        from src.ui.pages.enablement.page import EnablementPage
        stub = SimpleNamespace(_engine_db_path=lambda: r"C:\tmp\alma_test.db")
        try:
            app_modes.set_current_mode(app_modes.MODE_ENABLEMENT)
            cfg = EnablementPage._build_mcp_config(stub)
            env = {e["name"]: e["value"] for e in cfg[0]["env"]}
            assert env.get("ALMA_MCP_EXCLUDE_TOOLS") == "semantic_search"

            app_modes.set_current_mode(app_modes.MODE_PRODUCT)
            cfg = EnablementPage._build_mcp_config(stub)
            env = {e["name"]: e["value"] for e in cfg[0]["env"]}
            assert "ALMA_MCP_EXCLUDE_TOOLS" not in env
        finally:
            app_modes.set_current_mode(app_modes.MODE_PRODUCT)
