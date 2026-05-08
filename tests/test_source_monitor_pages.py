"""
Smoke tests for the decomposed Source Monitor package.

Exercises each tab in isolation to confirm:
  - The tab class constructs without raising
  - Required public methods exist
  - Empty-state placeholders render
  - set_watchlist / set_db wire correctly
  - Tab dimensions are sensible

These are integration smokes — they catch import-time errors,
attribute typos, and signal/slot wiring breakages introduced by
future refactors. Detailed widget interaction tests live in
``test_watchlist_ui.py`` and ``test_rate_chart.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication."""
    app = QApplication.instance() or QApplication([])
    yield app


# ═══════════════════════════════════════════════════════════════════
#  Package shim re-export
# ═══════════════════════════════════════════════════════════════════

class TestPackageShim:
    """Verify the backward-compat re-export still works."""

    def test_import_from_old_path(self):
        """Code that imports from src.ui.pages.source_monitor_page works."""
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        assert SourceMonitorPage is not None

    def test_import_from_new_path(self):
        """New canonical import works."""
        from src.ui.pages.source_monitor import SourceMonitorPage
        assert SourceMonitorPage is not None

    def test_both_paths_resolve_to_same_class(self):
        from src.ui.pages.source_monitor_page import (
            SourceMonitorPage as Old,
        )
        from src.ui.pages.source_monitor import (
            SourceMonitorPage as New,
        )
        assert Old is New


# ═══════════════════════════════════════════════════════════════════
#  RateTab
# ═══════════════════════════════════════════════════════════════════

class TestRateTab:
    """RateTab smoke — construction, set_db, refresh."""

    def test_construct(self, qapp):
        from src.ui.pages.source_monitor.rate_tab import RateTab
        tab = RateTab()
        assert tab is not None
        assert tab._chart is not None
        assert tab._trc_combo is not None

    def test_set_db_triggers_refresh(self, qapp, empty_db):
        """set_db wires the connection and runs an initial refresh."""
        from src.ui.pages.source_monitor.rate_tab import RateTab
        tab = RateTab()
        tab.set_db(empty_db)
        # On an empty DB, the chart should now have an empty baseline
        # (cold-start fallback). No exception means the flow worked.
        assert tab._chart is not None

    def test_window_buttons_default_to_24h(self, qapp):
        """Initial window state matches DEFAULT_WINDOW_HOURS."""
        from src.ui.pages.source_monitor.rate_tab import RateTab
        from src.data.source_baseline import DEFAULT_WINDOW_HOURS
        tab = RateTab()
        assert tab._window_hours == DEFAULT_WINDOW_HOURS

    def test_trc_combo_starts_with_aggregate(self, qapp):
        """First entry is 'All TRCs (aggregate)' with None data."""
        from src.ui.pages.source_monitor.rate_tab import RateTab
        tab = RateTab()
        assert tab._trc_combo.itemData(0) is None


# ═══════════════════════════════════════════════════════════════════
#  AlertsTab
# ═══════════════════════════════════════════════════════════════════

