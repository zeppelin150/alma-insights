"""
Tests for src/ui/pages/guru_wip_page.py — the Guru WIP placeholder.

Verifies:
  - Placeholder constructs without exceptions
  - Compatibility shims (set_friction_pipeline, etc.) accept calls
  - connection_changed signal exists for API parity with GuruPage
  - Planned-features list renders all expected items
  - Workbench mockup paints without exception (empty / sized)
  - "Enable experimental UI" button is wired
  - MainWindow routes to GuruWipPage when flag is False, GuruPage when True
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication."""
    app = QApplication.instance() or QApplication([])
    yield app


# ═══════════════════════════════════════════════════════════════════
#  Placeholder construction + compat
# ═══════════════════════════════════════════════════════════════════

class TestGuruWipPageConstruction:
    """The wrapper page builds and exposes the same API as GuruPage."""

    def test_construct_without_db(self, qapp):
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = GuruWipPage()
        assert page is not None

    def test_construct_with_db(self, qapp):
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = GuruWipPage(db_manager=MagicMock())
        assert page is not None

    def test_compat_shims_accept_calls(self, qapp):
        """All setter methods that GuruPage defines must exist as no-ops."""
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = GuruWipPage()
        # None of these should raise.
        page.set_friction_pipeline(MagicMock())
        page.set_content_pipeline(MagicMock())
        page.set_effectiveness_tracker(MagicMock())
        page.set_drilldown_panel(MagicMock())
        page.set_guru_client(MagicMock())

    def test_connection_changed_signal_defined(self, qapp):
        """API parity with GuruPage — used by MainWindow signal wiring."""
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = GuruWipPage()
        captured = []
        page.connection_changed.connect(lambda: captured.append(True))
        # We never emit; confirm the signal at least connects.
        page.connection_changed.emit()
        assert captured == [True]


# ═══════════════════════════════════════════════════════════════════
#  Placeholder content
# ═══════════════════════════════════════════════════════════════════

class TestGuruWipPageContent:
    """The placeholder lists the planned features and provides the
    escape-hatch button."""

    def test_planned_features_constant_has_six_items(self, qapp):
        from src.ui.pages.guru_wip_page import PLANNED_FEATURES
        # The product spec lists 5 capabilities + 1 closed-loop bullet.
        assert len(PLANNED_FEATURES) == 6
        assert any("friction" in f.lower() for f in PLANNED_FEATURES)
        assert any("redline" in f.lower() or "diff" in f.lower()
                   for f in PLANNED_FEATURES)

    def test_enable_button_exists(self, qapp):
        """The escape-hatch button is reachable from outside."""
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = GuruWipPage()
        assert page._enable_btn is not None
        assert "Enable" in page._enable_btn.text()


# ═══════════════════════════════════════════════════════════════════
#  Mockup widget paints
# ═══════════════════════════════════════════════════════════════════

class TestWorkbenchMockupWidget:
    """The QPainter wireframe paints without exceptions for typical sizes."""

    def test_construct(self, qapp):
        from src.ui.pages.guru_wip_page import WorkbenchMockupWidget
        w = WorkbenchMockupWidget()
        assert w is not None

    def test_paint_at_default_size(self, qapp):
        from src.ui.pages.guru_wip_page import WorkbenchMockupWidget
        w = WorkbenchMockupWidget()
        image = QImage(420, 360, QImage.Format_ARGB32)
        image.fill(0)
        w.resize(420, 360)
        w.render(image)
        assert not image.isNull()

    def test_paint_at_small_size(self, qapp):
        """Tiny window — must not crash."""
        from src.ui.pages.guru_wip_page import WorkbenchMockupWidget
        w = WorkbenchMockupWidget()
        image = QImage(200, 140, QImage.Format_ARGB32)
        image.fill(0)
        w.resize(200, 140)
        w.render(image)

    def test_paint_at_large_size(self, qapp):
        """Generous window — proportions scale."""
        from src.ui.pages.guru_wip_page import WorkbenchMockupWidget
        w = WorkbenchMockupWidget()
        image = QImage(1200, 800, QImage.Format_ARGB32)
        image.fill(0)
        w.resize(1200, 800)
        w.render(image)


# ═══════════════════════════════════════════════════════════════════
#  Settings flag round-trip
# ═══════════════════════════════════════════════════════════════════

class TestEnableExperimentalFlag:
    """The 'Enable experimental UI' button persists the flag correctly."""

    def test_enable_writes_settings_flag(self, qapp, tmp_path, monkeypatch):
        """Click flow saves ``guru.experimental_ui_enabled = True``."""
        from src.ui.pages.guru_wip_page import GuruWipPage

        # Patch settings_manager to use a tmp path so we don't touch the
        # real settings.yaml on the test machine.
        captured = {}

        def fake_load_settings():
            return dict(captured.get("settings", {}))

        def fake_save_settings(settings):
            captured["settings"] = dict(settings)

        from PySide6.QtWidgets import QMessageBox
        with (
            patch(
                "src.data.settings_manager.load_settings",
                side_effect=fake_load_settings,
            ),
            patch(
                "src.data.settings_manager.save_settings",
                side_effect=fake_save_settings,
            ),
            patch.object(QMessageBox, "question",
                         return_value=QMessageBox.Yes),
            patch.object(QMessageBox, "information",
                         return_value=QMessageBox.Ok),
        ):
            page = GuruWipPage()
            page._on_enable_experimental()

        assert "settings" in captured
        assert captured["settings"]["guru"]["experimental_ui_enabled"] is True


# ═══════════════════════════════════════════════════════════════════
#  MainWindow routing
# ═══════════════════════════════════════════════════════════════════

class TestMainWindowRouting:
    """_make_guru_page: the Enablement Workbench is the default front door;
    legacy GuruPage / WIP placeholder only when enablement.workbench_enabled=False."""

    def _make(self, sections):
        from src.ui.main_window import MainWindow

        def fake_get_section(name, default=None):
            return sections.get(name, default if default is not None else {})

        with patch("src.data.settings_manager.get_section", side_effect=fake_get_section):
            instance = type("Stub", (), {"db": MagicMock()})()
            return MainWindow._make_guru_page(instance)

    def test_default_returns_enablement(self, qapp):
        """No flags → the Enablement Workbench (the new default)."""
        from src.ui.pages.enablement import EnablementPage
        assert isinstance(self._make({}), EnablementPage)

    def test_workbench_disabled_returns_wip(self, qapp):
        """enablement.workbench_enabled=False + no guru flag → WIP placeholder."""
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = self._make({"enablement": {"workbench_enabled": False},
                           "guru": {"experimental_ui_enabled": False}})
        assert isinstance(page, GuruWipPage)

    def test_workbench_disabled_guru_flag_returns_legacy(self, qapp):
        """workbench off + guru.experimental_ui_enabled=True → legacy GuruPage."""
        from src.ui.pages.guru_page import GuruPage
        from src.ui.pages.guru_wip_page import GuruWipPage
        page = self._make({"enablement": {"workbench_enabled": False},
                           "guru": {"experimental_ui_enabled": True}})
        assert isinstance(page, GuruPage)
        assert not isinstance(page, GuruWipPage)
