"""Tests for the 2026-04-23 bug-bash fixes.

Covers:
  F-2b · query_issues trc filter uses substring match
  F-3b · query_stats anomaly rows get a synthesized description field
  F-5  · MCP _execute_tool routes through dispatch_tool (logged)
  F-8  · audit_tag_correlation falls back to ticket_index.friction_type
         when ticket_tags is empty
  F-9  · ChatEngine tracks a degraded-response streak, emits
         bridge_recycle_requested when the threshold is hit, and resets
         the streak on a clean response.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """Tiny DB with just the tables these tests need — no seeded fixture
    dependency so the tests stay self-contained."""
    p = tmp_path / "bugbash.db"
    conn = sqlite3.connect(str(p))
    conn.executescript("""
        CREATE TABLE tickets (
            ticket_id TEXT PRIMARY KEY,
            trc_code TEXT,
            trc_label TEXT,
            subject TEXT,
            created_at TEXT
        );
        CREATE TABLE ticket_index (
            ticket_id TEXT PRIMARY KEY,
            trc_code TEXT,
            trc_label TEXT,
            canonical_issue_id TEXT,
            insurance_payer TEXT,
            provider_id TEXT,
            friction_type TEXT,
            subject_sanitized TEXT,
            issue_snippet TEXT,
            ticket_created_date TEXT
        );
        CREATE TABLE ticket_tags (
            ticket_id TEXT,
            tag TEXT
        );
        CREATE TABLE canonical_clusters (
            cluster_id TEXT PRIMARY KEY,
            concept_id TEXT,
            canonical_label TEXT,
            incident_description TEXT
        );
        CREATE TABLE canonical_concepts (
            concept_id TEXT PRIMARY KEY,
            concept_label TEXT
        );
        CREATE TABLE anomaly_flags (
            flag_id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            trc_code TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            metric_key TEXT DEFAULT '',
            observed_value REAL NOT NULL,
            expected_mean REAL NOT NULL,
            expected_std REAL NOT NULL,
            z_score REAL NOT NULL,
            theta_level INTEGER NOT NULL,
            status TEXT DEFAULT 'open',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            resolved_at TEXT DEFAULT ''
        );
        INSERT INTO ticket_index (ticket_id, trc_code, trc_label, friction_type, insurance_payer, canonical_issue_id) VALUES
            ('T1', 'Refund cash pay invoice OR Charge cancellation fee', 'Refund', 'incorrect_charge', 'Thunderbird', NULL),
            ('T2', 'Client portal access issue', 'Portal', 'feature_broken', 'Unicorn', NULL),
            ('T3', 'Refund cash pay invoice OR Charge cancellation fee', 'Refund', 'incorrect_charge', 'Thunderbird', NULL);
        INSERT INTO anomaly_flags (date, trc_code, metric_type, metric_key,
                                   observed_value, expected_mean, expected_std,
                                   z_score, theta_level, notes, created_at) VALUES
            ('2025-04-10', 'TRC-A', 'sentiment', '', -0.49, 0.79, 0.15, -11.01, 2, '', '2025-04-15'),
            ('2025-03-11', 'TRC-B', 'term_freq', 'business days', 0.22, 0.10, 0.03, 9.11, 2, '', '2025-03-12'),
            ('2025-02-01', 'TRC-C', 'sentiment', '', 0.1, 0.5, 0.1, -4.0, 2, 'analyst says: real incident', '2025-02-02');
    """)
    conn.commit()
    conn.close()
    return p


# ──────────────────────────────────────────────────────────────────
# F-2b · trc substring match
# ──────────────────────────────────────────────────────────────────

class TestF2b_TrcSubstringMatch:
    def test_partial_trc_matches(self, tmp_db):
        from src.data.issue_query_handler import handle_query_issues
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_issues(
            conn, {"trc": "Refund cash", "group_by": "trc"}, session_filters={},
        )
        assert r["scope"]["total_tickets"] == 2, (
            "substring 'Refund cash' should match 2 seeded tickets"
        )
        conn.close()

    def test_fragment_matches_trc_label(self, tmp_db):
        from src.data.issue_query_handler import handle_query_issues
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        # 'Portal' is the trc_label, not the trc_code — must still match
        r = handle_query_issues(
            conn, {"trc": "Portal", "group_by": "trc"}, session_filters={},
        )
        assert r["scope"]["total_tickets"] == 1
        conn.close()

    def test_case_insensitive(self, tmp_db):
        from src.data.issue_query_handler import handle_query_issues
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_issues(
            conn, {"trc": "REFUND", "group_by": "trc"}, session_filters={},
        )
        assert r["scope"]["total_tickets"] == 2
        conn.close()


# ──────────────────────────────────────────────────────────────────
# F-3b · synthetic descriptions
# ──────────────────────────────────────────────────────────────────

class TestF3b_SyntheticAnomalyDescription:
    def test_sentiment_crash_description(self, tmp_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(
            conn, {"stat_type": "anomalies", "severity": "severe", "limit": 10}, {},
        )
        conn.close()
        descs = [a["description"] for a in r["anomalies"]]
        assert any("sentiment crashed" in d.lower() for d in descs)
        assert any("z=-11.01" in d for d in descs)
        assert any("TRC-A" in d for d in descs)

    def test_term_freq_spike_description(self, tmp_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(
            conn, {"stat_type": "anomalies", "severity": "severe", "limit": 10}, {},
        )
        conn.close()
        descs = [a["description"] for a in r["anomalies"]]
        assert any("business days" in d for d in descs)
        assert any("z=9.11" in d for d in descs)

    def test_notes_override_synthesis(self, tmp_db):
        """When notes column is populated we use it verbatim, never the synth."""
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(
            conn, {"stat_type": "anomalies", "severity": "severe", "limit": 10}, {},
        )
        conn.close()
        notes_populated = [
            a for a in r["anomalies"]
            if (a.get("notes") or "").strip()
        ]
        for a in notes_populated:
            assert a["description"] == a["notes"], (
                "Populated notes should be used as description verbatim"
            )


# ──────────────────────────────────────────────────────────────────
# F-5 · chat_tool_executions is written
# ──────────────────────────────────────────────────────────────────

class TestF5_ToolExecutionAuditing:
    def test_mcp_execute_tool_routes_via_registry(self, tmp_db, monkeypatch):
        # MCP _execute_tool should log to chat_tool_executions through dispatch_tool
        # (the previous bypass path left the audit empty).
        conn = sqlite3.connect(str(tmp_db))
        conn.executescript("""
            CREATE TABLE chat_tool_executions (
                execution_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_json TEXT,
                result_json TEXT,
                result_rows INTEGER,
                tables_touched TEXT,
                elapsed_ms INTEGER,
                error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        conn.close()
        monkeypatch.setenv("ALMA_DB_PATH", str(tmp_db))

        from src.mcp.chat_mcp_server import _execute_tool
        r = _execute_tool("query_issues", {"trc": "Refund", "group_by": "trc"})
        assert "error" not in r or r.get("scope") is not None

        # Verify a row landed in chat_tool_executions
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute(
            "SELECT session_id, tool_name FROM chat_tool_executions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None, "dispatch_tool should have written an audit row"
        assert row[0] == "adhoc_probe", (
            "No session_id => synthetic 'adhoc_probe' id (F-5 behavior)"
        )
        assert row[1] == "query_issues"

    def test_mcp_rejects_non_schema_tool(self):
        """Legacy registry aliases (query_entities etc.) must not leak
        through the MCP interface even though they're in the registry."""
        from src.mcp.chat_mcp_server import _execute_tool
        r = _execute_tool("query_entities", {"entity_type": "payer"})
        assert "error" in r
        assert "Unknown tool" in r["error"]


# ──────────────────────────────────────────────────────────────────
# F-8 · tag audit fallback
# ──────────────────────────────────────────────────────────────────

class TestF8_TagAuditFallback:
    def test_falls_back_to_friction_type(self, tmp_db):
        # ticket_tags is empty; the 2 'incorrect_charge' tickets live in
        # ticket_index.friction_type. Fallback should pick them up.
        from src.data.tag_audit import handle_audit_tag_correlation
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_audit_tag_correlation(conn, {"tag": "incorrect_charge"})
        conn.close()
        assert r["total_tagged_tickets"] == 2
        assert r["tag_source"] == "ticket_index.friction_type"

    def test_primary_wins_when_populated(self, tmp_db):
        """If ticket_tags has rows, use that path (don't fall back)."""
        conn = sqlite3.connect(str(tmp_db))
        conn.execute("INSERT INTO ticket_tags VALUES ('T1', 'incorrect_charge')")
        conn.commit()
        conn.row_factory = sqlite3.Row
        from src.data.tag_audit import handle_audit_tag_correlation
        r = handle_audit_tag_correlation(conn, {"tag": "incorrect_charge"})
        conn.close()
        assert r["tag_source"] == "ticket_tags"
        # ticket_tags has 1 row → we don't also union friction_type's 2
        assert r["total_tagged_tickets"] == 1

    def test_unknown_tag_returns_zero(self, tmp_db):
        from src.data.tag_audit import handle_audit_tag_correlation
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        r = handle_audit_tag_correlation(conn, {"tag": "nonexistent_tag"})
        conn.close()
        assert r["total_tagged_tickets"] == 0


# ──────────────────────────────────────────────────────────────────
# F-9 · adaptive bridge recycle
# ──────────────────────────────────────────────────────────────────

class TestF9_AdaptiveBridgeRecycle:
    def test_degraded_phrase_detected(self):
        from src.services.chat_engine import ChatEngine
        assert ChatEngine._is_degraded_response(
            "I encountered an issue retrieving ticket data"
        ) is True
        assert ChatEngine._is_degraded_response(
            "I was unable to retrieve the anomaly data"
        ) is True
        assert ChatEngine._is_degraded_response(
            "I've encountered some connection issues with the tools"
        ) is True

    def test_legitimate_empty_not_degraded(self):
        """'no matches for that filter' is legitimate, not a tool failure."""
        from src.services.chat_engine import ChatEngine
        assert ChatEngine._is_degraded_response(
            "The tool returned no matches for that filter. "
            "Would you like me to broaden the query?"
        ) is False

    def test_data_response_not_degraded(self):
        from src.services.chat_engine import ChatEngine
        assert ChatEngine._is_degraded_response(
            "Here are the top 5 payers: Thunderbird (289), Unicorn (270)…"
        ) is False

    def test_empty_response_is_degraded(self):
        from src.services.chat_engine import ChatEngine
        assert ChatEngine._is_degraded_response("") is True
        assert ChatEngine._is_degraded_response(None) is True  # defensive

    def test_streak_increments_and_resets(self):
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="x", recycle_threshold=3)
        assert engine.degraded_streak == 0

        # Simulate a bad response being processed
        engine._history = [
            {"role": "user", "content": "q"},
        ]
        engine._on_worker_finished("I encountered an issue retrieving data", {})
        assert engine.degraded_streak == 1

        engine._history.append({"role": "user", "content": "q2"})
        engine._on_worker_finished("I was unable to retrieve data", {})
        assert engine.degraded_streak == 2

        # Clean response resets
        engine._history.append({"role": "user", "content": "q3"})
        engine._on_worker_finished("Here are 5 tickets from the DB.", {})
        assert engine.degraded_streak == 0

    def test_signal_fires_at_threshold(self):
        """On the next send after threshold-many degraded responses,
        bridge_recycle_requested should emit AND _warm_client should clear."""
        from src.services.chat_engine import ChatEngine

        engine = ChatEngine(system_prompt="x", recycle_threshold=2)
        # Inject a fake warm client so the engine has something to clear
        fake_client = MagicMock()
        engine.set_client(fake_client)

        received = []
        engine.bridge_recycle_requested.connect(lambda: received.append(True))

        # Accumulate 2 degraded responses
        engine._history = [{"role": "user", "content": "q"}]
        engine._on_worker_finished("I encountered an issue retrieving data", {})
        engine._history.append({"role": "user", "content": "q2"})
        engine._on_worker_finished("I encountered an issue retrieving data", {})
        assert engine.degraded_streak >= engine._recycle_threshold

        # Now a send() should recycle. We short-circuit the actual worker
        # launch by patching _launch_worker (we only care that the recycle
        # path runs before the client is consulted).
        engine._launch_worker = lambda *a, **kw: None
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([])
        engine.send("next question")
        app.processEvents()

        assert received == [True], "bridge_recycle_requested should have emitted once"
        assert engine._warm_client is None, "warm client must be dropped"
        assert engine.degraded_streak == 0, "streak must reset after recycle"

    def test_worker_error_counts_as_degraded(self):
        """Worker-level errors (bridge crash, timeout) are the strongest
        degraded signal — streak must bump."""
        from src.services.chat_engine import ChatEngine
        engine = ChatEngine(system_prompt="x")
        assert engine.degraded_streak == 0
        engine._on_worker_error("Bridge call failed: timeout")
        assert engine.degraded_streak == 1


# ──────────────────────────────────────────────────────────────────
# R-1 · strict suppression prompt
# ──────────────────────────────────────────────────────────────────

class TestR1_StrictSuppressionPrompt:
    """Lock the suppression-block wording so we don't accidentally
    soften it again. Live behavior is verified separately by
    tests/gemini_chats_live/probe_q22.py."""

    def test_production_prompt_has_strict_enumeration_clause(self):
        from pathlib import Path
        text = Path("src/ui/pages/gemini_chats_page.py").read_text(encoding="utf-8")
        # The exact phrasing of the strict clause
        assert "NEVER invoke, name, mention, list, describe, hint at, or acknowledge" in text
        assert "list every tool" in text.lower()

    def test_runner_prompt_mirrors_production(self):
        from pathlib import Path
        text = Path("tests/gemini_chats_live/run_programmatic.py").read_text(encoding="utf-8")
        assert "NEVER invoke, name, mention, list, describe, hint at, or acknowledge" in text


# ──────────────────────────────────────────────────────────────────
# R-2 · friction_distribution tool + canonical taxonomy
# ──────────────────────────────────────────────────────────────────

class TestCanonicalTaxonomy:
    def test_friction_types_constant(self):
        from src.data.chat_tools.canonical_taxonomy import CANONICAL_FRICTION_TYPES
        assert "incorrect_charge" in CANONICAL_FRICTION_TYPES
        assert "feature_broken" in CANONICAL_FRICTION_TYPES
        assert len(CANONICAL_FRICTION_TYPES) == 12

    def test_coerce_or_warn_canonical_exact(self):
        from src.data.chat_tools.canonical_taxonomy import (
            CANONICAL_FRICTION_TYPES, coerce_or_warn,
        )
        v, w = coerce_or_warn("incorrect_charge", CANONICAL_FRICTION_TYPES,
                              column_name="friction_type")
        assert v == "incorrect_charge"
        assert w is None

    def test_coerce_or_warn_case_silent_coerce(self):
        from src.data.chat_tools.canonical_taxonomy import (
            CANONICAL_FRICTION_TYPES, coerce_or_warn,
        )
        v, w = coerce_or_warn("INCORRECT_CHARGE", CANONICAL_FRICTION_TYPES,
                              column_name="friction_type")
        assert v == "incorrect_charge"
        assert w is None

    def test_coerce_or_warn_novel_pass_through_with_warning(self):
        from src.data.chat_tools.canonical_taxonomy import (
            CANONICAL_FRICTION_TYPES, coerce_or_warn,
        )
        v, w = coerce_or_warn("brand_new_type", CANONICAL_FRICTION_TYPES,
                              column_name="friction_type")
        assert v == "brand_new_type"  # pass-through
        assert w is not None
        assert "audit" in w.lower()

    def test_coerce_or_warn_none(self):
        from src.data.chat_tools.canonical_taxonomy import (
            CANONICAL_FRICTION_TYPES, coerce_or_warn,
        )
        assert coerce_or_warn(None, CANONICAL_FRICTION_TYPES, column_name="x") == (None, None)
        assert coerce_or_warn("", CANONICAL_FRICTION_TYPES, column_name="x") == (None, None)


@pytest.fixture
def friction_db(tmp_path) -> Path:
    """DB with a deterministic friction_type distribution for tests."""
    p = tmp_path / "friction.db"
    conn = sqlite3.connect(str(p))
    conn.executescript("""
        CREATE TABLE ticket_index (
            ticket_id TEXT PRIMARY KEY,
            trc_code TEXT,
            trc_label TEXT,
            insurance_payer TEXT,
            friction_type TEXT,
            ticket_created_date TEXT
        );
    """)
    # 5 incorrect_charge, 3 feature_broken, 2 repeat_contact, 1 access_blocked
    rows = (
        [(f"T{i}", "TRC-A", "A", "PayerX", "incorrect_charge", "2025-02-15") for i in range(5)]
        + [(f"T{i+5}", "TRC-B", "B", "PayerX", "feature_broken", "2025-02-15") for i in range(3)]
        + [(f"T{i+8}", "TRC-A", "A", "PayerY", "repeat_contact", "2025-03-15") for i in range(2)]
        + [(f"T{i+10}", "TRC-B", "B", "PayerY", "access_blocked", "2025-03-15") for i in range(1)]
    )
    conn.executemany(
        "INSERT INTO ticket_index VALUES (?,?,?,?,?,?)", rows,
    )
    conn.commit()
    conn.close()
    return p


class TestFrictionDistribution:
    def test_no_filter_ranks_by_count(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution"}, {})
        conn.close()
        types = [d["friction_type"] for d in r["friction_distribution"]]
        counts = [d["ticket_count"] for d in r["friction_distribution"]]
        assert types[:4] == ["incorrect_charge", "feature_broken", "repeat_contact", "access_blocked"]
        assert counts[:4] == [5, 3, 2, 1]

    def test_pct_of_total_correct(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution"}, {})
        conn.close()
        total = r["scope"]["total_tickets"]
        assert total == 11
        for d in r["friction_distribution"]:
            assert abs(d["pct_of_total"] - d["ticket_count"] / 11) < 1e-3

    def test_trc_filter_scopes(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution",
                                       "trc": "TRC-A"}, {})
        conn.close()
        # TRC-A has 5 incorrect_charge + 2 repeat_contact = 7
        assert r["scope"]["total_tickets"] == 7
        types = {d["friction_type"] for d in r["friction_distribution"]}
        assert types == {"incorrect_charge", "repeat_contact"}

    def test_payer_filter_scopes(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution",
                                       "payer": "PayerY"}, {})
        conn.close()
        assert r["scope"]["total_tickets"] == 3  # 2 repeat + 1 access_blocked

    def test_cross_dim_trc_nests_breakdown(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution",
                                       "cross_dim": "trc"}, {})
        conn.close()
        top = r["friction_distribution"][0]
        assert top["friction_type"] == "incorrect_charge"
        assert "by_trc" in top
        assert top["by_trc"][0]["trc"] == "TRC-A"
        assert top["by_trc"][0]["n"] == 5

    def test_invalid_cross_dim_returns_error(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution",
                                       "cross_dim": "bogus"}, {})
        conn.close()
        assert "error" in r

    def test_friction_type_filter_canonical(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution",
                                       "friction_type": "incorrect_charge"}, {})
        conn.close()
        assert len(r["friction_distribution"]) == 1
        assert r["friction_distribution"][0]["ticket_count"] == 5
        assert r.get("gate_warning") is None

    def test_friction_type_filter_novel_warns(self, friction_db):
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution",
                                       "friction_type": "completely_made_up"}, {})
        conn.close()
        assert r.get("gate_warning") is not None
        assert "completely_made_up" in r["gate_warning"]

    def test_appears_in_mcp_schema_enum(self):
        from src.mcp.chat_mcp_server import TOOL_SCHEMAS
        query_stats_schema = next(
            t for t in TOOL_SCHEMAS if t["name"] == "query_stats"
        )
        stat_enum = query_stats_schema["inputSchema"]["properties"]["stat_type"]["enum"]
        assert "friction_distribution" in stat_enum

    def test_canonical_list_returned_in_response(self, friction_db):
        """LLM gets the canonical 12 in every response so it can self-correct."""
        from src.data.chat_tools.fast_path import handle_query_stats
        conn = sqlite3.connect(str(friction_db))
        conn.row_factory = sqlite3.Row
        r = handle_query_stats(conn, {"stat_type": "friction_distribution"}, {})
        conn.close()
        assert "canonical_friction_types" in r
        assert len(r["canonical_friction_types"]) == 12
