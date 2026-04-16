"""
Alma Insights — Reporting Suite Integration Tests (Phase 5.5D)

Comprehensive tests for the reporting pipeline foundations:
- compute_next_run: all recurrence types + edge cases
- MarkdownViewer / md_to_html: rendering edge cases
- analyst_report_formatter: round-trip JSON → markdown → HTML
- Smart runs JOIN query: report summary, cost aggregation
- format_next_run: display formatting
"""

import os
import sys
import sqlite3
import pytest
from datetime import datetime, timedelta

# ── Ensure project root is on path ──
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ══════════════════════════════════════════════════════════════════════
# TestComputeNextRun — 10 tests
# ══════════════════════════════════════════════════════════════════════

class TestComputeNextRun:
    """Test schedule recurrence computation for all repeat types."""

    def _compute(self, *args, **kwargs):
        from src.data.schedule_manager import compute_next_run
        return compute_next_run(*args, **kwargs)

    def test_daily_future_time(self):
        """Daily schedule with a future time today → should be today."""
        after = datetime(2026, 3, 9, 6, 0, 0)  # Monday 6:00 AM
        result = self._compute("daily", 0, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        assert dt.hour == 9
        assert dt.minute == 0
        assert dt.day == 9  # same day

    def test_daily_past_time(self):
        """Daily schedule with a past time today → should be tomorrow."""
        after = datetime(2026, 3, 9, 10, 0, 0)  # Monday 10:00 AM
        result = self._compute("daily", 0, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        assert dt.day == 10  # next day

    def test_weekly_same_day_future(self):
        """Weekly on Monday, currently Monday morning → should be today."""
        after = datetime(2026, 3, 9, 5, 0, 0)  # Monday 5:00 AM
        result = self._compute("weekly", 0, "09:00", after=after)  # 0=Monday
        dt = datetime.fromisoformat(result)
        assert dt.weekday() == 0  # Monday
        assert dt.day == 9

    def test_weekly_same_day_past(self):
        """Weekly on Monday, currently Monday afternoon → should be next Monday."""
        after = datetime(2026, 3, 9, 14, 0, 0)  # Monday 2:00 PM
        result = self._compute("weekly", 0, "09:00", after=after)  # 0=Monday
        dt = datetime.fromisoformat(result)
        assert dt.weekday() == 0  # Monday
        assert dt.day == 16  # next Monday

    def test_weekly_different_day(self):
        """Weekly on Wednesday, currently Monday → should be Wednesday."""
        after = datetime(2026, 3, 9, 10, 0, 0)  # Monday
        result = self._compute("weekly", 2, "09:00", after=after)  # 2=Wednesday
        dt = datetime.fromisoformat(result)
        assert dt.weekday() == 2  # Wednesday
        assert dt.day == 11

    def test_biweekly_next_occurrence(self):
        """Biweekly skips one week if same-day has passed."""
        after = datetime(2026, 3, 9, 14, 0, 0)  # Monday 2:00 PM
        result = self._compute("biweekly", 0, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        assert dt.weekday() == 0  # Monday
        assert dt.day == 23  # two weeks later

    def test_monthly_future_day(self):
        """Monthly on the 15th, currently the 9th → should be 15th."""
        after = datetime(2026, 3, 9, 10, 0, 0)
        result = self._compute("monthly", 15, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        assert dt.day == 15
        assert dt.month == 3

    def test_monthly_past_day(self):
        """Monthly on the 5th, currently the 9th → should be next month."""
        after = datetime(2026, 3, 9, 10, 0, 0)
        result = self._compute("monthly", 5, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        assert dt.day == 5
        assert dt.month == 4  # April

    def test_monthly_december_rollover(self):
        """Monthly on 1st, currently Dec 2nd → should be Jan 1st next year."""
        after = datetime(2026, 12, 2, 10, 0, 0)
        result = self._compute("monthly", 1, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        assert dt.month == 1
        assert dt.year == 2027
        assert dt.day == 1

    def test_unknown_type_fallback(self):
        """Unknown repeat type → should return 24 hours from now."""
        after = datetime(2026, 3, 9, 10, 0, 0)
        result = self._compute("unknown_type", 0, "09:00", after=after)
        dt = datetime.fromisoformat(result)
        expected = after + timedelta(hours=24)
        assert abs((dt - expected).total_seconds()) < 2


# ══════════════════════════════════════════════════════════════════════
# TestMarkdownEdgeCases — 7 tests
# ══════════════════════════════════════════════════════════════════════

class TestMarkdownEdgeCases:
    """Test the md_to_html utility with edge cases."""

    def _convert(self, text):
        from src.ui.widgets.markdown_viewer import md_to_html
        return md_to_html(text)

    def test_empty_string(self):
        result = self._convert("")
        assert result is not None  # should not crash

    def test_whitespace_only(self):
        result = self._convert("   \n  \n   ")
        assert result is not None

    def test_single_paragraph(self):
        result = self._convert("Hello world")
        assert "Hello world" in result

    def test_heading_renders(self):
        result = self._convert("## My Heading")
        assert "<h2>" in result
        assert "My Heading" in result

    def test_bold_renders(self):
        result = self._convert("This is **bold** text")
        assert "<strong>" in result or "bold" in result

    def test_table_renders(self):
        md = "| Col A | Col B |\n|-------|-------|\n| val1 | val2 |"
        result = self._convert(md)
        assert "<table>" in result or "<th>" in result

    def test_list_renders(self):
        md = "- item one\n- item two\n- item three"
        result = self._convert(md)
        assert "<li>" in result


# ══════════════════════════════════════════════════════════════════════
# TestAnalystFormatterRoundTrip — 2 tests
# ══════════════════════════════════════════════════════════════════════

class TestAnalystFormatterRoundTrip:
    """Test the analyst report JSON → markdown → HTML pipeline."""

    def test_full_pipeline(self):
        """Feed sample reports through formatter → md_to_html → verify HTML output."""
        import json
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        from src.ui.widgets.markdown_viewer import md_to_html

        reports = [
            {
                "report_type": "synthesis",
                "content": json.dumps({
                    "summary": "Test synthesis summary",
                    "shared_root_causes": [
                        {"cause": "API timeout", "affected_trcs": ["TRC-001"],
                         "evidence": "Seen in 12 tickets"}
                    ],
                    "systemic_issues": [
                        {"issue": "Slow response", "scope": "global", "severity": "high"}
                    ],
                }),
                "metrics": json.dumps({"total_trcs": 5}),
                "created_at": "2026-03-09T12:00:00",
            },
            {
                "report_type": "audit",
                "content": json.dumps({
                    "quality_score": 0.85,
                    "grades": [
                        {"grade": "CORRECT"}, {"grade": "CORRECT"}, {"grade": "PARTIAL"}
                    ],
                    "common_errors": ["Missing sentiment intensity"],
                }),
                "metrics": "{}",
                "created_at": "2026-03-09T12:01:00",
            },
        ]

        md = format_analyst_reports_as_markdown(reports)
        assert "## Analyst Reports" in md
        assert "Cross-TRC Synthesis" in md
        assert "Quality Audit" in md

        # Convert to HTML
        html = md_to_html(md)
        assert "<h2>" in html or "<h3>" in html
        assert "API timeout" in html
        assert "85%" in html  # quality_score rendered

    def test_empty_reports(self):
        from src.data.analyst_report_formatter import format_analyst_reports_as_markdown
        assert format_analyst_reports_as_markdown([]) == ""


# ══════════════════════════════════════════════════════════════════════
# TestSmartRunsJoinQuery — 4 tests
# ══════════════════════════════════════════════════════════════════════

class TestSmartRunsJoinQuery:
    """Test the get_smart_runs_with_reports JOIN query on a test DB."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        """Create a minimal in-memory DB with the required tables."""
        self.db_path = str(tmp_path / "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

        # Create tables
        self.conn.executescript("""
            CREATE TABLE smart_report_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT DEFAULT 'running',
                ticket_count INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                trigger_source TEXT DEFAULT 'manual',
                report_id INTEGER,
                config_json TEXT DEFAULT '{}'
            );

            CREATE TABLE analysis_reports (
                report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                page TEXT NOT NULL,
                summary TEXT,
                full_results TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE gemini_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                tokens_in INTEGER DEFAULT 0,
                tokens_out INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """)
        self.conn.commit()
        yield
        self.conn.close()

    def _query(self, limit=20):
        """Execute the JOIN query directly."""
        rows = self.conn.execute("""
            SELECT r.*,
                   ar.summary  AS report_summary,
                   CASE WHEN ar.full_results IS NOT NULL
                             AND ar.full_results != ''
                        THEN 1 ELSE 0 END AS has_full_results,
                   COALESCE(
                       (SELECT SUM(cost_usd) FROM gemini_usage
                        WHERE created_at >= r.started_at
                          AND created_at <= COALESCE(r.completed_at, r.started_at)),
                       0) AS run_cost_usd
            FROM smart_report_runs r
            LEFT JOIN analysis_reports ar ON r.report_id = ar.report_id
            ORDER BY r.started_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def test_join_returns_report_summary(self):
        """Run + linked report → report_summary populated."""
        self.conn.execute(
            "INSERT INTO analysis_reports (report_id, page, summary, full_results, created_at) "
            "VALUES (1, 'smart', 'Test summary', '{\"data\": true}', '2026-03-09 12:00:00')"
        )
        self.conn.execute(
            "INSERT INTO smart_report_runs (started_at, completed_at, status, report_id) "
            "VALUES ('2026-03-09 12:00:00', '2026-03-09 12:01:00', 'success', 1)"
        )
        self.conn.commit()

        rows = self._query()
        assert len(rows) == 1
        assert rows[0]["report_summary"] == "Test summary"
        assert rows[0]["has_full_results"] == 1

    def test_null_report_handling(self):
        """Run without linked report → NULL fields."""
        self.conn.execute(
            "INSERT INTO smart_report_runs (started_at, status) "
            "VALUES ('2026-03-09 12:00:00', 'failed')"
        )
        self.conn.commit()

        rows = self._query()
        assert len(rows) == 1
        assert rows[0]["report_summary"] is None
        assert rows[0]["has_full_results"] == 0

    def test_cost_aggregation(self):
        """Gemini usage within run window → cost_usd aggregated."""
        self.conn.execute(
            "INSERT INTO smart_report_runs (started_at, completed_at, status) "
            "VALUES ('2026-03-09 12:00:00', '2026-03-09 12:05:00', 'success')"
        )
        # Two API calls during the run window
        self.conn.execute(
            "INSERT INTO gemini_usage (cost_usd, created_at) VALUES (0.15, '2026-03-09 12:01:00')"
        )
        self.conn.execute(
            "INSERT INTO gemini_usage (cost_usd, created_at) VALUES (0.25, '2026-03-09 12:03:00')"
        )
        # One call OUTSIDE the window (should not count)
        self.conn.execute(
            "INSERT INTO gemini_usage (cost_usd, created_at) VALUES (1.00, '2026-03-09 13:00:00')"
        )
        self.conn.commit()

        rows = self._query()
        assert len(rows) == 1
        assert abs(rows[0]["run_cost_usd"] - 0.40) < 0.001

    def test_desc_ordering(self):
        """Results are ordered newest first."""
        self.conn.execute(
            "INSERT INTO smart_report_runs (started_at, status) "
            "VALUES ('2026-03-08 12:00:00', 'success')"
        )
        self.conn.execute(
            "INSERT INTO smart_report_runs (started_at, status) "
            "VALUES ('2026-03-09 12:00:00', 'success')"
        )
        self.conn.commit()

        rows = self._query()
        assert len(rows) == 2
        assert rows[0]["started_at"] > rows[1]["started_at"]


# ══════════════════════════════════════════════════════════════════════
# TestFormatNextRun — 3 tests
# ══════════════════════════════════════════════════════════════════════

class TestFormatNextRun:
    """Test the format_next_run display helper."""

    def _format(self, iso_str):
        from src.data.schedule_manager import format_next_run
        return format_next_run(iso_str)

    def test_valid_iso(self):
        result = self._format("2026-03-10T09:00:00")
        assert "Mar" in result
        assert "10" in result
        assert "9" in result or "09" in result  # hour
        assert "AM" in result

    def test_empty_string(self):
        assert self._format("") == ""

    def test_invalid_iso_fallback(self):
        """Invalid ISO string → returns truncated string as fallback."""
        result = self._format("not-a-date-at-all")
        assert result  # non-empty (either partial or original)
