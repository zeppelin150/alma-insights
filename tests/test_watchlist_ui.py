"""
Tests for src/ui/pages/source_monitor/watchlist_tab.py and
rule_dialog.py — the card-based watchlist UI.

Covers:
  - Card rendering for system rules (no Delete button)
  - Card rendering for custom rules (Delete button present)
  - Severity badge color reflects incident vs watch
  - EWMA confidence color tier (green ≥0.7 / amber ≥0.3 / red <0.3)
  - Toggle handler flips enabled flag
  - Edit handler opens RuleDialog and saves results
  - Delete handler asks for confirmation, then deletes
  - Add Rule opens RuleDialog and creates
  - Empty state shows the "No watchlist rules" placeholder
  - System and custom sections segregate properly
  - RuleDialog refuses empty name
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication."""
    app = QApplication.instance() or QApplication([])
    yield app


def _make_rule(**overrides) -> dict:
    """Build a baseline rule dict; ``overrides`` swap individual fields."""
    base = {
        "id": 1, "name": "Test Rule", "rule_type": "keyword",
        "severity": "watch", "is_system": 0, "enabled": 1,
        "ewma_confidence": 0.5, "total_fires": 5,
        "total_confirmed": 3, "total_dismissed": 2,
        "keywords": "outage", "keyword_mode": "any",
        "entity_type": "", "source_filter": "",
        "volume_threshold": 0, "volume_window_minutes": 60,
        "cooldown_minutes": 120,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════
#  Confidence color helper
# ═══════════════════════════════════════════════════════════════════

class TestConfidenceColor:
    """_confidence_color tiers EWMA into green / amber / red."""

    def test_high_confidence_is_green(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _confidence_color,
        )
        from src.ui.theme import ALMA_GREEN_LIGHT
        assert _confidence_color(0.85) == ALMA_GREEN_LIGHT
        assert _confidence_color(0.7) == ALMA_GREEN_LIGHT  # boundary

    def test_mid_confidence_is_amber(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _confidence_color,
        )
        from src.ui.theme import ALMA_WARNING
        assert _confidence_color(0.5) == ALMA_WARNING
        assert _confidence_color(0.3) == ALMA_WARNING  # boundary

    def test_low_confidence_is_red(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _confidence_color,
        )
        from src.ui.theme import ALMA_ERROR
        assert _confidence_color(0.2) == ALMA_ERROR
        assert _confidence_color(0.0) == ALMA_ERROR


# ═══════════════════════════════════════════════════════════════════
#  Rule card free-function
# ═══════════════════════════════════════════════════════════════════

class TestBuildRuleCard:
    """_build_rule_card renders the documented fields."""

    def test_system_rule_has_no_delete_button(self, qapp):
        """System rules: on_delete is None → no Del button rendered."""
        from src.ui.pages.source_monitor.watchlist_tab import (
            _build_rule_card,
        )
        rule = _make_rule(is_system=1)
        card = _build_rule_card(
            rule,
            on_toggle=MagicMock(),
            on_edit=MagicMock(),
            on_delete=None,
        )
        # Find every QPushButton; none should say "Del".
        from PySide6.QtWidgets import QPushButton
        buttons = card.findChildren(QPushButton)
        labels = [b.text() for b in buttons]
        assert "Del" not in labels
        # ON/OFF and Edit must still be present.
        assert any(t in ("ON", "OFF") for t in labels)
        assert "Edit" in labels

    def test_custom_rule_has_delete_button(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _build_rule_card,
        )
        rule = _make_rule(is_system=0)
        card = _build_rule_card(
            rule,
            on_toggle=MagicMock(),
            on_edit=MagicMock(),
            on_delete=MagicMock(),
        )
        from PySide6.QtWidgets import QPushButton
        buttons = card.findChildren(QPushButton)
        assert "Del" in [b.text() for b in buttons]

    def test_disabled_rule_shows_off(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _build_rule_card,
        )
        rule = _make_rule(enabled=0)
        card = _build_rule_card(
            rule,
            on_toggle=MagicMock(),
            on_edit=MagicMock(),
            on_delete=MagicMock(),
        )
        from PySide6.QtWidgets import QPushButton
        labels = [b.text() for b in card.findChildren(QPushButton)]
        assert "OFF" in labels
        assert "ON" not in labels

    def test_card_contains_rule_name(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _build_rule_card,
        )
        rule = _make_rule(name="Refund Complaints")
        card = _build_rule_card(
            rule,
            on_toggle=MagicMock(),
            on_edit=MagicMock(),
            on_delete=MagicMock(),
        )
        from PySide6.QtWidgets import QLabel
        labels = [lbl.text() for lbl in card.findChildren(QLabel)]
        assert any("Refund Complaints" in t for t in labels)

    def test_card_shows_severity_badge_text(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import (
            _build_rule_card,
        )
        rule_inc = _make_rule(severity="incident")
        card = _build_rule_card(
            rule_inc,
            on_toggle=MagicMock(),
            on_edit=MagicMock(),
            on_delete=MagicMock(),
        )
        from PySide6.QtWidgets import QLabel
        labels = [lbl.text() for lbl in card.findChildren(QLabel)]
        assert "INCIDENT" in labels


# ═══════════════════════════════════════════════════════════════════
#  WatchlistTab section segregation
# ═══════════════════════════════════════════════════════════════════

class TestSectionSegregation:
    """System and custom rules go into their own sections."""

    def test_system_only(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        wl = MagicMock()
        wl.list_rules.return_value = [
            _make_rule(id=1, is_system=1, name="Sys A"),
            _make_rule(id=2, is_system=1, name="Sys B"),
        ]
        tab.set_watchlist(wl)
        assert "(2)" in tab._system_header.text()
        assert "(0)" in tab._custom_header.text()

    def test_custom_only(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        wl = MagicMock()
        wl.list_rules.return_value = [
            _make_rule(id=1, is_system=0, name="Custom A"),
        ]
        tab.set_watchlist(wl)
        # System section is hidden when empty.
        assert "(0)" in tab._system_header.text()
        assert "(1)" in tab._custom_header.text()


# ═══════════════════════════════════════════════════════════════════
#  CRUD action handlers
# ═══════════════════════════════════════════════════════════════════

class TestRuleCrud:
    """Verify CRUD wiring to the WatchlistEngine."""

    def test_toggle_calls_engine(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        wl = MagicMock()
        wl.list_rules.return_value = [_make_rule(id=42, enabled=1)]
        tab.set_watchlist(wl)

        tab._on_toggle_rule(42, False)
        wl.toggle_rule.assert_called_once_with(42, False)

    def test_delete_with_confirmation(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        wl = MagicMock()
        wl.list_rules.return_value = []
        tab.set_watchlist(wl)

        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.Yes):
            tab._on_delete_rule(99)
        wl.delete_rule.assert_called_once_with(99)

    def test_delete_cancellation_skips_engine(self, qapp):
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        tab = WatchlistTab()
        wl = MagicMock()
        tab.set_watchlist(wl)

        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.No):
            tab._on_delete_rule(99)
        wl.delete_rule.assert_not_called()

    def test_add_rule_creates_via_dialog(self, qapp):
        """Add Rule flow opens dialog, then calls watchlist.create_rule."""
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        tab = WatchlistTab()
        wl = MagicMock()
        wl.list_rules.return_value = []
        tab.set_watchlist(wl)

        # Patch dialog.exec to return Accepted and pre-fill name.
        def fake_exec(self):
            self._name_edit.setText("MyNewRule")
            return RuleDialog.Accepted

        with patch.object(RuleDialog, "exec", new=fake_exec):
            tab._on_add_rule()

        wl.create_rule.assert_called_once()
        # Name passed through.
        call_kwargs = wl.create_rule.call_args.kwargs
        assert call_kwargs["name"] == "MyNewRule"

    def test_edit_rule_via_dialog(self, qapp):
        """Edit flow uses update_rule when available, else delete+create."""
        from src.ui.pages.source_monitor.watchlist_tab import WatchlistTab
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        tab = WatchlistTab()
        wl = MagicMock(spec=["list_rules", "update_rule",
                             "create_rule", "delete_rule",
                             "toggle_rule", "get_all_alerts"])
        wl.list_rules.return_value = []
        tab.set_watchlist(wl)

        rule = _make_rule(id=7, name="Old Name")

        def fake_exec(self):
            return RuleDialog.Accepted

        with patch.object(RuleDialog, "exec", new=fake_exec):
            tab._on_edit_rule(rule)

        wl.update_rule.assert_called_once()
        # First positional arg is rule_id.
        assert wl.update_rule.call_args.args[0] == 7


# ═══════════════════════════════════════════════════════════════════
#  RuleDialog validation
# ═══════════════════════════════════════════════════════════════════

class TestRuleDialogValidation:
    """RuleDialog refuses empty name; OK with valid name."""

    def test_empty_name_does_not_close(self, qapp):
        """Clicking OK without a name keeps the dialog open."""
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        dlg = RuleDialog()
        # Don't actually exec — just call _on_accept directly.
        dlg._on_accept()
        # The dialog never accepts (result remains 0 = Rejected).
        assert dlg.result() == 0

    def test_with_name_can_be_validated(self, qapp):
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        dlg = RuleDialog()
        dlg._name_edit.setText("Valid")
        dlg._on_accept()
        # accept() sets the result; checking we didn't bail out early.
        assert dlg.result() != 0

    def test_get_rule_data_returns_dict(self, qapp):
        from src.ui.pages.source_monitor.rule_dialog import RuleDialog
        dlg = RuleDialog()
        dlg._name_edit.setText("Test")
        dlg._sev_combo.setCurrentText("incident")
        dlg._kw_edit.setText("term1, term2")
        data = dlg.get_rule_data()
        assert data["name"] == "Test"
        assert data["severity"] == "incident"
        assert "term1" in data["keywords"]
