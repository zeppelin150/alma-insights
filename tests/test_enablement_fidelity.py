"""Enablement fidelity guards — single-source primitives, no emoji glyphs,
resize robustness (P5)."""

import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

pytestmark = pytest.mark.ui

# Emoji / dingbats / misc-symbols ranges (tofu risk offscreen + unprofessional).
# Arrows (U+2190+), box drawing (U+2500+), ‹›•— etc. are deliberate and allowed.
_EMOJI = re.compile(r"[☀-➿\U0001F000-\U0001FAFF]")

_SOURCES = (
    list(Path("src/ui/pages/enablement").glob("*.py"))
    + [Path("src/ui/pages/home_page.py"), Path("src/ui/app_modes.py")]
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_no_emoji_glyphs_in_sources():
    offenders = []
    for p in _SOURCES:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _EMOJI.search(line):
                offenders.append(f"{p.name}:{i}")
    assert not offenders, f"emoji/dingbat glyphs found: {offenders}"


def test_workbench_uses_shared_primitives():
    from src.ui.pages.enablement import _common, workbench
    assert workbench._badge is _common.badge
    assert workbench._card_frame is _common.card_frame
    assert workbench._TINT is _common.TINT


def test_teal_single_source():
    from src.ui.design import tokens
    from src.ui.pages.enablement import _common, settings, workbench
    assert _common._TEAL == tokens.ALMA_ACCENT_TEAL
    assert workbench._TEAL == tokens.ALMA_ACCENT_TEAL
    assert settings._TEAL == tokens.ALMA_ACCENT_TEAL


def test_enablement_page_resize_sweep(qapp, empty_db):
    """Pages survive narrow→wide→narrow without layout exceptions."""
    from src.ui.pages.enablement import EnablementPage
    page = EnablementPage(empty_db, demo=True)
    for w, h in ((1200, 750), (2400, 1300), (1200, 750)):
        page.resize(w, h)
        qapp.processEvents()
        for key in ("calendar", "tasks", "workbench", "settings"):
            page.select_tab(key)
            qapp.processEvents()
    assert page.tabs.currentWidget() is not None