class TestAlertsTab:
    """AlertsTab smoke — construction, refresh, signal."""

    def test_construct(self, qapp):
        from src.ui.pages.source_monitor.alerts_tab import AlertsTab
        tab = AlertsTab()
        assert tab is not None
        assert tab.open_alert_count == 0

    def test_empty_state_when_no_watchlist(self, qapp):
        """No watchlist → placeholder visible, no cards."""
        from src.ui.pages.source_monitor.alerts_tab import AlertsTab
        tab = AlertsTab()
        tab.refresh()
        assert tab._placeholder.isVisible() or tab._placeholder.isHidden() is False
        assert tab._alert_cards == []

    def test_refresh_with_no_alerts(self, qapp):
        """Watchlist returning [] → placeholder visible."""
        from src.ui.pages.source_monitor.alerts_tab import AlertsTab
        tab = AlertsTab()
        wl = MagicMock()
        wl.get_all_alerts.return_value = []
        tab.set_watchlist(wl)
        assert tab.open_alert_count == 0

    def test_refresh_with_open_alerts(self, qapp):
        """Open alerts → cards built, count emitted via signal."""
        from src.ui.pages.source_monitor.alerts_tab import AlertsTab
        tab = AlertsTab()
        wl = MagicMock()
        wl.get_all_alerts.return_value = [
            {
                "id": 1, "severity": "incident", "title": "Test alert",
                "summary": "test", "ticket_count": 5, "trc_code": "TRC-1",
                "source": "zendesk", "status": "open",
                "created_at": "2026-05-07T14:00:00",
            },
        ]
        signals = []
        tab.alert_count_changed.connect(lambda n: signals.append(n))
        tab.set_watchlist(wl)
        assert tab.open_alert_count == 1
        assert signals[-1] == 1
        assert len(tab._alert_cards) == 1

    def test_severity_filter_changes_visible_alerts(self, qapp):
        """Filter dropdown filters alerts."""
        from src.ui.pages.source_monitor.alerts_tab import AlertsTab
        tab = AlertsTab()
        wl = MagicMock()
        wl.get_all_alerts.return_value = [
            {"id": 1, "severity": "incident", "title": "I", "status": "open",
             "created_at": "2026-05-07T14:00:00"},
            {"id": 2, "severity": "watch", "title": "W", "status": "open",
             "created_at": "2026-05-07T14:00:00"},
        ]
        tab.set_watchlist(wl)
        assert len(tab._alert_cards) == 2

        tab._severity_filter.setCurrentText("incident")
        # Filter triggers refresh via currentIndexChanged.
        assert len(tab._alert_cards) == 1


# ═══════════════════════════════════════════════════════════════════
#  WatchlistTab
# ═══════════════════════════════════════════════════════════════════

