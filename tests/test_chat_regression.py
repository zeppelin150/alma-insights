"""Phase 9 chat regression tests.

Light-weight end-to-end: exercise query_issues + audit_tag_correlation
through the MCP dispatch layer AND the Claude executor layer, to prove
they're wired up the whole way. Heavy-weight subprocess MCP tests belong
elsewhere; these are pure Python and fast.

Kernel reference: phase-6-9-session-kernel.md "Session N+2" — the
Thunderbird example.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from src.data.db_manager import DatabaseManager


@pytest.fixture
def seeded_db_path(tmp_path: Path):
    """Fresh DB + seeded dataset, returning the on-disk path so the MCP
    executor can reopen it via ALMA_DB_PATH."""
    db_path = tmp_path / "chat_regression.db"
    mgr = DatabaseManager(db_path)
    mgr.initialize()
    conn = mgr.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")

    # Minimal dataset: 2 concepts, 3 clusters, mixed payers
    rng = np.random.default_rng(42)
    def centroid(seed: int) -> bytes:
        v = np.random.default_rng(seed).standard_normal(1024).astype(np.float32)
        n = float(np.linalg.norm(v))
        return (v / n if n else v).astype(np.float32).tobytes()

    conn.execute(
        """INSERT INTO canonical_concepts (concept_id, concept_label)
           VALUES ('bill', 'Billing disputes'), ('port', 'Portal outages')"""
    )
    conn.execute(
        """INSERT INTO canonical_clusters
             (cluster_id, trc, canonical_label, centroid_blob, tier, concept_id)
           VALUES
             ('bill-dup', 'BILLING', 'Duplicate invoice', ?, 'active', 'bill'),
             ('bill-over','BILLING', 'Overcharge',        ?, 'active', 'bill'),
             ('port-out', 'PORTAL',  'Portal outage',     ?, 'active', 'port')""",
        (centroid(1), centroid(2), centroid(3)),
    )
    rows = []
    for i in range(10):
        rows.append((f"TB{i}", "BILLING", "Billing", "Thunderbird Insurance",
                     "dup invoice", "bill-dup"))
    for i in range(5):
        rows.append((f"MB{i}", "BILLING", "Billing", "Molina Health",
                     "overcharge", "bill-over"))
    for i in range(3):
        rows.append((f"TP{i}", "PORTAL", "Portal", "Thunderbird Insurance",
                     "portal down", "port-out"))
    for tid, trc, trc_label, payer, subj, cid in rows:
        conn.execute(
            """INSERT INTO ticket_index
                 (ticket_id, trc_code, trc_label, insurance_payer,
                  subject_sanitized, canonical_issue_id,
                  first_seen_scan_id, last_seen_scan_id,
                  first_seen_date, ticket_created_date)
               VALUES (?, ?, ?, ?, ?, ?, 'scan1', 'scan1', '2026-01-15', '2026-01-15')""",
            (tid, trc, trc_label, payer, subj, cid),
        )
    conn.commit()
    conn.close()
    yield str(db_path)


# ──────────────────────────────────────────────────────────────────────
# MCP dispatch path
# ──────────────────────────────────────────────────────────────────────

class TestMcpDispatch:
    def test_query_issues_thunderbird_scoped(self, seeded_db_path, monkeypatch):
        """The headline Phase 9 scenario: 'What are the top issues for
        Thunderbird?' — result must contain ONLY Thunderbird tickets."""
        monkeypatch.setenv("ALMA_DB_PATH", seeded_db_path)
        from src.mcp.chat_mcp_server import _execute_tool
        result = _execute_tool("query_issues", {
            "payer": "Thunderbird",
            "group_by": "concept",
        })
        assert "error" not in result
        assert result["scope"]["filters"]["payer"] == "Thunderbird"
        # Thunderbird has 10 BILL-dup + 3 PORT = 13 assigned tickets
        assert result["scope"]["total_tickets"] == 13
        ids = {i["group_id"] for i in result["top_issues"]}
        # Both concepts present; top should be 'bill' (10 vs 3)
        assert "bill" in ids
        assert "port" in ids
        assert result["top_issues"][0]["group_id"] == "bill"
        assert result["top_issues"][0]["ticket_count"] == 10
        # Samples are populated with Thunderbird-only ticket_ids
        sample_ids = result["top_issues"][0]["sample_ticket_ids"]
        for sid in sample_ids:
            assert sid.startswith("TB"), f"sample {sid} isn't a Thunderbird ticket"

    def test_audit_tag_correlation_smoke(self, seeded_db_path, monkeypatch):
        monkeypatch.setenv("ALMA_DB_PATH", seeded_db_path)
        from src.mcp.chat_mcp_server import _execute_tool
        result = _execute_tool("audit_tag_correlation", {"tag": "never_applied_tag"})
        assert "error" not in result
        assert result["tag"] == "never_applied_tag"
        assert result["total_tagged_tickets"] == 0

    def test_deprecated_tool_returns_unknown(self, seeded_db_path, monkeypatch):
        """query_entities was removed in Phase 9 — calling it should return
        a graceful 'unknown tool' error rather than crashing the server."""
        monkeypatch.setenv("ALMA_DB_PATH", seeded_db_path)
        from src.mcp.chat_mcp_server import _execute_tool
        r = _execute_tool("query_entities", {"entity_type": "payer"})
        assert "error" in r
        assert "Unknown tool" in r["error"]

    def test_mcp_schema_includes_new_tools(self):
        from src.mcp.chat_mcp_server import TOOL_SCHEMAS
        names = {t["name"] for t in TOOL_SCHEMAS}
        assert "query_issues" in names
        assert "audit_tag_correlation" in names
        assert "query_entities" not in names
        assert "query_ticket_classifications" not in names
        assert "query_findings" not in names


