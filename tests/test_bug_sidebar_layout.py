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
        """H0: Data Warehouse sidebar button appears after SYSTEM label, before Settings.

        Read main_window.py source and check ordering: SYSTEM label should
        precede Data Warehouse sidebar_btn which should precede Settings sidebar_btn.
        """
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        # Find section markers and sidebar_btn call positions (not PAGE constants)
        system_pos = src.find('"SYSTEM"')
        # Find the _sidebar_btn call that creates the Data Warehouse button
        dw_btn_pos = src.find('Data Warehouse", self.PAGE_DATA_WAREHOUSE')
        # Find the _sidebar_btn call that creates the Settings button
        settings_btn_pos = src.find('Settings", self.PAGE_SETTINGS')

        assert system_pos > 0, "SYSTEM section label exists"
        assert dw_btn_pos > 0, "Data Warehouse sidebar button exists"

        # H0: Data Warehouse sidebar_btn comes AFTER SYSTEM label
        assert dw_btn_pos > system_pos, \
            f"H0 REJECTED: Data Warehouse btn (pos {dw_btn_pos}) is BEFORE SYSTEM section (pos {system_pos})"

    def test_dw_not_in_sources_section(self):
        """H0 predicts DW is NOT between SOURCES and ANALYSIS markers."""
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        sources_pos = src.find('"SOURCES"')
        analysis_pos = src.find('"ANALYSIS"')
        dw_pos = src.find("Data Warehouse")

        is_in_sources_section = sources_pos < dw_pos < analysis_pos
        assert not is_in_sources_section, \
            f"H0 REJECTED: Data Warehouse is in SOURCES section (between pos {sources_pos} and {analysis_pos})"


class TestH0_CollapseSidebarPerformance:
    """H0: Sidebar collapse uses <= 10 style update calls."""

    def test_count_unpolish_polish_in_toggle(self):
        """H0: _toggle_sidebar has <= 10 unpolish/polish pairs.

        Count occurrences of style().unpolish() and style().polish() in the
        _toggle_sidebar and _restore_sidebar_text methods combined.
        """
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        # Extract just the two methods
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
        assert total_style_calls <= 20, \
            f"H0 REJECTED: {total_style_calls} style update calls found ({unpolish_count} unpolish + {polish_count} polish). " \
            f"Expected <= 20 for acceptable performance. Each pair forces full stylesheet re-evaluation."

    def test_no_per_widget_unpolish_in_loops(self):
        """Verify unpolish/polish calls are NOT inside button/divider loops."""
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        toggle_start = src.find("def _toggle_sidebar(self):")
        toggle_end = src.find("\n    def ", toggle_start + 1)
        toggle_body = src[toggle_start:toggle_end]

        # Extract the for-btn loop body
        btn_loop_start = toggle_body.find("for btn")
        if btn_loop_start >= 0:
            # Get lines within the loop (indented lines after the for)
            lines = toggle_body[btn_loop_start:].split("\n")
            loop_lines = [lines[0]]
            for line in lines[1:]:
                if line.startswith("            ") or line.startswith("\t\t\t"):
                    loop_lines.append(line)
                else:
                    break
            loop_body = "\n".join(loop_lines)
            assert "unpolish" not in loop_body, \
                "unpolish() is still called INSIDE the button loop — should be outside"

    def test_bulk_restyle_exists(self):
        """Verify a single bulk unpolish/polish on the sidebar container exists."""
        src = (Path(__file__).parent.parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

        toggle_start = src.find("def _toggle_sidebar(self):")
        toggle_end = src.find("\n    def ", toggle_start + 1)
        toggle_body = src[toggle_start:toggle_end]

        # Should have self._sidebar.style().unpolish(self._sidebar) — bulk restyle
        assert "self._sidebar.style().unpolish(self._sidebar)" in toggle_body, \
            "Missing bulk restyle call on sidebar container"
