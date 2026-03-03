"""
Alma Insights — Mapping Preview Dialog (Build 8.0)

Modal dialog showing Gemini-proposed (or manual) column mappings for user
review and editing.  Shown only when there's ambiguity — fast-path and
all-high-confidence cases skip this dialog entirely.

Returns the final mapping_dict on Accept, or None on Cancel.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
    QFrame, QAbstractItemView, QSizePolicy,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from src.ui.theme import *
from src.agents.csv_reformatter import (
    TARGET_SCHEMA, REQUIRED_FIELDS, ALL_TARGET_FIELDS, _normalize_header,
)


# Target field options for the dropdown (including skip)
_SKIP_LABEL = "— Skip —"
_TARGET_OPTIONS = [_SKIP_LABEL] + ALL_TARGET_FIELDS


class MappingPreviewDialog(QDialog):
    """Column mapping preview/edit dialog.

    Args:
        mapping_result: MappingResult from CSVReformatter.analyze_csv()
        parent: Parent widget.
    """

    def __init__(self, mapping_result, parent=None):
        super().__init__(parent)
        self._mapping_result = mapping_result
        self._combo_widgets = []  # list of (source_header, QComboBox)
        self._accepted_override = None

        self.setWindowTitle("Column Mapping Preview")
        self.setMinimumSize(780, 520)
        self.setModal(True)

        self._build_ui()
        self._populate_table()
        self._update_accept_state()

    # ═══════════════════════════════════════════════════════════
    #  UI CONSTRUCTION
    # ═══════════════════════════════════════════════════════════

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # ── Header ──
        header_row = QHBoxLayout()
        title = QLabel("Column Mapping Preview")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        header_row.addWidget(title)

        # Source badge
        source = self._mapping_result.source
        badge_text = {
            "gemini": "Gemini",
            "column_map": "Auto-detected",
            "cache": "Cached",
            "offline": "Manual",
        }.get(source, source)
        badge_color = {
            "gemini": ALMA_INFO,
            "column_map": ALMA_SUCCESS,
            "cache": ALMA_SUCCESS,
            "offline": ALMA_WARNING,
        }.get(source, ALMA_TEXT_MID)
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"background: {badge_color}; color: white; "
            f"border-radius: 4px; padding: 3px 10px; "
            f"font-size: 11px; font-weight: 600;"
        )
        header_row.addStretch()
        header_row.addWidget(badge)
        layout.addLayout(header_row)

        # Stats line
        n_mapped = self._mapping_result.get_mapped_field_count()
        n_headers = len(self._mapping_result.input_headers)
        stats_text = (
            f"{n_mapped} of {len(ALL_TARGET_FIELDS)} target fields mapped "
            f"from {n_headers} source columns"
        )
        stats = QLabel(stats_text)
        stats.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_LIGHT};"
        )
        layout.addWidget(stats)

        # ── Table ──
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            "Source Column", "Sample Value", "Target Field", "Confidence"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 100)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                alternate-background-color: rgba(3,40,27,0.02);
                border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
        """)
        layout.addWidget(self.table, 1)

        # ── Warnings ──
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(
            f"color: {ALMA_ERROR}; font-size: 12px; padding: 4px 0;"
        )
        layout.addWidget(self.warning_label)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setMinimumWidth(100)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.accept_btn = QPushButton("  Accept Mapping  ")
        self.accept_btn.setMinimumHeight(36)
        self.accept_btn.setMinimumWidth(160)
        self.accept_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_LIGHT}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 6px; padding: 8px 20px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
            QPushButton:disabled {{
                background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT};
            }}
        """)
        self.accept_btn.setCursor(Qt.PointingHandCursor)
        self.accept_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self.accept_btn)

        layout.addLayout(btn_row)

    # ═══════════════════════════════════════════════════════════
    #  TABLE POPULATION
    # ═══════════════════════════════════════════════════════════

    def _populate_table(self):
        """Fill the table with one row per source header."""
        mr = self._mapping_result
        headers = mr.input_headers
        sample_rows = mr.sample_rows

        # Build a lookup: source_column → mapping entry
        mapping_lookup = {}
        for m in mr.mappings:
            mapping_lookup[m["source_column"]] = m

        self.table.setRowCount(len(headers))
        self._combo_widgets = []

        for i, header in enumerate(headers):
            m = mapping_lookup.get(header, {})
            target = m.get("target_field", "")
            confidence = m.get("confidence", "")

            # Col 0: Source column name
            src_item = QTableWidgetItem(header)
            src_item.setFont(QFont("Consolas", 10))
            self.table.setItem(i, 0, src_item)

            # Col 1: Sample value (first row)
            sample_val = ""
            if sample_rows:
                sample_val = sample_rows[0].get(header, "")
                if len(sample_val) > 60:
                    sample_val = sample_val[:57] + "..."
            sample_item = QTableWidgetItem(sample_val)
            sample_item.setForeground(Qt.GlobalColor.gray)
            sample_item.setFont(QFont("Consolas", 9))
            self.table.setItem(i, 1, sample_item)

            # Col 2: Target field dropdown
            combo = QComboBox()
            combo.addItems(_TARGET_OPTIONS)
            if target and target in ALL_TARGET_FIELDS:
                combo.setCurrentText(target)
            else:
                combo.setCurrentText(_SKIP_LABEL)
            combo.currentTextChanged.connect(lambda _: self._update_accept_state())
            self.table.setCellWidget(i, 2, combo)
            self._combo_widgets.append((header, combo))

            # Col 3: Confidence badge
            if confidence:
                badge = self._make_confidence_badge(confidence)
                self.table.setCellWidget(i, 3, badge)
            else:
                self.table.setItem(i, 3, QTableWidgetItem(""))

    @staticmethod
    def _make_confidence_badge(confidence):
        """Create a colored confidence label."""
        colors = {
            "high": ALMA_SUCCESS,
            "medium": ALMA_WARNING,
            "low": ALMA_ERROR,
        }
        color = colors.get(confidence, ALMA_TEXT_LIGHT)
        label = QLabel(confidence.capitalize())
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600; "
            f"padding: 2px 6px;"
        )
        return label

    # ═══════════════════════════════════════════════════════════
    #  VALIDATION
    # ═══════════════════════════════════════════════════════════

    def _update_accept_state(self):
        """Enable/disable Accept button based on required field coverage."""
        assigned = set()
        for _, combo in self._combo_widgets:
            val = combo.currentText()
            if val != _SKIP_LABEL:
                assigned.add(val)

        missing = REQUIRED_FIELDS - assigned
        if missing:
            self.warning_label.setText(
                f"Required fields not assigned: {', '.join(sorted(missing))}. "
                f"Please map them to proceed."
            )
            self.accept_btn.setEnabled(False)
        else:
            # Show any Gemini warnings
            warnings = self._mapping_result.warnings
            if warnings:
                self.warning_label.setText(" | ".join(warnings))
                self.warning_label.setStyleSheet(
                    f"color: {ALMA_WARNING}; font-size: 12px; padding: 4px 0;"
                )
            else:
                self.warning_label.setText("")
            self.accept_btn.setEnabled(True)

    # ═══════════════════════════════════════════════════════════
    #  ACCEPT / RESULT
    # ═══════════════════════════════════════════════════════════

    def _on_accept(self):
        """Build the final column override dict from user selections."""
        override = {}
        for header, combo in self._combo_widgets:
            target = combo.currentText()
            if target != _SKIP_LABEL:
                norm = _normalize_header(header)
                override[norm] = target

        self._accepted_override = override
        self.accept()

    def get_column_override(self):
        """Return the final {normalized_header: target_field} dict.

        Call after dialog.exec() == QDialog.Accepted.
        """
        return self._accepted_override
