"""Native dialog buttons must stay READABLE under the page stylesheets.

Field bug (2026-07-22, first drag-reschedule on the web calendar): the native
"Move task" QMessageBox rendered with BLANK Yes/No buttons — one visible only
as a focus outline. Root cause: EnablementPage/SettingsPage set a SELECTORLESS
``background: <cream>`` stylesheet, which Qt applies to every descendant —
including native dialog buttons — overriding the app QSS's green button fill
while that sheet's white text survives. White on cream = invisible labels on
every confirm gate parented to those pages (Move task, publish confirm, the
Drive folder picker, "+ Add folder", info boxes).

A rule on an ANCESTOR (app QSS or the page) loses to the page's own
selectorless background by proximity — measured — so the corrective has to be
set ON THE DIALOG. ``_common.style_native_dialog`` does that; the two web
confirm gates, the folder picker, and the "+ Add folder" prompt all route
through it. These tests pin it functionally: a pixel-contrast check on a real
QMessageBox styled the way the confirm helpers style theirs, under the real
app-QSS + page composition.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch):
    import src.data.settings_manager as sm
    path = tmp_path / "settings.yaml"
    monkeypatch.setattr(sm, "get_settings_path", lambda: path)
    return path


def _interior_luminance_spread(btn) -> float:
    """Render the button and return max-min luminance of its INTERIOR pixels
    (inset past the border/focus ring). Invisible text ⇒ near-flat interior;
    readable text ⇒ a large spread between fill and glyph pixels."""
    btn.ensurePolished()
    img = btn.grab().toImage()
    inset = 8
    lums = []
    for x in range(inset, img.width() - inset, 2):
        for y in range(inset, img.height() - inset, 2):
            c = img.pixelColor(x, y)
            lums.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
    return (max(lums) - min(lums)) if lums else 0.0


def _confirm_under(parent) -> QMessageBox:
    from src.ui.pages.enablement._common import style_native_dialog
    box = QMessageBox(parent)
    box.setWindowTitle("Move task")
    box.setText("Move “Draft the Returns policy v3 KB article” to Jul 3?")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No)
    style_native_dialog(box)   # exactly what _web_reschedule_confirm does
    return box


class TestConfirmButtonsReadable:
    def test_yes_button_text_is_visible_under_the_real_composition(
            self, qapp, temp_settings):
        """The exact field composition: app QSS on an ancestor + SettingsPage's
        own sheet + a native QMessageBox parented to the page. Pre-fix the Yes
        button's interior was cream fill + white text (luminance spread ≈ 12);
        readable chrome spreads > 80."""
        from src.ui.design.qss import build_stylesheet
        from src.ui.pages.enablement.settings import SettingsPage

        root = QWidget()
        root.setStyleSheet(build_stylesheet())
        page = SettingsPage(parent=root)
        box = _confirm_under(page)
        # An unshown QMessageBox has zero-size buttons — show() forces the real
        # layout (a brief window during the test run is the price of sampling
        # true pixels).
        box.show()
        qapp.processEvents()
        yes = box.button(QMessageBox.Yes)
        assert yes is not None and yes.text()
        assert yes.width() > 30 and yes.height() > 15, "button never laid out"
        spread = _interior_luminance_spread(yes)
        box.hide()
        assert spread > 80, (
            f"Yes-button interior luminance spread {spread:.0f} — the label is "
            "not visibly rendered (the blank-confirm bug)")
        box.deleteLater()
        page.deleteLater()
        root.deleteLater()


class TestGatesRouteThroughTheCorrective:
    """Every native confirm/prompt parented to an Enablement page must apply
    the corrective (style_native_dialog / native_dialog_button_qss), or its
    buttons blank out under the page's cascaded cream background."""

    @pytest.mark.parametrize("fn", [
        "_web_reschedule_confirm", "_web_workbench_publish_confirm",
    ])
    def test_web_confirm_gates_style_the_native_dialog(self, fn):
        import inspect
        from src.ui.pages.enablement.page import EnablementPage
        src = inspect.getsource(getattr(EnablementPage, fn))
        assert "style_native_dialog" in src, (
            f"{fn}: the native confirm must be styled or its buttons blank out")
        # And it must build an INSTANCE (static QMessageBox.question can't be
        # styled) and exec it.
        assert ".exec()" in src and "QMessageBox.question(" not in src

    def test_folder_picker_dialog_styles_itself(self):
        import inspect
        from src.ui.dialogs.drive_folder_picker_dialog import (
            DriveFolderPickerDialog)
        assert "style_native_dialog" in inspect.getsource(
            DriveFolderPickerDialog.__init__)

    def test_add_folder_prompt_styles_the_input_dialog(self):
        import inspect
        from src.ui.pages.enablement.settings import SettingsPage
        src = inspect.getsource(SettingsPage._prompt_text)
        assert "native_dialog_button_qss" in src
        assert "QInputDialog.getText(" not in inspect.getsource(
            SettingsPage._on_add_drive_folder)

    def test_corrective_styles_button_text_and_fill(self):
        """The qss must set BOTH a fill and a contrasting text colour — a fill
        alone (or text alone) still yields an unreadable button."""
        from src.ui.pages.enablement._common import native_dialog_button_qss
        css = native_dialog_button_qss()
        assert "QPushButton" in css and "background:" in css and "color:" in css
