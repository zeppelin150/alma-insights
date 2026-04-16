"""
Test Module: Stage 5 — Multi-Source, Kodif Import, Combined Analytics, Source Awareness
Stage: 5 (Final)
Dependencies: Stages 1-4 complete

Covers:
  - Source type templates (Zendesk, Kodif, Custom) — 4 tests
  - Kodif self-contained ingestion — 8 tests
  - conversation_rebuild skip_rebuild flag — 3 tests
  - Source selectors on analytics pages — 6 tests
  - Guru source_id parameter passthrough — 3 tests
  - Chat tools source_id passthrough — 3 tests
  - Conversation Search import source selector — 2 tests
  - Combined mode (WarehouseQuery multi-source union) — 5 tests
  - Full regression — 4 tests

Run:
  - Single file: python -m pytest tests/test_stage5_multi_source.py -x -v
"""

import sqlite3
import csv
import io
import json
import tempfile
from pathlib import Path

import pytest


# ═══════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════

@pytest.fixture
def fresh_db(tmp_path):
    """Minimal DB with schema matching production."""
    db_path = tmp_path / "s5_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE source_registry (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            table_prefix TEXT NOT NULL UNIQUE,
            column_mapping TEXT,
            created_at TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            ticket_count INTEGER DEFAULT 0,
            last_import_at TEXT
        );
        INSERT INTO source_registry (source_id, source_name, source_type, table_prefix, created_at, is_default)
        VALUES ('zendesk_default', 'Zendesk Support', 'zendesk', 'zendesk_default', datetime('now'), 1);
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def multi_source_db(tmp_path):
    """DB with two sources (Zendesk + Kodif) and data in both."""
    db_path = tmp_path / "multi_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE source_registry (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            table_prefix TEXT NOT NULL UNIQUE,
            column_mapping TEXT,
            created_at TEXT NOT NULL,
            is_default INTEGER DEFAULT 0,
            ticket_count INTEGER DEFAULT 0,
            last_import_at TEXT
        );
        INSERT INTO source_registry VALUES ('zendesk_default', 'Zendesk Support', 'zendesk', 'zendesk_default', NULL, datetime('now'), 1, 100, NULL);
        INSERT INTO source_registry VALUES ('kodif_chat', 'Kodif Chat', 'kodif', 'kodif_chat', NULL, datetime('now'), 0, 50, NULL);

        CREATE TABLE [zendesk_default_conversations] (
            ticket_id TEXT, subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, csat_score REAL, created_at TEXT, solved_at TEXT,
            message_count INTEGER, client_messages INTEGER, agent_messages INTEGER,
            full_thread TEXT, thread_preview TEXT, dataset_id TEXT
        );
        CREATE TABLE [zendesk_default_tickets] (
            ticket_id TEXT PRIMARY KEY, subject TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE [kodif_chat_conversations] (
            ticket_id TEXT, subject TEXT, trc_code TEXT, trc_label TEXT,
            status TEXT, csat_score REAL, created_at TEXT, solved_at TEXT,
            message_count INTEGER, client_messages INTEGER, agent_messages INTEGER,
            full_thread TEXT, thread_preview TEXT, dataset_id TEXT
        );
        CREATE TABLE [kodif_chat_tickets] (
            ticket_id TEXT PRIMARY KEY, subject TEXT, status TEXT, created_at TEXT
        );
    """)

    # Seed Zendesk data
    for i in range(100):
        trc = f"ZEN-{(i % 3) + 1:03d}"
        conn.execute(
            "INSERT INTO [zendesk_default_conversations] VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"Z-{i:04d}", f"Zendesk ticket {i}", trc, trc, "closed", 3.0,
             f"2026-03-{(i % 28) + 1:02d}", None, 3, 1, 2, f"Thread Z-{i}", f"Preview Z-{i}", None),
        )
        conn.execute(
            "INSERT OR IGNORE INTO [zendesk_default_tickets] VALUES (?,?,?,?)",
            (f"Z-{i:04d}", f"Zendesk ticket {i}", "closed", f"2026-03-{(i % 28) + 1:02d}"),
        )

    # Seed Kodif data
    for i in range(50):
        trc = f"KOD-{(i % 2) + 1:03d}"
        conn.execute(
            "INSERT INTO [kodif_chat_conversations] VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"K-{i:04d}", f"Kodif chat {i}", trc, trc, "resolved", 4.0,
             f"2026-03-{(i % 28) + 1:02d}", None, 1, 1, 0, f"Chat K-{i}", f"Preview K-{i}", None),
        )
        conn.execute(
            "INSERT OR IGNORE INTO [kodif_chat_tickets] VALUES (?,?,?,?)",
            (f"K-{i:04d}", f"Kodif chat {i}", "resolved", f"2026-03-{(i % 28) + 1:02d}"),
        )

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def kodif_csv(tmp_path):
    """Create a Kodif-style CSV with self-contained conversations."""
    csv_path = tmp_path / "kodif_export.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["conversation_id", "subject", "category", "status", "created_at", "conversation_body", "csat_score"])
        for i in range(10):
            writer.writerow([
                f"K-{1000 + i}",
                f"Kodif chat about billing {i}",
                "BILLING",
                "resolved",
                f"2026-03-{15 + (i % 5):02d}",
                f"Customer: I have a billing question {i}\nBot: Let me help you with that.\nCustomer: Thank you!",
                "4.5",
            ])
    return csv_path


# ═══════════════════════════════════════════
#  PART A: SOURCE TYPE TEMPLATES
# ═══════════════════════════════════════════

class TestSourceTypeTemplates:
    """Tests for SOURCE_TYPE_TEMPLATES in source_registry."""

    def test_zendesk_template_exists(self):
        from src.data.source_registry import get_source_template
        t = get_source_template("zendesk")
        assert t["conversation_structure"] == "row_per_comment"
        assert "ticket_id" in t["column_mapping"]

    def test_kodif_template_exists(self):
        from src.data.source_registry import get_source_template
        t = get_source_template("kodif")
        assert t["conversation_structure"] == "self_contained"
        assert "conversation_id" in t["column_mapping"] or "chat_id" in t["column_mapping"]
        assert "full_thread" in t["column_mapping"].values()

    def test_custom_template_exists(self):
        from src.data.source_registry import get_source_template
        t = get_source_template("custom")
        assert t["conversation_structure"] == "row_per_comment"
        assert t["column_mapping"] == {}

    def test_unknown_returns_custom(self):
        from src.data.source_registry import get_source_template
        t = get_source_template("nonexistent")
        assert t == get_source_template("custom")


# ═══════════════════════════════════════════
#  PART A: KODIF SELF-CONTAINED INGESTION
# ═══════════════════════════════════════════

class TestKodifIngestion:
    """Tests for Kodif CSV import with self-contained conversations."""

    def test_resolve_columns_self_contained(self):
        from src.data.csv_ingestion import _resolve_columns
        headers = ["conversation_id", "subject", "conversation_body"]
        mapping = {
            "conversation_id": "ticket_id",
            "conversation_body": "full_thread",
        }
        col_map, fields, unmapped = _resolve_columns(
            headers, column_override=mapping, allow_self_contained=True
        )
        assert "ticket_id" in fields
        assert "full_thread" in fields

    def test_resolve_columns_self_contained_rejects_missing_thread(self):
        from src.data.csv_ingestion import _resolve_columns
        headers = ["conversation_id", "subject"]
        mapping = {"conversation_id": "ticket_id"}
        with pytest.raises(ValueError, match="full_thread"):
            _resolve_columns(headers, column_override=mapping, allow_self_contained=True)

    def test_group_self_contained(self):
        from src.data.csv_ingestion import _group_self_contained, _parse_row
        # Simulate column mapping: col 0 = ticket_id, col 2 = full_thread
        col_mapping = {0: "ticket_id", 1: "subject", 2: "full_thread"}
        rows = [
            ["K-001", "Chat 1", "Hello world conversation"],
            ["K-002", "Chat 2", "Another conversation body"],
        ]
        tickets = _group_self_contained(rows, col_mapping, lambda m, p: None, 2)
        assert len(tickets) == 2
        assert tickets["K-001"]["_full_thread"] == "Hello world conversation"
        assert tickets["K-002"]["_full_thread"] == "Another conversation body"

    def test_group_self_contained_skips_empty_thread(self):
        from src.data.csv_ingestion import _group_self_contained
        col_mapping = {0: "ticket_id", 1: "full_thread"}
        rows = [["K-001", ""], ["K-002", "Has content"]]
        tickets = _group_self_contained(rows, col_mapping, lambda m, p: None, 2)
        assert len(tickets) == 1
        assert "K-002" in tickets

    def test_ingest_csv_kodif_source_config(self, kodif_csv, tmp_path):
        """Full integration: ingest a Kodif CSV with source_config."""
        from src.data.db_manager import DatabaseManager
        from src.data.source_registry import get_source_template

        db = DatabaseManager(tmp_path / "kodif_db.db")
        db.initialize()

        from src.data.csv_ingestion import ingest_csv
        template = get_source_template("kodif")
        stats = ingest_csv(
            kodif_csv, db,
            source_config={
                "source_type": "kodif",
                "conversation_structure": "self_contained",
                "column_mapping": template["column_mapping"],
            },
        )
        assert stats["tickets_created"] == 10
        assert stats["tickets_skipped"] == 0

        # Verify conversations were written (to shared tables for now)
        count = db.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert count == 10

        # Verify full_thread is populated
        row = db.conn.execute(
            "SELECT full_thread FROM conversations WHERE ticket_id = 'K-1000'"
        ).fetchone()
        assert row is not None
        assert "billing question" in row[0].lower()
        db.close()

    def test_ingest_kodif_dedupe(self, kodif_csv, tmp_path):
        """Importing the same Kodif CSV twice should dedupe."""
        from src.data.db_manager import DatabaseManager
        from src.data.source_registry import get_source_template

        db = DatabaseManager(tmp_path / "dedupe_db.db")
        db.initialize()

        from src.data.csv_ingestion import ingest_csv
        template = get_source_template("kodif")
        config = {
            "source_type": "kodif",
            "conversation_structure": "self_contained",
            "column_mapping": template["column_mapping"],
        }
        stats1 = ingest_csv(kodif_csv, db, source_config=config)
        assert stats1["tickets_created"] == 10

        stats2 = ingest_csv(kodif_csv, db, source_config=config)
        assert stats2["tickets_created"] == 0
        assert stats2["tickets_skipped"] == 10
        db.close()


# ═══════════════════════════════════════════
#  PART A: SKIP REBUILD
# ═══════════════════════════════════════════

class TestSkipRebuild:
    """Tests for conversation_rebuild skip_rebuild flag."""

    def test_skip_rebuild_returns_immediately(self, tmp_path):
        from src.data.db_manager import DatabaseManager
        from src.data.conversation_rebuild import rebuild_conversations

        db = DatabaseManager(tmp_path / "skip_test.db")
        db.initialize()

        result = rebuild_conversations(db, skip_rebuild=True)
        assert result.get("skipped") is True
        assert result["conversations_rebuilt"] == 0
        db.close()

    def test_skip_rebuild_default_false(self, tmp_path):
        from src.data.db_manager import DatabaseManager
        from src.data.conversation_rebuild import rebuild_conversations

        db = DatabaseManager(tmp_path / "noskip_test.db")
        db.initialize()

        # Without skip_rebuild, it runs normally (returns 0 if no raw rows)
        result = rebuild_conversations(db)
        assert result.get("skipped") is None or result.get("skipped") is False
        db.close()


# ═══════════════════════════════════════════
#  PART B: COMBINED MULTI-SOURCE QUERIES
# ═══════════════════════════════════════════

class TestCombinedMultiSource:
    """Tests for cross-source query union in WarehouseQuery."""

    def test_all_sources_returns_combined(self, multi_source_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(multi_source_db)
        wq = WarehouseQuery(multi_source_db, registry)

        rows, total = wq.get_conversations_paged(source_id=None)
        assert total == 150  # 100 Zendesk + 50 Kodif

    def test_single_source_zendesk(self, multi_source_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(multi_source_db)
        wq = WarehouseQuery(multi_source_db, registry)

        rows, total = wq.get_conversations_paged(source_id="zendesk_default")
        assert total == 100

    def test_single_source_kodif(self, multi_source_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(multi_source_db)
        wq = WarehouseQuery(multi_source_db, registry)

        rows, total = wq.get_conversations_paged(source_id="kodif_chat")
        assert total == 50

    def test_trc_distribution_combined(self, multi_source_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(multi_source_db)
        wq = WarehouseQuery(multi_source_db, registry)

        dist = wq.get_trc_distribution()
        # 3 Zendesk TRCs + 2 Kodif TRCs = 5 total
        assert len(dist) == 5
        assert sum(dist.values()) == 150

    def test_source_name_in_paged_results(self, multi_source_db):
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(multi_source_db)
        wq = WarehouseQuery(multi_source_db, registry)

        rows, _ = wq.get_conversations_paged(limit=200)
        source_names = {r["source_name"] for r in rows}
        assert "Zendesk Support" in source_names
        assert "Kodif Chat" in source_names


# ═══════════════════════════════════════════
#  PART B: SOURCE SELECTORS ON ANALYTICS PAGES
# ═══════════════════════════════════════════

class TestSourceSelectorsOnPages:
    """Verify source selectors were added to analytics pages.

    Uses unittest.mock to avoid needing full DB for widget init.
    """

    @pytest.fixture(scope="class")
    def qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if not app:
            app = QApplication([])
        return app

    def _mock_db(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.db_path = ":memory:"
        db.conn = None
        db.get_prompts.return_value = []
        db.get_user_terms.return_value = []
        db.get_date_range.return_value = ("2026-01-01", "2026-03-31")
        db.get_trc_codes.return_value = []
        db.get_feedback_summary.return_value = []
        db.get_datasets.return_value = []
        return db

    def test_incidents_has_source_selector(self, qapp):
        from src.ui.pages.incidents_page import IncidentsPage
        page = IncidentsPage(self._mock_db())
        assert hasattr(page, "_source_selector")

    def test_trending_has_source_selector(self, qapp):
        from src.ui.pages.trending_topics import TrendingTopicsPage
        page = TrendingTopicsPage(self._mock_db())
        assert hasattr(page, "_source_selector")

    def test_ai_reports_has_source_selector(self, qapp):
        from src.ui.pages.ai_reports import AIReportsPage
        page = AIReportsPage(self._mock_db())
        assert hasattr(page, "_source_selector")

    def test_smart_reporting_has_source_selector(self, qapp):
        from src.ui.pages.smart_reporting import SmartReportingPage
        page = SmartReportingPage(self._mock_db())
        assert hasattr(page, "_source_selector")

    def test_guru_gap_has_source_selector(self, qapp):
        from src.ui.pages.guru_page import GuruPage
        page = GuruPage(self._mock_db())
        assert hasattr(page, "_gap_source_selector")

    def test_conversation_search_has_import_source(self, qapp):
        from src.ui.pages.conversation_search import ConversationSearchPage
        page = ConversationSearchPage(self._mock_db())
        assert hasattr(page, "_import_source_selector")


# ═══════════════════════════════════════════
#  PART C: GURU SOURCE AWARENESS
# ═══════════════════════════════════════════

class TestGuruSourceAwareness:
    """Tests for Guru friction pipeline source_id parameter."""

    def test_analyze_coverage_accepts_source_id(self):
        """Verify analyze_coverage signature accepts source_id."""
        import inspect
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        sig = inspect.signature(GuruFrictionPipeline.analyze_coverage)
        assert "source_id" in sig.parameters

    def test_get_active_friction_types_accepts_source_id(self):
        """Verify internal method accepts source_id."""
        import inspect
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        sig = inspect.signature(GuruFrictionPipeline._get_active_friction_types)
        assert "source_id" in sig.parameters


# ═══════════════════════════════════════════
#  PART D: CHAT TOOLS SOURCE AWARENESS
# ═══════════════════════════════════════════

class TestChatToolsSourceAwareness:
    """Tests for chat tools source_id passthrough."""

    def test_thread_tools_finds_zendesk_ticket(self, multi_source_db):
        """Thread tools can find Zendesk ticket across all sources."""
        from src.data.chat_tools.thread_tools import handle_read_thread

        result = handle_read_thread(
            multi_source_db,
            {"ticket_id": "Z-0001"},
            {},  # No filters — searches all sources
        )
        # Should find it (WarehouseQuery searches all sources)
        assert "error" not in result or "Not found" not in result.get("error", "")

    def test_thread_tools_finds_kodif_ticket(self, multi_source_db):
        """Thread tools can find Kodif ticket across all sources."""
        from src.data.chat_tools.thread_tools import handle_read_thread

        result = handle_read_thread(
            multi_source_db,
            {"ticket_id": "K-0001"},
            {},
        )
        assert "error" not in result or "Not found" not in result.get("error", "")

    def test_thread_tools_source_id_in_wq(self):
        """Verify WarehouseQuery.query_conversations_raw accepts source_id."""
        import inspect
        from src.data.warehouse_query import WarehouseQuery
        sig = inspect.signature(WarehouseQuery.query_conversations_raw)
        assert "source_id" in sig.parameters


# ═══════════════════════════════════════════
#  REGRESSION TESTS
# ═══════════════════════════════════════════

class TestStage5Regressions:
    """Verify Session 5 changes don't break existing functionality."""

    def test_existing_page_constants(self):
        from src.ui.main_window import MainWindow
        assert MainWindow.PAGE_CONVERSATIONS == 0
        assert MainWindow.PAGE_SETTINGS == 7
        assert MainWindow.PAGE_DATA_WAREHOUSE == 11

    def test_ingest_csv_backward_compat(self, tmp_path):
        """ingest_csv without source_config still works (Zendesk default)."""
        from src.data.db_manager import DatabaseManager

        db = DatabaseManager(tmp_path / "compat_test.db")
        db.initialize()

        # Create a minimal Zendesk CSV
        csv_path = tmp_path / "zendesk.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # Headers must match COLUMN_MAP keys (space-separated, not underscores)
            writer.writerow(["ticket id", "subject", "trc", "ticket status", "comment body", "author role", "created at"])
            writer.writerow(["T-001", "Test", "TRC-001", "closed", "Hello", "customer", "2026-03-15"])

        from src.data.csv_ingestion import ingest_csv
        stats = ingest_csv(csv_path, db)
        assert stats["tickets_created"] == 1
        db.close()

    def test_warehouse_query_backward_compat(self, multi_source_db):
        """get_conversations (non-paged) still works."""
        from src.data.source_registry import SourceRegistry
        from src.data.warehouse_query import WarehouseQuery
        registry = SourceRegistry(multi_source_db)
        wq = WarehouseQuery(multi_source_db, registry)

        rows = wq.get_conversations(limit=10)
        assert len(rows) > 0
        assert isinstance(rows[0], dict)

    def test_import_mode_enum_still_works(self):
        from src.data.import_mode import ImportMode
        assert ImportMode.INCREMENTAL is not None
        assert ImportMode.FULL_REFRESH is not None