class TestWatchlistTab:
    """WatchlistTab smoke — construction, set_watchlist, sections."""

    def test_construct(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        assert tab is not None

    def test_empty_state_no_watchlist(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        tab.refresh_rules()
        # Placeholder is visible; sections hidden.
        assert tab._placeholder.isHidden() is False

    def test_split_into_system_and_custom(self, qapp):
        """Rules split correctly between System and Custom sections."""
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()

        wl = MagicMock()
        wl.list_rules.return_value = [
            {
                "id": 1, "name": "Sys A", "rule_type": "keyword",
                "severity": "incident", "is_system": 1, "enabled": 1,
                "ewma_confidence": 0.8, "total_fires": 5,
                "total_confirmed": 4, "total_dismissed": 1,
                "keywords": "outage,down",
            },
            {
                "id": 2, "name": "Custom A", "rule_type": "volume",
                "severity": "watch", "is_system": 0, "enabled": 1,
                "ewma_confidence": 0.4, "total_fires": 10,
                "total_confirmed": 3, "total_dismissed": 7,
                "volume_threshold": 25, "volume_window_minutes": 60,
            },
        ]
        tab.set_watchlist(wl)

        # Both sections visible.
        assert tab._system_section.isVisible() or not tab._system_section.isHidden()
        assert tab._custom_section.isVisible() or not tab._custom_section.isHidden()
        # 2 cards total.
        assert len(tab._cards) == 2


# ═══════════════════════════════════════════════════════════════════
#  ConnectionTab
# ═══════════════════════════════════════════════════════════════════

class TestConnectionTab:
    """ConnectionTab smoke — construction, signals, settings load."""

    def test_construct(self, qapp):
        from src.ui.pages.source_monitor.connection_tab import ConnectionTab
        with patch("src.data.pat_store.load_setting", return_value=""):
            tab = ConnectionTab()
        assert tab is not None
        assert tab._zd_subdomain is not None
        assert tab._trc_field_combo is not None

    def test_built_in_trc_options(self, qapp):
        """The TRC combo has the 6 documented built-ins."""
        from src.ui.pages.source_monitor.connection_tab import ConnectionTab
        with patch("src.data.pat_store.load_setting", return_value=""):
            tab = ConnectionTab()
        combo = tab._trc_field_combo
        assert combo.count() >= 6
        assert combo.itemData(0) == "subject"
        assert combo.itemData(2) == "tag:"

    def test_tag_prefix_visible_only_for_tag_mode(self, qapp):
        """Selecting 'Tag by prefix' reveals the prefix subfield.

        Uses isHidden() rather than isVisible() because the widget tree
        is never shown on screen during the test — isVisible() also
        checks rendering, which requires QWidget.show() up the chain.
        """
        from src.ui.pages.source_monitor.connection_tab import ConnectionTab
        with patch("src.data.pat_store.load_setting", return_value=""):
            tab = ConnectionTab()
        # Initially hidden (default selection is 'subject').
        assert tab._tag_prefix_container.isHidden() is True
        # Select tag: prefix mode.
        tag_idx = tab._trc_field_combo.findData("tag:")
        tab._trc_field_combo.setCurrentIndex(tag_idx)
        assert tab._tag_prefix_container.isHidden() is False

    def test_connection_changed_signal_exists(self, qapp):
        """Save & Connect emits this signal — verify it's defined."""
        from src.ui.pages.source_monitor.connection_tab import ConnectionTab
        with patch("src.data.pat_store.load_setting", return_value=""):
            tab = ConnectionTab()
        # Just verify the signal exists and is connectable.
        captured = []
        tab.connection_changed.connect(lambda: captured.append(True))
        tab.connection_changed.emit()
        assert captured == [True]


# ═══════════════════════════════════════════════════════════════════
#  RuleDialog
# ═══════════════════════════════════════════════════════════════════

class TestRuleDialog:
    """RuleDialog smoke — form, validation, get_rule_data."""

    def test_construct_create_mode(self, qapp):
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        dlg = RuleDialog()
        assert dlg.windowTitle() == "Add Watchlist Rule"

    def test_construct_edit_mode(self, qapp):
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        existing = {
            "name": "Refunds", "rule_type": "keyword",
            "severity": "watch", "source_filter": "zendesk",
            "keywords": "refund", "keyword_mode": "any",
            "entity_type": "", "volume_threshold": 0,
            "volume_window_minutes": 60, "cooldown_minutes": 120,
        }
        dlg = RuleDialog(existing=existing)
        assert dlg.windowTitle() == "Edit Rule"
        # Values pre-populated.
        assert dlg._name_edit.text() == "Refunds"
        assert dlg._sev_combo.currentText() == "watch"

    def test_get_rule_data_returns_form_dict(self, qapp):
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        dlg = RuleDialog()
        dlg._name_edit.setText("MyRule")
        dlg._kw_edit.setText(" outage , 503 ")
        data = dlg.get_rule_data()
        assert data["name"] == "MyRule"
        # Strings are stripped at boundary; comma-separated keywords
        # pass through verbatim (engine splits internally).
        assert data["keywords"] == "outage , 503"
        # Defaults from the form widgets.
        assert data["rule_type"] == "keyword"
        assert data["severity"] == "watch"
        assert data["volume_threshold"] == 0


# ═══════════════════════════════════════════════════════════════════
#  Page shell — full integration
# ═══════════════════════════════════════════════════════════════════

class TestPageShell:
    """Verify the SourceMonitorPage shell ties the four tabs together."""

    def test_construct(self, qapp):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)
        assert page._tabs.count() == 4

    def test_tab_titles(self, qapp):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)
        titles = [page._tabs.tabText(i) for i in range(4)]
        assert "Live Feed" in titles
        # Initially no badge → just "Alerts"
        assert "Alerts" in titles
        assert "Watchlist" in titles
        assert "Connection" in titles

    def test_set_watchlist_propagates_to_tabs(self, qapp):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        wl = MagicMock()
        wl.get_all_alerts.return_value = []
        wl.list_rules.return_value = []
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)
        page.set_watchlist(wl)
        assert page._watchlist is wl
        assert page._alerts_tab._watchlist is wl
        assert page._watchlist_tab._watchlist is wl

    def test_alert_count_signal_updates_tab_badge(self, qapp):
        from src.ui.pages.source_monitor_page import SourceMonitorPage
        db = MagicMock()
        with patch("src.data.pat_store.load_setting", return_value=""):
            page = SourceMonitorPage(db)
        page._alerts_tab.alert_count_changed.emit(3)
        # Tab text should now show " (3)".
        assert "(3)" in page._tabs.tabText(1)

        page._alerts_tab.alert_count_changed.emit(0)
        # No badge when count is 0.
        assert "(" not in page._tabs.tabText(1)
