"""QSS builder — composition, key selectors, offscreen application."""

import pytest
from PySide6.QtWidgets import QApplication

from src.ui.design.qss import build_stylesheet, _extras, _legacy_sections

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_build_composes_legacy_plus_extras():
    qss = build_stylesheet()
    assert qss == _legacy_sections() + _extras()


def test_key_selectors_present():
    qss = build_stylesheet()
    for selector in ("#TopBar", "#Sidebar", "#SidebarButton", "QPushButton",
                     "QTableWidget", "QTabBar::tab", "#KPICard", "QMenu",
                     "#SidebarButton[collapsed=\"true\"]"):
        assert selector in qss, f"missing selector {selector}"


def test_theme_facade_delegates():
    from src.ui.theme import get_stylesheet
    assert get_stylesheet() == build_stylesheet()


def test_applies_cleanly_offscreen(qapp):
    """Qt silently ignores broken QSS but parses what it can — applying
    the sheet and reading it back proves it is at least well-formed
    enough to set."""
    qapp.setStyleSheet(build_stylesheet())
    assert len(qapp.styleSheet()) > 10_000
