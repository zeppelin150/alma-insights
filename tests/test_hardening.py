"""
Alma Insights — Hardening Tests (H1-H9)

Tests for:
    - Task routing config + factory evolution (H1)
    - Call site migration (H2)
    - Scan ledger builder (H3)
    - Claude tool definitions + executor (H4)
    - Card graph schema + builder (H5)
    - Multi-agent friction analysis (H6)
    - Guru UI integration (H7)
    - Settings UI task routing section (H8)
"""

import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
import os

# ── Ensure project root on path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════
#  H1: Task Routing Config + Factory
# ═══════════════════════════════════════════

class TestTaskRouting(unittest.TestCase):
    """H1: Task routing config and build_client_for_task()."""

    def test_default_routes_defined(self):
        """Default route map covers the core task types + the enablement lanes."""
        from src.gemini.client_factory import _DEFAULT_ROUTES
        keys = set(_DEFAULT_ROUTES.keys())
        core = {
            "nlp_classification", "voc_analysis", "report_generation",
            "guru_analysis", "guru_content_generation",
            "watchlist_triage", "meta_analytics", "ab_comparison",
        }
        self.assertTrue(core.issubset(keys))
        # Enablement Workbench lanes (added with the live back-end build).
        enablement = {"enablement_card_gen", "enablement_extract", "enablement_triage",
                      "enablement_subtasks", "enablement_chat"}
        self.assertTrue(enablement.issubset(keys))

    def test_phi_tasks_default_to_gemini(self):
        """PHI-bearing tasks default to Gemini."""
        from src.gemini.client_factory import _DEFAULT_ROUTES
        phi_tasks = ["nlp_classification", "voc_analysis", "report_generation"]
        for task in phi_tasks:
            self.assertEqual(_DEFAULT_ROUTES[task], "gemini",
                             f"{task} should default to gemini")

    def test_ops_tasks_default_to_claude(self):
        """Ops tasks default to Claude."""
        from src.gemini.client_factory import _DEFAULT_ROUTES
        ops_tasks = ["guru_analysis", "guru_content_generation",
                      "watchlist_triage", "meta_analytics"]
        for task in ops_tasks:
            self.assertEqual(_DEFAULT_ROUTES[task], "claude",
                             f"{task} should default to claude")

    def test_resolve_provider_defaults(self):
        """resolve_provider_for_task returns defaults when no settings."""
        from src.gemini.client_factory import resolve_provider_for_task
        with patch("src.gemini.client_factory.get_task_routing") as mock_rt:
            mock_rt.return_value = {"override_all": "", "routes": {}}
            # Unknown task falls back to _DEFAULT_ROUTES then to gemini
            self.assertEqual(resolve_provider_for_task("guru_analysis"), "claude")
            self.assertEqual(resolve_provider_for_task("nlp_classification"), "gemini")

    def test_resolve_provider_override_all(self):
        """override_all forces all tasks to one provider."""
        from src.gemini.client_factory import resolve_provider_for_task
        with patch("src.gemini.client_factory.get_task_routing") as mock_rt:
            mock_rt.return_value = {"override_all": "gemini", "routes": {}}
            self.assertEqual(resolve_provider_for_task("guru_analysis"), "gemini")
            self.assertEqual(resolve_provider_for_task("watchlist_triage"), "gemini")

    def test_resolve_provider_custom_route(self):
        """Custom per-task route takes precedence over default."""
        from src.gemini.client_factory import resolve_provider_for_task
        with patch("src.gemini.client_factory.get_task_routing") as mock_rt:
            mock_rt.return_value = {
                "override_all": "",
                "routes": {"guru_analysis": "gemini"},
            }
            self.assertEqual(resolve_provider_for_task("guru_analysis"), "gemini")

    def test_build_client_for_task_gemini_path(self):
        """build_client_for_task returns Gemini client for PHI tasks."""
        from src.gemini.client_factory import build_client_for_task
        with patch("src.gemini.client_factory.resolve_provider_for_task", return_value="gemini"), \
             patch("src.gemini.client_factory._build_gemini_client_internal") as mock_gem:
            mock_gem.return_value = MagicMock()
            client = build_client_for_task("nlp_classification")
            mock_gem.assert_called_once_with(False)
            self.assertIsNotNone(client)

    def test_build_client_for_task_claude_fallback(self):
        """build_client_for_task falls back to Gemini if Claude unavailable."""
        from src.gemini.client_factory import build_client_for_task
        with patch("src.gemini.client_factory.resolve_provider_for_task", return_value="claude"), \
             patch("src.gemini.client_factory._build_claude_client_from_registry", return_value=None), \
             patch("src.gemini.client_factory._build_gemini_client_internal") as mock_gem:
            mock_gem.return_value = MagicMock()
            client = build_client_for_task("guru_analysis")
            mock_gem.assert_called_once_with(False)

    def test_build_client_for_task_claude_success(self):
        """build_client_for_task returns Claude client when available."""
        from src.gemini.client_factory import build_client_for_task
        mock_claude = MagicMock()
        with patch("src.gemini.client_factory.resolve_provider_for_task", return_value="claude"), \
             patch("src.gemini.client_factory._build_claude_client_from_registry", return_value=mock_claude):
            client = build_client_for_task("guru_analysis")
            self.assertIs(client, mock_claude)

    def test_get_task_routing_with_settings(self):
        """get_task_routing reads from settings manager."""
        from src.gemini.client_factory import get_task_routing
        mock_ai = {
            "task_routing": {
                "override_all": "claude",
                "routes": {"guru_analysis": "gemini"},
            }
        }
        with patch("src.data.settings_manager.get_section", return_value=mock_ai):
            routing = get_task_routing()
            self.assertEqual(routing["override_all"], "claude")
            self.assertEqual(routing["routes"]["guru_analysis"], "gemini")

    def test_backward_compat_build_client_for_model(self):
        """build_client_for_model still works (backward compat)."""
        from src.gemini.client_factory import build_client_for_model
        with patch("src.gemini.client_factory._build_gemini_client_internal") as mock_gem:
            mock_gem.return_value = MagicMock()
            client = build_client_for_model()
            self.assertIsNotNone(client)

    def test_backward_compat_build_gemini_client(self):
        """build_gemini_client shim still works."""
        from src.gemini.client_factory import build_gemini_client
        with patch("src.gemini.client_factory.build_client_for_model") as mock_bcm:
            mock_bcm.return_value = MagicMock()
            build_gemini_client(use_bridge=True)
            mock_bcm.assert_called_once_with(use_bridge=True)


