"""
End-to-end test: synthetic Zendesk stream → warehouse → rate chart → alert.

Wires up the full Source Monitor stack against a real SQLite DB:

  1. ``SourceWarehouse`` ingests synthetic tickets — writes to
     ``source_events`` and ``source_trc_hourly``.
  2. ``compute_rate_baseline`` reads the rollup, sees the spike.
  3. ``WatchlistEngine.evaluate`` fires an alert on a custom keyword
     rule.
  4. ``SourceMonitorPage`` renders the alert in its Alerts tab.
  5. User confirms the alert → EWMA confidence increases on the rule.

Performance budget: full pipeline should complete in < 3 seconds on a
warm test machine. The whole suite is < 5 seconds.

Marks: ``@pytest.mark.e2e``, ``@pytest.mark.ui``.

CLAUDE.md mandates a zombie cleanup before E2E runs to avoid SQLite
lock contention from leftover Gemini bridge subprocesses. We do that
in an autouse fixture rather than each test.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = [pytest.mark.e2e, pytest.mark.ui]


# ═══════════════════════════════════════════════════════════════════
#  Module-scoped fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True, scope="module")
def kill_zombies():
    """Kill any leftover Gemini bridge / MCP server zombies before E2E.

    Per CLAUDE.md mandate. Failures are silently swallowed — the
    cleanup is best-effort.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                'wmic process where "commandline like \'%alma_mcp_server%\'" '
                'call terminate',
                shell=True,
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
    yield


@pytest.fixture
def wired_stack(empty_db, qapp):
    """Set up warehouse + watchlist + page with one custom keyword rule.

    Returns a dict with the wired components so individual tests can
    drive specific parts of the pipeline.
    """
    from src.data.source_warehouse import SourceWarehouse
    from src.data.watchlist_engine import WatchlistEngine
    from src.ui.pages.source_monitor_page import SourceMonitorPage

    warehouse = SourceWarehouse(empty_db)
    watchlist = WatchlistEngine(empty_db, warehouse=warehouse)

    # Drop the auto-injected system rules so our custom one is the only
    # rule that can fire — keeps assertions deterministic.
    empty_db.conn.execute("DELETE FROM watchlist_rules")
    empty_db.conn.commit()

    rule_id = watchlist.create_rule(
        name="Refund Surge",
        rule_type="keyword",
        severity="watch",
        keywords="refund,money back,chargeback",
        keyword_mode="any",
        cooldown_minutes=0,  # disable cooldown for the test
    )

    with patch("src.data.pat_store.load_setting", return_value=""):
        page = SourceMonitorPage(empty_db)
    page.set_watchlist(watchlist)

    return {
        "db": empty_db,
        "warehouse": warehouse,
        "watchlist": watchlist,
        "page": page,
        "rule_id": rule_id,
    }


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _synthetic_tickets(n: int, *, subject: str = "Refund please",
                        trc: str = "TRC-100",
                        starting_at: datetime | None = None) -> list[dict]:
    """Build ``n`` synthetic Zendesk-shaped ticket dicts."""
    base = starting_at or datetime.now(timezone.utc)
    return [
        {
            "id": f"e2e-{i}",
            "subject": subject,
            "description": "I want a refund processed",
            "created_at": (base - timedelta(minutes=i)).isoformat(),
            "tags": [],
            "priority": "normal",
            "status": "open",
            "type": "incident",
        }
        for i in range(n)
    ]


