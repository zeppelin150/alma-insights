"""
Alma Insights — Collapsible Section Widget
A section with a clickable header that collapses/expands its content.
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QPushButton,
)
from PySide6.QtCore import Qt
from src.ui.theme import (
    ALMA_WHITE, ALMA_BORDER_LIGHT, ALMA_TEXT_DARK, ALMA_TEXT_LIGHT,
    ALMA_GREEN_DARK, ALMA_HOVER_LIGHT,
    apply_card_shadow,
)


class CollapsibleSection(QFrame):
    """
    A section with a clickable header that collapses/expands its content.

    Collapsed:
        [> Volume Trends ─────────────────────────]

    Expanded:
        [v Volume Trends ─────────────────────────]
        |  (chart content here)                    |
        [──────────────────────────────────────────]

    Pass ``section_key`` (e.g. "trc_analytics.csat_heatmap") to auto-read
    the default collapsed/expanded state from config/settings.yaml on
    construction.
    """

    def __init__(self, title: str, initially_collapsed=False,
                 section_key: str = "", show_expand_button=True,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._section_key = section_key
        self._title = title
        self._show_expand_button = show_expand_button

        # Resolve initial state: explicit section_key overrides the flag
        if section_key:
            self._collapsed = self.get_default_collapsed(section_key)
        else:
            self._collapsed = initially_collapsed

        self._build_ui()

        # Calendar-quality 3D shadow
        apply_card_shadow(self)

    # ── Static helper ──

    @staticmethod
    def get_default_collapsed(section_key: str) -> bool:
        """Read the default collapsed state from settings.

        Returns True if the section should start collapsed, False if expanded.
        """
        from src.data.settings_manager import get_section

        try:
            behavior = get_section("behavior", {})
            sd = behavior.get("section_defaults", {})
            preset = sd.get("preset", "all_open")

            if preset == "all_open":
                return False
            if preset == "all_collapsed":
                return True

            # Custom mode: look up the specific key
            custom = sd.get("custom", {})
            parts = section_key.split(".", 1)
            if len(parts) == 2:
                page_cfg = custom.get(parts[0], {})
                # custom stores True = expanded, so invert for "collapsed"
                return not page_cfg.get(parts[1], True)
            return False
        except Exception:
            return False

    # ── UI ──

    def _build_ui(self):
        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._outer_layout.setSpacing(0)

        # Header bar (always visible, clickable)
        self._header = QFrame()
        self._header.setCursor(Qt.PointingHandCursor)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(8)

        self._arrow = QLabel("\u25be" if not self._collapsed else "\u25b8")
        self._arrow.setStyleSheet(
            f"color: {ALMA_TEXT_LIGHT}; font-size: 12px;"
        )
        self._arrow.setFixedWidth(14)
        header_layout.addWidget(self._arrow)

        self._title_label = QLabel(self._title)
        self._title_label.setObjectName("CardTitle")
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()

        # Expand-to-fullscreen button (right edge of header)
        if self._show_expand_button:
            self._expand_btn = QPushButton("\u2922")  # ⤢
            self._expand_btn.setFixedSize(28, 28)
            self._expand_btn.setCursor(Qt.PointingHandCursor)
            self._expand_btn.setToolTip("Expand to full window")
            self._expand_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {ALMA_TEXT_LIGHT};
                    border: 1px solid transparent;
                    border-radius: 6px;
                    font-size: 16px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    color: {ALMA_GREEN_DARK};
                    background: {ALMA_HOVER_LIGHT};
                    border: 1px solid {ALMA_BORDER_LIGHT};
                }}
            """)
            self._expand_btn.clicked.connect(self._on_expand_clicked)
            header_layout.addWidget(self._expand_btn)

        self._header.mousePressEvent = lambda e: self.toggle()
        self._outer_layout.addWidget(self._header)

        # Divider line between header and content
        self._divider = QFrame()
        self._divider.setFixedHeight(1)
        self._divider.setStyleSheet(f"background: {ALMA_BORDER_LIGHT};")
        self._outer_layout.addWidget(self._divider)

        # Content area
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(16, 12, 16, 16)
        self._content_layout.setSpacing(8)
        self._outer_layout.addWidget(self._content)

        # Apply initial state
        if self._collapsed:
            self._content.setVisible(False)
            self._divider.setVisible(False)

    # ── Public API ──

    @property
    def section_key(self) -> str:
        return self._section_key

    def add_widget(self, widget):
        """Add a widget to the collapsible content area."""
        self._content_layout.addWidget(widget)

    def add_layout(self, layout):
        """Add a layout to the collapsible content area."""
        self._content_layout.addLayout(layout)

    def content_layout(self):
        """Return the content layout for direct manipulation."""
        return self._content_layout

    def toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        self._divider.setVisible(not self._collapsed)
        self._arrow.setText("\u25b8" if self._collapsed else "\u25be")

    def set_collapsed(self, collapsed: bool):
        if collapsed != self._collapsed:
            self.toggle()

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ── Expand to fullscreen ──

    def _on_expand_clicked(self):
        """Open the section's content in a near-fullscreen dialog."""
        from src.ui.dialogs.expanded_section_dialog import ExpandedSectionDialog

        # Ensure section is expanded so content is visible
        if self._collapsed:
            self.toggle()

        # Remove content from this section's layout
        self._outer_layout.removeWidget(self._content)

        # Open modal dialog with the content widget
        dlg = ExpandedSectionDialog(self._title, self._content,
                                     parent=self.window())
        dlg.exec()

        # Re-parent content back into this section
        # (addWidget appends after header + divider = correct position)
        self._outer_layout.addWidget(self._content)
        self._content.setVisible(not self._collapsed)
