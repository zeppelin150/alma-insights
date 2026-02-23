"""
Alma Insights — Report Chat Widget (Pass 3.0)
DRILLDOWN follow-up chat for AI reports.
Maintains conversation history and packs full context per Gemini call
(CLI is stateless — each call includes the full conversation).
"""

import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_MID, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_INFO,
)


class ChatWorker(QThread):
    """Background thread for Gemini chat follow-up."""
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, gemini_client, prompt, system_prompt=""):
        super().__init__()
        self._client = gemini_client
        self._prompt = prompt
        self._system_prompt = system_prompt

    def run(self):
        try:
            response = self._client.generate(self._prompt, self._system_prompt)
            self.finished.emit(response)
        except Exception as e:
            self.error.emit(str(e))


class ChatBubble(QFrame):
    """Styled chat message bubble."""

    def __init__(self, text, role="user", parent=None):
        super().__init__(parent)
        self._role = role
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)

        if role == "user":
            self.setStyleSheet(f"""
                ChatBubble {{
                    background: {ALMA_INFO}; border-radius: 10px;
                    margin-left: 80px; margin-right: 4px;
                }}
                QLabel {{
                    color: white; font-size: 12px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                ChatBubble {{
                    background: {ALMA_CREAM}; border-radius: 10px;
                    border: 1px solid {ALMA_BORDER_LIGHT};
                    margin-left: 4px; margin-right: 80px;
                }}
                QLabel {{
                    color: {ALMA_TEXT_DARK}; font-size: 12px;
                }}
            """)