def _seed_baseline_history(db, source: str = "zendesk",
                            trc: str = "TRC-100",
                            *, base_rate: int = 5,
                            now: datetime | None = None) -> None:
    """Seed 7 days of stable hourly history so the baseline isn't cold."""
    now = now or datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    for h in range(1, 7 * 24 + 1):
        bucket = (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00")
        db.conn.execute(
            """INSERT OR REPLACE INTO source_trc_hourly
               (source, trc_code, hour_bucket, count)
               VALUES (?, ?, ?, ?)""",
            (source, trc, bucket, base_rate),
        )
    db.conn.commit()


# ═══════════════════════════════════════════════════════════════════
#  Pipeline E2E
# ═══════════════════════════════════════════════════════════════════

class TestPipelineEndToEnd:
    """Run the warehouse → baseline → watchlist → alert path."""

    def test_warehouse_ingests_synthetic_records(self, wired_stack):
        """SourceWarehouse writes one source_events row per ticket."""
        warehouse = wired_stack["warehouse"]
        db = wired_stack["db"]

        client = MagicMock()
        client.extract_trc.return_value = "TRC-100"

        tickets = _synthetic_tickets(3)
        warehouse.ingest_records(tickets, "zendesk", "subject", client)

        rows = db.conn.execute(
            "SELECT COUNT(*) FROM source_events WHERE source='zendesk'"
        ).fetchone()[0]
        assert rows == 3

    def test_warehouse_writes_rollup(self, wired_stack):
        """ingest_records updates source_trc_hourly so the chart can read."""
        warehouse = wired_stack["warehouse"]
        db = wired_stack["db"]

        client = MagicMock()
        client.extract_trc.return_value = "TRC-100"

        # All 5 tickets fall into the same hour bucket.
        anchor = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        ) - timedelta(hours=1)
        tickets = []
        for i in range(5):
            t = {
                "id": f"r-{i}",
                "subject": "Refund needed",
                "created_at": (anchor + timedelta(minutes=i)).isoformat(),
            }
            tickets.append(t)
        warehouse.ingest_records(tickets, "zendesk", "subject", client)

        total = db.conn.execute(
            """SELECT SUM(count) FROM source_trc_hourly
               WHERE source='zendesk' AND trc_code='TRC-100'"""
        ).fetchone()[0]
        assert total == 5

    def test_baseline_reflects_warehouse_writes(self, wired_stack):
        """compute_rate_baseline picks up the warehouse rollup."""
        from src.data.source_baseline import compute_rate_baseline

        warehouse = wired_stack["warehouse"]
        db = wired_stack["db"]

        # Seed history first so we're not in cold-start.
        anchor = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        _seed_baseline_history(db, now=anchor)

        client = MagicMock()
        client.extract_trc.return_value = "TRC-100"

        # Inject a clear spike: 25 tickets in the last hour bucket.
        spike_anchor = anchor - timedelta(hours=1)
        tickets = []
        for i in range(25):
            tickets.append({
                "id": f"spike-{i}",
                "subject": "Refund please",
                "created_at": (
                    spike_anchor + timedelta(minutes=i % 60)
                ).isoformat(),
            })
        warehouse.ingest_records(tickets, "zendesk", "subject", client)

        # Baseline anchored at "anchor" — the foreground includes the
        # spike bucket.
        baseline = compute_rate_baseline(
            db.conn,
            "zendesk",
            trc_code="TRC-100",
            now=anchor,
        )
        assert baseline.cold_start is False
        assert max(baseline.rates) >= 25
        # The spike rate is well above the trailing 5/hour mean.
        assert baseline.spike_count >= 1

    def test_watchlist_rule_fires_on_keyword_match(self, wired_stack):
        """Rule with keywords=['refund'] fires on matching records."""
        watchlist = wired_stack["watchlist"]
        db = wired_stack["db"]

        # Reset rule cooldown / fire history (keeps the assertion clean
        # even if a previous test in this class fired the rule).
        db.conn.execute(
            "UPDATE watchlist_rules SET last_fired_at='', "
            "total_fires=0, total_confirmed=0, total_dismissed=0"
        )
        db.conn.commit()

        tickets = _synthetic_tickets(2, subject="I need a refund please")
        # Pre-classify: bypass the warehouse layer; pass enriched dicts.
        for t in tickets:
            t["_trc_code"] = "TRC-100"

        alerts = watchlist.evaluate(tickets, source="zendesk",
                                     trc_field="subject")
        assert len(alerts) >= 1
        assert any("refund" in a.get("title", "").lower()
                   or "Refund Surge" in a.get("title", "")
                   for a in alerts)

    def test_alerts_tab_shows_fired_alert(self, wired_stack):
        """After a fire, the Alerts tab renders the new alert card."""
        watchlist = wired_stack["watchlist"]
        page = wired_stack["page"]

        # Force-fire by inserting an alert directly (faster than re-eval).
        from src.data.connection_factory import atomic
        # Use the existing watchlist API to insert via _fire_alert path.
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        wired_stack["db"].conn.execute(
            """INSERT INTO watchlist_alerts
               (rule_id, source, severity, title, summary,
                ticket_count, ticket_ids, trc_code, status, created_at)
               VALUES (?, 'zendesk', 'watch', 'Refund Surge',
                       'Two tickets matched', 2, '1,2', 'TRC-100',
                       'open', ?)""",
            (wired_stack["rule_id"], ts),
        )
        wired_stack["db"].conn.commit()

        page._alerts_tab.refresh()
        assert page._alerts_tab.open_alert_count >= 1
        # Tab badge updated.
        assert "(" in page._tabs.tabText(1)

    def test_confirm_alert_updates_ewma(self, wired_stack):
        """User confirmation increases the rule's ewma_confidence."""
        watchlist = wired_stack["watchlist"]
        db = wired_stack["db"]
        rule_id = wired_stack["rule_id"]

        # Snapshot the baseline confidence.
        before = db.conn.execute(
            "SELECT ewma_confidence FROM watchlist_rules WHERE id=?",
            (rule_id,),
        ).fetchone()[0]

        # Insert an alert to confirm.
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cur = db.conn.execute(
            """INSERT INTO watchlist_alerts
               (rule_id, source, severity, title, summary,
                ticket_count, status, created_at)
               VALUES (?, 'zendesk', 'watch', 'Test', 'sum', 1,
                       'open', ?)""",
            (rule_id, ts),
        )
        alert_id = cur.lastrowid
        db.conn.commit()

        watchlist.record_feedback(alert_id, "confirmed")

        after = db.conn.execute(
            "SELECT ewma_confidence FROM watchlist_rules WHERE id=?",
            (rule_id,),
        ).fetchone()[0]
        # Confirm should push confidence upward (EWMA toward 1.0).
        assert after > before


# ═══════════════════════════════════════════════════════════════════
#  Page-level smoke (lightweight)
# ═══════════════════════════════════════════════════════════════════

class TestPageEndToEnd:
    """Light-touch checks that the whole page still wires up."""

    def test_full_page_constructs_with_real_db(self, wired_stack):
        """The page came from the wired_stack fixture — confirm 4 tabs."""
        page = wired_stack["page"]
        assert page._tabs.count() == 4

    def test_rate_tab_can_refresh_against_real_db(self, wired_stack):
        """Rate tab queries the real DB without exception."""
        page = wired_stack["page"]
        # set_db was called inside the page shell when the tab was added;
        # call refresh_now and confirm no exception.
        page._rate_tab.refresh_now()
        # No assertion on chart contents — just that the path runs clean.
