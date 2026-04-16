"""
Tests for ScanReportBuilder — post-scan auto-report generation.

Uses in-memory SQLite to verify each report section and the full
build-and-save pipeline.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta

import pytest

from src.data.scan_report_builder import ScanReportBuilder


# ── Fixtures ────────────────────────────────────────────────────────

def _create_tables(conn):
    """Create the minimal schema needed by ScanReportBuilder."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nlp_scan_runs (
            scan_id          TEXT PRIMARY KEY,
            created_at       TEXT NOT NULL,
            status           TEXT NOT NULL,
            date_range_start TEXT NOT NULL,
            date_range_end   TEXT NOT NULL,
            trc_filter       TEXT,
            mode             TEXT NOT NULL DEFAULT 'full',
            batch_strategy   TEXT DEFAULT 'trc',
            total_batches    INTEGER DEFAULT 0,
            completed_batches INTEGER DEFAULT 0,
            total_tickets    INTEGER DEFAULT 0,
            total_comments   INTEGER DEFAULT 0,
            total_input_tokens  INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            estimated_cost_usd  REAL DEFAULT 0.0,
            actual_cost_usd     REAL DEFAULT 0.0,
            budget_cap_usd      REAL DEFAULT 50.0,
            error_log        TEXT,
            config_snapshot   TEXT,
            completed_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS nlp_batches (
            batch_id        TEXT PRIMARY KEY,
            scan_id         TEXT NOT NULL,
            batch_number    INTEGER NOT NULL,
            trc             TEXT NOT NULL,
            trc_chunk       INTEGER DEFAULT 1,
            trc_chunk_total INTEGER DEFAULT 1,
            status          TEXT NOT NULL,
            ticket_count    INTEGER DEFAULT 0,
            comment_count   INTEGER DEFAULT 0,
            input_tokens    INTEGER DEFAULT 0,
            output_tokens   INTEGER DEFAULT 0,
            cost_usd        REAL DEFAULT 0.0,
            latency_ms      INTEGER DEFAULT 0,
            retry_count     INTEGER DEFAULT 0,
            error_message   TEXT,
            worker_id       TEXT,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS nlp_ticket_classifications (
            classification_id TEXT PRIMARY KEY,
            batch_id        TEXT NOT NULL,
            scan_id         TEXT NOT NULL,
            ticket_id       TEXT NOT NULL,
            trc             TEXT NOT NULL,
            sub_cluster     TEXT,
            friction_type   TEXT,
            root_cause_hint TEXT,
            sentiment_polarity TEXT,
            sentiment_intensity INTEGER,
            anomaly_flag    TEXT,
            is_novel        INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS nlp_findings (
            finding_id      TEXT PRIMARY KEY,
            scan_id         TEXT NOT NULL,
            finding_type    TEXT NOT NULL,
            scope           TEXT,
            title           TEXT NOT NULL,
            description     TEXT,
            ticket_count    INTEGER,
            pct_of_scanned  REAL,
            avg_sentiment_intensity REAL,
            dominant_friction_type TEXT,
            top_trcs        TEXT,
            top_sub_patterns TEXT,
            top_entities    TEXT,
            date_concentration TEXT,
            temporal_trend  TEXT,
            exemplar_ticket_ids TEXT,
            statistical_validation TEXT,
            baseline_comparison TEXT,
            impact_score    REAL
        );

        CREATE TABLE IF NOT EXISTS probe_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id         TEXT,
            bridge_index    INTEGER,
            probe_type      TEXT DEFAULT 'pre_scan',
            timestamp       TEXT,
            status          TEXT,
            latency_ms      INTEGER,
            error_message   TEXT,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS scan_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id         TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            status          TEXT,
            message         TEXT,
            duration_ms     INTEGER,
            metadata_json   TEXT
        );

        CREATE TABLE IF NOT EXISTS analysis_reports (
            report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            page            TEXT NOT NULL,
            run_at          TEXT NOT NULL,
            parameters      TEXT NOT NULL,
            summary         TEXT NOT NULL,
            full_results    TEXT DEFAULT '',
            ticket_count    INTEGER NOT NULL DEFAULT 0,
            duration_ms     INTEGER NOT NULL DEFAULT 0,
            notes           TEXT DEFAULT '',
            report_type     TEXT DEFAULT 'standard',
            chat_history    TEXT DEFAULT '',
            exported_at     TEXT DEFAULT ''
        );
    """)


def _insert_scan(conn, scan_id, status="completed", tickets=100,
                  tokens_in=50000, tokens_out=20000, cost=0.05,
                  created_offset_min=10, model="gemini-2.5-flash"):
    """Insert a scan run record."""
    now = datetime.utcnow()
    config = json.dumps({
        "model": model,
        "mode": "agentic",
        "batch_size": 25,
        "parallel_workers": 3,
        "pipeline_version": "5.0",
    })
    conn.execute("""
        INSERT INTO nlp_scan_runs
            (scan_id, created_at, status, date_range_start, date_range_end,
             total_tickets, total_input_tokens, total_output_tokens,
             actual_cost_usd, budget_cap_usd, completed_at, config_snapshot)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scan_id,
        (now - timedelta(minutes=created_offset_min)).isoformat(),
        status,
        "2025-01-01", "2025-03-12",
        tickets, tokens_in, tokens_out, cost, 1.00,
        now.isoformat(),
        config,
    ))


