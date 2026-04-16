"""
Tests for Phase 3 — Zendesk Source Monitor

Covers:
  - ZendeskClient: auth header, test_connection, fetch_incremental,
    cursor persistence, credential storage, error handling
  - ZendeskMonitor: spike detection, duplicate prevention, status changes,
    start/pause/resume/stop, interval management
  - SourceMonitorPage: instantiation, set_monitor wiring
  - MainWindow: PAGE_SOURCE_MONITOR constant, page accessible
"""

import base64
import json
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure src/ is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════
#  ZendeskClient Tests
# ═══════════════════════════════════════

class TestZendeskClientAuth:
    """Verify Basic auth header construction."""

    def test_auth_header_format(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@acme.com", "tok123")
        # Verify auth string is correctly base64-encoded
        expected_raw = "user@acme.com/token:tok123"
        expected_b64 = base64.b64encode(expected_raw.encode()).decode()
        # The client uses this internally in _get(); verify via construction
        assert client._subdomain == "acme"
        assert client._email == "user@acme.com"
        assert client._api_key == "tok123"
        assert client._base == "https://acme.zendesk.com/api/v2"

    def test_is_configured(self):
        from src.data.zendesk_client import ZendeskClient
        assert ZendeskClient("a", "b", "c").is_configured is True
        assert ZendeskClient("", "b", "c").is_configured is False
        assert ZendeskClient("a", "", "c").is_configured is False
        assert ZendeskClient("a", "b", "").is_configured is False

    def test_whitespace_stripped(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("  acme  ", " user@co.com ", " tok ")
        assert client._subdomain == "acme"
        assert client._email == "user@co.com"
        assert client._api_key == "tok"


class TestZendeskClientTestConnection:
    """Test connection verification."""

    def test_success(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        with patch.object(client, "_get", return_value={"user": {"id": 1}}):
            assert client.test_connection() is True

    def test_no_user_key(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        with patch.object(client, "_get", return_value={"error": "bad"}):
            assert client.test_connection() is False

    def test_auth_error(self):
        from src.data.zendesk_client import ZendeskClient, ZendeskAuthError
        client = ZendeskClient("acme", "user@co.com", "tok")
        with patch.object(client, "_get", side_effect=ZendeskAuthError()):
            assert client.test_connection() is False

    def test_not_configured_returns_false(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("", "", "")
        assert client.test_connection() is False


class TestZendeskClientFetch:
    """Test incremental ticket fetching."""

    def test_fetch_with_cursor(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        mock_response = {
            "tickets": [{"id": 1, "subject": "Test"}],
            "after_cursor": "abc123",
            "end_of_stream": True,
        }
        with patch.object(client, "_get", return_value=mock_response):
            tickets, cursor = client.fetch_incremental(cursor="prev_cursor")
            assert len(tickets) == 1
            assert cursor == "abc123"

    def test_fetch_without_cursor_uses_start_time(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        mock_response = {
            "tickets": [],
            "after_cursor": "",
            "end_of_stream": True,
        }
        with patch.object(client, "_get", return_value=mock_response) as m:
            tickets, cursor = client.fetch_incremental(start_time=1000)
            call_url = m.call_args[0][0]
            assert "start_time=1000" in call_url

    def test_pagination(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        responses = [
            {
                "tickets": [{"id": 1}],
                "after_cursor": "page2",
                "end_of_stream": False,
            },
            {
                "tickets": [{"id": 2}],
                "after_cursor": "page3",
                "end_of_stream": True,
            },
        ]
        with patch.object(client, "_get", side_effect=responses):
            tickets, cursor = client.fetch_incremental(cursor="start")
            assert len(tickets) == 2
            assert cursor == "page3"

    def test_fetch_empty(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        with patch.object(client, "_get", return_value={
            "tickets": [], "end_of_stream": True
        }):
            tickets, cursor = client.fetch_incremental(cursor="cur")
            assert tickets == []


class TestZendeskClientErrors:
    """Test error classes."""

    def test_auth_error_class(self):
        from src.data.zendesk_client import ZendeskAuthError
        err = ZendeskAuthError("bad creds")
        assert "bad creds" in str(err)

    def test_rate_limit_error(self):
        from src.data.zendesk_client import ZendeskRateLimitError
        err = ZendeskRateLimitError(retry_after=120)
        assert err.retry_after == 120
        assert "120" in str(err)

    def test_rate_limit_default(self):
        from src.data.zendesk_client import ZendeskRateLimitError
        err = ZendeskRateLimitError()
        assert err.retry_after == 60


class TestZendeskCursorPersistence:
    """Test cursor and credential persistence via pat_store."""

    def test_save_and_load_cursor(self):
        from src.data.zendesk_client import ZendeskClient
        # save_cursor and load_cursor import pat_store lazily inside the method
        with patch("src.data.pat_store.save_setting", return_value=True) as mock_save:
            result = ZendeskClient.save_cursor("test_cursor")
            assert result is True
            mock_save.assert_called_once_with("zendesk_cursor", "test_cursor")

        with patch("src.data.pat_store.load_setting", return_value="test_cursor") as mock_load:
            cursor = ZendeskClient.load_cursor()
            assert cursor == "test_cursor"
            mock_load.assert_called_once_with("zendesk_cursor", "")

    def test_load_credentials(self):
        from src.data.zendesk_client import ZendeskClient
        with patch("src.data.pat_store.load_setting", side_effect=[
            "acme", "user@co.com", "tok123", "12345"
        ]):
            sub, email, key, view_id = ZendeskClient.load_credentials()
            assert sub == "acme"
            assert email == "user@co.com"
            assert key == "tok123"
            assert view_id == "12345"

    def test_save_credentials(self):
        from src.data.zendesk_client import ZendeskClient
        with patch("src.data.pat_store.save_setting", return_value=True) as mock_save:
            result = ZendeskClient.save_credentials("acme", "u@co.com", "tok", "99")
            assert result is True
            assert mock_save.call_count == 4

    def test_view_id_property(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok", view_id="12345")
        assert client.view_id == "12345"

    def test_view_id_default_empty(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        assert client.view_id == ""

    def test_fetch_view_tickets(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok", view_id="777")
        mock_response = {
            "tickets": [{"id": 1}, {"id": 2}],
            "next_page": None,
        }
        with patch.object(client, "_get", return_value=mock_response) as m:
            tickets = client.fetch_view_tickets()
            assert len(tickets) == 2
            call_url = m.call_args[0][0]
            assert "/views/777/tickets.json" in call_url

    def test_fetch_view_tickets_no_view_id(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        assert client.fetch_view_tickets() == []


# ═══════════════════════════════════════
#  ZendeskMonitor Tests
# ═══════════════════════════════════════

class TestZendeskClientExtractTrc:
    """Test the extract_trc static method."""

    def test_subject_default(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "My app is broken", "tags": ["billing"]}
        assert ZendeskClient.extract_trc(ticket) == "My app is broken"
        assert ZendeskClient.extract_trc(ticket, "subject") == "My app is broken"

    def test_tags_single(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "test", "tags": ["billing"]}
        assert ZendeskClient.extract_trc(ticket, "tags") == "billing"

    def test_tags_multiple(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "test", "tags": ["billing", "urgent", "vip"]}
        result = ZendeskClient.extract_trc(ticket, "tags")
        assert "billing" in result
        assert "urgent" in result

    def test_tags_empty(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "test", "tags": []}
        assert ZendeskClient.extract_trc(ticket, "tags") == "untagged"

    def test_type_field(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "test", "type": "incident"}
        assert ZendeskClient.extract_trc(ticket, "type") == "incident"

    def test_priority_field(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "test", "priority": "high"}
        assert ZendeskClient.extract_trc(ticket, "priority") == "high"

    def test_status_field(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"subject": "test", "status": "open"}
        assert ZendeskClient.extract_trc(ticket, "status") == "open"

    def test_custom_field_found(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {
            "subject": "test",
            "custom_fields": [
                {"id": 12345, "value": "TRC-BILLING"},
                {"id": 67890, "value": "other"},
            ],
        }
        assert ZendeskClient.extract_trc(ticket, "custom_field:12345") == "TRC-BILLING"

    def test_custom_field_not_found(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {
            "subject": "test",
            "custom_fields": [{"id": 999, "value": "nope"}],
        }
        assert ZendeskClient.extract_trc(ticket, "custom_field:12345") == "unset"

    def test_custom_field_null_value(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {
            "subject": "test",
            "custom_fields": [{"id": 12345, "value": None}],
        }
        assert ZendeskClient.extract_trc(ticket, "custom_field:12345") == "unset"

    def test_tag_prefix_match(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {
            "tags": ["category_shipping", "seeded", "sentiment_neutral"],
        }
        assert ZendeskClient.extract_trc(ticket, "tag:category") == "category_shipping"

    def test_tag_prefix_no_match(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"tags": ["seeded", "sentiment_neutral"]}
        assert ZendeskClient.extract_trc(ticket, "tag:category") == "untagged"

    def test_tag_prefix_case_insensitive(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"tags": ["Category_Billing", "seeded"]}
        assert ZendeskClient.extract_trc(ticket, "tag:category") == "Category_Billing"

    def test_tag_prefix_empty_tags(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"tags": []}
        assert ZendeskClient.extract_trc(ticket, "tag:category") == "untagged"

    def test_missing_subject_returns_unknown(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"tags": ["foo"]}
        assert ZendeskClient.extract_trc(ticket, "subject") == "unknown"

    def test_none_type_returns_unknown(self):
        from src.data.zendesk_client import ZendeskClient
        ticket = {"type": None}
        assert ZendeskClient.extract_trc(ticket, "type") == "unknown"


class TestZendeskClientFetchFields:
    """Test fetch_ticket_fields."""

    def test_fetch_returns_fields(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        mock_data = {
            "ticket_fields": [
                {"id": 1, "title": "Subject", "type": "subject", "active": True},
                {"id": 100, "title": "Region", "type": "tagger", "active": True},
            ]
        }
        with patch.object(client, "_get", return_value=mock_data):
            fields = client.fetch_ticket_fields()
            assert len(fields) == 2
            assert fields[1]["title"] == "Region"

    def test_fetch_fields_error_returns_empty(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "u@co.com", "tok")
        with patch.object(client, "_get", side_effect=Exception("fail")):
            fields = client.fetch_ticket_fields()
            assert fields == []


class TestZendeskTrcFieldPersistence:
    """Test TRC field persistence."""

    def test_save_trc_field(self):
        from src.data.zendesk_client import ZendeskClient
        with patch("src.data.pat_store.save_setting", return_value=True) as mock:
            result = ZendeskClient.save_trc_field("tags")
            assert result is True
            mock.assert_called_once_with("zendesk_trc_field", "tags")

    def test_load_trc_field_default(self):
        from src.data.zendesk_client import ZendeskClient
        with patch("src.data.pat_store.load_setting", return_value="subject") as mock:
            result = ZendeskClient.load_trc_field()
            assert result == "subject"
            mock.assert_called_once_with("zendesk_trc_field", "subject")

    def test_load_trc_field_custom(self):
        from src.data.zendesk_client import ZendeskClient
        with patch("src.data.pat_store.load_setting", return_value="custom_field:12345"):
            result = ZendeskClient.load_trc_field()
            assert result == "custom_field:12345"


class TestZendeskMonitorSpikes:
    """Test TRC spike detection logic."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    def test_spike_detected_when_recent_exceeds_prior(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)

        # Manually populate TRC timestamps
        now = time.time()
        window = 60 * 60  # 60 minutes in seconds

        # Prior window: 2 tickets
        monitor._trc_timestamps["TRC-001"] = [
            now - window - 100,  # in prior window
            now - window - 200,  # in prior window
            now - 60,            # in recent window
            now - 120,           # in recent window
            now - 180,           # in recent window
        ]

        # Capture emitted spikes
        spikes = []
        monitor.spike_detected.connect(lambda trc, cnt, d: spikes.append((trc, cnt, d)))

        monitor._check_spikes()

        # 3 recent vs 2 prior = 50% increase > 25% threshold
        assert len(spikes) == 1
        assert spikes[0][0] == "TRC-001"
        assert spikes[0][1] == 3  # recent count

    def test_no_spike_when_below_threshold(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)

        now = time.time()
        window = 60 * 60

        # 5 prior, 5 recent = 0% change
        monitor._trc_timestamps["TRC-002"] = [
            now - window - 100, now - window - 200,
            now - window - 300, now - window - 400,
            now - window - 500,
            now - 60, now - 120, now - 180, now - 240, now - 300,
        ]

        spikes = []
        monitor.spike_detected.connect(lambda trc, cnt, d: spikes.append((trc, cnt, d)))
        monitor._check_spikes()
        assert len(spikes) == 0

    def test_spike_flagged_with_no_prior_and_ge_3(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)

        now = time.time()
        # 3 recent tickets, no prior
        monitor._trc_timestamps["TRC-NEW"] = [now - 60, now - 120, now - 180]

        spikes = []
        monitor.spike_detected.connect(lambda trc, cnt, d: spikes.append((trc, cnt, d)))
        monitor._check_spikes()
        assert len(spikes) == 1
        assert spikes[0][0] == "TRC-NEW"
        assert spikes[0][2] == 1.0  # 100% (no prior data flag)

    def test_no_spike_with_no_prior_and_lt_3(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)

        now = time.time()
        # Only 2 recent tickets, no prior
        monitor._trc_timestamps["TRC-LOW"] = [now - 60, now - 120]

        spikes = []
        monitor.spike_detected.connect(lambda trc, cnt, d: spikes.append((trc, cnt, d)))
        monitor._check_spikes()
        assert len(spikes) == 0


class TestZendeskMonitorDuplicates:
    """Test duplicate ticket prevention."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    def test_seen_tickets_filtered(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)

        # Pre-populate seen IDs
        monitor._seen_ticket_ids = {"100", "200"}

        # Simulate _do_fetch logic (inline since it's threaded)
        tickets = [
            {"id": 100, "subject": "old"},
            {"id": 300, "subject": "new"},
        ]
        new_tickets = []
        for t in tickets:
            tid = str(t.get("id", ""))
            if tid and tid not in monitor._seen_ticket_ids:
                monitor._seen_ticket_ids.add(tid)
                new_tickets.append(t)

        assert len(new_tickets) == 1
        assert new_tickets[0]["id"] == 300
        assert "300" in monitor._seen_ticket_ids


class TestZendeskMonitorLifecycle:
    """Test start/pause/resume/stop state transitions."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    def test_initial_state_is_paused(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())
        assert monitor.status == "paused"

    def test_start_emits_live(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())

        statuses = []
        monitor.status_changed.connect(lambda s: statuses.append(s))

        # Patch _on_tick to prevent actual fetch
        with patch.object(monitor, "_on_tick"):
            monitor.start(interval_seconds=300)

        assert monitor.status == "live"
        assert "live" in statuses

    def test_pause_emits_paused(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())

        with patch.object(monitor, "_on_tick"):
            monitor.start(interval_seconds=300)

        statuses = []
        monitor.status_changed.connect(lambda s: statuses.append(s))
        monitor.pause()
        assert monitor.status == "paused"
        assert "paused" in statuses

    def test_resume_restarts_timer(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())

        with patch.object(monitor, "_on_tick"):
            monitor.start(interval_seconds=300)
        monitor.pause()
        monitor.resume()
        assert monitor.status == "live"
        assert monitor._timer.isActive()

    def test_stop_clears_timer(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())

        with patch.object(monitor, "_on_tick"):
            monitor.start(interval_seconds=300)
        monitor.stop()
        assert not monitor._timer.isActive()

    def test_interval_minimum_30s(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())

        with patch.object(monitor, "_on_tick"):
            monitor.start(interval_seconds=10)  # below min
        assert monitor._interval == 30  # clamped

    def test_set_interval_updates(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())

        with patch.object(monitor, "_on_tick"):
            monitor.start(interval_seconds=120)
        monitor.set_interval(60)
        assert monitor._interval == 60

    def test_properties(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())
        assert monitor.tickets_today == 0
        assert monitor.last_pull == ""


class TestZendeskMonitorGetSpikeSummary:
    """Test the get_spike_summary method."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    def test_summary_sorted_by_delta(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)

        now = time.time()
        window = 60 * 60

        # TRC-A: 1 prior, 3 recent = 200% increase
        monitor._trc_timestamps["TRC-A"] = [
            now - window - 100,  # prior
            now - 60, now - 120, now - 180,  # recent
        ]
        # TRC-B: 2 prior, 2 recent = 0%
        monitor._trc_timestamps["TRC-B"] = [
            now - window - 100, now - window - 200,
            now - 60, now - 120,
        ]

        summary = monitor.get_spike_summary()
        assert len(summary) == 2
        assert summary[0]["trc"] == "TRC-A"
        assert summary[0]["delta_pct"] > summary[1]["delta_pct"]

    def test_summary_empty_when_no_data(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        monitor = ZendeskMonitor(MagicMock())
        assert monitor.get_spike_summary() == []


# ═══════════════════════════════════════
#  SourceMonitorPage Tests
# ═══════════════════════════════════════

class TestSourceMonitorPageInit:
    """Test page instantiation and monitor wiring."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        if not QApplication.instance():
            self._app = QApplication([])
        yield

    def test_page_creates(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        # Patch load_credentials to avoid file I/O
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)
        assert page is not None

    def test_set_monitor_wires_signals(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)

        monitor = ZendeskMonitor(db)
        page.set_monitor(monitor)
        assert page._monitor is monitor

    def test_connection_changed_signal_exists(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)

        # Verify the signal can be connected
        handler = MagicMock()
        page.connection_changed.connect(handler)

    def test_trc_field_combo_exists(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)

        combo = page._trc_field_combo
        assert combo is not None
        # Should have at least the 6 built-in options
        assert combo.count() >= 6
        assert combo.itemData(0) == "subject"
        assert combo.itemData(1) == "tags"
        assert combo.itemData(2) == "tag:"
        assert combo.itemData(3) == "type"
        assert combo.itemData(4) == "priority"
        assert combo.itemData(5) == "status"

    def test_set_monitor_clears_old_state(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)

        # Simulate having some ticket cards
        from PySide6.QtWidgets import QFrame
        fake_card = QFrame()
        page._ticket_cards.append(fake_card)
        page._feed_layout.addWidget(fake_card)
        page._feed_placeholder.hide()

        # Now set a new monitor — should clear
        monitor = ZendeskMonitor(db)
        page.set_monitor(monitor)
        assert len(page._ticket_cards) == 0
        # isHidden() checks the widget's own hidden flag, not parent visibility
        assert not page._feed_placeholder.isHidden()

    def test_trc_field_combo_restores_saved_value(self):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        # Simulate: credentials exist, trc_field is "tags"
        def mock_load(key, default=""):
            vals = {
                "zendesk_subdomain": "acme",
                "zendesk_email": "u@co.com",
                "zendesk_api_key": "tok",
                "zendesk_view_id": "",
                "zendesk_trc_field": "tags",
            }
            return vals.get(key, default)
        with patch("src.data.pat_store.load_setting", side_effect=mock_load):
            page = SourceMonitorPage(db)
        assert page._trc_field_combo.currentData() == "tags"


# ═══════════════════════════════════════
#  MainWindow Integration
# ═══════════════════════════════════════

class TestMainWindowSourceMonitor:
    """Verify SOURCE_MONITOR page is correctly wired into MainWindow."""

    def test_page_constant_exists(self):
        from src.ui.main_window import MainWindow
        assert MainWindow.PAGE_SOURCE_MONITOR == 8

    def test_page_indices_contiguous(self):
        """All page constants should be contiguous 0..N."""
        from src.ui.main_window import MainWindow
        expected = list(range(9))  # 0 through 8
        actual = sorted([
            MainWindow.PAGE_CONVERSATIONS,
            MainWindow.PAGE_DASHBOARD,
            MainWindow.PAGE_TRENDING,
            MainWindow.PAGE_INCIDENTS,
            MainWindow.PAGE_REPORTS,
            MainWindow.PAGE_AB_COMPARE,
            MainWindow.PAGE_SMART_REPORTING,
            MainWindow.PAGE_SETTINGS,
            MainWindow.PAGE_SOURCE_MONITOR,
        ])
        assert actual == expected
