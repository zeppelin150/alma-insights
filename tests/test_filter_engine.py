"""
Tests for src.data.filter_engine

Validates:
    - Single-table queries (ticket_index, conversations)
    - Multi-table JOIN queries
    - FTS5 keyword filter
    - Date range filters (with SUBSTR)
    - Empty filters → full table
    - Invalid filter keys → warning
    - Conflicting dates → error
    - Limit + order_by
    - All filter types: trc, friction, sentiment, anomaly, entity, theme_id, dataset_id
    - Validators
    - Column map
    - Tool logger
"""

import json
import sqlite3
import os
import tempfile
import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MIG_005 = (_PROJECT_ROOT / "migrations" / "005_persistence_layer.sql").read_text(encoding="utf-8")
_MIG_006 = (_PROJECT_ROOT / "migrations" / "006_hybrid_chat.sql").read_text(encoding="utf-8")

# Conversations table is created by db_manager, not migrations.
# We need a minimal version for tests.
_CONVERSATIONS_DDL = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    subject TEXT,
    trc_code TEXT,
    created_at TEXT,
    csat_score REAL
);
CREATE TABLE IF NOT EXISTS conversations (
    ticket_id TEXT PRIMARY KEY,
    subject TEXT,
    trc_code TEXT,
    trc_label TEXT,
    status TEXT,
    csat_score REAL,
    created_at TEXT,
    solved_at TEXT,
    message_count INTEGER,
    client_messages INTEGER,
    agent_messages INTEGER,
    full_thread TEXT,
    thread_preview TEXT,
    dataset_id INTEGER DEFAULT 0,
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
    ticket_id,
    subject,
    trc_label,
    full_thread,
    content=conversations,
    content_rowid=rowid
);
"""


@pytest.fixture
def db():
    """In-memory DB with all needed tables + sample data."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(_CONVERSATIONS_DDL)
    conn.executescript(_MIG_005)
    conn.executescript(_MIG_006)
    _seed_data(conn)
    yield conn
    conn.close()


def _seed_data(conn):
    """Insert test rows into ticket_index, conversations, ticket_theme_tags."""
    # ticket_index rows
    for i, (trc, friction, sentiment, anomaly, ds) in enumerate([
        ("Billing", "incorrect_charge", "negative", "critical", 1),
        ("Billing", "duplicate_billing", "negative", None, 1),
        ("Claims", "slow_processing", "neutral", None, 2),
        ("Claims", "denied_claim", "negative", "warning", 2),
        ("Tech", "login_failure", "negative", "critical", 1),
    ]):
        tid = f"T-{i+1}"
        conn.execute(
            "INSERT INTO ticket_index "
            "(ticket_id, first_seen_scan_id, last_seen_scan_id, "
            "first_seen_date, ticket_created_date, trc_code, "
            "friction_type, sub_pattern, sentiment_polarity, "
            "anomaly_flag, entities_json, dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, "s1", "s1", "2026-01-01",
             f"2026-01-{10+i:02d}", trc, friction, friction,
             sentiment, anomaly,
             json.dumps({"payer": "BlueCross" if i < 2 else "Aetna"}),
             ds),
        )

    # conversations rows
    for i in range(5):
        tid = f"T-{i+1}"
        conn.execute(
            "INSERT INTO conversations "
            "(ticket_id, subject, trc_code, created_at, full_thread, "
            "thread_preview, dataset_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, f"Subject {i+1}", ["Billing", "Billing", "Claims", "Claims", "Tech"][i],
             f"2026-01-{10+i:02d}", f"Thread text for ticket {i+1} about billing dispute",
             f"Preview {i+1}", [1, 1, 2, 2, 1][i]),
        )
        # FTS index
        conn.execute(
            "INSERT INTO conversations_fts (rowid, ticket_id, subject, trc_label, full_thread) "
            "VALUES (?, ?, ?, ?, ?)",
            (i + 1, tid, f"Subject {i+1}", ["Billing", "Billing", "Claims", "Claims", "Tech"][i],
             f"Thread text for ticket {i+1} about billing dispute"),
        )

    # ticket_theme_tags
    conn.execute(
        "INSERT INTO ticket_theme_tags "
        "(ticket_id, theme_id, scan_id, tagged_at, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("T-1", "theme_billing", "s1", "2026-01-01", 1.0),
    )
    conn.execute(
        "INSERT INTO ticket_theme_tags "
        "(ticket_id, theme_id, scan_id, tagged_at, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("T-2", "theme_billing", "s1", "2026-01-01", 0.8),
    )
    conn.commit()