# ═══════════════════════════════════════════
#  H2: Call Site Migration
# ═══════════════════════════════════════════

class TestCallSiteMigration(unittest.TestCase):
    """H2: Verify call sites use build_client_for_task."""

    def test_watchlist_uses_task_routing(self):
        """watchlist_engine imports build_client_for_task, not build_client_for_model."""
        source = Path(PROJECT_ROOT / "src" / "data" / "watchlist_engine.py").read_text(encoding="utf-8")
        self.assertIn("build_client_for_task", source)
        self.assertNotIn("build_client_for_model", source)

    def test_source_warehouse_uses_task_routing(self):
        """source_warehouse imports build_client_for_task."""
        source = Path(PROJECT_ROOT / "src" / "data" / "source_warehouse.py").read_text(encoding="utf-8")
        self.assertIn("build_client_for_task", source)
        self.assertNotIn("build_client_for_model", source)

    def test_ai_reports_uses_task_routing(self):
        """ai_reports page uses build_client_for_task."""
        source = Path(PROJECT_ROOT / "src" / "ui" / "pages" / "ai_reports.py").read_text(encoding="utf-8")
        self.assertIn("build_client_for_task", source)

    def test_ab_compare_uses_task_routing(self):
        """ab_compare page uses build_client_for_task."""
        source = Path(PROJECT_ROOT / "src" / "ui" / "pages" / "ab_compare.py").read_text(encoding="utf-8")
        self.assertIn("build_client_for_task", source)

    def test_smart_reporting_uses_task_routing(self):
        """smart_reporting page uses build_client_for_task."""
        source = Path(PROJECT_ROOT / "src" / "ui" / "pages" / "smart_reporting.py").read_text(encoding="utf-8")
        self.assertIn("build_client_for_task", source)


# ═══════════════════════════════════════════
#  H3: Scan Ledger Builder
# ═══════════════════════════════════════════

def _make_ledger_db():
    """Create an in-memory DB with the tables scan_ledger.py reads from."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE nlp_scan_runs (
            scan_id TEXT PRIMARY KEY, created_at TEXT, status TEXT,
            date_range_start TEXT, date_range_end TEXT, trc_filter TEXT,
            total_tickets INTEGER, total_batches INTEGER, completed_batches INTEGER,
            estimated_cost_usd REAL, actual_cost_usd REAL
        )
    """)
    conn.execute("""
        CREATE TABLE nlp_ticket_classifications (
            classification_id TEXT PRIMARY KEY, batch_id TEXT, scan_id TEXT,
            ticket_id TEXT, trc TEXT, sub_cluster TEXT,
            sub_cluster_confidence REAL, is_novel INTEGER DEFAULT 0,
            sentiment_intensity INTEGER, sentiment_polarity TEXT,
            friction_type TEXT, anomaly_flag TEXT, anomaly_reason TEXT,
            entities_json TEXT, key_phrases TEXT, root_cause_hint TEXT,
            summary TEXT, raw_classification TEXT, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE sub_patterns (
            pattern_id TEXT PRIMARY KEY, trc TEXT, label TEXT,
            description TEXT, friction_type TEXT, tier TEXT DEFAULT 'probationary',
            discovered_scan TEXT, discovered_at TEXT, last_seen_scan TEXT,
            last_seen_at TEXT, lifetime_tickets INTEGER DEFAULT 0,
            lifetime_scans INTEGER DEFAULT 0, merged_into TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE nlp_findings (
            finding_id TEXT PRIMARY KEY, scan_id TEXT, finding_type TEXT,
            scope TEXT, title TEXT, description TEXT, ticket_count INTEGER,
            pct_of_scanned REAL, avg_sentiment_intensity REAL,
            dominant_friction_type TEXT, top_trcs TEXT, top_sub_patterns TEXT,
            top_entities TEXT, date_concentration TEXT, temporal_trend TEXT,
            exemplar_ticket_ids TEXT, statistical_validation TEXT,
            baseline_comparison TEXT, impact_score REAL, created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE source_trc_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT DEFAULT 'zendesk', trc_code TEXT, day_bucket TEXT,
            count INTEGER DEFAULT 0, avg_sentiment REAL DEFAULT 0.0,
            UNIQUE(source, trc_code, day_bucket)
        )
    """)
    conn.execute("""
        CREATE TABLE watchlist_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE watchlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER, source TEXT, severity TEXT, title TEXT,
            summary TEXT, ticket_count INTEGER, ticket_ids TEXT,
            trc_code TEXT, status TEXT, llm_triage TEXT,
            llm_confidence REAL, created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT, resolved_by TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE guru_friction_coverage (
            friction_type TEXT, card_id TEXT, coverage_score REAL,
            gap_description TEXT, analyzed_at TEXT, scan_id TEXT,
            PRIMARY KEY(friction_type, card_id)
        )
    """)
    return conn


