"""
Phase 5.5A — Reporting Foundation Unit Tests

Tests:
  1. MarkdownViewer: headings, tables, code blocks, inline formatting
  2. TechSummaryBuilder: aggregation queries, formatting
  3. AnalystReportFormatter: all 4 report types
  4. Report schedules CRUD: create/read/update/delete
  5. ClientFactory: build_gemini_client() basic paths
"""

import json
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock


# ══════════════════════════════════════════════════════════════════════
# 1. MarkdownViewer (library-based — no PySide6 needed for conversion tests)
# ══════════════════════════════════════════════════════════════════════

class TestMarkdownConversion(unittest.TestCase):
    """Test the markdown library → HTML conversion."""

    def setUp(self):
        from src.ui.widgets.markdown_viewer import _md_to_html
        self._md_to_html = _md_to_html

    def test_headings(self):
        html = self._md_to_html("# Title\n## Subtitle\n### Section")
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<h2>Subtitle</h2>", html)
        self.assertIn("<h3>Section</h3>", html)

    def test_bold_italic(self):
        html = self._md_to_html("**bold** and *italic* and ***both***")
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)
        self.assertIn("<strong><em>both</em></strong>", html)

    def test_inline_code(self):
        html = self._md_to_html("Use `foo()` here")
        self.assertIn("<code>foo()</code>", html)

    def test_fenced_code_block(self):
        md = "```python\ndef hello():\n    pass\n```"
        html = self._md_to_html(md)
        self.assertIn("<pre>", html)
        self.assertIn("</pre>", html)
        self.assertIn("def hello():", html)

    def test_table(self):
        md = "| Name | Value |\n|------|-------|\n| A | 1 |\n| B | 2 |"
        html = self._md_to_html(md)
        self.assertIn("<table>", html)
        self.assertIn("<th>Name</th>", html)
        self.assertIn("<td>A</td>", html)
        self.assertIn("<td>2</td>", html)

    def test_unordered_list(self):
        md = "- one\n- two\n- three"
        html = self._md_to_html(md)
        self.assertIn("<ul>", html)
        self.assertIn("<li>one</li>", html)

    def test_ordered_list(self):
        md = "1. first\n2. second"
        html = self._md_to_html(md)
        self.assertIn("<ol>", html)
        self.assertIn("<li>first</li>", html)

    def test_blockquote(self):
        md = "> This is a quote"
        html = self._md_to_html(md)
        self.assertIn("<blockquote>", html)
        self.assertIn("This is a quote", html)

    def test_horizontal_rule(self):
        html = self._md_to_html("---")
        self.assertTrue("<hr>" in html or "<hr />" in html or "<hr/>" in html)

    def test_link(self):
        md = "[click here](https://example.com)"
        html = self._md_to_html(md)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("click here", html)

    def test_voc_style_report(self):
        """Simulate a 7-section VOC-style markdown report."""
        md = """## Executive Summary

**Key finding**: Volume increased 15% with critical friction in billing.

## Volume & Trend Analysis

| Metric | Value |
|--------|-------|
| Total Tickets | 1,234 |
| Critical | 89 |

## Sentiment Analysis

- Overall sentiment: **Negative** (62%)
- Top friction: `billing_errors`

## Root Cause Analysis

> Multiple TRCs share a common root cause in payment processing timeouts.

### Shared Root Causes

1. Payment gateway timeout
2. Duplicate charge detection

## Recommendations

- Implement retry logic for payment gateway
- Add duplicate charge guard

---

*Report generated at 2025-03-09 14:30:00*"""

        html = self._md_to_html(md)
        self.assertIn("<h2>Executive Summary</h2>", html)
        self.assertIn("Volume", html)
        self.assertIn("Trend Analysis", html)
        self.assertIn("<table>", html)
        self.assertIn("<th>Metric</th>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<blockquote>", html)
        self.assertIn("<ol>", html)
        self.assertTrue("<hr>" in html or "<hr />" in html or "<hr/>" in html)


class TestMarkdownStyles(unittest.TestCase):
    """Test the CSS wrapper uses Alma design tokens."""

    def test_styles_include_alma_colors(self):
        from src.ui.widgets.markdown_viewer import _wrap_with_styles
        from src.ui.theme import ALMA_GREEN_DARK, ALMA_CREAM
        html = _wrap_with_styles("<h1>Test</h1>")
        self.assertIn(ALMA_GREEN_DARK, html)
        self.assertIn(ALMA_CREAM, html)
        self.assertIn("<style>", html)