# ═══════════════════════════════════════
#  COLUMN MAP
# ═══════════════════════════════════════

class TestColumnMap:

    def test_resolve_known_column(self):
        from src.data.filter_engine.column_map import resolve_column
        assert resolve_column("trc_codes", "ticket_index") == "trc_code"
        assert resolve_column("date_start", "conversations") == "created_at"

    def test_resolve_unknown_key_raises(self):
        from src.data.filter_engine.column_map import resolve_column
        with pytest.raises(KeyError, match="Unknown filter key"):
            resolve_column("bogus_key", "ticket_index")

    def test_resolve_wrong_table_raises(self):
        from src.data.filter_engine.column_map import resolve_column
        with pytest.raises(KeyError, match="not available on table"):
            resolve_column("keyword", "ticket_index")

    def test_infer_tables(self):
        from src.data.filter_engine.column_map import infer_tables
        tables = infer_tables({"trc_codes": ["Billing"], "keyword": "test"})
        assert "ticket_index" in tables
        assert "conversations" in tables

    def test_best_table_prefers_base(self):
        from src.data.filter_engine.column_map import best_table_for_filter
        assert best_table_for_filter("trc_codes", "conversations") == "conversations"
        assert best_table_for_filter("trc_codes", "ticket_index") == "ticket_index"


# ═══════════════════════════════════════
#  VALIDATORS
# ═══════════════════════════════════════

class TestValidators:

    def test_valid_filters_pass_through(self):
        from src.data.filter_engine.validators import validate_filters
        inp = {"trc_codes": ["Billing"], "date_start": "2026-01-01"}
        cleaned, warnings = validate_filters(inp)
        assert cleaned == inp
        assert warnings == []

    def test_unknown_key_warned(self):
        from src.data.filter_engine.validators import validate_filters
        cleaned, warnings = validate_filters({"bogus": "value"})
        assert "bogus" not in cleaned
        assert any("Unknown" in w for w in warnings)

    def test_invalid_date_warned(self):
        from src.data.filter_engine.validators import validate_filters
        cleaned, warnings = validate_filters({"date_start": "not-a-date"})
        assert "date_start" not in cleaned
        assert any("Invalid date" in w for w in warnings)

    def test_date_range_conflict(self):
        from src.data.filter_engine.validators import validate_filters
        cleaned, warnings = validate_filters({
            "date_start": "2026-03-01",
            "date_end": "2026-01-01",
        })
        assert "date_start" not in cleaned
        assert "date_end" not in cleaned
        assert any("date_start" in w for w in warnings)

    def test_none_values_skipped(self):
        from src.data.filter_engine.validators import validate_filters
        cleaned, _ = validate_filters({"trc_codes": None, "sentiment": None})
        assert cleaned == {}

    def test_string_coerced_to_list(self):
        from src.data.filter_engine.validators import validate_filters
        cleaned, _ = validate_filters({"trc_codes": "Billing"})
        assert cleaned["trc_codes"] == ["Billing"]

    def test_dataset_id_string_coerced(self):
        from src.data.filter_engine.validators import validate_filters
        cleaned, _ = validate_filters({"dataset_id": "3"})
        assert cleaned["dataset_id"] == 3

    def test_sanitize_date_valid(self):
        from src.data.filter_engine.validators import sanitize_date
        assert sanitize_date("2026-01-15") == "2026-01-15"

    def test_sanitize_date_invalid(self):
        from src.data.filter_engine.validators import sanitize_date
        assert sanitize_date("2026-13-01") is None
        assert sanitize_date("not-a-date") is None
        assert sanitize_date("") is None