class _FakeDB:
    def __init__(self, conn):
        self.conn = conn
    def get_connection(self):
        return self.conn


class TestScanLedger(unittest.TestCase):
    """H3: Scan ledger builds PHI-free structured markdown."""

    def setUp(self):
        self.conn = _make_ledger_db()
        self.db = _FakeDB(self.conn)

        # Insert test scan
        self.conn.execute(
            "INSERT INTO nlp_scan_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("scan_001", "2026-03-10T10:00:00", "completed",
             "2026-02-01", "2026-03-01", None, 500, 20, 20, 5.0, 4.5),
        )
        # Insert classifications
        for i in range(10):
            trc = "AUTH" if i < 6 else "BILLING"
            self.conn.execute(
                "INSERT INTO nlp_ticket_classifications "
                "(classification_id, batch_id, scan_id, ticket_id, trc, "
                "is_novel, sentiment_intensity, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"cls_{i}", "b1", "scan_001", f"T{i}", trc,
                 1 if i < 3 else 0, 5 + i, "2026-03-10"),
            )
        # Insert sub_patterns
        self.conn.execute(
            "INSERT INTO sub_patterns "
            "(pattern_id, trc, label, friction_type, tier, "
            "discovered_scan, discovered_at, lifetime_tickets) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("p1", "AUTH", "Login Failure", "login_friction", "confirmed",
             "scan_001", "2026-03-10", 42),
        )
        # Insert finding
        self.conn.execute(
            "INSERT INTO nlp_findings "
            "(finding_id, scan_id, finding_type, title, ticket_count, "
            "impact_score, created_at) VALUES (?,?,?,?,?,?,?)",
            ("f1", "scan_001", "spike", "Auth spike detected", 15, 0.85,
             "2026-03-10"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_current_ledger_contains_scan_overview(self):
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        self.assertIn("scan_001", md)
        self.assertIn("500", md)  # total tickets
        self.assertIn("2026-02-01", md)

    def test_current_ledger_contains_classification_breakdown(self):
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        self.assertIn("Classification Breakdown", md)
        self.assertIn("AUTH", md)
        self.assertIn("BILLING", md)

    def test_current_ledger_contains_sub_patterns(self):
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        self.assertIn("Login Failure", md)
        self.assertIn("login_friction", md)

    def test_current_ledger_contains_findings(self):
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        self.assertIn("Auth spike detected", md)
        self.assertIn("0.85", md)

    def test_current_ledger_no_phi(self):
        """Ledger must not contain ticket text, subjects, or descriptions."""
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        # No ticket IDs in output (only aggregates)
        self.assertNotIn("T0", md)
        self.assertNotIn("T9", md)

    def test_current_ledger_missing_scan(self):
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "nonexistent")
        self.assertIn("No scan found", md)

    def test_historical_ledger_contains_scan_list(self):
        from src.data.scan_ledger import build_historical_ledger
        md = build_historical_ledger(self.db, num_scans=5)
        self.assertIn("Recent Scans", md)
        self.assertIn("scan_001", md)

    def test_historical_ledger_empty_db(self):
        """Historical ledger gracefully handles empty DB."""
        empty_conn = _make_ledger_db()
        empty_db = _FakeDB(empty_conn)
        from src.data.scan_ledger import build_historical_ledger
        md = build_historical_ledger(empty_db, num_scans=5)
        self.assertIn("No scan history", md)
        empty_conn.close()

    def test_historical_ledger_with_trc_daily(self):
        """Historical ledger includes TRC volume trends."""
        # Add some daily data
        self.conn.execute(
            "INSERT INTO source_trc_daily (source, trc_code, day_bucket, count, avg_sentiment) "
            "VALUES (?, ?, date('now'), ?, ?)",
            ("zendesk", "AUTH", 25, -0.3),
        )
        self.conn.commit()
        from src.data.scan_ledger import build_historical_ledger
        md = build_historical_ledger(self.db, num_scans=5)
        self.assertIn("Active Sub-Patterns", md)

    def test_guru_coverage_summary(self):
        """Current ledger includes guru coverage summary when data exists."""
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("login_friction", "card_1", 0.8, "Minor gap", "2026-03-10", "scan_001"),
        )
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("billing_friction", "card_2", 0.2, "Major gap", "2026-03-10", "scan_001"),
        )
        self.conn.commit()
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        self.assertIn("Guru Coverage Summary", md)
        self.assertIn("2", md)  # total friction types

    def test_active_alerts_in_ledger(self):
        """Current ledger includes active watchlist alerts."""
        self.conn.execute(
            "INSERT INTO watchlist_rules (id, name) VALUES (1, 'Site Outage')"
        )
        self.conn.execute(
            "INSERT INTO watchlist_alerts "
            "(rule_id, source, severity, title, summary, ticket_count, "
            "ticket_ids, trc_code, status) "
            "VALUES (1, 'zendesk', 'incident', 'Site Outage', "
            "'Outage detected', 5, '1,2,3', 'AUTH', 'open')"
        )
        self.conn.commit()
        from src.data.scan_ledger import build_current_ledger
        md = build_current_ledger(self.db, "scan_001")
        self.assertIn("Active Watchlist Alerts", md)
        self.assertIn("Site Outage", md)


