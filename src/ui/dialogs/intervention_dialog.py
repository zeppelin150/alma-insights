"""
Alma Insights — Intervention Dialog (Pass 3.0)
Add/edit intervention events for the intervention timeline.
Categories: payer_launch, product_release, process_change, policy_update,
            staffing_change, vendor_change, other.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QComboBox, QFrame,
    QListWidget, QListWidgetItem, QAbstractItemView,
)
from src.ui.widgets.date_picker import ModernDatePicker
from PySide6.QtCore import Qt, QDate

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT,
)

INTERVENTION_CATEGORIES = [
    ("payer_launch", "Payer Launch"),
    ("product_release", "Product Release"),
    ("process_change", "Process Change"),
    ("policy_update", "Policy Update"),
    ("staffing_change", "Staffing Change"),
    ("vendor_change", "Vendor Change"),
    ("other", "Other"),
]

CATEGORY_COLORS = {
    "payer_launch": "#1D6FA5",
    "product_release": "#16763A",
    "process_change": "#B45309",
    "policy_update": "#7B3FA0",
    "staffing_change": "#C41E1E",
    "vendor_change": "#4A7A8A",
    "other": "#7A7A7A",
}


class InterventionDialog(QDialog):
    """Dialog for adding/editing an intervention marker."""

    def __init__(self, intervention_data=None, trc_codes=None, parent=None):
        super().__init__(parent)
        self._data = intervention_data or {}
        self._trc_codes = trc_codes or []
        self.setWindowTitle(
            "Edit Intervention" if intervention_data else "Add Intervention"
        )
        self.setMinimumSize(500, 520)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("Edit Intervention" if self._data else "Add Intervention")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        layout.addWidget(title)

        # Name
        layout.addWidget(self._field_label("Event Name"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., Pegasus PPO Launch")
        self._name_edit.setText(self._data.get("name", ""))
        self._style_input(self._name_edit)
        layout.addWidget(self._name_edit)

        # Category + Date row
        row = QHBoxLayout()

        cat_col = QVBoxLayout()
        cat_col.addWidget(self._field_label("Category"))
        self._category_combo = QComboBox()
        for code, label in INTERVENTION_CATEGORIES:
            self._category_combo.addItem(label, code)
        if self._data.get("category"):
            idx = self._category_combo.findData(self._data["category"])
            if idx >= 0:
                self._category_combo.setCurrentIndex(idx)
        self._category_combo.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 6px 10px; font-size: 13px;
            }}
        """)
        cat_col.addWidget(self._category_combo)
        row.addLayout(cat_col)

        date_col = QVBoxLayout()
        date_col.addWidget(self._field_label("Event Date"))
        self._date_edit = ModernDatePicker()
        if self._data.get("event_date"):
            self._date_edit.setDate(QDate.fromString(self._data["event_date"], "yyyy-MM-dd"))
        else:
            self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setStyleSheet(f"""
            QDateEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 6px 10px; font-size: 13px;
            }}
        """)
        date_col.addWidget(self._date_edit)
        row.addLayout(date_col)

        layout.addLayout(row)

        # Description
        layout.addWidget(self._field_label("Description"))
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText("Details about the intervention...")
        self._desc_edit.setPlainText(self._data.get("description", ""))
        self._desc_edit.setMaximumHeight(70)
        self._desc_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 8px; font-size: 12px;
            }}
        """)
        layout.addWidget(self._desc_edit)

        # Affected TRCs
        layout.addWidget(self._field_label("Affected TRCs (leave empty for all)"))
        self._trc_list = QListWidget()
        self._trc_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self._trc_list.setMaximumHeight(120)
        self._trc_list.setStyleSheet(f"""
            QListWidget {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 4px; font-size: 12px;
            }}
            QListWidget::item:selected {{
                background: {ALMA_GREEN_MID}; color: white;
            }}
        """)

        import json
        affected = self._data.get("affected_trcs", [])
        if isinstance(affected, str):
            try:
                affected = json.loads(affected)
            except (json.JSONDecodeError, TypeError):
                affected = []

        for trc in self._trc_codes:
            code = trc if isinstance(trc, str) else trc.get("code", "")
            item = QListWidgetItem(code)
            self._trc_list.addItem(item)
            if code in affected:
                item.setSelected(True)

        layout.addWidget(self._trc_list)

        # Tags
        layout.addWidget(self._field_label("Tags (comma-separated)"))
        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("e.g., payer, eligibility, billing")
        tags = self._data.get("tags", [])
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        self._tags_edit.setText(", ".join(tags) if isinstance(tags, list) else str(tags))
        self._style_input(self._tags_edit)
        layout.addWidget(self._tags_edit)

        layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 8px 24px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _field_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ALMA_TEXT_MID};")
        return lbl

    def _style_input(self, widget):
        widget.setStyleSheet(f"""
            QLineEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 8px; font-size: 13px;
            }}
        """)

    def _on_save(self):
        if not self._name_edit.text().strip():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Required", "Event name is required.")
            return
        self.accept()

    def get_intervention_data(self):
        """Return the intervention data dict."""
        selected_trcs = [
            item.text() for item in self._trc_list.selectedItems()
        ]
        tags_text = self._tags_edit.text().strip()
        tags = [t.strip() for t in tags_text.split(",") if t.strip()] if tags_text else []

        return {
            "name": self._name_edit.text().strip(),
            "category": self._category_combo.currentData(),
            "description": self._desc_edit.toPlainText().strip(),
            "event_date": self._date_edit.date().toString("yyyy-MM-dd"),
            "affected_trcs": selected_trcs,
            "tags": tags,
        }
