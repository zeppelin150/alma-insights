"""Unit tests for post_scan_persist — snapshot, trend, insight writers."""

import sqlite3
import json
import pytest
from pathlib import Path

_MIGRATION_SQL = (
    Path(__file__).resolve().parent.parent.parent / "migrations" / "005_persistence_layer.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.executescript(_MIGRATION_SQL)
    yield db
    db.close()


def _seed_tickets(conn, scan_id, tickets):
    """Seed ticket_index with test data."""
    for t in tickets:
        conn.execute(
            """INSERT INTO ticket_index
            (ticket_id, first_seen_scan_id, last_seen_scan_id, first_seen_date,
             trc_code, friction_type, sub_pattern, sentiment_intensity,
             csat_score, anomaly_flag)
            VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)""",
            (
                t["id"], scan_id, scan_id,
                t.get("trc", "BIL-01"),
                t.get("friction", "billing_error"),
                t.get("sub_pattern", "duplicate_charge"),
                t.get("sentiment", 0.5),
                t.get("csat", 3.0),
                t.get("anomaly"),
            ),
        )
    conn.commit()


class TestWriteScanCategorySnapshot:
    def test_writes_grouped_rows(self, conn):
        from src.services.post_scan_persist import write_scan_category_snapshot
        _seed_tickets(conn, "scan-1", [
            {"id": "T-1", "friction": "billing_error"},
            {"id": "T-2", "friction": "billing_error"},
            {"id": "T-3", "friction": "access_issue"},
        ])
        count = write_scan_category_snapshot("scan-1", "2025-03-01", conn)
        assert count == 2  # 2 groups: billing_error, access_issue

        rows = conn.execute("SELECT * FROM scan_category_snapshots").fetchall()
        assert len(rows) == 2


class TestWriteTrendDeltas:
    def test_no_prior_scan_returns_zero(self, conn):
        from src.services.post_scan_persist import write_trend_deltas
        _seed_tickets(conn, "scan-1", [{"id": "T-1"}])
        # Need snapshot first
        from src.services.post_scan_persist import write_scan_category_snapshot
        write_scan_category_snapshot("scan-1", "2025-03-01", conn)
        count = write_trend_deltas("scan-1", conn)
        assert count == 0

    def test_detects_growth(self, conn):
        from src.services.post_scan_persist import (
            write_scan_category_snapshot, write_trend_deltas,
        )
        # Prior scan: 5 billing tickets
        _seed_tickets(conn, "scan-1", [{"id": f"T-{i}", "friction": "billing"} for i in range(5)])
        write_scan_category_snapshot("scan-1", "2025-02-01", conn)

        # Current scan: 10 billing tickets (100% growth)
        _seed_tickets(conn, "scan-2", [{"id": f"T-{i+10}", "friction": "billing"} for i in range(10)])
        write_scan_category_snapshot("scan-2", "2025-03-01", conn)

        count = write_trend_deltas("scan-2", conn)
        assert count >= 1

        row = conn.execute("SELECT direction, pct_change FROM trend_snapshots").fetchone()
        assert row[0] == "rising"
        assert row[1] == 100.0


class TestWriteScanInsights:
    def test_new_pattern_with_10_plus_tickets(self, conn):
        from src.services.post_scan_persist import write_scan_insights
        _seed_tickets(conn, "scan-1", [
            {"id": f"T-{i}", "sub_pattern": "new_pattern"} for i in range(12)
        ])
        count = write_scan_insights("scan-1", conn)
        assert count >= 1

        row = conn.execute("SELECT title FROM insight_ledger").fetchone()
        assert "new_pattern" in row[0]

    def test_critical_anomaly_cluster(self, conn):
        from src.services.post_scan_persist import write_scan_insights
        _seed_tickets(conn, "scan-1", [
            {"id": f"T-{i}", "anomaly": "critical"} for i in range(6)
        ])
        count = write_scan_insights("scan-1", conn)
        assert count >= 1