# ═══════════════════════════════════════════
#  H8: Settings UI Task Routing
# ═══════════════════════════════════════════

class TestSettingsTaskRouting(unittest.TestCase):
    """H8: Task routing section exists in settings page."""

    def test_settings_page_has_task_routing_method(self):
        """SettingsPage has _build_task_routing_section method."""
        from src.ui.pages.settings_page import SettingsPage
        self.assertTrue(hasattr(SettingsPage, '_build_task_routing_section'))

    def test_settings_page_has_route_change_handler(self):
        """SettingsPage has _on_task_route_changed handler."""
        from src.ui.pages.settings_page import SettingsPage
        self.assertTrue(hasattr(SettingsPage, '_on_task_route_changed'))

    def test_task_routing_in_settings_yaml(self):
        """settings.yaml contains ai.task_routing section."""
        from src.data.settings_manager import get_section
        ai_cfg = get_section("ai", {})
        self.assertIn("task_routing", ai_cfg)
        routing = ai_cfg["task_routing"]
        self.assertIn("override_all", routing)
        self.assertIn("routes", routing)

    def test_task_routing_has_all_tasks(self):
        """settings.yaml task_routing.routes ships the core task types.

        The enablement_* lanes are intentionally NOT in the settings routes
        table — they default via _DEFAULT_ROUTES and the enablement.provider
        toggle (see resolve_provider_for_task), so the operator flips them with
        one control instead of per-lane rows.
        """
        from src.data.settings_manager import get_section
        ai_cfg = get_section("ai", {})
        routes = ai_cfg.get("task_routing", {}).get("routes", {})
        core = {
            "nlp_classification", "voc_analysis", "report_generation",
            "guru_analysis", "guru_content_generation",
            "watchlist_triage", "meta_analytics", "ab_comparison",
        }
        for task in core:
            self.assertIn(task, routes,
                          f"Task '{task}' missing from settings routes")


# ═══════════════════════════════════════════
#  H4: Claude Tool Definitions + Executor
# ═══════════════════════════════════════════

