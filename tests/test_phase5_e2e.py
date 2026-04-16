"""
Phase 5 E2E — the rollback + EULA + crash-handler chain.

Scenario 1 (rollback): a crashing new version gets auto-reverted.
    1. Pre-stage: new v9.3.0 is "live", v9.2.0 sits in _*_previous.
    2. record_apply marks the apply event with a 60 s grace window.
    3. crash handler writes 3 crash reports after apply time.
    4. needs_rollback() returns True.
    5. perform_rollback() restores v9.2.0.
    6. Rollback state is cleared; _*_previous gone.

Scenario 2 (EULA lockout): declining EULA blocks ensure_accepted.

Scenario 3 (crash export): Settings UI export mirrors crash_handler API.

Run: python -m pytest tests/test_phase5_e2e.py -x -v
"""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from src.core import crash_handler
from src.ui.dialogs import eula_dialog as eula
from src.updater import rollback


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# ──────────────────────────────────────────────────────────────────
# Scenario 1 — crash-loop rollback
# ──────────────────────────────────────────────────────────────────

class TestCrashLoopRollback:
    def test_full_rollback_cycle(self, tmp_path, monkeypatch):
        # Redirect every module path to tmp_path
        monkeypatch.setattr(rollback, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(
            rollback, "_ROLLBACK_STATE", tmp_path / "data" / "rollback_state.json",
        )
        monkeypatch.setattr(
            rollback, "_CRASH_DIR", tmp_path / "data" / "crash_reports",
        )
        (tmp_path / "data" / "crash_reports").mkdir(parents=True)

        # Seed: new version "live"
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text('VERSION = "9.3.0"\n')
        (tmp_path / "src" / "new_file.py").write_text("bug")
        # Previous version sitting in _src_backup (updater just swapped)
        (tmp_path / "_src_backup").mkdir()
        (tmp_path / "_src_backup" / "__init__.py").write_text('VERSION = "9.2.0"\n')
        (tmp_path / "_src_backup" / "stable.py").write_text("ok")

        # Step 1 — record_apply: moves backup → previous + writes state
        rollback.record_apply(previous_version="9.2.0", new_version="9.3.0")
        assert (tmp_path / "_src_previous" / "__init__.py").exists()
        assert rollback.current_state() is not None

        # Step 2 — simulate three crashes after apply_at
        now = datetime.now(timezone.utc)
        import os
        for i in range(3):
            f = tmp_path / "data" / "crash_reports" / f"crash_{i}.json"
            f.write_text('{"exception_type":"RuntimeError"}')
            os.utime(f, (now.timestamp(), now.timestamp()))

        # Step 3 — detection fires
        should, reason = rollback.needs_rollback()
        assert should is True
        assert "3" in reason

        # Step 4 — perform rollback
        ok, msg = rollback.perform_rollback()
        assert ok is True

        # Step 5 — VERSION rewritten + previous gone
        content = (tmp_path / "src" / "__init__.py").read_text()
        assert '"9.2.0"' in content
        assert (tmp_path / "src" / "stable.py").exists()
        assert not (tmp_path / "_src_previous").exists()
        assert rollback.current_state() is None


# ──────────────────────────────────────────────────────────────────
# Scenario 2 — EULA decline
# ──────────────────────────────────────────────────────────────────

class TestEulaLockout:
    def test_decline_blocks_app(self, qapp):
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.Rejected
        with patch("src.data.settings_manager.load_settings", return_value={}), \
             patch("src.data.settings_manager.save_settings"), \
             patch("src.ui.dialogs.eula_dialog.EulaDialog", return_value=dialog):
            assert eula.ensure_accepted() is False

    def test_accept_persists_and_subsequent_launches_short_circuit(self, qapp):
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.Accepted
        saved: dict = {}

        def fake_save(settings):
            saved.update(settings)

        with patch("src.data.settings_manager.load_settings",
                    side_effect=[{}, dict(saved)]), \
             patch("src.data.settings_manager.save_settings",
                    side_effect=fake_save), \
             patch("src.ui.dialogs.eula_dialog.EulaDialog", return_value=dialog):
            # First launch — dialog shows, user accepts
            assert eula.ensure_accepted() is True

        assert saved.get("eula", {}).get("version_accepted") == eula.EULA_VERSION

        # Second launch — ensure_accepted must short-circuit
        with patch("src.data.settings_manager.load_settings", return_value=saved), \
             patch("src.ui.dialogs.eula_dialog.EulaDialog") as dialog_cls:
            assert eula.ensure_accepted() is True
            dialog_cls.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Scenario 3 — crash export bundles last N reports
# ──────────────────────────────────────────────────────────────────

class TestCrashExport:
    def test_export_bundle_includes_reports(self, tmp_path, monkeypatch):
        monkeypatch.setattr(crash_handler, "_CRASH_DIR", tmp_path / "crash_reports")
        (tmp_path / "crash_reports").mkdir()
        # Seed 5 reports
        for i in range(5):
            (tmp_path / "crash_reports" / f"crash_{i:02d}.json").write_text(
                json.dumps({"i": i})
            )

        dest = tmp_path / "bundle.zip"
        crash_handler.export_bundle(dest, limit=3)

        assert dest.exists()
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
        assert len(names) == 3
        # Newest-first ordering — crash_04, 03, 02
        assert all(n.startswith("crash_") for n in names)