# ═══════════════════════════════════════
#  FTS HANDLER
# ═══════════════════════════════════════

class TestFTSHandler:

    def test_fts_clause_conversations_base(self):
        from src.data.filter_engine.fts_handler import build_fts_clause
        clause, params = build_fts_clause("billing", "c", "conversations")
        assert "conversations_fts MATCH" in clause
        assert len(params) == 1

    def test_fts_clause_ticket_index_base(self):
        from src.data.filter_engine.fts_handler import build_fts_clause
        clause, params = build_fts_clause("billing", "ti", "ticket_index")
        assert "conversations_fts MATCH" in clause

    def test_fts_sanitize_wraps_in_quotes(self):
        from src.data.filter_engine.fts_handler import _sanitize_fts_query
        assert _sanitize_fts_query("billing dispute") == '"billing dispute"'

    def test_fts_sanitize_preserves_quoted(self):
        from src.data.filter_engine.fts_handler import _sanitize_fts_query
        assert _sanitize_fts_query('"already quoted"') == '"already quoted"'

    def test_like_fallback(self):
        from src.data.filter_engine.fts_handler import build_like_fallback
        clause, params = build_like_fallback("billing", "c", "conversations")
        assert "LIKE" in clause
        assert len(params) == 2


# ═══════════════════════════════════════
#  JOIN BUILDER
# ═══════════════════════════════════════

class TestJoinBuilder:

    def test_no_join_same_table(self):
        from src.data.filter_engine.join_builder import build_join_clause
        result = build_join_clause({"ticket_index"}, "ticket_index")
        assert result == ""

    def test_join_ticket_index_to_conversations(self):
        from src.data.filter_engine.join_builder import build_join_clause
        result = build_join_clause(
            {"ticket_index", "conversations"}, "ticket_index"
        )
        assert "JOIN conversations" in result
        assert "ticket_id" in result

    def test_join_ticket_index_to_theme_tags(self):
        from src.data.filter_engine.join_builder import build_join_clause
        result = build_join_clause(
            {"ticket_index", "ticket_theme_tags"}, "ticket_index"
        )
        assert "JOIN ticket_theme_tags" in result

    def test_unknown_join_raises(self):
        from src.data.filter_engine.join_builder import build_join_clause
        with pytest.raises(ValueError, match="No JOIN path"):
            build_join_clause({"ticket_index", "nonexistent"}, "ticket_index")

    def test_get_join_key(self):
        from src.data.filter_engine.join_builder import get_join_key
        assert get_join_key("ticket_index", "conversations") == "ticket_id"


# ═══════════════════════════════════════
#  CORE: build_filter_query()
# ═══════════════════════════════════════