# ──────────────────────────────────────────────────────────────────────
# Claude executor path
# ──────────────────────────────────────────────────────────────────────

class TestClaudeExecutor:
    def test_claude_tool_definitions_include_new_tools(self):
        from src.llm.claude_tools import TOOL_DEFINITIONS
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "query_issues" in names
        assert "audit_tag_correlation" in names

    def test_claude_executor_dispatches_query_issues(self, seeded_db_path):
        from src.llm.claude_tools import execute_tool
        mgr = DatabaseManager(Path(seeded_db_path))
        mgr.initialize()
        raw = execute_tool("query_issues", {"payer": "Thunderbird"}, mgr)
        data = json.loads(raw)
        assert "scope" in data
        assert data["scope"]["total_tickets"] == 13
        mgr.conn.close()

    def test_claude_executor_dispatches_tag_audit(self, seeded_db_path):
        from src.llm.claude_tools import execute_tool
        mgr = DatabaseManager(Path(seeded_db_path))
        mgr.initialize()
        raw = execute_tool("audit_tag_correlation",
                           {"tag": "x", "top_k": 5}, mgr)
        data = json.loads(raw)
        assert data["tag"] == "x"
        mgr.conn.close()


# ──────────────────────────────────────────────────────────────────────
# Chat-tool registry path
# ──────────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_registry_includes_new_tools(self):
        from src.data.chat_tools.registry import get_tool_registry
        reg = get_tool_registry()
        assert "query_issues" in reg
        assert "audit_tag_correlation" in reg

    def test_prompt_addendum_lists_new_tools(self):
        from src.data.chat_tools.tool_prompts import (
            TOOL_PROMPT_ADDENDUM, NEW_TOOL_NAMES,
        )
        assert "query_issues" in NEW_TOOL_NAMES
        assert "audit_tag_correlation" in NEW_TOOL_NAMES
        assert "query_issues" in TOOL_PROMPT_ADDENDUM
        assert "audit_tag_correlation" in TOOL_PROMPT_ADDENDUM
