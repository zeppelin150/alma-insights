"""Tests for the enablement chat tools (Claude/Bedrock path).

search_local_documents — chat can find stored docs + drafts ("look up an old document").
query_business_drive    — chat can query the business Drive (local mirror until live scope lands).
"""

import json

import pytest

from src.data import enablement_store as S
from src.llm.claude_tools import TOOL_DEFINITIONS, execute_tool


@pytest.fixture(autouse=True)
def _isolate_pat_store(monkeypatch):
    """Keep these unit tests off the real OS keyring. asana_discover (and other
    tools) fall back to pat_store for credentials; with a real asana_api_key
    saved, discover() would hit LIVE Asana instead of returning MOCK_DISCOVERY,
    making the asana-tool assertions non-deterministic. Force the mock path."""
    from src.data import pat_store
    monkeypatch.setattr(pat_store, "load_setting", lambda key, default="": default)


def test_tools_registered():
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "search_local_documents" in names
    assert "query_business_drive" in names


def test_search_local_documents_tool(empty_db):
    S.save_document(empty_db.conn, source="drive", doc_id="d1",
                    name="SSO Setup.gdoc", full_text="Provider SSO self-serve, launches June 24.")
    S.save_card_draft(empty_db.conn, title="SSO Guide", content="How to set up SSO", source_ref="d1")
    out = json.loads(execute_tool("search_local_documents", {"query": "SSO"}, empty_db))
    assert out["doc_count"] >= 1
    assert any(d["name"] == "SSO Setup.gdoc" for d in out["documents"])
    assert out["draft_count"] >= 1


def test_query_business_drive_tool_local_mirror(empty_db):
    S.save_document(empty_db.conn, source="drive", doc_id="d2",
                    name="Returns Policy.gdoc", full_text="Returns window is now 30 days.")
    # a non-drive doc must not leak into a Drive query
    S.save_document(empty_db.conn, source="upload", doc_id="u1",
                    name="Returns notes.txt", full_text="Returns brainstorm.")
    out = json.loads(execute_tool("query_business_drive", {"query": "Returns"}, empty_db))
    assert out["mode"] in ("local_index", "live")
    assert out["count"] == 1
    assert out["results"][0]["name"] == "Returns Policy.gdoc"


def test_unknown_tool_returns_error(empty_db):
    out = json.loads(execute_tool("does_not_exist", {}, empty_db))
    assert "error" in out


# ── Gemini/MCP path (registry.dispatch_tool, what chat_mcp_server uses) ──

def test_gemini_registry_has_enablement_tools():
    from src.data.chat_tools.registry import get_tool_registry
    reg = get_tool_registry()
    assert "search_local_documents" in reg
    assert "query_business_drive" in reg


def test_gemini_registry_search_local_documents(empty_db):
    from src.data.chat_tools.registry import dispatch_tool
    S.save_document(empty_db.conn, source="drive", doc_id="g1",
                    name="SSO Setup.gdoc", full_text="Provider SSO self-serve.")
    out = json.loads(dispatch_tool("search_local_documents", {"query": "SSO"}, empty_db.conn))
    assert out["doc_count"] >= 1


def test_gemini_registry_query_business_drive(empty_db):
    from src.data.chat_tools.registry import dispatch_tool
    S.save_document(empty_db.conn, source="drive", doc_id="g2",
                    name="Returns Policy.gdoc", full_text="Returns window is 30 days.")
    out = json.loads(dispatch_tool("query_business_drive", {"query": "Returns"}, empty_db.conn))
    assert out["count"] >= 1
    assert out["results"][0]["name"] == "Returns Policy.gdoc"


# ── Renn's Asana setup (GID discovery + the single scoped write) ──

def test_asana_discover_and_scoped_write(empty_db):
    from src.data import asana_setup
    disc = asana_setup.discover()                              # mock (no key)
    assert disc["projects"][0]["name"] == "Enablement Requests"
    fields = disc["custom_fields"]["120420000111"]
    team = next(f for f in fields if f["name"] == "Assigned Team")
    enab = next(o for o in team["enum_options"] if o["name"] == "Enablement")
    res = asana_setup.set_asana_board_config(
        empty_db.conn, project_gid="120420000111", project_name="Enablement Requests",
        indicator_field_gid=team["gid"], indicator_field_name="Assigned Team",
        indicator_value_gid=enab["gid"], indicator_value_name="Enablement")
    assert res["ok"]
    cfg = asana_setup.get_asana_config(empty_db.conn)[0]["config"]
    assert cfg["indicators"][0]["field_gid"] == team["gid"]
    assert cfg["indicators"][0]["trigger_value_gids"] == [enab["gid"]]


def test_asana_tools_registered_and_write(empty_db):
    from src.llm.claude_tools import TOOL_DEFINITIONS, execute_tool
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "asana_discover" in names and "set_asana_board_config" in names
    disc = json.loads(execute_tool("asana_discover", {}, empty_db))
    assert disc["projects"][0]["gid"] == "120420000111"
    out = json.loads(execute_tool("set_asana_board_config", {
        "project_gid": "P1", "project_name": "Board", "indicator_field_gid": "F1",
        "indicator_field_name": "Team", "indicator_value_gid": "V1",
        "indicator_value_name": "Enablement"}, empty_db))
    assert out["ok"] and out["source_id"] == "asana:P1"


def test_asana_client_parses_discovery(monkeypatch):
    from src.data import asana_client as ac
    responses = {
        "/custom_field_settings": {"data": [   # check before "/projects" (substring of this path)
            {"custom_field": {"gid": "F1", "name": "Assigned Team", "resource_subtype": "enum",
                              "enum_options": [{"gid": "E1", "name": "Enablement", "enabled": True},
                                               {"gid": "E2", "name": "Support", "enabled": True}]}},
        ]},
        "/workspaces": {"data": [{"gid": "W1", "name": "Alma"}]},
        "/projects": {"data": [{"gid": "P1", "name": "Enablement Requests"}]},
    }

    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        for key, val in responses.items():
            if key in url:
                return _Resp(json.dumps(val).encode())
        return _Resp(b'{"data": []}')

    monkeypatch.setattr(ac.urllib.request, "urlopen", fake_urlopen)
    disc = ac.AsanaClient("fake-key").discover()
    assert disc["workspace"]["gid"] == "W1"
    assert disc["projects"][0]["name"] == "Enablement Requests"
    fields = disc["custom_fields"]["P1"]
    assert fields[0]["gid"] == "F1"
    assert fields[0]["enum_options"][0] == {"gid": "E1", "name": "Enablement"}


def test_gemini_registry_asana_tools(empty_db):
    from src.data.chat_tools.registry import dispatch_tool, get_tool_registry
    reg = get_tool_registry()
    assert "asana_discover" in reg and "set_asana_board_config" in reg
    disc = json.loads(dispatch_tool("asana_discover", {}, empty_db.conn))
    assert disc["projects"][0]["gid"] == "120420000111"
    out = json.loads(dispatch_tool("set_asana_board_config", {
        "project_gid": "P9", "project_name": "B", "indicator_field_gid": "F",
        "indicator_field_name": "T", "indicator_value_gid": "V",
        "indicator_value_name": "Enablement"}, empty_db.conn))
    assert out["ok"] and out["source_id"] == "asana:P9"