class TestBuildFilterQuery:

    def test_empty_filters_full_table(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        assert "WHERE" not in sql
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 5

    def test_single_trc_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"trc_codes": ["Billing"]},
            select_columns=["ti.ticket_id", "ti.trc_code"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2
        assert all(r[1] == "Billing" for r in rows)

    def test_multiple_trc_codes(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"trc_codes": ["Billing", "Claims"]},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 4

    def test_friction_type_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"friction_types": ["incorrect_charge"]},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "T-1"

    def test_sentiment_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"sentiment": "neutral"},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "T-3"

    def test_anomaly_flag_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"anomaly_flag": "critical"},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2

    def test_date_range_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"date_start": "2026-01-12", "date_end": "2026-01-14"},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 3  # T-3 (01-12), T-4 (01-13), T-5 (01-14)

    def test_date_uses_substr(self):
        """Date comparison uses SUBSTR for safety (danger_zones.md)."""
        from src.data.filter_engine import build_filter_query
        sql, _ = build_filter_query(
            filters={"date_start": "2026-01-01"},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        assert "SUBSTR(" in sql

    def test_dataset_id_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"dataset_id": 2},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2

    def test_entity_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"entities": {"payer": "BlueCross"}},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2

    def test_theme_id_filter_with_join(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"theme_ids": ["theme_billing"]},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        assert "JOIN ticket_theme_tags" in sql
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2

    def test_keyword_fts_filter(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"keyword": "billing dispute"},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        assert "conversations_fts MATCH" in sql
        rows = db.execute(sql, params).fetchall()
        # All 5 conversations contain "billing dispute" in thread text
        assert len(rows) == 5

    def test_conversations_base_table(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={"trc_codes": ["Billing"]},
            select_columns=["c.ticket_id", "c.subject"],
            base_table="conversations",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2

    def test_limit(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={},
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
            limit=2,
        )
        assert "LIMIT 2" in sql
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2

    def test_order_by(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={},
            select_columns=["ti.ticket_id", "ti.ticket_created_date"],
            base_table="ticket_index",
            order_by="ti.ticket_created_date DESC",
            limit=1,
        )
        rows = db.execute(sql, params).fetchall()
        assert rows[0][0] == "T-5"  # latest date

    def test_invalid_base_table_raises(self):
        from src.data.filter_engine import build_filter_query
        with pytest.raises(ValueError, match="Invalid base table"):
            build_filter_query(
                filters={}, select_columns=["x"],
                base_table="nonexistent",
            )

    def test_combined_filters(self, db):
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={
                "trc_codes": ["Billing"],
                "sentiment": "negative",
            },
            select_columns=["ti.ticket_id"],
            base_table="ticket_index",
        )
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 2  # T-1 and T-2

    def test_multi_table_join_query(self, db):
        """Filter spanning ticket_index + conversations triggers JOIN."""
        from src.data.filter_engine import build_filter_query
        sql, params = build_filter_query(
            filters={
                "friction_types": ["incorrect_charge"],
                "keyword": "billing",
            },
            select_columns=["ti.ticket_id", "ti.friction_type"],
            base_table="ticket_index",
        )
        assert "conversations_fts" in sql
        rows = db.execute(sql, params).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "T-1"


# ═══════════════════════════════════════
#  TOOL LOGGER
# ═══════════════════════════════════════

class TestToolLogger:

    def test_log_and_read(self, tmp_path):
        from src.data.chat_tools.tool_logger import ToolLogger
        logger = ToolLogger(log_dir=str(tmp_path))
        logger.log_call(
            tool_name="test_tool",
            args={"key": "value"},
            result_size=42,
            elapsed_ms=1.5,
        )
        entries = logger.read_recent(10)
        assert len(entries) == 1
        assert entries[0]["tool"] == "test_tool"
        assert entries[0]["result_size"] == 42

    def test_log_with_error(self, tmp_path):
        from src.data.chat_tools.tool_logger import ToolLogger
        logger = ToolLogger(log_dir=str(tmp_path))
        logger.log_call(
            tool_name="fail_tool",
            args={},
            result_size=0,
            elapsed_ms=5.0,
            error="Something broke",
        )
        entries = logger.read_recent(10)
        assert entries[0]["error"] == "Something broke"

    def test_decorator(self, tmp_path):
        from src.data.chat_tools.tool_logger import log_tool_call, _get_logger, ToolLogger
        import src.data.chat_tools.tool_logger as tl_mod

        # Inject test logger
        tl_mod._default_logger = ToolLogger(log_dir=str(tmp_path))

        @log_tool_call
        def my_tool(x=1):
            return {"count": x}

        result = my_tool(x=5)
        assert result == {"count": 5}

        entries = tl_mod._default_logger.read_recent(10)
        assert len(entries) == 1
        assert entries[0]["tool"] == "my_tool"
        assert entries[0]["error"] is None

        # Reset
        tl_mod._default_logger = None

    def test_read_empty_log(self, tmp_path):
        from src.data.chat_tools.tool_logger import ToolLogger
        logger = ToolLogger(log_dir=str(tmp_path))
        assert logger.read_recent(10) == []
