"""Icon registry — completeness against the page registry + rendering."""

import pytest
from PySide6.QtWidgets import QApplication

from src.ui import app_modes
from src.ui.design import icons

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_every_registry_icon_has_a_glyph():
    names = set(icons.glyph_names())
    for spec in app_modes.PAGES:
        assert spec.icon in names, (
            f"{spec.page_id} references missing glyph {spec.icon!r}"
        )


def test_icon_renders_non_null(qapp):
    ic = icons.icon("home", 18)
    assert not ic.isNull()
    pm = ic.pixmap(18, 18)
    assert not pm.isNull()


def test_unknown_glyph_returns_null_icon(qapp):
    assert icons.icon("definitely-not-a-glyph").isNull()


def test_cache_returns_same_object(qapp):
    a = icons.icon("calendar", 18, "#FFFFFF")
    b = icons.icon("calendar", 18, "#FFFFFF")
    assert a is b
    c = icons.icon("calendar", 18, "#000000")
    assert c is not a


def test_no_emoji_in_registry_icons():
    """Gotcha 5: glyph names are ASCII identifiers, never emoji chars."""
    for spec in app_modes.PAGES:
        assert spec.icon.isascii(), f"{spec.page_id} icon is not ASCII"
