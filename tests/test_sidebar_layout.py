"""Sidebar must not compress its entries when the nav list is long.

Regression for the truncation seen after the enablement nav grew to eleven
entries: the nav had no scroll area and #SidebarButton had no min-height, so
once the content exceeded the window the outer stretch collapsed to zero and
Qt squeezed every widget below its sizeHint — clipping descenders on every
label (Agent, Queue, Analytics, Settings, and Collapse).

The nav is expected to keep growing, so this asserts the structural property
(entries keep their natural height; overflow scrolls) rather than a pixel
count for today's list.
"""

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from src.ui import app_modes
from src.ui.main_window import MainWindow

pytestmark = pytest.mark.ui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _sidebar(qapp, mode, height):
    """A built, populated sidebar constrained to `height` pixels."""
    win = MainWindow.__new__(MainWindow)
    win._mode = mode
    win._sidebar_collapsed = False
    win._active_page = None
    win._sidebar_buttons = []
    win._sidebar_dividers = []
    frame = win._build_sidebar()
    win._populate_sidebar(mode)
    frame.resize(220, height)
    frame.show()
    qapp.processEvents()
    return win, frame


@pytest.mark.parametrize("mode", list(app_modes.MODES))
@pytest.mark.parametrize("height", [1000, 700, 520])
def test_sidebar_buttons_keep_their_natural_height(qapp, mode, height):
    """No entry may render shorter than the height its own font needs —
    that is what clips descenders."""
    win, frame = _sidebar(qapp, mode, height)
    try:
        squashed = [
            (btn.text(), btn.height(), btn.sizeHint().height())
            for btn, _pid in win._sidebar_buttons
            if btn.isVisible() and btn.height() < btn.sizeHint().height()
        ]
        assert not squashed, (
            f"{mode} sidebar at {height}px compressed entries below their "
            f"sizeHint (descenders clip): {squashed}")
    finally:
        frame.close()


@pytest.mark.parametrize("mode", list(app_modes.MODES))
def test_sidebar_overflow_scrolls_rather_than_squeezing(qapp, mode):
    """A short window must produce a scrollable nav, not a compressed one."""
    win, frame = _sidebar(qapp, mode, 420)
    try:
        areas = frame.findChildren(QScrollArea)
        assert areas, (
            "the sidebar nav has no QScrollArea, so a long nav list can only "
            "be fitted by compressing entries")
        for btn, _pid in win._sidebar_buttons:
            if btn.isVisible():
                assert btn.height() >= btn.sizeHint().height(), (
                    f"{btn.text()!r} still compressed despite the scroll area")
    finally:
        frame.close()


def test_collapse_button_and_footer_stay_readable(qapp):
    """The chrome below the nav is in the OUTER layout — it was clipped too,
    which is what proved this was whole-sidebar compression."""
    win, frame = _sidebar(qapp, app_modes.MODE_ENABLEMENT, 520)
    try:
        assert win._collapse_btn.height() >= win._collapse_btn.sizeHint().height()
        assert win._sidebar_footer.height() >= \
            win._sidebar_footer.sizeHint().height()
    finally:
        frame.close()


def test_every_enablement_entry_is_present(qapp):
    """The nav grew to eleven entries; none may be dropped to make room."""
    win, frame = _sidebar(qapp, app_modes.MODE_ENABLEMENT, 520)
    try:
        labels = {btn.text() for btn, _pid in win._sidebar_buttons}
        for expected in ("Home", "Agent", "Calendar", "Tasks", "Workbench",
                         "PowerPoint", "Zendesk", "Attention Queue",
                         "Guru Analytics", "Help", "Settings"):
            assert expected in labels, f"{expected} missing from the sidebar"
    finally:
        frame.close()