def _insert_batches(conn, scan_id, completed=10, failed=0, retries=0):
    """Insert batch records."""
    for i in range(completed):
        conn.execute("""
            INSERT INTO nlp_batches
                (batch_id, scan_id, batch_number, trc, status,
                 ticket_count, input_tokens, output_tokens, cost_usd,
                 latency_ms, retry_count)
            VALUES (?, ?, ?, ?, 'completed', 10, 5000, 2000, 0.005, 8000, ?)
        """, (str(uuid.uuid4()), scan_id, i + 1, f"TRC_{i % 3}",
              1 if i < retries else 0))

    for j in range(failed):
        conn.execute("""
            INSERT INTO nlp_batches
                (batch_id, scan_id, batch_number, trc, status,
                 ticket_count, retry_count, error_message)
            VALUES (?, ?, ?, ?, 'failed', 10, 3, 'stall_timeout')
        """, (str(uuid.uuid4()), scan_id, completed + j + 1, "TRC_0"))


def _insert_classifications(conn, scan_id, count=50):
    """Insert ticket classification records across 3 TRCs."""
    trcs = ["Update Credentialing", "Payout Rates Inquiry", "Re-Credentialing"]
    frictions = ["Documentation/Knowledge Gap", "Process/Workflow Breakdown",
                 "Product/UI Friction"]
    for i in range(count):
        conn.execute("""
            INSERT INTO nlp_ticket_classifications
                (classification_id, batch_id, scan_id, ticket_id, trc,
                 sub_cluster, friction_type, root_cause_hint)
            VALUES (?, 'batch_1', ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), scan_id, f"ticket_{i}",
            trcs[i % 3],
            f"cluster_{i % 5}",
            frictions[i % 3],
            f"root_cause_{i % 4}",
        ))


def _insert_findings(conn, scan_id, count=5):
    """Insert finding records with descending impact scores."""
    for i in range(count):
        conn.execute("""
            INSERT INTO nlp_findings
                (finding_id, scan_id, finding_type, title, ticket_count,
                 dominant_friction_type, impact_score, temporal_trend)
            VALUES (?, ?, 'within_trc', ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), scan_id,
            f"Finding {i + 1}: Test pattern",
            20 - i * 3,
            "Documentation/Knowledge Gap" if i % 2 == 0 else "Process/Workflow Breakdown",
            90 - i * 10,
            "new" if i == 0 else "stable",
        ))


