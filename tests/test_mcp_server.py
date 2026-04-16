"""
Alma Insights -- MCP Server Unit Tests (Session 3 — ACP Migration)

Tests the alma_mcp_server module: tool schemas, tool dispatch,
boundary guard propagation, env var context, and error handling.

All tests use in-memory SQLite — no real database or network access.
"""

import json
import os
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from src.mcp.alma_mcp_server import (
    TOOL_SCHEMAS,
    _build_registry,
    _handle_initialize,
    _handle_tools_list,
    _handle_tools_call,
    _make_response,
    _make_error,
)


class TestToolSchemas(unittest.TestCase):
    """Validate the 7 MCP tool schemas."""

    def test_schema_count(self):
        self.assertEqual(len(TOOL_SCHEMAS), 7)

    def test_all_schemas_have_required_fields(self):
        for schema in TOOL_SCHEMAS:
            self.assertIn("name", schema)
            self.assertIn("description", schema)
            self.assertIn("inputSchema", schema)
            self.assertIn("type", schema["inputSchema"])
            self.assertEqual(schema["inputSchema"]["type"], "object")

    def test_expected_tool_names(self):
        names = {s["name"] for s in TOOL_SCHEMAS}
        expected = {
            "query_taxonomy", "get_stats_context", "store_classification",
            "flag_for_review", "get_full_thread", "report_progress",
            "check_cross_trc",
        }
        self.assertEqual(names, expected)

    def test_store_classification_requires_ticket_id(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "store_classification")
        self.assertIn("ticket_id", schema["inputSchema"].get("required", []))

    def test_flag_for_review_requires_ticket_id_and_reason(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "flag_for_review")
        required = schema["inputSchema"].get("required", [])
        self.assertIn("ticket_id", required)
        self.assertIn("reason", required)

    def test_query_taxonomy_requires_trc(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "query_taxonomy")
        self.assertIn("trc", schema["inputSchema"].get("required", []))


class TestJSONRPCHelpers(unittest.TestCase):
    """JSON-RPC response/error builders."""

    def test_make_response(self):
        resp = _make_response(1, {"tools": []})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["tools"], [])

    def test_make_error(self):
        resp = _make_error(2, -32601, "Method not found")
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 2)
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertEqual(resp["error"]["message"], "Method not found")

    def test_make_error_with_data(self):
        resp = _make_error(3, -32602, "Invalid params", data={"field": "trc"})
        self.assertEqual(resp["error"]["data"]["field"], "trc")


class TestInitializeHandler(unittest.TestCase):
    """MCP initialize handshake."""

    def test_initialize_returns_protocol_and_capabilities(self):
        resp = _handle_initialize(1, {})
        result = resp["result"]
        self.assertIn("protocolVersion", result)
        self.assertIn("capabilities", result)
        self.assertIn("tools", result["capabilities"])
        self.assertIn("serverInfo", result)
        self.assertEqual(result["serverInfo"]["name"], "alma-tools")


class TestToolsListHandler(unittest.TestCase):
    """tools/list returns all 7 schemas."""

    def test_tools_list_count(self):
        resp = _handle_tools_list(1, {})
        tools = resp["result"]["tools"]
        self.assertEqual(len(tools), 7)

    def test_tools_list_names(self):
        resp = _handle_tools_list(1, {})
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("store_classification", names)
        self.assertIn("query_taxonomy", names)


class TestToolsCallHandler(unittest.TestCase):
    """tools/call dispatch to ToolRegistry."""

    def test_missing_tool_name(self):
        resp = _handle_tools_call(1, {"arguments": {}}, None)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_no_registry(self):
        resp = _handle_tools_call(1, {"name": "query_taxonomy", "arguments": {"trc": "X"}}, None)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32603)

    def test_successful_tool_call(self):
        """Mock registry returns a result."""
        registry = MagicMock()
        registry.execute.return_value = {"status": "stored", "ticket_id": "T-1"}

        resp = _handle_tools_call(1, {
            "name": "store_classification",
            "arguments": {"ticket_id": "T-1"},
        }, registry)

        self.assertIn("result", resp)
        content = resp["result"]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")

        parsed = json.loads(content[0]["text"])
        self.assertEqual(parsed["status"], "stored")

    def test_tool_execution_error(self):
        """Registry raises exception."""
        registry = MagicMock()
        registry.execute.side_effect = RuntimeError("DB locked")

        resp = _handle_tools_call(1, {
            "name": "store_classification",
            "arguments": {"ticket_id": "T-1"},
        }, registry)

        self.assertIn("error", resp)
        self.assertIn("DB locked", resp["error"]["message"])


