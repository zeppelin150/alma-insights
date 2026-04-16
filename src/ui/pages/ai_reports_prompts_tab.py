"""
Alma Insights — AI Reports: Manage Prompts Tab (Build 11.0)

Prompt library browser + Gemini-powered conversational prompt builder.
Left sidebar: list of built-in and custom prompts.
Right panel: conversational builder where Gemini helps create prompts
step-by-step, with generated prompt preview at bottom.
"""

import json
import re
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QSplitter,
    QPlainTextEdit, QScrollArea, QSizePolicy, QLineEdit, QComboBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
    ALMA_INFO, ALMA_SUCCESS,
    apply_card_shadow_soft,
)

logger = logging.getLogger("alma.prompts_tab")

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "config" / "prompts"

# Initial prompt builder system message
_BUILDER_SYSTEM = (
    "You are a report prompt builder for Alma Insights, an RCM ticket analysis tool. "
    "Help the user build a report prompt step by step. Ask what the report should answer, "
    "what data to scope (TRCs, date ranges, ticket types), and what output structure is needed. "
    "After gathering requirements, generate a complete prompt template with {variables} like "
    "{ticket_count}, {date_start}, {date_end}, {statistical_context}, {data_block}. "
    "Keep prompts focused and under 500 words."
)



class BuilderMessageBubble(QFrame):
    """Chat message bubble for the prompt builder."""

    def __init__(self, role, content, parent=None):
        super().__init__(parent)
        is_user = role == "user"

        self.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_GREEN_SUBTLE if is_user else ALMA_BG_ELEVATED};
                border: none;
                border-radius: 12px;
                margin: {'2px 0px 2px 40px' if is_user else '2px 40px 2px 0px'};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        role_lbl = QLabel("You" if is_user else "Gemini")
        role_lbl.setStyleSheet(f"""
            font-size: 10px; font-weight: 700;
            color: {ALMA_GREEN_DARK if is_user else ALMA_INFO};
        """)
        layout.addWidget(role_lbl)

        content_lbl = QLabel(content)
        content_lbl.setWordWrap(True)
        content_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        content_lbl.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_DARK}; line-height: 1.5;")
        layout.addWidget(content_lbl)