def _make_db(tmp_path):
    """Create an in-memory-like file DB (needed for ScanReportBuilder path-based init)."""
    db_path = str(tmp_path / "test_report.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn, db_path


# ── Tests ───────────────────────────────────────────────────────────

class TestScanReportBuilder:
    """Tests for ScanReportBuilder."""

    def test_build_report_basic(self, tmp_path):
        """Report generates valid markdown with all 5 sections."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=10, failed=1, retries=2)
        _insert_classifications(conn, scan_id, count=50)
        _insert_findings(conn, scan_id, count=5)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        # Use internal method to get markdown without saving
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        md = builder._assemble_markdown(conn2, scan, scan_id)
        conn2.close()

        assert "# Scan Report" in md
        assert "## Cost Breakdown" in md
        assert "## Bridge Status Report" in md
        assert "## Scan Diagnostics" in md
        assert "## Key Findings" in md
        assert "## Friction Ledger" in md
        assert "## Trend Summary" in md

    def test_cost_section_format(self, tmp_path):
        """Cost section includes token counts and USD total."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id, tokens_in=100000, tokens_out=40000, cost=0.12)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_cost_section(scan)
        conn2.close()

        assert "100,000" in section  # input tokens formatted
        assert "40,000" in section   # output tokens formatted
        assert "$0.12" in section    # cost
        assert "140,000" in section  # total tokens

    def test_bridge_section_with_failures(self, tmp_path):
        """Bridge section shows failed batch count and error breakdown."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=8, failed=2, retries=3)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        section = builder._build_bridge_section(conn2, scan_id)
        conn2.close()

        assert "8/10" in section
        assert "2 failed" in section
        assert "stall_timeout" in section

    def test_bridge_section_all_success(self, tmp_path):
        """Bridge section shows clean pass when no failures."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=10, failed=0)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        section = builder._build_bridge_section(conn2, scan_id)
        conn2.close()

        assert "10/10" in section
        assert "All batches completed successfully" in section

    def test_findings_ranked(self, tmp_path):
        """Findings appear in impact_score descending order."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_findings(conn, scan_id, count=5)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        section = builder._build_findings_section(conn2, scan_id)
        conn2.close()

        # Finding 1 (impact 90) should appear before Finding 5 (impact 50)
        pos1 = section.index("Finding 1")
        pos5 = section.index("Finding 5")
        assert pos1 < pos5

    def test_ledger_table_format(self, tmp_path):
        """Ledger has correct markdown table headers and rows."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_classifications(conn, scan_id, count=30)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        section = builder._build_ledger_section(conn2, scan_id)
        conn2.close()

        assert "| TRC |" in section
        assert "| Sub-Cluster |" in section
        assert "| Friction Type |" in section
        assert "| Root Cause |" in section
        # Should have data rows
        assert "cluster_" in section

    def test_trend_no_previous(self, tmp_path):
        """Trend section shows 'first scan' message when no prior scan exists."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        section = builder._build_trend_section(conn2, scan_id)
        conn2.close()

        assert "First scan" in section

    def test_trend_with_previous(self, tmp_path):
        """Trend section computes correct deltas between scans."""
        conn, db_path = _make_db(tmp_path)

        # Previous scan (older)
        prev_id = str(uuid.uuid4())
        _insert_scan(conn, prev_id, created_offset_min=60)
        _insert_classifications(conn, prev_id, count=30)

        # Current scan (newer)
        curr_id = str(uuid.uuid4())
        _insert_scan(conn, curr_id, created_offset_min=5)
        _insert_classifications(conn, curr_id, count=50)

        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        section = builder._build_trend_section(conn2, curr_id)
        conn2.close()

        assert "| This Scan |" in section
        assert "| Previous |" in section
        # Should have arrows for deltas
        assert "↑" in section or "↓" in section or "—" in section

    def test_save_persists_to_analysis_reports(self, tmp_path):
        """save() inserts a row with page='nlp_scanner' and report_type='auto_scan_report'."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=5)
        _insert_classifications(conn, scan_id, count=20)
        _insert_findings(conn, scan_id, count=3)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        builder.build_and_save(scan_id)

        # Verify persistence
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT * FROM analysis_reports WHERE page = 'nlp_scanner'"
        ).fetchone()
        conn2.close()

        assert row is not None
        assert row["report_type"] == "auto_scan_report"
        assert row["ticket_count"] == 100  # from _insert_scan default
        assert len(row["full_results"]) > 100  # non-trivial markdown

    def test_missing_scan_graceful(self, tmp_path):
        """build_and_save() for nonexistent scan_id does nothing."""
        conn, db_path = _make_db(tmp_path)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        # Should not raise
        builder.build_and_save("nonexistent-scan-id")

        # No report should be created
        conn2 = sqlite3.connect(db_path)
        count = conn2.execute(
            "SELECT COUNT(*) FROM analysis_reports"
        ).fetchone()[0]
        conn2.close()
        assert count == 0

    def test_empty_classifications_handled(self, tmp_path):
        """Report handles zero classifications without crashing."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id, tickets=0)
        _insert_batches(conn, scan_id, completed=1)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        builder.build_and_save(scan_id)

        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT * FROM analysis_reports WHERE page = 'nlp_scanner'"
        ).fetchone()
        conn2.close()

        assert row is not None
        assert "No classification data" in row["full_results"]
        assert "No findings generated" in row["full_results"]

    def test_report_summary_json(self, tmp_path):
        """Summary dict has expected keys for KPI cards."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id, tickets=200, cost=0.08)
        _insert_batches(conn, scan_id, completed=10, failed=1)
        _insert_classifications(conn, scan_id, count=50)
        _insert_findings(conn, scan_id, count=3)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        builder.build_and_save(scan_id)

        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT summary FROM analysis_reports WHERE page = 'nlp_scanner'"
        ).fetchone()
        conn2.close()

        summary = json.loads(row["summary"])
        assert summary["total_tickets"] == 200
        assert summary["total_cost_usd"] == 0.08
        assert summary["batches_succeeded"] == 10
        assert summary["batches_failed"] == 1
        assert summary["finding_count"] == 3
        assert summary["trc_count"] == 3  # 3 distinct TRCs from _insert_classifications
        assert summary["top_finding"] is not None
        # New telemetry fields (D8)
        assert summary["model"] == "gemini-2.5-flash"
        assert "total_retries" in summary
        assert "avg_latency_ms" in summary
        assert "max_latency_ms" in summary
        assert "probe_p50_ms" in summary
        assert "probe_p95_ms" in summary

    # ── D9: Telemetry Tests ──────────────────────────────────────────

    def test_diagnostics_model_from_config(self, tmp_path):
        """Diagnostics section shows model name from config_snapshot."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id, model="gemini-3-flash-preview")
        _insert_batches(conn, scan_id, completed=5)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "## Scan Diagnostics" in section
        assert "`gemini-3-flash-preview`" in section

    def test_diagnostics_model_missing_config(self, tmp_path):
        """Diagnostics shows 'unknown' when config_snapshot is empty."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        # Insert scan with NULL config_snapshot
        now = datetime.utcnow()
        conn.execute("""
            INSERT INTO nlp_scan_runs
                (scan_id, created_at, status, date_range_start, date_range_end,
                 total_tickets, completed_at, config_snapshot)
            VALUES (?, ?, 'completed', '2025-01-01', '2025-03-12', 100, ?, NULL)
        """, (scan_id, now.isoformat(), now.isoformat()))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "`unknown`" in section

    def test_diagnostics_probe_latency(self, tmp_path):
        """Diagnostics shows P50/P95 probe latency from probe_history."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)

        # Insert 10 probes with known latencies
        for i, lat in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]):
            conn.execute("""
                INSERT INTO probe_history
                    (scan_id, bridge_index, probe_type, status, latency_ms, created_at)
                VALUES (?, 0, 'pre_scan', 'success', ?, ?)
            """, (scan_id, lat, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "Probe Latency" in section
        assert "P50=" in section
        assert "P95=" in section
        assert "10 probes" in section

    def test_diagnostics_latency_trend_degrading(self, tmp_path):
        """Diagnostics detects latency degradation (second half 2x+ slower)."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)

        # First 5 batches: fast (5s), last 5 batches: slow (15s)
        for i in range(10):
            lat = 5000 if i < 5 else 15000
            conn.execute("""
                INSERT INTO nlp_batches
                    (batch_id, scan_id, batch_number, trc, status,
                     ticket_count, latency_ms)
                VALUES (?, ?, ?, 'TRC_0', 'completed', 10, ?)
            """, (str(uuid.uuid4()), scan_id, i + 1, lat))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "Latency Trend" in section
        assert "degrading" in section

    def test_diagnostics_retry_summary(self, tmp_path):
        """Diagnostics shows retry count and error breakdown from scan_events."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=5)

        # Insert batch_retry scan_events
        now = datetime.utcnow()
        for i, error in enumerate(["stall_timeout", "stall_timeout", "bridge_shutdown"]):
            conn.execute("""
                INSERT INTO scan_events
                    (scan_id, timestamp, event_type, status, message, metadata_json)
                VALUES (?, ?, 'batch_retry', 'running', ?, ?)
            """, (
                scan_id,
                now.isoformat(),
                f'Batch {i} retry',
                json.dumps({
                    "batch_id": f"b{i}",
                    "retry_count": 1,
                    "error": error,
                    "error_code": error,
                    "worker_id": f"worker_{i % 2}",
                    "attempt_latency_ms": 5000 + i * 1000,
                }),
            ))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "Retries" in section
        assert "3 total" in section
        assert "stall_timeout" in section
        assert "bridge_shutdown" in section

    def test_diagnostics_worker_health_table(self, tmp_path):
        """Diagnostics renders per-worker health table."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)

        # Worker 0: 5 OK, 1 failed
        for i in range(5):
            conn.execute("""
                INSERT INTO nlp_batches
                    (batch_id, scan_id, batch_number, trc, status,
                     ticket_count, worker_id, latency_ms)
                VALUES (?, ?, ?, 'TRC_0', 'completed', 10, 'worker_0', 8000)
            """, (str(uuid.uuid4()), scan_id, i + 1))
        conn.execute("""
            INSERT INTO nlp_batches
                (batch_id, scan_id, batch_number, trc, status,
                 ticket_count, worker_id, retry_count, error_message)
            VALUES (?, ?, 6, 'TRC_0', 'failed', 10, 'worker_0', 3, 'stall_timeout')
        """, (str(uuid.uuid4()), scan_id))

        # Worker 1: 4 OK, 0 failed
        for i in range(4):
            conn.execute("""
                INSERT INTO nlp_batches
                    (batch_id, scan_id, batch_number, trc, status,
                     ticket_count, worker_id, latency_ms)
                VALUES (?, ?, ?, 'TRC_1', 'completed', 10, 'worker_1', 7000)
            """, (str(uuid.uuid4()), scan_id, i + 7))

        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "Worker Health" in section
        assert "worker_0" in section
        assert "worker_1" in section

    def test_diagnostics_rate_governor_snapshot(self, tmp_path):
        """Diagnostics shows rate governor final state from scan_events."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)

        now = datetime.utcnow()
        conn.execute("""
            INSERT INTO scan_events
                (scan_id, timestamp, event_type, status, message, metadata_json)
            VALUES (?, ?, 'info', 'complete', ?, ?)
        """, (
            scan_id,
            now.isoformat(),
            "Rate governor final: interval=2.5s, throughput=12.3/min",
            json.dumps({
                "min_interval": 2.5,
                "throughput": 12.3,
                "probe_floor": 1.8,
                "total_calls": 245,
                "rate_limit_events": 0,
            }),
        ))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "Rate Governor" in section
        assert "2.5" in section
        assert "12.3" in section
        assert "245" in section

    def test_diagnostics_bridge_events(self, tmp_path):
        """Diagnostics shows bridge death/stall events timeline."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)

        now = datetime.utcnow()
        conn.execute("""
            INSERT INTO scan_events
                (scan_id, timestamp, event_type, status, message, metadata_json)
            VALUES (?, ?, 'warning', 'running',
                    'worker_0 bridge died (exit_code=1)', ?)
        """, (
            scan_id,
            now.isoformat(),
            json.dumps({"worker": "worker_0", "death_count": 1, "exit_code": 1}),
        ))
        conn.execute("""
            INSERT INTO scan_events
                (scan_id, timestamp, event_type, status, message, metadata_json)
            VALUES (?, ?, 'warning', 'running',
                    'Stall escalation: auto-restarting worker_0 bridge (consecutive=3)', ?)
        """, (
            scan_id,
            now.isoformat(),
            json.dumps({"worker": "worker_0", "consecutive_stalls": 3}),
        ))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        section = builder._build_diagnostics_section(conn2, scan, scan_id)
        conn2.close()

        assert "Bridge Events" in section
        assert "bridge died" in section
        assert "Stall escalation" in section

    def test_summary_json_probe_latency(self, tmp_path):
        """Summary JSON includes probe P50/P95 when probe_history exists."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=5)
        _insert_classifications(conn, scan_id, count=10)

        # Insert probes
        now = datetime.utcnow()
        for lat in [200, 300, 400, 500, 600]:
            conn.execute("""
                INSERT INTO probe_history
                    (scan_id, bridge_index, status, latency_ms, created_at)
                VALUES (?, 0, 'success', ?, ?)
            """, (scan_id, lat, now.isoformat()))
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        summary = builder._build_summary_json(conn2, scan, scan_id)
        conn2.close()

        assert summary["probe_p50_ms"] == 400  # middle of 5 sorted values
        assert summary["probe_p95_ms"] == 600  # 95th pct of 5 values

    def test_summary_json_no_probes(self, tmp_path):
        """Summary JSON has None probe values when no probe_history."""
        conn, db_path = _make_db(tmp_path)
        scan_id = str(uuid.uuid4())
        _insert_scan(conn, scan_id)
        _insert_batches(conn, scan_id, completed=3)
        conn.commit()
        conn.close()

        builder = ScanReportBuilder(db_path)
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        scan = builder._get_scan(conn2, scan_id)
        summary = builder._build_summary_json(conn2, scan, scan_id)
        conn2.close()

        assert summary["probe_p50_ms"] is None
        assert summary["probe_p95_ms"] is None