# ══════════════════════════════════════════════════════════════════════
# 2. TechSummaryBuilder
# ══════════════════════════════════════════════════════════════════════

class TestTechSummaryBuilder(unittest.TestCase):
    """Test tech summary aggregation and formatting."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._insert_test_data()
        # Mock db object
        self.db = MagicMock()
        self.db.conn = self.conn

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE gemini_usage (
                id INTEGER PRIMARY KEY, date TEXT, hour INTEGER,
                source TEXT, scan_id TEXT, tokens_in INTEGER,
                tokens_out INTEGER, cost_usd REAL, api_calls INTEGER DEFAULT 1,
                model TEXT DEFAULT 'gemini-2.5-flash', created_at TEXT
            );
            CREATE TABLE nlp_scan_runs (
                scan_id TEXT PRIMARY KEY, created_at TEXT, status TEXT,
                date_range_start TEXT, date_range_end TEXT, dataset_id INTEGER,
                batches_total INTEGER, batches_completed INTEGER,
                tickets_total INTEGER, tickets_classified INTEGER,
                findings_count INTEGER, critical_findings INTEGER,
                notes TEXT, error_log TEXT, config_snapshot TEXT, completed_at TEXT
            );
            CREATE TABLE nlp_batches (
                batch_id TEXT PRIMARY KEY, scan_id TEXT, batch_number INTEGER,
                trc TEXT, trc_chunk INTEGER, ticket_count INTEGER,
                model TEXT, status TEXT, prompt_tokens INTEGER,
                completion_tokens INTEGER, total_tokens INTEGER,
                cost_usd REAL, error_log TEXT, created_at TEXT, completed_at TEXT
            );
            CREATE TABLE scan_events (
                id INTEGER PRIMARY KEY, scan_id TEXT, timestamp TEXT,
                event_type TEXT, status TEXT, message TEXT,
                duration_ms INTEGER, metadata_json TEXT
            );
            CREATE TABLE probe_history (
                id INTEGER PRIMARY KEY, scan_id TEXT, bridge_index INTEGER,
                probe_type TEXT, timestamp TEXT, status TEXT,
                latency_ms INTEGER, error_message TEXT, created_at TEXT
            );
        """)

    def _insert_test_data(self):
        scan_id = "test-scan-001"
        # Gemini usage: 3 API calls
        for i in range(3):
            self.conn.execute(
                "INSERT INTO gemini_usage (date, source, scan_id, tokens_in, tokens_out, "
                "cost_usd, api_calls, model, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("2025-03-09", "nlp_scan", scan_id, 50000, 5000,
                 0.01, 1, "gemini-2.5-flash", "2025-03-09T10:00:00")
            )
        # Scan run
        self.conn.execute(
            "INSERT INTO nlp_scan_runs (scan_id, created_at, status, date_range_start, "
            "date_range_end, batches_total, batches_completed, tickets_total, "
            "tickets_classified, findings_count, critical_findings, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (scan_id, "2025-03-09T10:00:00", "completed", "2025-03-01",
             "2025-03-09", 10, 10, 500, 495, 42, 5, "2025-03-09T10:15:00")
        )
        # Batches
        for i, trc in enumerate(["billing", "billing", "shipping"]):
            self.conn.execute(
                "INSERT INTO nlp_batches (batch_id, scan_id, batch_number, trc, "
                "ticket_count, status, created_at, completed_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"b-{i}", scan_id, i, trc, 50, "completed",
                 "2025-03-09T10:01:00", "2025-03-09T10:02:00")
            )
        # Scan events
        self.conn.execute(
            "INSERT INTO scan_events (scan_id, timestamp, event_type, status, "
            "message, duration_ms) VALUES (?,?,?,?,?,?)",
            (scan_id, "2025-03-09T10:00:01", "preflight", "ok", "Preflight complete", 1200)
        )
        # Probes
        for status in ["ok", "ok", "error"]:
            self.conn.execute(
                "INSERT INTO probe_history (scan_id, bridge_index, probe_type, "
                "timestamp, status, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                (scan_id, 0, "canary", "2025-03-09T10:00:00", status,
                 350 if status == "ok" else None, "2025-03-09T10:00:00")
            )
        self.conn.commit()

    def test_build_tech_summary_with_scan(self):
        from src.data.tech_summary_builder import build_tech_summary
        summary = build_tech_summary(self.db, scan_id="test-scan-001")

        # Gemini usage
        self.assertEqual(summary["gemini_usage"]["tokens_in"], 150000)
        self.assertEqual(summary["gemini_usage"]["tokens_out"], 15000)
        self.assertEqual(summary["gemini_usage"]["api_calls"], 3)

        # Scan stats
        self.assertIsNotNone(summary["scan_stats"])
        self.assertEqual(summary["scan_stats"]["batches_total"], 10)
        self.assertEqual(summary["scan_stats"]["tickets_classified"], 495)

        # Batch breakdown
        self.assertTrue(len(summary["batch_breakdown"]) >= 2)

        # Pipeline stages
        self.assertTrue(len(summary["pipeline_stages"]) >= 1)

        # Probe stats
        self.assertIsNotNone(summary["probe_stats"])
        self.assertEqual(summary["probe_stats"]["total"], 3)
        self.assertEqual(summary["probe_stats"]["success"], 2)
        self.assertEqual(summary["probe_stats"]["failed"], 1)

        # Timing
        self.assertEqual(summary["timing"]["duration_seconds"], 900)  # 15 min

    def test_build_tech_summary_empty_scan(self):
        from src.data.tech_summary_builder import build_tech_summary
        summary = build_tech_summary(self.db, scan_id="nonexistent")
        self.assertEqual(summary["gemini_usage"]["tokens_in"], 0)
        self.assertIsNone(summary["scan_stats"])

    def test_format_tech_summary(self):
        from src.data.tech_summary_builder import (
            build_tech_summary, format_tech_summary_as_markdown,
        )
        summary = build_tech_summary(self.db, scan_id="test-scan-001")
        md = format_tech_summary_as_markdown(summary)
        self.assertIn("## Technical Process Summary", md)
        self.assertIn("150,000", md)  # tokens_in formatted
        self.assertIn("15,000", md)   # tokens_out formatted
        self.assertIn("15m 0s", md)   # 900s = 15m
        self.assertIn("Batch Breakdown", md)
        self.assertIn("billing", md)

    def test_format_empty_summary(self):
        from src.data.tech_summary_builder import format_tech_summary_as_markdown
        md = format_tech_summary_as_markdown({
            "gemini_usage": {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0, "api_calls": 0, "model": ""},
            "scan_stats": None, "batch_breakdown": [], "pipeline_stages": [],
            "cost_breakdown": {}, "probe_stats": None, "timing": {},
        })
        self.assertIn("No technical process data", md)


