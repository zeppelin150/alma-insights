"""
Alma Insights — Prompt Editor Dialog (Pass 3.0)
Create/edit prompts for the AI Reports prompt library.
Supports canned prompts (editable, not deletable) and custom prompts.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QComboBox, QFrame, QFileDialog,
    QSizePolicy, QScrollArea, QWidget, QGroupBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_INFO,
)

# Available prompt variables
PROMPT_VARIABLES = [
    ("{ticket_count}", "Total ticket count"),
    ("{date_range}", "Analysis date range"),
    ("{data_block}", "Full formatted data block (all analytics)"),
    ("{trc_distribution}", "TRC code distribution"),
    ("{csat_summary}", "CSAT score summary"),
    ("{sentiment_by_trc}", "Sentiment scores by TRC"),
    ("{incident_flags}", "Active incident flags"),
    ("{top_terms}", "Top TF-IDF terms"),
    ("{rising_terms}", "Rising velocity terms"),
    ("{correlations}", "Cross-TRC correlations"),
    ("{redacted_samples}", "Redacted conversation samples (max 10)"),
    ("{intervention_context}", "Recent intervention markers"),
    ("{payer_distribution}", "Payer entity distribution"),
    ("{product_area_distribution}", "Product area distribution"),
    ("{product_gap_flags}", "Product gap detection flags"),
]


class ExpandedPromptDialog(QDialog):
    """Near-fullscreen modal for editing long prompts."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Prompt — Expanded View")
        self.setMinimumSize(900, 650)
        self.resize(1100, 750)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        self._editor = QTextEdit()
        self._editor.setPlainText(text)
        self._editor.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 12px; font-family: 'Consolas', monospace;
                font-size: 13px; color: {ALMA_TEXT_DARK};
            }}
        """)
        layout.addWidget(self._editor, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("Apply")
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 8px 24px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

    def get_text(self):
        return self._editor.toPlainText()


class PromptEditorDialog(QDialog):
    """Dialog for creating/editing prompts in the prompt library."""

    def __init__(self, prompt_data=None, is_canned=False, parent=None):
        super().__init__(parent)
        self._prompt_data = prompt_data or {}
        self._is_canned = is_canned
        self.setWindowTitle(
            "Edit Prompt" if prompt_data else "New Prompt"
        )
        self.setMinimumSize(700, 580)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Title
        title = QLabel("Edit Prompt" if self._prompt_data else "New Prompt")
        title.setStyleSheet(
            f"font-size: 18px; font-weight: 700; color: {ALMA_TEXT_DARK};"
        )
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_widget = QWidget()
        form = QVBoxLayout(form_widget)
        form.setSpacing(10)

        # Name
        form.addWidget(self._field_label("Prompt Name"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., Weekly TRC Review")
        self._name_edit.setText(self._prompt_data.get("name", ""))
        self._style_input(self._name_edit)
        form.addWidget(self._name_edit)

        # Description
        form.addWidget(self._field_label("Description"))
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Brief description of what this prompt analyzes")
        self._desc_edit.setText(self._prompt_data.get("description", ""))
        self._style_input(self._desc_edit)
        form.addWidget(self._desc_edit)

        # System Prompt
        form.addWidget(self._field_label("System Prompt (optional)"))
        self._system_edit = QTextEdit()
        self._system_edit.setPlaceholderText(
            "System instructions for the AI model (e.g., persona, output format)..."
        )
        self._system_edit.setPlainText(self._prompt_data.get("system_prompt", ""))
        self._system_edit.setMaximumHeight(80)
        self._style_textedit(self._system_edit)
        form.addWidget(self._system_edit)

        # Analysis Prompt with Expand button
        prompt_header = QHBoxLayout()
        prompt_header.addWidget(self._field_label("Analysis Prompt"))
        prompt_header.addStretch()

        expand_btn = QPushButton("Expand")
        expand_btn.setToolTip("Open near-fullscreen editor")
        expand_btn.setCursor(Qt.PointingHandCursor)
        expand_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_INFO}; color: white;
                border: none; border-radius: 4px; padding: 4px 12px;
                font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #1A5F90; }}
        """)
        expand_btn.clicked.connect(self._expand_prompt)
        prompt_header.addWidget(expand_btn)

        import_btn = QPushButton("Import .txt")
        import_btn.setToolTip("Load prompt text from a file")
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_INFO};
                border: 1px solid {ALMA_INFO}; border-radius: 4px;
                padding: 4px 12px; font-size: 11px;
            }}
            QPushButton:hover {{ background: #E8F0FE; }}
        """)
        import_btn.clicked.connect(self._import_from_file)
        prompt_header.addWidget(import_btn)

        form.addLayout(prompt_header)

        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlaceholderText(
            "Enter the analysis prompt here. Use {variables} for dynamic data injection.\n"
            "Example: Analyze the following data:\n{data_block}"
        )
        self._prompt_edit.setPlainText(self._prompt_data.get("prompt_text", ""))
        self._prompt_edit.setMinimumHeight(140)
        self._style_textedit(self._prompt_edit)
        form.addWidget(self._prompt_edit)

        # Variable reference
        var_group = QGroupBox("Available Variables (click to insert)")
        var_group.setStyleSheet(f"""
            QGroupBox {{
                font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 6px;
                padding: 12px; padding-top: 20px; margin-top: 6px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 12px;
            }}
        """)
        var_layout = QHBoxLayout(var_group)
        var_layout.setSpacing(4)

        # Wrap variables in a flow layout
        var_inner = QWidget()
        var_flow = QVBoxLayout(var_inner)
        var_flow.setSpacing(2)

        row = QHBoxLayout()
        for i, (var, desc) in enumerate(PROMPT_VARIABLES):
            btn = QPushButton(var)
            btn.setToolTip(desc)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {ALMA_CREAM}; color: {ALMA_TEXT_MID};
                    border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 3px;
                    padding: 2px 6px; font-size: 10px; font-family: monospace;
                }}
                QPushButton:hover {{ background: {ALMA_WHITE}; border-color: {ALMA_INFO}; }}
            """)
            btn.clicked.connect(lambda checked, v=var: self._insert_variable(v))
            row.addWidget(btn)
            if (i + 1) % 5 == 0:
                var_flow.addLayout(row)
                row = QHBoxLayout()
        if row.count() > 0:
            var_flow.addLayout(row)

        var_layout.addWidget(var_inner)
        form.addWidget(var_group)

        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)

        # Buttons
        btn_row = QHBoxLayout()

        if self._is_canned:
            reset_btn = QPushButton("Reset to Default")
            reset_btn.setToolTip("Reset this canned prompt to its original text")
            reset_btn.setCursor(Qt.PointingHandCursor)
            reset_btn.clicked.connect(self._reset_to_default)
            btn_row.addWidget(reset_btn)

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
                color: {ALMA_TEXT_DARK};
            }}
            QLineEdit:focus {{ border-color: {ALMA_GREEN_MID}; }}
        """)

    def _style_textedit(self, widget):
        widget.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 8px; font-size: 12px;
                color: {ALMA_TEXT_DARK};
            }}
            QTextEdit:focus {{ border-color: {ALMA_GREEN_MID}; }}
        """)

    def _insert_variable(self, var_text):
        self._prompt_edit.insertPlainText(var_text)
        self._prompt_edit.setFocus()

    def _expand_prompt(self):
        dlg = ExpandedPromptDialog(self._prompt_edit.toPlainText(), self)
        if dlg.exec() == QDialog.Accepted:
            self._prompt_edit.setPlainText(dlg.get_text())

    def _import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Prompt", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._prompt_edit.setPlainText(f.read())
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Import Error", str(e))

    def _reset_to_default(self):
        """Reset canned prompt to its original text from config file."""
        # This is handled by the caller (ai_reports page) which knows the defaults
        self._prompt_data["_reset_requested"] = True
        self.accept()

    def _on_save(self):
        name = self._name_edit.text().strip()
        prompt_text = self._prompt_edit.toPlainText().strip()
        if not name:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Required", "Prompt name is required.")
            return
        if not prompt_text:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Required", "Analysis prompt text is required.")
            return
        self.accept()

    def get_prompt_data(self):
        """Return the edited prompt data dict."""
        return {
            "name": self._name_edit.text().strip(),
            "description": self._desc_edit.text().strip(),
            "system_prompt": self._system_edit.toPlainText().strip(),
            "prompt_text": self._prompt_edit.toPlainText().strip(),
            "category": self._prompt_data.get("category", "custom"),
            "_reset_requested": self._prompt_data.get("_reset_requested", False),
        }
