"""
Unit tests for src/ui/dialogs/eula_dialog.py

Covers:
  - is_accepted / record_acceptance helpers (pure data)
  - Dialog constructs, Accept button starts disabled
  - Ticking the consent checkbox enables Accept
  - Accept triggers QDialog.Accepted, decline triggers QDialog.Rejected
  - ensure_accepted short-circuits when settings already record consent
  - ensure_accepted persists new consent via save_settings

Run: python -m pytest tests/test_eula_dialog.py -x -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from src.ui.dialogs import eula_dialog as eula


# ──────────────────────────────────────────────────────────────────
# Qt app (shared)
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# ──────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_is_accepted_true_for_current_version(self):
        settings = {"eula": {"version_accepted": eula.EULA_VERSION}}
        assert eula.is_accepted(settings) is True

    def test_is_accepted_false_for_old_version(self):
        settings = {"eula": {"version_accepted": "0"}}
        assert eula.is_accepted(settings) is False

    def test_is_accepted_false_when_missing(self):
        assert eula.is_accepted({}) is False

    def test_is_accepted_false_for_none_settings(self):
        assert eula.is_accepted(None) is False  # type: ignore[arg-type]

    def test_record_acceptance_mutates_in_place(self):
        settings = {"other": "keep"}
        eula.record_acceptance(settings)
        assert "eula" in settings
        assert settings["eula"]["version_accepted"] == eula.EULA_VERSION
        assert "accepted_at" in settings["eula"]
        assert "accepted_by" in settings["eula"]
        assert settings["other"] == "keep"  # not clobbered


# ──────────────────────────────────────────────────────────────────
# Dialog behaviour
# ──────────────────────────────────────────────────────────────────

class TestDialog:
    def test_accept_disabled_initially(self, qapp):
        d = eula.EulaDialog()
        assert d._accept_btn.isEnabled() is False
        d.close()

    def test_checking_consent_enables_accept(self, qapp):
        d = eula.EulaDialog()
        d._consent_box.setChecked(True)
        assert d._accept_btn.isEnabled() is True
        d.close()

    def test_unchecking_disables_accept_again(self, qapp):
        d = eula.EulaDialog()
        d._consent_box.setChecked(True)
        d._consent_box.setChecked(False)
        assert d._accept_btn.isEnabled() is False
        d.close()

    def test_accept_button_accepts_dialog(self, qapp):
        d = eula.EulaDialog()
        d._consent_box.setChecked(True)
        d._accept_btn.click()
        assert d.result() == QDialog.Accepted

    def test_decline_button_rejects_dialog(self, qapp):
        d = eula.EulaDialog()
        d.reject()
        assert d.result() == QDialog.Rejected


# ──────────────────────────────────────────────────────────────────
# ensure_accepted convenience entrypoint
# ──────────────────────────────────────────────────────────────────

class TestEnsureAccepted:
    def test_short_circuits_if_already_accepted(self, qapp):
        existing = {"eula": {"version_accepted": eula.EULA_VERSION}}
        with patch("src.data.settings_manager.load_settings", return_value=existing), \
             patch("src.data.settings_manager.save_settings") as save, \
             patch("src.ui.dialogs.eula_dialog.EulaDialog") as dialog_cls:
            assert eula.ensure_accepted() is True
            dialog_cls.assert_not_called()
            save.assert_not_called()

    def test_declined_returns_false(self, qapp):
        declined_dialog = MagicMock()
        declined_dialog.exec.return_value = QDialog.Rejected

        with patch("src.data.settings_manager.load_settings", return_value={}), \
             patch("src.data.settings_manager.save_settings") as save, \
             patch("src.ui.dialogs.eula_dialog.EulaDialog",
                    return_value=declined_dialog):
            assert eula.ensure_accepted() is False
            save.assert_not_called()

    def test_accepted_persists_to_settings(self, qapp):
        accepted_dialog = MagicMock()
        accepted_dialog.exec.return_value = QDialog.Accepted
        settings: dict = {}

        with patch("src.data.settings_manager.load_settings", return_value=settings), \
             patch("src.data.settings_manager.save_settings") as save, \
             patch("src.ui.dialogs.eula_dialog.EulaDialog",
                    return_value=accepted_dialog):
            assert eula.ensure_accepted() is True
            save.assert_called_once()
            saved = save.call_args[0][0]
            assert saved["eula"]["version_accepted"] == eula.EULA_VERSION