# ══════════════════════════════════════════════════════════════════════
# 3. AnalystReportFormatter
# ══════════════════════════════════════════════════════════════════════

class TestAnalystReportFormatter(unittest.TestCase):
    """Test formatting of all 4 analyst report types."""

    def test_synthesis(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        reports = [{"report_type": "synthesis", "content": json.dumps({
            "summary": "Billing and shipping share payment gateway root cause.",
            "shared_root_causes": [
                {"cause": "Payment gateway timeout", "affected_trcs": ["billing", "shipping"],
                 "evidence": "80% of errors reference gateway"},
            ],
            "systemic_issues": [
                {"issue": "Retry not implemented", "scope": "all payment TRCs", "severity": "high"},
            ],
            "correlations": [
                {"trc_a": "billing", "trc_b": "shipping", "correlation": "shared gateway"},
            ],
        }), "metrics": None, "created_at": "2025-03-09"}]

        md = format_analyst_reports_as_markdown(reports)
        self.assertIn("## Analyst Reports", md)
        self.assertIn("### Cross-TRC Synthesis", md)
        self.assertIn("Payment gateway timeout", md)
        self.assertIn("`billing`", md)
        self.assertIn("Retry not implemented", md)
        self.assertIn("billing", md)

    def test_audit(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        reports = [{"report_type": "audit", "content": json.dumps({
            "quality_score": 0.85,
            "grades": [
                {"ticket_id": "T1", "grade": "CORRECT", "notes": "good"},
                {"ticket_id": "T2", "grade": "PARTIAL", "notes": "ok"},
                {"ticket_id": "T3", "grade": "INCORRECT", "notes": "bad"},
            ],
            "common_errors": ["Missing sub-cluster", "Wrong severity"],
            "recommendations": ["Add validation step"],
        }), "metrics": None, "created_at": "2025-03-09"}]

        md = format_analyst_reports_as_markdown(reports)
        self.assertIn("### Quality Audit", md)
        self.assertIn("85%", md)
        self.assertIn("CORRECT", md)
        self.assertIn("Missing sub-cluster", md)

    def test_novelty(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        reports = [{"report_type": "novelty", "content": json.dumps({
            "summary": {"validated": 8, "rejected": 2, "merged": 1},
            "validations": [
                {"ticket_id": "T5", "verdict": "DUPLICATE", "existing_match": "billing_error",
                 "notes": "Same as existing pattern"},
            ],
        }), "metrics": None, "created_at": "2025-03-09"}]

        md = format_analyst_reports_as_markdown(reports)
        self.assertIn("### Novelty Validation", md)
        self.assertIn("VALID | 8", md)
        self.assertIn("DUPLICATE | 2", md)
        self.assertIn("MERGE | 1", md)
        self.assertIn("T5", md)

    def test_merge(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        reports = [{"report_type": "merge", "content": json.dumps({
            "merge_suggestions": [
                {"primary_label": "billing_timeout", "merge_candidates": ["payment_timeout"],
                 "rationale": "Same root cause", "confidence": 0.92},
            ],
            "count": 1,
        }), "metrics": None, "created_at": "2025-03-09"}]

        md = format_analyst_reports_as_markdown(reports)
        self.assertIn("### Pattern Merge Suggestions", md)
        self.assertIn("`billing_timeout`", md)
        self.assertIn("`payment_timeout`", md)
        self.assertIn("92%", md)

    def test_all_four_types_together(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        reports = [
            {"report_type": "synthesis", "content": json.dumps({"summary": "ok", "shared_root_causes": []}),
             "metrics": None, "created_at": "2025-03-09"},
            {"report_type": "audit", "content": json.dumps({"quality_score": 0.9, "grades": []}),
             "metrics": None, "created_at": "2025-03-09"},
            {"report_type": "novelty", "content": json.dumps({"summary": {"validated": 5, "rejected": 0, "merged": 0}, "validations": []}),
             "metrics": None, "created_at": "2025-03-09"},
            {"report_type": "merge", "content": json.dumps({"merge_suggestions": [], "count": 0}),
             "metrics": None, "created_at": "2025-03-09"},
        ]
        md = format_analyst_reports_as_markdown(reports)
        self.assertIn("### Cross-TRC Synthesis", md)
        self.assertIn("### Quality Audit", md)
        self.assertIn("### Novelty Validation", md)
        self.assertIn("### Pattern Merge Suggestions", md)

    def test_empty_reports(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        self.assertEqual(format_analyst_reports_as_markdown([]), "")


# ══════════════════════════════════════════════════════════════════════
# 4. Report Schedules CRUD
# ══════════════════════════════════════════════════════════════════════

class TestReportSchedulesCRUD(unittest.TestCase):
    """Test report_schedules table CRUD operations."""

    def setUp(self):
        """Set up an in-memory DB with report_schedules schema."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS report_schedules (
                schedule_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                page            TEXT NOT NULL,
                config_json     TEXT NOT NULL,
                timezone        TEXT DEFAULT 'America/New_York',
                repeat_type     TEXT DEFAULT 'weekly',
                repeat_day      INTEGER DEFAULT 1,
                repeat_time     TEXT DEFAULT '06:00',
                enabled         INTEGER DEFAULT 1,
                last_run_at     TEXT,
                next_run_at     TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );
        """)
        # Create a lightweight mock DB with the CRUD methods
        from src.data.db_manager import DatabaseManager
        self.db = object.__new__(DatabaseManager)
        self.db._conn = self.conn
        self.db.db_path = None

    def test_save_and_get_schedule(self):
        sid = self.db.save_schedule(
            name="Weekly NLP Report",
            page="smart_reporting",
            config_json={"pipeline": "full", "nlp": True},
            timezone="America/New_York",
            repeat_type="weekly",
            repeat_day=1,
            repeat_time="09:00",
            next_run_at="2025-03-10T09:00:00",
        )
        self.assertIsNotNone(sid)

        schedules = self.db.get_schedules()
        self.assertEqual(len(schedules), 1)
        s = schedules[0]
        self.assertEqual(s["name"], "Weekly NLP Report")
        self.assertEqual(s["page"], "smart_reporting")
        self.assertEqual(s["timezone"], "America/New_York")
        self.assertEqual(s["repeat_type"], "weekly")
        self.assertEqual(s["repeat_day"], 1)
        self.assertEqual(s["repeat_time"], "09:00")
        self.assertEqual(s["enabled"], 1)

    def test_get_schedules_enabled_only(self):
        self.db.save_schedule("A", "smart_reporting", "{}", next_run_at=None)
        sid2 = self.db.save_schedule("B", "ai_reports", "{}", next_run_at=None)
        self.db.update_schedule(sid2, enabled=0)

        all_s = self.db.get_schedules()
        self.assertEqual(len(all_s), 2)

        enabled = self.db.get_schedules(enabled_only=True)
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["name"], "A")

    def test_update_schedule(self):
        sid = self.db.save_schedule("Test", "smart_reporting", "{}", next_run_at=None)
        self.db.update_schedule(sid, name="Updated", repeat_time="14:00", enabled=0)

        schedules = self.db.get_schedules()
        s = schedules[0]
        self.assertEqual(s["name"], "Updated")
        self.assertEqual(s["repeat_time"], "14:00")
        self.assertEqual(s["enabled"], 0)

    def test_delete_schedule(self):
        sid = self.db.save_schedule("ToDelete", "ai_reports", "{}", next_run_at=None)
        self.db.delete_schedule(sid)
        self.assertEqual(len(self.db.get_schedules()), 0)

    def test_update_last_run(self):
        sid = self.db.save_schedule("Run", "smart_reporting", "{}", next_run_at="2025-03-10T09:00:00")
        self.db.update_schedule_last_run(sid, next_run_at="2025-03-17T09:00:00")

        schedules = self.db.get_schedules()
        s = schedules[0]
        self.assertIsNotNone(s["last_run_at"])
        self.assertEqual(s["next_run_at"], "2025-03-17T09:00:00")

    def test_config_json_dict_serialization(self):
        """config_json dict should be serialized to JSON string."""
        sid = self.db.save_schedule(
            "Dict Config", "smart_reporting",
            config_json={"stages": ["nlp", "synthesis"], "budget": 2.0},
            next_run_at=None,
        )
        schedules = self.db.get_schedules()
        raw = schedules[0]["config_json"]
        parsed = json.loads(raw)
        self.assertEqual(parsed["stages"], ["nlp", "synthesis"])
        self.assertEqual(parsed["budget"], 2.0)


# ══════════════════════════════════════════════════════════════════════
# 5. Client Factory
# ══════════════════════════════════════════════════════════════════════

class TestClientFactory(unittest.TestCase):
    """Test the shared Gemini client factory."""

    @patch("src.gemini.client_factory._load_gemini_config")
    def test_returns_none_when_config_missing(self, mock_cfg):
        mock_cfg.return_value = None
        from src.gemini.client_factory import build_gemini_client
        client = build_gemini_client()
        self.assertIsNone(client)

    @patch("src.gemini.client_factory._load_gemini_config")
    def test_returns_gemini_client_by_default(self, mock_cfg):
        mock_cfg.return_value = {"cli_path": "/usr/bin/gemini", "model": "gemini-2.5-flash"}
        from src.gemini.client_factory import build_gemini_client
        client = build_gemini_client(use_bridge=False)
        from src.gemini.gemini_client import GeminiClient
        self.assertIsInstance(client, GeminiClient)

    @patch("src.gemini.client_factory._load_gemini_config")
    def test_bridge_fallback_when_unavailable(self, mock_cfg):
        """When ReportBridgeClient is not installed, use_bridge=True falls back to GeminiClient."""
        mock_cfg.return_value = {"cli_path": "/usr/bin/gemini", "model": "gemini-2.5-flash"}
        from src.gemini.client_factory import build_gemini_client
        client = build_gemini_client(use_bridge=True)
        # If bridge module is unavailable, should fall back to GeminiClient
        from src.gemini.gemini_client import GeminiClient
        self.assertIsInstance(client, GeminiClient)


if __name__ == "__main__":
    unittest.main()