class ManagePromptsTab(QWidget):
    """Prompt library browser and Gemini-powered conversational builder."""

    def __init__(self, db_manager, parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._current_prompt_name = None
        self._builder_messages = []  # kept for UI bubble tracking
        self._worker = None  # legacy compat

        # ChatEngine — build-per-message, no tools
        from src.services.chat_engine import ChatEngine
        self._engine = ChatEngine(
            system_prompt=_BUILDER_SYSTEM,
            task_type="report_generation",
            history_packer=self._pack_builder_history,
            response_handler=self._extract_prompt_from_response,
        )
        self._engine.response_ready.connect(self._on_builder_response)
        self._engine.error_occurred.connect(self._on_builder_error)
        self._engine.busy_changed.connect(self._on_builder_busy)

        self._build_ui()
        self._load_prompts()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)

        # ══ Left sidebar: prompt list ══
        left = QFrame()
        left.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border-right: 1px solid {ALMA_BORDER_LIGHT};
            }}
        """)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(8)

        section_style = f"font-size: 10px; font-weight: 700; color: {ALMA_TEXT_LIGHT}; letter-spacing: 1px;"

        lbl = QLabel("BUILT-IN PROMPTS")
        lbl.setStyleSheet(section_style)
        left_layout.addWidget(lbl)

        self._builtin_list = QListWidget()
        self._builtin_list.setStyleSheet(self._list_style())
        self._builtin_list.currentItemChanged.connect(self._on_prompt_selected)
        left_layout.addWidget(self._builtin_list)

        lbl2 = QLabel("CUSTOM PROMPTS")
        lbl2.setStyleSheet(section_style)
        left_layout.addWidget(lbl2)

        self._custom_list = QListWidget()
        self._custom_list.setStyleSheet(self._list_style())
        self._custom_list.currentItemChanged.connect(self._on_prompt_selected)
        left_layout.addWidget(self._custom_list)

        new_btn = QPushButton("+ New prompt")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {ALMA_GREEN_DARK};
                border: 1px dashed {ALMA_GREEN_MID}; border-radius: 8px;
                padding: 8px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}
        """)
        new_btn.clicked.connect(self._on_new_prompt)
        left_layout.addWidget(new_btn)

        left.setMinimumWidth(220)
        left.setMaximumWidth(280)
        splitter.addWidget(left)

        # ══ Right panel: conversational builder ══
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Title bar with action buttons
        title_bar = QFrame()
        title_bar.setStyleSheet(f"background: transparent;")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(24, 12, 24, 8)

        self._title_label = QLabel("Select a prompt")
        self._title_label.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        tb_layout.addWidget(self._title_label)
        tb_layout.addStretch()

        self._preview_btn = QPushButton("Preview prompt")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.setStyleSheet(self._action_btn_style())
        self._preview_btn.setVisible(False)
        self._preview_btn.clicked.connect(self._toggle_preview)
        tb_layout.addWidget(self._preview_btn)

        self._test_btn = QPushButton("Test run")
        self._test_btn.setCursor(Qt.PointingHandCursor)
        self._test_btn.setStyleSheet(self._action_btn_style())
        self._test_btn.setVisible(False)
        tb_layout.addWidget(self._test_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: white;
                border: none; border-radius: 6px;
                padding: 6px 16px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._save_btn.setVisible(False)
        self._save_btn.clicked.connect(self._on_save_prompt)
        tb_layout.addWidget(self._save_btn)

        self._test_btn.clicked.connect(self._on_test_prompt)

        # Model selector (compact)
        model_lbl = QLabel("Model:")
        model_lbl.setStyleSheet(f"font-size: 10px; color: {ALMA_TEXT_LIGHT};")
        tb_layout.addWidget(model_lbl)

        self._model_combo = QComboBox()
        self._model_combo.setStyleSheet(f"""
            QComboBox {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 6px;
                padding: 3px 8px; font-size: 10px; min-width: 120px;
            }}
        """)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        tb_layout.addWidget(self._model_combo)

        right_layout.addWidget(title_bar)

        # Status label (thinking indicator)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"""
            font-size: 11px; color: {ALMA_TEXT_LIGHT}; font-style: italic;
            padding: 0px 24px;
        """)
        self._status_label.setVisible(False)
        right_layout.addWidget(self._status_label)

        # Chat conversation area (scrollable)
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setFrameShape(QFrame.NoFrame)
        self._chat_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(24, 8, 24, 8)
        self._chat_layout.setSpacing(10)
        self._chat_layout.addStretch()

        self._chat_scroll.setWidget(self._chat_container)
        right_layout.addWidget(self._chat_scroll, 1)

        # Suggestion chips (hidden by default)
        self._chips_frame = QFrame()
        chips_layout = QHBoxLayout(self._chips_frame)
        chips_layout.setContentsMargins(24, 4, 24, 4)
        chips_layout.setSpacing(6)
        self._chips_layout = chips_layout
        chips_layout.addStretch()
        self._chips_frame.setVisible(False)
        right_layout.addWidget(self._chips_frame)

        # Generated prompt preview (collapsible)
        self._preview_frame = QFrame()
        self._preview_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_INSET};
                border-top: 1px solid {ALMA_BORDER_LIGHT};
            }}
        """)
        pf_layout = QVBoxLayout(self._preview_frame)
        pf_layout.setContentsMargins(24, 8, 24, 8)
        pf_layout.setSpacing(4)

        pf_hdr = QLabel("Generated prompt preview")
        pf_hdr.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_LIGHT}; letter-spacing: 0.5px;")
        pf_layout.addWidget(pf_hdr)

        self._prompt_preview = QPlainTextEdit()
        self._prompt_preview.setReadOnly(True)
        self._prompt_preview.setMaximumHeight(200)
        self._prompt_preview.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {ALMA_WHITE};
                border: none;
                border-radius: 6px;
                padding: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                color: {ALMA_TEXT_DARK};
            }}
        """)
        pf_layout.addWidget(self._prompt_preview)

        self._preview_frame.setVisible(False)
        right_layout.addWidget(self._preview_frame)

        # Input bar
        input_frame = QFrame()
        input_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED};
                border-top: 1px solid {ALMA_BORDER_LIGHT};
            }}
        """)
        inp_layout = QHBoxLayout(input_frame)
        inp_layout.setContentsMargins(24, 10, 24, 10)
        inp_layout.setSpacing(8)

        self._builder_input = QLineEdit()
        self._builder_input.setPlaceholderText(
            "Refine this prompt... (e.g., 'add a section for CSAT')"
        )
        self._builder_input.setStyleSheet(f"""
            QLineEdit {{
                background: {ALMA_WHITE};
                border: 1px solid {ALMA_BORDER};
                border-radius: 8px;
                padding: 8px 14px;
                font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
            QLineEdit:focus {{ border-color: {ALMA_GREEN_MID}; }}
        """)
        self._builder_input.returnPressed.connect(self._on_builder_send)
        inp_layout.addWidget(self._builder_input, 1)

        send_btn = QPushButton("Send")
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: white;
                border: none; border-radius: 8px;
                padding: 8px 20px; font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        send_btn.clicked.connect(self._on_builder_send)
        inp_layout.addWidget(send_btn)

        right_layout.addWidget(input_frame)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

    # ── Styles ──

    def _list_style(self):
        return f"""
            QListWidget {{
                background: transparent; border: none;
                font-size: 13px; color: {ALMA_TEXT_DARK};
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 6px;
            }}
            QListWidget::item:selected {{
                background: {ALMA_GREEN_SUBTLE};
                color: {ALMA_GREEN_DARK};
            }}
            QListWidget::item:hover {{
                background: {ALMA_CREAM};
            }}
        """

    def _action_btn_style(self):
        return f"""
            QPushButton {{
                background: transparent; color: {ALMA_TEXT_MID};
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 5px 12px; font-size: 11px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
        """

    def _chip_style(self):
        return f"""
            QPushButton {{
                background: {ALMA_WHITE};
                color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER};
                border-radius: 14px;
                padding: 5px 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {ALMA_GREEN_SUBTLE};
                border-color: {ALMA_GREEN_MID};
            }}
        """

    # ── Prompt loading ──

    def _load_prompts(self):
        self._builtin_list.clear()
        self._custom_list.clear()

        builtin_prompts = [
            ("[VOC] Root Cause Analysis", "voc_convergence.txt", "3-bridge · 22 variables"),
            ("[VOC] Executive Summary", "executive_summary.txt", "Per-TRC · 8 variables"),
            ("[TRC] Thematic Analysis", "rcm_themes.txt", "Per-TRC · 12 variables"),
            ("[TREND] Deterioration", "general_trend.txt", "Trend-focused · 9 variables"),
        ]
        for name, filename, desc in builtin_prompts:
            item = QListWidgetItem(f"{name}\n{desc}")
            item.setData(Qt.UserRole, {"name": name, "file": filename, "builtin": True})
            self._builtin_list.addItem(item)

        try:
            rows = self.db.conn.execute(
                "SELECT name, prompt_text FROM prompt_library WHERE category = 'custom' ORDER BY name"
            ).fetchall()
            for row in rows:
                name = row[0] if isinstance(row, tuple) else row["name"]
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, {
                    "name": name,
                    "text": row[1] if isinstance(row, tuple) else row["prompt_text"],
                    "builtin": False,
                })
                self._custom_list.addItem(item)
        except Exception:
            pass

    # ── Selection handlers ──

    def _on_prompt_selected(self, current, previous=None):
        if current is None:
            return
        data = current.data(Qt.UserRole)
        if not data:
            return

        self._current_prompt_name = data["name"]
        self._title_label.setText(data["name"])
        self._preview_btn.setVisible(True)
        self._test_btn.setVisible(True)

        # Load prompt text into preview
        if data.get("builtin"):
            path = _PROMPTS_DIR / data["file"]
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                text = "(prompt file not found)"
            self._prompt_preview.setPlainText(text)
            self._save_btn.setVisible(False)
        else:
            text = data.get("text", "")
            self._prompt_preview.setPlainText(text)
            self._save_btn.setVisible(True)

        self._preview_frame.setVisible(True)

        # Clear builder chat when viewing existing prompt
        self._clear_chat()

    def _on_new_prompt(self):
        """Start the conversational prompt builder."""
        self._current_prompt_name = None
        self._title_label.setText("Building: New custom prompt")
        self._save_btn.setVisible(True)
        self._preview_btn.setVisible(True)
        self._test_btn.setVisible(True)
        self._preview_frame.setVisible(True)
        self._prompt_preview.setPlainText("")

        # Clear and start conversation
        self._clear_chat()
        self._builder_messages = []

        # Add Gemini's opening message
        opening = (
            "I'll help you build a report prompt. Let's start with the basics. "
            "What's the core question this report should answer?"
        )
        self._add_chat_message("assistant", opening)

        # Show suggestion chips
        self._show_chips([
            "Why are clients leaving?",
            "Which clients are at risk?",
            "How has churn changed over time?",
        ])

        self._builder_input.setFocus()

    # ── Chat conversation ──

    def _clear_chat(self):
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips_frame.setVisible(False)

    def _add_chat_message(self, role, content):
        bubble = BuilderMessageBubble(role, content)
        self._chat_layout.insertWidget(self._chat_layout.count() - 1, bubble)
        QTimer.singleShot(50, self._scroll_chat_bottom)

    def _scroll_chat_bottom(self):
        vbar = self._chat_scroll.verticalScrollBar()
        vbar.setValue(vbar.maximum())

    def _show_chips(self, suggestions):
        """Show suggestion chips below the chat."""
        # Clear existing chips
        while self._chips_layout.count() > 1:
            item = self._chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for text in suggestions:
            chip = QPushButton(text)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(self._chip_style())
            chip.clicked.connect(lambda _, t=text: self._on_chip_clicked(t))
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, chip)

        self._chips_frame.setVisible(True)

    def _on_chip_clicked(self, text):
        self._chips_frame.setVisible(False)
        self._builder_input.setText(text)
        self._on_builder_send()

    def _on_builder_send(self):
        """Send message to Gemini prompt builder via ChatEngine."""
        text = self._builder_input.text().strip()
        if not text or self._engine.is_busy:
            return

        self._builder_input.clear()
        self._chips_frame.setVisible(False)

        # Add user bubble immediately
        self._builder_messages.append({"role": "user", "content": text})
        self._add_chat_message("user", text)

        # Send to engine
        self._engine.send(text)

    # ── ChatEngine callbacks ──

    def _pack_builder_history(self, user_message, history):
        """Custom history packer: adds pseudo-response guiding template generation."""
        parts = []
        for msg in history:
            role = "User" if msg["role"] == "user" else "Assistant"
            parts.append(f"{role}: {msg['content']}")
        parts.append(
            "Assistant: (Continue helping build the prompt. If you have enough information, "
            "generate the complete prompt template and say 'Here's the generated prompt:' "
            "followed by the prompt text. Include {variables} for data injection.)"
        )
        return "\n\n".join(parts)

    def _extract_prompt_from_response(self, raw_response):
        """Response handler: extract template variables and update preview."""
        if "generated prompt" in raw_response.lower() or "{data_block}" in raw_response or "{ticket_count}" in raw_response:
            lines = raw_response.split("\n")
            prompt_lines = []
            in_prompt = False
            for line in lines:
                if "```" in line:
                    in_prompt = not in_prompt
                    continue
                if in_prompt or (
                    any(v in line for v in ["{data_block}", "{ticket_count}", "{date_start}", "{statistical_context}"])
                ):
                    prompt_lines.append(line)
                    in_prompt = True

            if prompt_lines:
                self._prompt_preview.setPlainText("\n".join(prompt_lines))
                self._preview_frame.setVisible(True)

        return raw_response  # always return full response for display

    def _on_builder_response(self, response):
        self._builder_messages.append({"role": "assistant", "content": response})
        self._add_chat_message("assistant", response)

    def _on_builder_error(self, error):
        self._add_chat_message(
            "assistant",
            f"Sorry, I couldn't connect to Gemini: {error[:150]}. "
            "You can type your prompt directly in the preview area below."
        )
        self._prompt_preview.setReadOnly(False)

    def _on_builder_busy(self, busy):
        self._builder_input.setEnabled(not busy)
        self._status_label.setText("Building prompt..." if busy else "")
        self._status_label.setVisible(busy)

    def _on_model_changed(self, idx):
        if idx < 0:
            return
        model_id = self._model_combo.currentData()
        if model_id:
            self._engine.set_model(model_id)

    def _populate_model_combo(self):
        """Populate from cached settings (no subprocess, no freeze)."""
        from src.ui.pages.gemini_chats_page import _get_cached_models
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for m in _get_cached_models():
            self._model_combo.addItem(m, m)
        self._model_combo.blockSignals(False)

    def _on_save_prompt(self):
        """Save the current prompt preview to the prompt_library table."""
        text = self._prompt_preview.toPlainText().strip()
        if not text:
            self._add_chat_message("assistant", "Nothing to save — the prompt preview is empty.")
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Prompt Name", "Enter a name for this prompt:")
        if not ok or not name.strip():
            return
        try:
            self.db.save_prompt({
                "name": name.strip(),
                "category": "custom",
                "prompt_text": text,
            })
            self._load_prompts()
            self._add_chat_message("assistant", f"Prompt '{name.strip()}' saved successfully.")
        except Exception as e:
            logger.warning("Prompt save failed: %s", e)
            self._add_chat_message("assistant", f"Failed to save prompt: {e}")

    def _on_test_prompt(self):
        """Test-run the current prompt preview against Gemini via ChatEngine."""
        text = self._prompt_preview.toPlainText().strip()
        if not text:
            self._add_chat_message("assistant", "Nothing to test — the prompt preview is empty.")
            return
        self._test_btn.setEnabled(False)
        self._test_btn.setText("Testing...")
        # Use a temporary ChatEngine for the test (separate from builder conversation)
        from src.services.chat_engine import ChatEngine
        self._test_engine = ChatEngine(
            system_prompt="Generate a short sample report to verify this prompt template works.",
        )
        self._test_engine.response_ready.connect(self._on_test_finished)
        self._test_engine.error_occurred.connect(self._on_test_error)
        self._test_engine.send(
            f"Using this prompt template, generate a brief test report with sample data:\n\n{text}"
        )

    def _on_test_finished(self, response):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("Test run")
        self._add_chat_message("assistant", f"**Test result:**\n\n{response[:500]}")

    def _on_test_error(self, error):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("Test run")
        self._add_chat_message("assistant", f"Test failed: {error[:200]}")

    def _toggle_preview(self):
        self._preview_frame.setVisible(not self._preview_frame.isVisible())

    def showEvent(self, event):
        super().showEvent(event)
        self._load_prompts()
        self._populate_model_combo()