def _make_tools_db():
    """In-memory DB with all tables claude_tools.py queries."""
    conn = _make_ledger_db()  # reuse H3 schema (scan_runs, classifications, etc.)
    # Add guru_articles
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guru_articles (
            card_id TEXT PRIMARY KEY, collection_id TEXT,
            collection_name TEXT, title TEXT, content_hash TEXT,
            last_synced_at TEXT, friction_score REAL, status TEXT
        )
    """)
    # Add guru_effectiveness
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guru_effectiveness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT, friction_type TEXT, measurement_date TEXT,
            pre_volume REAL, post_volume REAL, delta_pct REAL,
            is_significant INTEGER DEFAULT 0,
            pre_window_days INTEGER DEFAULT 14,
            post_window_days INTEGER DEFAULT 14
        )
    """)
    # Add guru_content_drafts
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guru_content_drafts (
            id TEXT PRIMARY KEY, card_id TEXT, friction_type TEXT,
            draft_type TEXT, title TEXT, content TEXT,
            source_tickets TEXT, status TEXT DEFAULT 'pending',
            approved_by TEXT, pushed_at TEXT
        )
    """)
    return conn


class TestClaudeToolDefinitions(unittest.TestCase):
    """H4: Tool definitions are valid and complete."""

    def test_eight_tools_defined(self):
        # The 8 core H4 tools must always be present; the tool set has since grown
        # (query_issues, audit_tag_correlation, enablement doc/drive/asana tools),
        # so assert the core subset + a lower bound rather than an exact count.
        from src.llm.claude_tools import TOOL_DEFINITIONS
        names = {t["name"] for t in TOOL_DEFINITIONS}
        core = {"read_scan_summary", "get_friction_gaps", "get_sub_pattern_trends",
                "search_guru_cards", "get_card_detail", "get_card_relationships",
                "get_effectiveness_report", "propose_guru_edit"}
        self.assertTrue(core.issubset(names), f"missing core tools: {core - names}")
        self.assertGreaterEqual(len(TOOL_DEFINITIONS), 8)

    def test_all_tools_have_required_fields(self):
        from src.llm.claude_tools import TOOL_DEFINITIONS
        for tool in TOOL_DEFINITIONS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("input_schema", tool)
            self.assertEqual(tool["input_schema"]["type"], "object")

    def test_tool_names_match_dispatch(self):
        from src.llm.claude_tools import TOOL_DEFINITIONS, _DISPATCH
        defined = {t["name"] for t in TOOL_DEFINITIONS}
        dispatched = set(_DISPATCH.keys())
        self.assertEqual(defined, dispatched)

    def test_unknown_tool_returns_error(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool("nonexistent_tool", {}, None))
        self.assertIn("error", result)


class TestClaudeToolExecution(unittest.TestCase):
    """H4: Tool execution with in-memory DB."""

    def setUp(self):
        self.conn = _make_tools_db()
        self.db = _FakeDB(self.conn)

        # Seed data
        self.conn.execute(
            "INSERT INTO nlp_scan_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("scan_t01", "2026-03-10T10:00:00", "completed",
             "2026-02-01", "2026-03-01", None, 200, 8, 8, 2.0, 1.8),
        )
        self.conn.execute(
            "INSERT INTO sub_patterns "
            "(pattern_id, trc, label, friction_type, tier, "
            "discovered_scan, discovered_at, last_seen_at, lifetime_tickets) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("p_t1", "AUTH", "Login Loop", "login_loop", "confirmed",
             "scan_t01", "2026-03-01", "2026-03-10", 35),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("card_t1", "coll_1", "Support Docs", "How to Reset Password",
             "abc123", "2026-03-10", 0.7, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("card_t2", "coll_1", "Support Docs", "Billing FAQ",
             "def456", "2026-03-10", 0.5, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("login_loop", "card_t1", 0.6, "Partial coverage", "2026-03-10", "scan_t01"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_read_scan_summary_current(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "read_scan_summary", {"mode": "current", "scan_id": "scan_t01"}, self.db
        ))
        self.assertIn("ledger", result)
        self.assertIn("scan_t01", result["ledger"])

    def test_read_scan_summary_historical(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "read_scan_summary", {"mode": "historical"}, self.db
        ))
        self.assertIn("ledger", result)
        self.assertIn("Recent Scans", result["ledger"])

    def test_read_scan_summary_latest_fallback(self):
        """mode=current without scan_id uses latest scan."""
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "read_scan_summary", {"mode": "current"}, self.db
        ))
        self.assertIn("ledger", result)
        self.assertIn("scan_t01", result["ledger"])

    def test_get_sub_pattern_trends(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "get_sub_pattern_trends", {"limit": 10}, self.db
        ))
        self.assertIn("patterns", result)
        self.assertGreater(len(result["patterns"]), 0)
        self.assertEqual(result["patterns"][0]["label"], "Login Loop")

    def test_get_sub_pattern_trends_trc_filter(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "get_sub_pattern_trends", {"trc_filter": "BILLING", "limit": 10}, self.db
        ))
        self.assertEqual(result["count"], 0)

    def test_search_guru_cards(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "search_guru_cards", {"query": "Password"}, self.db
        ))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["cards"][0]["title"], "How to Reset Password")

    def test_get_card_detail(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "get_card_detail", {"card_id": "card_t1"}, self.db
        ))
        self.assertEqual(result["title"], "How to Reset Password")
        self.assertEqual(len(result["coverage"]), 1)

    def test_get_card_detail_not_found(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "get_card_detail", {"card_id": "nonexistent"}, self.db
        ))
        self.assertIn("error", result)

    def test_get_card_relationships(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "get_card_relationships", {"card_id": "card_t1"}, self.db
        ))
        self.assertIn("siblings", result)
        # card_t2 is in the same collection
        sibling_ids = [s["card_id"] for s in result["siblings"]]
        self.assertIn("card_t2", sibling_ids)

    def test_propose_guru_edit(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "propose_guru_edit",
            {
                "card_id": "card_t1",
                "friction_type": "login_loop",
                "draft_content": "Updated instructions for login resets.",
            },
            self.db,
        ))
        self.assertIn("draft_id", result)
        self.assertEqual(result["status"], "pending")
        # Verify draft exists in DB
        row = self.conn.execute(
            "SELECT status FROM guru_content_drafts WHERE id = ?",
            (result["draft_id"],)
        ).fetchone()
        self.assertEqual(row[0], "pending")

    def test_propose_guru_edit_missing_card(self):
        import json
        from src.llm.claude_tools import execute_tool
        result = json.loads(execute_tool(
            "propose_guru_edit",
            {"card_id": "no_card", "friction_type": "x", "draft_content": "y"},
            self.db,
        ))
        self.assertIn("error", result)


# ═══════════════════════════════════════════
#  H5: Card Graph Schema + Builder
# ═══════════════════════════════════════════

def _make_graph_db():
    """In-memory DB with guru_articles + graph tables for H5 tests."""
    conn = sqlite3.connect(":memory:")

    # guru_articles (source data)
    conn.execute("""
        CREATE TABLE guru_articles (
            card_id TEXT PRIMARY KEY, collection_id TEXT,
            collection_name TEXT, title TEXT, content_hash TEXT,
            last_synced_at TEXT, friction_score REAL, status TEXT
        )
    """)
    # guru_friction_coverage (for shared_friction edges)
    conn.execute("""
        CREATE TABLE guru_friction_coverage (
            friction_type TEXT, card_id TEXT, coverage_score REAL,
            gap_description TEXT, analyzed_at TEXT, scan_id TEXT,
            PRIMARY KEY(friction_type, card_id)
        )
    """)
    # Card graph tables (migration 004)
    conn.execute("""
        CREATE TABLE guru_card_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL, target_id TEXT NOT NULL,
            rel_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, target_id, rel_type)
        )
    """)
    conn.execute("""
        CREATE TABLE guru_card_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL, domain TEXT NOT NULL,
            source TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(card_id, domain, source)
        )
    """)
    return conn


class TestGuruGraphBuilder(unittest.TestCase):
    """H5: Card graph builder creates edges and domains."""

    def setUp(self):
        self.conn = _make_graph_db()
        self.db = _FakeDB(self.conn)

        # Seed 4 cards across 2 collections
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("c1", "coll_A", "Billing Help", "How to pay your bill",
             "h1", "2026-03-10", 0.0, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("c2", "coll_A", "Billing Help", "Refund process",
             "h2", "2026-03-10", 0.0, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("c3", "coll_B", "Account Setup", "How to reset your password",
             "h3", "2026-03-10", 0.0, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("c4", "coll_B", "Account Setup", "Login troubleshooting guide",
             "h4", "2026-03-10", 0.0, "active"),
        )
        # Shared friction: c1 and c3 both cover "payment_friction"
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("payment_friction", "c1", 0.8, "", "2026-03-10", "s1"),
        )
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("payment_friction", "c3", 0.3, "", "2026-03-10", "s1"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_rebuild_creates_collection_edges(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        stats = builder.rebuild()
        self.assertGreater(stats["edge_count"], 0)

        # c1 and c2 are in same collection
        edge = self.conn.execute(
            "SELECT 1 FROM guru_card_graph "
            "WHERE source_id='c1' AND target_id='c2' AND rel_type='same_collection'"
        ).fetchone()
        self.assertIsNotNone(edge)

    def test_rebuild_creates_shared_friction_edges(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        edge = self.conn.execute(
            "SELECT 1 FROM guru_card_graph "
            "WHERE rel_type='shared_friction' "
            "AND ((source_id='c1' AND target_id='c3') OR (source_id='c3' AND target_id='c1'))"
        ).fetchone()
        self.assertIsNotNone(edge)

    def test_rebuild_creates_domain_tags(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        stats = builder.rebuild()
        self.assertGreater(stats["domain_tag_count"], 0)

        # Collection-based: "Billing Help" → "billing_help" domain
        tag = self.conn.execute(
            "SELECT domain FROM guru_card_domains WHERE card_id='c1' AND source='collection'"
        ).fetchone()
        self.assertIsNotNone(tag)
        self.assertEqual(tag[0], "billing_help")

    def test_keyword_domain_tagging(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        # "How to pay your bill" should match "billing" keyword domain
        tags = self.conn.execute(
            "SELECT domain FROM guru_card_domains WHERE card_id='c1' AND source='keyword'"
        ).fetchall()
        domains = [t[0] for t in tags]
        self.assertIn("billing", domains)

    def test_constellation_traversal(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        # From c1: should reach c2 (same_collection) and c3 (shared_friction)
        constellation = builder.get_constellation("c1", max_hops=1)
        card_ids = {c["card_id"] for c in constellation}
        self.assertIn("c2", card_ids)
        self.assertIn("c3", card_ids)

    def test_constellation_hop_distance(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        constellation = builder.get_constellation("c1", max_hops=2)
        for entry in constellation:
            self.assertIn("hop_distance", entry)
            self.assertLessEqual(entry["hop_distance"], 2)

    def test_constellation_two_hops(self):
        """2-hop from c1 should reach c4 via c3."""
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        constellation = builder.get_constellation("c1", max_hops=2)
        card_ids = {c["card_id"] for c in constellation}
        self.assertIn("c4", card_ids)  # c1→c3 (shared_friction) → c4 (same_collection)

    def test_get_domain(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        billing_cards = builder.get_domain("billing")
        self.assertGreater(len(billing_cards), 0)

    def test_list_domains(self):
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        builder.rebuild()

        domains = builder.list_domains()
        self.assertGreater(len(domains), 0)
        domain_names = [d["domain"] for d in domains]
        self.assertIn("billing_help", domain_names)  # collection-based

    def test_rebuild_is_idempotent(self):
        """Rebuilding twice produces same edge count."""
        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(self.db)
        stats1 = builder.rebuild()
        stats2 = builder.rebuild()
        self.assertEqual(stats1["edge_count"], stats2["edge_count"])
        self.assertEqual(stats1["domain_tag_count"], stats2["domain_tag_count"])


class TestCardGraphMigration(unittest.TestCase):
    """H5: Migration file exists and is valid SQL."""

    def test_migration_file_exists(self):
        path = PROJECT_ROOT / "migrations" / "004_guru_card_graph.sql"
        self.assertTrue(path.exists())

    def test_migration_creates_tables(self):
        """Migration SQL creates both tables without error."""
        conn = sqlite3.connect(":memory:")
        sql = (PROJECT_ROOT / "migrations" / "004_guru_card_graph.sql").read_text(
            encoding="utf-8"
        )
        conn.executescript(sql)
        # Verify tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        self.assertIn("guru_card_graph", table_names)
        self.assertIn("guru_card_domains", table_names)
        conn.close()

    def test_migration_enforces_constraints(self):
        """Duplicate edges are rejected by UNIQUE constraint."""
        conn = sqlite3.connect(":memory:")
        sql = (PROJECT_ROOT / "migrations" / "004_guru_card_graph.sql").read_text(
            encoding="utf-8"
        )
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO guru_card_graph (source_id, target_id, rel_type) "
            "VALUES ('a', 'b', 'same_collection')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO guru_card_graph (source_id, target_id, rel_type) "
                "VALUES ('a', 'b', 'same_collection')"
            )
        conn.close()

    def test_migration_validates_rel_type(self):
        """CHECK constraint rejects invalid rel_type."""
        conn = sqlite3.connect(":memory:")
        sql = (PROJECT_ROOT / "migrations" / "004_guru_card_graph.sql").read_text(
            encoding="utf-8"
        )
        conn.executescript(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO guru_card_graph (source_id, target_id, rel_type) "
                "VALUES ('a', 'b', 'invalid_type')"
            )
        conn.close()


# ═══════════════════════════════════════════
#  H6: Multi-Agent Friction Analysis
# ═══════════════════════════════════════════

def _make_deep_analysis_db():
    """In-memory DB for deep analysis tests."""
    conn = _make_graph_db()  # has guru_articles, guru_friction_coverage, graph tables

    # Add sub_patterns
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sub_patterns (
            pattern_id TEXT PRIMARY KEY, trc TEXT, label TEXT,
            description TEXT, friction_type TEXT, tier TEXT DEFAULT 'probationary',
            discovered_scan TEXT, discovered_at TEXT, last_seen_scan TEXT,
            last_seen_at TEXT, lifetime_tickets INTEGER DEFAULT 0,
            lifetime_scans INTEGER DEFAULT 0, merged_into TEXT
        )
    """)
    # Add guru_content_drafts (for propose_guru_edit)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS guru_content_drafts (
            id TEXT PRIMARY KEY, card_id TEXT, friction_type TEXT,
            draft_type TEXT, title TEXT, content TEXT,
            source_tickets TEXT, status TEXT DEFAULT 'pending',
            approved_by TEXT, pushed_at TEXT
        )
    """)
    return conn


class TestDeepFrictionAnalysis(unittest.TestCase):
    """H6: Multi-agent deep friction analysis pipeline."""

    def setUp(self):
        self.conn = _make_deep_analysis_db()
        self.db = _FakeDB(self.conn)

        # Seed cards and coverage
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("dc1", "coll_X", "Support", "Login Help Guide",
             "hx1", "2026-03-10", 0.0, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("dc2", "coll_X", "Support", "Password Reset Steps",
             "hx2", "2026-03-10", 0.0, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("dc3", "coll_Y", "Billing", "Billing Overview",
             "hx3", "2026-03-10", 0.0, "active"),
        )
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("login_friction", "dc1", 0.6, "Partial coverage", "2026-03-10", "s1"),
        )
        self.conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("login_friction", "dc2", 0.3, "Missing MFA steps", "2026-03-10", "s1"),
        )
        # Build graph edges
        self.conn.execute(
            "INSERT INTO guru_card_graph (source_id, target_id, rel_type, weight) "
            "VALUES ('dc1', 'dc2', 'same_collection', 0.5)"
        )
        self.conn.commit()

        # Create a mock GuruClient
        from unittest.mock import MagicMock
        mock_client = MagicMock()
        mock_client.list_cards.return_value = []

        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        self.pipeline = GuruFrictionPipeline(self.db, mock_client)

    def tearDown(self):
        self.conn.close()

    def test_analyze_deep_returns_dict(self):
        result = self.pipeline.analyze_friction_deep("login_friction")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["friction_type"], "login_friction")

    def test_analyze_deep_finds_seed_card(self):
        result = self.pipeline.analyze_friction_deep("login_friction")
        self.assertGreater(result["cards_analyzed"], 0)
        self.assertIn("search", result["phases_completed"])

    def test_analyze_deep_generates_flow_map(self):
        result = self.pipeline.analyze_friction_deep("login_friction")
        self.assertIn("login_friction", result["flow_map_md"])

    def test_analyze_deep_returns_priorities(self):
        result = self.pipeline.analyze_friction_deep("login_friction")
        # dc2 has coverage 0.3 → should be flagged
        if result["rewrite_priorities"]:
            self.assertTrue(
                any(p["card_id"] == "dc2" for p in result["rewrite_priorities"])
            )

    def test_analyze_deep_returns_constellation(self):
        result = self.pipeline.analyze_friction_deep("login_friction")
        self.assertIsInstance(result["constellation"], list)

    def test_analyze_deep_no_matching_card(self):
        result = self.pipeline.analyze_friction_deep("nonexistent_type")
        self.assertIn("No Guru cards found", result["flow_map_md"])

    def test_analyze_deep_with_progress_cb(self):
        messages = []
        result = self.pipeline.analyze_friction_deep(
            "login_friction", progress_cb=messages.append
        )
        self.assertGreater(len(messages), 0)
        self.assertTrue(any("Phase 1" in m for m in messages))

    def test_search_agent_returns_seed(self):
        constellation, seed_id = self.pipeline._search_agent("login_friction")
        self.assertEqual(seed_id, "dc1")  # highest coverage

    def test_per_card_analysis_agent(self):
        constellation, seed_id = self.pipeline._search_agent("login_friction")
        analyses = self.pipeline._per_card_analysis_agent(
            "login_friction", seed_id, constellation, None
        )
        self.assertGreater(len(analyses), 0)
        seed = next((a for a in analyses if a["is_seed"]), None)
        self.assertIsNotNone(seed)
        self.assertEqual(seed["card_id"], "dc1")


# ═══════════════════════════════════════════
#  H7: Guru UI Integration
# ═══════════════════════════════════════════

class TestGuruUIIntegration(unittest.TestCase):
    """H7: Guru page has deep analysis UI elements."""

    def test_guru_page_has_analyze_handler(self):
        from src.ui.pages.guru_page import GuruPage
        self.assertTrue(hasattr(GuruPage, '_on_analyze_friction_deep'))

    def test_guru_page_has_deep_analysis_frame(self):
        """GuruPage._build_gap_tab creates the analysis frame attribute."""
        # We can't instantiate the full page without QApplication,
        # but we can check the method exists
        from src.ui.pages.guru_page import GuruPage
        import inspect
        source = inspect.getsource(GuruPage._build_gap_tab)
        self.assertIn("_deep_analysis_frame", source)
        self.assertIn("_deep_analysis_text", source)

    def test_analyze_friction_button_exists(self):
        """Gap tab has Analyze Friction button."""
        from src.ui.pages.guru_page import GuruPage
        import inspect
        source = inspect.getsource(GuruPage._build_gap_tab)
        self.assertIn("Analyze Friction", source)

    def test_flow_map_display_uses_markdown(self):
        """Handler uses setMarkdown for flow map display."""
        from src.ui.pages.guru_page import GuruPage
        import inspect
        source = inspect.getsource(GuruPage._on_analyze_friction_deep)
        self.assertIn("setMarkdown", source)


# ═══════════════════════════════════════════
#  H9: Integration Tests
# ═══════════════════════════════════════════

class TestHardeningIntegration(unittest.TestCase):
    """H9: Cross-cutting integration tests."""

    def test_claude_tools_use_scan_ledger(self):
        """read_scan_summary tool uses scan_ledger module."""
        from src.llm.claude_tools import _read_scan_summary
        import inspect
        source = inspect.getsource(_read_scan_summary)
        self.assertIn("scan_ledger", source)

    def test_deep_analysis_uses_graph_builder(self):
        """analyze_friction_deep imports GuruGraphBuilder."""
        from src.data.guru_friction_pipeline import GuruFrictionPipeline
        import inspect
        source = inspect.getsource(GuruFrictionPipeline._search_agent)
        self.assertIn("GuruGraphBuilder", source)

    def test_task_routing_covers_guru_tasks(self):
        """Task routing has entries for all guru-related tasks."""
        from src.gemini.client_factory import _DEFAULT_ROUTES
        guru_tasks = ["guru_analysis", "guru_content_generation"]
        for task in guru_tasks:
            self.assertIn(task, _DEFAULT_ROUTES)
            self.assertEqual(_DEFAULT_ROUTES[task], "claude")

    def test_full_tool_definitions_valid_json_schema(self):
        """All tool input_schema have valid JSON Schema structure."""
        from src.llm.claude_tools import TOOL_DEFINITIONS
        for tool in TOOL_DEFINITIONS:
            schema = tool["input_schema"]
            self.assertEqual(schema["type"], "object")
            self.assertIn("properties", schema)

    def test_graph_builder_and_deep_analysis_compatible(self):
        """Graph builder output format matches deep analysis expectations."""
        conn = _make_deep_analysis_db()
        db = _FakeDB(conn)

        # Seed minimal data
        conn.execute(
            "INSERT INTO guru_articles VALUES (?,?,?,?,?,?,?,?)",
            ("compat_1", "coll_Z", "Test Coll", "Test Card",
             "hz1", "2026-03-10", 0.0, "active"),
        )
        conn.execute(
            "INSERT INTO guru_friction_coverage VALUES (?,?,?,?,?,?)",
            ("test_friction", "compat_1", 0.4, "Test gap", "2026-03-10", "s1"),
        )
        conn.commit()

        from src.data.guru_graph_builder import GuruGraphBuilder
        builder = GuruGraphBuilder(db)
        builder.rebuild()

        constellation = builder.get_constellation("compat_1", max_hops=1)
        # Constellation entries must have keys expected by deep analysis
        for c in constellation:
            self.assertIn("card_id", c)
            self.assertIn("title", c)
            self.assertIn("hop_distance", c)
            self.assertIn("rel_types", c)
        conn.close()


if __name__ == "__main__":
    unittest.main()