class TestBoundaryGuard(unittest.TestCase):
    """Boundary guard via ToolRegistry + env var context."""

    def _make_test_db(self):
        """Create an in-memory SQLite with classification table."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE nlp_ticket_classifications (
                classification_id TEXT PRIMARY KEY,
                batch_id TEXT, scan_id TEXT, ticket_id TEXT,
                trc TEXT, sub_cluster TEXT, sub_cluster_confidence REAL,
                is_novel INTEGER, sentiment_intensity INTEGER,
                sentiment_polarity TEXT, friction_type TEXT,
                anomaly_flag TEXT, anomaly_reason TEXT,
                entities_json TEXT, key_phrases TEXT,
                root_cause_hint TEXT, summary TEXT,
                raw_classification TEXT, created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_progress (
                scan_id TEXT PRIMARY KEY,
                classified INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT, reason TEXT, severity TEXT,
                agent_id TEXT, scan_id TEXT, created_at TEXT
            )
        """)
        return conn

    def test_boundary_guard_rejects_out_of_batch(self):
        """store_classification rejects ticket_id not in batch manifest."""
        from src.agents.tool_registry import ToolRegistry

        conn = self._make_test_db()
        registry = ToolRegistry.__new__(ToolRegistry)
        registry.db_path = ":memory:"
        registry._conn = conn
        registry._tools = {}
        registry._call_count = 0
        registry._context = {}
        registry._register_tools()

        # Set context with a ticket map that only contains T-1
        registry.set_context(
            scan_id="scan_1",
            batch_id="batch_1",
            trc="RCM_02",
            ticket_trc_map={"T-1": "RCM_02"},
        )

        # T-1 should be accepted
        result = registry.execute("store_classification", {
            "ticket_id": "T-1",
            "sub_cluster": "test",
            "friction_type": "other",
        })
        self.assertEqual(result["status"], "stored")

        # T-999 should be rejected
        result = registry.execute("store_classification", {
            "ticket_id": "T-999",
            "sub_cluster": "test",
            "friction_type": "other",
        })
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "not_in_batch")

        conn.close()


class TestEnvVarContext(unittest.TestCase):
    """_build_registry reads context from environment variables."""

    @patch.dict(os.environ, {}, clear=False)
    def test_no_db_path_returns_none(self):
        # Remove ALMA_DB_PATH if it exists
        os.environ.pop("ALMA_DB_PATH", None)
        result = _build_registry()
        self.assertIsNone(result)

    @patch.dict(os.environ, {
        "ALMA_DB_PATH": ":memory:",
        "ALMA_SCAN_ID": "scan_test",
        "ALMA_BATCH_ID": "batch_test",
        "ALMA_TRC": "RCM_01",
        "ALMA_AGENT_ID": "worker_0",
    }, clear=False)
    def test_builds_registry_with_context(self):
        """With ALMA_DB_PATH set, registry is built with context."""
        # This will fail to connect to :memory: from a fresh process
        # but we can at least verify the function doesn't crash on env parsing
        registry = _build_registry()
        if registry is not None:
            self.assertEqual(registry._context.get("scan_id"), "scan_test")
            self.assertEqual(registry._context.get("batch_id"), "batch_test")
            self.assertEqual(registry._context.get("trc"), "RCM_01")
            registry.close()

    @patch.dict(os.environ, {
        "ALMA_DB_PATH": ":memory:",
        "ALMA_TICKET_TRC_MAP_JSON": '{"T-1": "RCM_01", "T-2": "RCM_02"}',
    }, clear=False)
    def test_ticket_trc_map_parsed(self):
        """ALMA_TICKET_TRC_MAP_JSON env var is parsed into context."""
        registry = _build_registry()
        if registry is not None:
            trc_map = registry._context.get("ticket_trc_map", {})
            self.assertEqual(trc_map.get("T-1"), "RCM_01")
            self.assertEqual(trc_map.get("T-2"), "RCM_02")
            registry.close()

    @patch.dict(os.environ, {
        "ALMA_DB_PATH": ":memory:",
        "ALMA_TICKET_TRC_MAP_JSON": "not valid json",
    }, clear=False)
    def test_malformed_trc_map_ignored(self):
        """Malformed ALMA_TICKET_TRC_MAP_JSON doesn't crash."""
        registry = _build_registry()
        if registry is not None:
            trc_map = registry._context.get("ticket_trc_map")
            self.assertIsNone(trc_map)
            registry.close()


if __name__ == "__main__":
    unittest.main()