class ReportChatWidget(QWidget):
    """DRILLDOWN follow-up chat embedded below AI report output."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history = []  # [{"role": "...", "content": "..."}, ...]
        self._data_block_text = ""
        self._report_text = ""
        self._gemini_client = None
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title = QLabel("Follow-Up Chat")
        title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {ALMA_TEXT_MID};")
        header.addWidget(title)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT};")
        header.addStretch()
        header.addWidget(self._status_label)
        layout.addLayout(header)

        # Chat area (scrollable)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setMinimumHeight(150)
        self._scroll.setMaximumHeight(400)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};
                border-radius: 8px;
            }}
        """)

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(8, 8, 8, 8)
        self._chat_layout.setSpacing(6)
        self._chat_layout.addStretch()
        self._scroll.setWidget(self._chat_container)
        layout.addWidget(self._scroll)

        # Input row
        input_row = QHBoxLayout()

        self._input_edit = QTextEdit()
        self._input_edit.setPlaceholderText("Ask a follow-up question about the report...")
        self._input_edit.setMaximumHeight(50)
        self._input_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; padding: 6px; font-size: 12px;
            }}
            QTextEdit:focus {{ border-color: {ALMA_GREEN_MID}; }}
        """)
        input_row.addWidget(self._input_edit, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setEnabled(False)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 6px; padding: 8px 16px;
                font-weight: 600; font-size: 12px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._send_btn.clicked.connect(self._send_message)
        input_row.addWidget(self._send_btn)

        layout.addLayout(input_row)

        # Placeholder
        self._placeholder = QLabel("Generate a report to enable follow-up chat")
        self._placeholder.setStyleSheet(f"color: {ALMA_TEXT_LIGHT}; font-size: 11px;")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._chat_layout.insertWidget(0, self._placeholder)

    def set_gemini_client(self, client):
        """Set the Gemini client for chat calls."""
        self._gemini_client = client

    def set_report_context(self, data_block_text, report_text):
        """Initialize context after report generation."""
        self._data_block_text = data_block_text
        self._report_text = report_text
        self._history = [
            {"role": "system", "content": data_block_text},
            {"role": "assistant", "content": report_text},
        ]
        self._send_btn.setEnabled(True)
        self._placeholder.hide()
        self._status_label.setText("Ready for questions")

    def load_chat_history(self, history):
        """Restore chat history from a saved report."""
        if isinstance(history, str):
            try:
                history = json.loads(history)
            except (json.JSONDecodeError, TypeError):
                return
        if not history:
            return

        self._history = history
        self._placeholder.hide()

        # Rebuild UI bubbles for user/assistant messages
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                self._add_bubble(msg["content"], msg["role"])

        # Re-extract context
        for msg in history:
            if msg["role"] == "system":
                self._data_block_text = msg["content"]
            elif msg["role"] == "assistant" and not self._report_text:
                self._report_text = msg["content"]

        self._send_btn.setEnabled(bool(self._gemini_client))

    def get_chat_history(self):
        """Return chat history for persistence."""
        return json.dumps(self._history)

    def clear(self):
        """Reset chat state."""
        self._history = []
        self._data_block_text = ""
        self._report_text = ""
        self._nlp_finding_id = None
        self._send_btn.setEnabled(False)
        self._status_label.setText("")
        self._placeholder.show()

        # Remove all bubbles
        while self._chat_layout.count() > 1:
            item = self._chat_layout.takeAt(0)
            if item.widget() and item.widget() != self._placeholder:
                item.widget().deleteLater()

    def _send_message(self):
        question = self._input_edit.toPlainText().strip()
        if not question or not self._gemini_client:
            return

        # Add user bubble
        self._add_bubble(question, "user")
        self._history.append({"role": "user", "content": question})
        self._input_edit.clear()

        # Check for NLP finding-aware drilldown
        finding_id = getattr(self, '_nlp_finding_id', None)
        if finding_id:
            self._send_finding_drilldown(finding_id, question)
            return

        # Build packed prompt (last 5 turns for size management)
        prompt = self._build_followup_prompt(question)

        # Disable while processing
        self._send_btn.setEnabled(False)
        self._status_label.setText("Thinking...")

        self._worker = ChatWorker(self._gemini_client, prompt,
                                   "You are a Support Analytics engine in follow-up mode.")
        self._worker.finished.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _send_finding_drilldown(self, finding_id, question):
        """Use NLP synthesizer for finding-aware drilldown response."""
        self._send_btn.setEnabled(False)
        self._status_label.setText("Analyzing finding...")

        class FindingWorker(QThread):
            finished = Signal(str)
            error = Signal(str)

            def __init__(self, db_path, finding_id, question, gemini_client):
                super().__init__()
                self._db_path = db_path
                self._finding_id = finding_id
                self._question = question
                self._gemini = gemini_client

            def run(self):
                try:
                    from src.data.db_manager import DatabaseManager
                    from src.data.nlp_synthesis import NLPSynthesizer
                    db = DatabaseManager(self._db_path)
                    db.initialize()
                    synth = NLPSynthesizer(db, self._gemini)
                    result = synth.synthesize_single_finding(
                        self._finding_id, user_question=self._question
                    )
                    db.close()
                    self.finished.emit(result)
                except Exception as e:
                    self.error.emit(str(e))

        # Get db_path from gemini_client's parent context
        # The db path is available via the report context
        db_path = None
        try:
            # Walk up to find the db manager
            parent = self.parent()
            while parent:
                if hasattr(parent, 'db'):
                    db_path = parent.db.db_path
                    break
                parent = parent.parent()
        except Exception:
            pass

        if not db_path:
            self._on_error("Cannot find database path for finding drilldown")
            return

        self._worker = FindingWorker(db_path, finding_id, question, self._gemini_client)
        self._worker.finished.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _build_followup_prompt(self, question):
        """Pack the full conversation context into a single prompt."""
        parts = []

        parts.append("ORIGINAL REPORT:")
        parts.append(self._report_text[:8000])  # Truncate if very long

        # Last 5 Q&A turns
        qa_turns = [m for m in self._history if m["role"] in ("user", "assistant")]
        # Skip the initial report (first assistant message)
        qa_turns = qa_turns[1:]  # Remove initial assistant response
        recent = qa_turns[-10:]  # Last 5 pairs = 10 messages

        if recent:
            parts.append("\nPRIOR Q&A:")
            for m in recent:
                prefix = "Q" if m["role"] == "user" else "A"
                parts.append(f"{prefix}: {m['content'][:2000]}")

        parts.append(f"\nNEW QUESTION: {question}")
        parts.append(
            "\nIf the question asks for a drilldown on a specific finding, produce:\n"
            "WHAT IT IS / EVIDENCE / WHO-WHERE / WHY / WHAT TO DO\n"
            "If it's a simple question, answer directly.\n"
            "Always cite specific metrics from the original report."
        )

        return "\n\n".join(parts)

    def _on_response(self, text):
        self._add_bubble(text, "assistant")
        self._history.append({"role": "assistant", "content": text})
        self._send_btn.setEnabled(True)
        self._status_label.setText("Ready")

    def _on_error(self, error_text):
        self._add_bubble(f"Error: {error_text}", "assistant")
        self._send_btn.setEnabled(True)
        self._status_label.setText("Error occurred")

    def _add_bubble(self, text, role):
        """Add a chat bubble to the scroll area."""
        bubble = ChatBubble(text, role)
        # Insert before the stretch
        idx = self._chat_layout.count() - 1
        self._chat_layout.insertWidget(idx, bubble)

        # Scroll to bottom
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))
