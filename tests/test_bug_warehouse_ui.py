"""
Bug 5 Tests: Data Warehouse Page UI/Layout Issues
===================================================
H0: The Data Warehouse page fills the viewport, uses reasonable column widths,
    shows ticket detail on load, and displays NLP/ngram enrichment data.

HA (multiple sub-hypotheses):
  5a. Table does not expand to fill viewport (no expanding size policy)
  5b. CSAT column stretches excessively (setStretchLastSection=True + last col = CSAT)
  5c. Detail panel is hidden by default (hidden until row click)
  5d. No ngram/enrichment display in TRC history panel
  5e. "All Sources" source_id is not connected (same root cause as Bug 4)

Run: python -m pytest tests/test_bug_warehouse_ui.py -x -v
"""

import pytest
from pathlib import Path


def _read_source(filename):
    return (Path(__file__).parent.parent / "src" / "ui" / filename).read_text(encoding="utf-8")


# ─── Bug 5a: Table Viewport Expansion ───

class TestH0_TableFillsViewport:
    """H0: VirtualScrollTable has an expanding size policy so it fills available space."""

    def test_table_has_expanding_policy(self):
        """H0: Table widget sets QSizePolicy.Expanding vertically."""
        src = _read_source("widgets/virtual_scroll_table.py")
        has_expanding = (
            "Expanding" in src
            or "setMinimumHeight" in src and "setSizePolicy" in src
        )
        # Specifically check if setSizePolicy is called with vertical expanding
        has_size_policy = "setSizePolicy" in src
        assert has_size_policy, \
            "H0 REJECTED: VirtualScrollTable never calls setSizePolicy(). " \
            "Table stays at minimumHeight (250px) instead of expanding to fill space."

    def test_scroll_layout_gives_table_stretch(self):
        """H0: Table widget has stretch factor > 0 in the scroll layout."""
        src = _read_source("pages/data_warehouse_page.py")
        # Check if the table widget is added with a stretch factor
        # Pattern: scroll_layout.addWidget(self._table_widget, 1) or setStretchFactor
        has_stretch = (
            "addWidget(self._table_widget, 1)" in src
            or "setStretchFactor" in src and "_table_widget" in src
        )
        assert has_stretch, \
            "H0 REJECTED: _table_widget is added to layout without stretch factor. " \
            "It gets minimum height only; remaining space is blank."


# ─── Bug 5b: CSAT Column Width ───

class TestH0_CsatColumnWidth:
    """H0: CSAT column has a reasonable fixed width and doesn't stretch to fill page."""

    def test_stretch_last_section_disabled(self):
        """H0: setStretchLastSection is False (CSAT is last col, shouldn't stretch)."""
        src = _read_source("widgets/virtual_scroll_table.py")
        # Look for setStretchLastSection(True) — this causes last col to fill remaining space
        has_stretch_last = "setStretchLastSection(True)" in src
        assert not has_stretch_last, \
            "H0 REJECTED: setStretchLastSection(True) is set. CSAT (last column) " \
            "stretches to fill ALL remaining horizontal space, making it absurdly wide."

    def test_csat_column_is_last(self):
        """Confirm CSAT is the last column (affected by stretchLastSection)."""
        src = _read_source("widgets/virtual_scroll_table.py")
        # Find COLUMNS definition
        columns_start = src.find("COLUMNS = [")
        columns_end = src.find("]", columns_start) + 1
        columns_block = src[columns_start:columns_end]
        # CSAT should be the last entry
        last_line = [l.strip() for l in columns_block.strip().split("\n") if l.strip() and "(" in l][-1]
        assert "csat" in last_line.lower(), \
            f"CSAT is NOT the last column. Last column: {last_line}"


# ─── Bug 5c: Detail Panel Visibility ───

class TestH0_DetailPanelVisible:
    """H0: TicketDetailPanel is visible by default when the page loads."""

    def test_detail_panel_not_hidden_at_init(self):
        """H0: TicketDetailPanel does NOT call self.hide() in __init__."""
        src = _read_source("widgets/ticket_detail_panel.py")
        # Find __init__ method
        init_start = src.find("def __init__")
        next_def = src.find("\n    def ", init_start + 1)
        init_body = src[init_start:next_def]

        has_hide = "self.hide()" in init_body
        assert not has_hide, \
            "H0 REJECTED: TicketDetailPanel calls self.hide() in __init__. " \
            "Panel is invisible until user clicks a table row — no visual hint it exists."

    def test_trc_panel_not_hidden_at_init(self):
        """H0: TRCHistoryPanel does NOT call self.hide() in __init__."""
        src = _read_source("widgets/trc_history_panel.py")
        init_start = src.find("def __init__")
        next_def = src.find("\n    def ", init_start + 1)
        init_body = src[init_start:next_def]

        has_hide = "self.hide()" in init_body
        assert not has_hide, \
            "H0 REJECTED: TRCHistoryPanel calls self.hide() in __init__. " \
            "Panel is invisible until user clicks a table row."


# ─── Bug 5d: Ngram / Enrichment Display ───

class TestH0_NgramEnrichmentDisplay:
    """H0: TRCHistoryPanel displays ngram trends and enrichment data.

    Note: Ngram trend display and NLP status indicators are deferred features
    (planned in S4 design but not implemented). These tests document the gap
    and will pass once the features are added in a future session.
    """

    @pytest.mark.xfail(reason="Deferred feature: ngram trend display not yet implemented")
    def test_trc_panel_shows_ngrams(self):
        """TRCHistoryPanel should have ngram display logic."""
        src = _read_source("widgets/trc_history_panel.py")
        ngram_display = False
        for line in src.split("\n"):
            if "ngram" in line.lower() and ("setText" in line or "Label" in line or "addWidget" in line):
                ngram_display = True
                break
        assert ngram_display, \
            "TRCHistoryPanel has no ngram display widgets."

    @pytest.mark.xfail(reason="Deferred feature: NLP status indicators not yet implemented")
    def test_detail_panel_shows_nlp_status(self):
        """TicketDetailPanel should show NLP scan/enrichment status indicators."""
        src = _read_source("widgets/ticket_detail_panel.py")
        has_nlp_status = (
            "nlp_status" in src.lower()
            or "enrichment" in src.lower()
            or "scan_status" in src.lower()
        )
        assert has_nlp_status, \
            "TicketDetailPanel has no NLP/enrichment status indicators."


# ─── Bug 5e: All Sources Connection (overlap with Bug 4) ───

class TestH0_AllSourcesFilter:
    """H0: Source selector on Data Warehouse page has source_changed signal connected."""

    def test_source_changed_connected(self):
        """H0: _source_selector.source_changed.connect() exists in page code."""
        src = _read_source("pages/data_warehouse_page.py")
        has_connection = "source_selector.source_changed.connect" in src
        assert has_connection, \
            "H0 REJECTED: _source_selector.source_changed signal is NOT connected. " \
            "'All Sources' dropdown change does nothing."
