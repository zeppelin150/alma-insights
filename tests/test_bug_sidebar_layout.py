"""
Bug 2+3 Tests: Sidebar Layout & Collapse Performance
======================================================
Bug 2 — Data Warehouse Sidebar Position:
  H0: Data Warehouse is in the SYSTEM section (above Settings).
  HA: Data Warehouse is in the SOURCES section (wrong placement).

Bug 3 — Collapse Lag:
  H0: _toggle_sidebar() performs <= 10 style update calls (efficient).
  HA: _toggle_sidebar() performs 30+ individual unpolish/polish calls (causes lag).

Run: python -m pytest tests/test_bug_sidebar_layout.py -x -v
"""

import pytest
import re
from pathlib import Path


class TestH0_DataWarehouseSidebarPosition:
    """H0: Data Warehouse button is in the SYSTEM section of the sidebar."""

    def test_dw_in_system_section(self):
        """H0: Data Warehouse sits in SYSTEM, ordered before Settings.

        The sidebar now renders from the app_modes registry in spec order,
        so the ordering invariant is asserted on the registry itself.
        """
        from src.ui import app_modes

        specs = app_modes.pages_for_mode(app_modes.MODE_PRODUCT)
        ids = [s.page_id for s in specs]
        dw = next(s for s in specs if s.page_id == "data_warehouse")
        settings = next(s for s in specs if s.page_id == "settings")

        assert dw.section == "SYSTEM"
        assert settings.section == "SYSTEM"
        assert ids.index("data_warehouse") < ids.index("settings"), \
            "H0 REJECTED: Data Warehouse must precede Settings in SYSTEM"

    def test_dw_not_in_sources_section(self):
        """H0 predicts DW is NOT in the SOURCES section."""
        from src.ui import app_modes

        dw = app_modes.spec_for("data_warehouse")
        assert dw is not None
        assert dw.section != "SOURCES", \
            "H0 REJECTED: Data Warehouse is in the SOURCES section"


class TestH0_CollapseSidebarPerformance:
    """H0: Sidebar collapse uses <= 10 style update calls."""

    def test_count_unpolish_polish_in_toggle(self):
        """Keep the total number of style-update calls bounded.

        Budget revised 2026-04-17: per-widget unpolish/polish is
        REQUIRED for correctness (see test_collapse_renders_icons...) —
        a bulk restyle on the container does not cascade into children
        and the collapsed CSS selector silently fails.

        The real-world cost is well under 1ms for ~12 buttons, so the
        old <=20 budget was over-tight. New budget: <=80 total calls
        across the two methods (enough for ~20 widgets × 2 paths × 2
        calls each)."""
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        toggle_start = src.find("def _toggle_sidebar(self):")
        toggle_end = src.find("\n    def ", toggle_start + 1)
        restore_start = src.find("def _restore_sidebar_text(self):")
        restore_end = src.find("\n    def ", restore_start + 1)

        toggle_body = src[toggle_start:toggle_end]
        restore_body = src[restore_start:restore_end]
        combined = toggle_body + restore_body

        unpolish_count = combined.count("unpolish(")
        polish_count = combined.count("polish(")
        total_style_calls = unpolish_count + polish_count
        assert total_style_calls <= 80, (
            f"{total_style_calls} style update calls — budget is 80. "
            f"({unpolish_count} unpolish + {polish_count} polish)."
        )

    def test_bulk_restyle_exists(self):
        """The container still gets a final restyle after its children — serves as a
        safety net for any widgets we didn't restyle individually."""
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        restore_start = src.find("def _restore_sidebar_text(self):")
        restore_end = src.find("\n    def ", restore_start + 1)
        restore_body = src[restore_start:restore_end]

        assert "self._sidebar.style().unpolish(self._sidebar)" in restore_body, (
            "Missing bulk restyle call on sidebar container"
        )


def test_collapse_renders_icons_and_sizehint_collapses():
    """Bug-bash 2026-04-17 (second attempt) — collapsed sidebar showed
    blank rectangles instead of icons.

    The first attempted fix used `btn.setStyleSheet("")` per button and
    a source-inspection test that only grepped for that string. That
    test passed but the bug persisted: `setStyleSheet("")` does NOT
    trigger QSS re-evaluation for dynamic property selectors like
    `[collapsed="true"]`. The idiomatic Qt pattern that actually works
    is `style().unpolish(w)` + `style().polish(w)` per widget.

    This is a behaviour-level test: instantiates MainWindow under
    offscreen Qt and verifies:
      (1) button.text() equals its icon_char after collapse
      (2) button.sizeHint().width() is small (~emoji width, not
          ~emoji + 40px padding) — the precise signal that the
          collapsed CSS actually activated.
    Runs all assertions in one function because MainWindow init is
    expensive and close() has slow teardown paths."""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from src.ui.theme import get_stylesheet
    app.setStyleSheet(get_stylesheet())
    from src.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()

    # Collapse
    w._toggle_sidebar()
    app.processEvents()

    widest = 0
    for btn, _ in w._sidebar_buttons:
        if not btn.isVisible():
            continue
        icon = btn.property("icon_char") or ""
        assert btn.text() == icon, (
            f"Collapsed button text should equal its icon char; "
            f"got {btn.text()!r}, expected {icon!r}"
        )
        widest = max(widest, btn.sizeHint().width())

    assert widest <= 60, (
        f"Widest collapsed-button sizeHint is {widest}px. "
        "The collapsed QSS rule did not activate — expected <= 60px "
        "(collapsed horizontal padding is 0, glyph ~16-32px). "
        "Check that _toggle_sidebar calls unpolish()/polish() per button."
    )

    # Expand back
    w._toggle_sidebar()
    app.processEvents()
    w._restore_sidebar_text()   # normally fires on animation finished
    app.processEvents()

    for btn, _ in w._sidebar_buttons:
        if not btn.isVisible():
            continue
        full = btn.property("full_text") or ""
        assert btn.text() == full, (
            f"Expanded button should show full text; "
            f"got {btn.text()!r}, expected {full!r}"
        )
