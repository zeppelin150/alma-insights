"""AI Reports — Conversational prompt builder widget (R4.2).

2-pane wizard backing the Manage-Prompts tab. Mirrors 3.24.26
Updated_AI_Reports_Prompt_Builder_Page.png:

  Left pane:  list of build-history (canned + custom prompts), "+ New prompt"
  Right pane: conversational chat canvas with sticky preview + Save button

The chat is driven by `AuthoringSession` (src/data/prompt_authoring.py).
The wizard widget is logic-light — it owns the layout and signal wiring;
the LLM call (via `build_client_for_task('prompt_authoring')`) is made
on the worker thread to keep the UI responsive.

Public API
----------
- `PromptWizard(db, parent=None)`
- `PromptWizard.refresh_prompt_list()`
- Signal: `prompt_saved(name)` — emitted after a custom prompt persists
- Signal: `prompt_selected(prompt_id)` — when a user clicks a left-pane row

Dependencies
------------
- PySide6.QtWidgets / QtCore
- src.data.prompt_authoring (AuthoringSession)
- src.ui.theme (color constants)
- DB manager exposing `get_prompts(category=None)` and `save_prompt(dict)`
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QSplitter, QLineEdit, QPlainTextEdit, QFrame,
    QSizePolicy, QInputDialog, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QThread

from src.data.prompt_authoring import AuthoringSession, AuthoringState
from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_MID, ALMA_GREEN_SUBTLE,
    ALMA_WHITE, ALMA_CREAM, ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_ELEVATED, ALMA_BG_INSET,
)


# ──────────────────────────────────────────────────────────────────────
# Worker thread — runs one wizard step off the main thread
# ──────────────────────────────────────────────────────────────────────

class _AuthoringWorker(QThread):
    finished_step = Signal(dict)

    def __init__(self, session: AuthoringSession, user_text: str) -> None:
        super().__init__()
        self._session = session
        self._user_text = user_text

    def run(self) -> None:
        try:
            self.finished_step.emit(self._session.step(self._user_text))
        except Exception as exc:
            self.finished_step.emit({"error": str(exc)})


# ──────────────────────────────────────────────────────────────────────
# PromptWizard
# ──────────────────────────────────────────────────────────────────────

class PromptWizard(QWidget):
    prompt_saved = Signal(str)
    prompt_selected = Signal(int)

    def __init__(self, db: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self._session: AuthoringSession | None = None
        self._worker: _AuthoringWorker | None = None
        self._llm_callable = self._maybe_build_llm()
        self._build_ui()
        self.refresh_prompt_list()

    # ── public ─────────────────────────────────────────────────────

    def refresh_prompt_list(self) -> None:
        """Reload the left-pane list from the prompt_library table."""
        self._prompt_list.clear()
        try:
            canned = self.db.get_prompts(category="canned") or []
            custom = self.db.get_prompts(category="custom") or []
        except Exception:
            canned, custom = [], []
        if canned:
            self._prompt_list.addItem(_section_item("BUILT-IN"))
            for p in canned:
                self._prompt_list.addItem(_prompt_item(p))
        if custom:
            self._prompt_list.addItem(_section_item("CUSTOM"))
            for p in custom:
                self._prompt_list.addItem(_prompt_item(p))
        if not canned and not custom:
            self._prompt_list.addItem(_section_item("No prompts yet — start one →"))

    # ── UI build ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_pane())
        splitter.addWidget(self._build_right_pane())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([220, 700])
        layout.addWidget(splitter)

    def _build_left_pane(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet(f"background: {ALMA_BG_ELEVATED};"
                               f" border-radius: 10px;")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(10, 10, 10, 10)
        wl.setSpacing(8)

        title = QLabel("BUILD HISTORY")
        title.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_LIGHT};"
            f" letter-spacing: 1px;"
        )
        wl.addWidget(title)

        self._prompt_list = QListWidget()
        self._prompt_list.setStyleSheet(_list_qss())
        self._prompt_list.itemClicked.connect(self._on_prompt_clicked)
        wl.addWidget(self._prompt_list, 1)

        new_btn = QPushButton("+ New prompt")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.setStyleSheet(_primary_button_qss())
        new_btn.clicked.connect(self._start_new_session)
        wl.addWidget(new_btn)
        return wrapper

    def _build_right_pane(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setStyleSheet(f"background: {ALMA_WHITE};"
                               f" border: 1px solid {ALMA_BORDER_LIGHT};"
                               f" border-radius: 10px;")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(14, 12, 14, 14)
        wl.setSpacing(8)

        self._title_lbl = QLabel("Start a new prompt to begin")
        self._title_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {ALMA_GREEN_DARK};"
        )
        wl.addWidget(self._title_lbl)

        self._chat_log = QPlainTextEdit()
        self._chat_log.setReadOnly(True)
        self._chat_log.setStyleSheet(_chat_qss())
        self._chat_log.setMinimumHeight(220)
        wl.addWidget(self._chat_log, 1)

        # Sticky preview block
        preview_label = QLabel("GENERATED PROMPT PREVIEW")
        preview_label.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {ALMA_TEXT_LIGHT};"
            f" letter-spacing: 1px; margin-top: 4px;"
        )
        wl.addWidget(preview_label)

        self._preview_box = QPlainTextEdit()
        self._preview_box.setReadOnly(True)
        self._preview_box.setStyleSheet(
            f"QPlainTextEdit {{ background: {ALMA_BG_INSET}; "
            f"color: {ALMA_TEXT_DARK}; border: none; border-radius: 8px;"
            f" padding: 10px; font-family: 'Consolas', monospace;"
            f" font-size: 11px; }}"
        )
        self._preview_box.setMinimumHeight(120)
        wl.addWidget(self._preview_box)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Refine this prompt… (e.g. 'add a section for CSAT')")
        self._input.setStyleSheet(_input_qss())
        self._input.returnPressed.connect(self._on_send)
        self._input.setEnabled(False)
        input_row.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setStyleSheet(_primary_button_qss())
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setEnabled(False)
        input_row.addWidget(self._send_btn)
        wl.addLayout(input_row)

        # Save button (separate row, prominent)
        self._save_btn = QPushButton("Save as new prompt")
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(_primary_button_qss(filled=True))
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setEnabled(False)
        wl.addWidget(self._save_btn)
        return wrapper

    # ── interactions ───────────────────────────────────────────────

    def _start_new_session(self) -> None:
        self._session = AuthoringSession(llm_callable=self._llm_callable)
        self._chat_log.clear()
        self._preview_box.clear()
        self._title_lbl.setText("Building a new custom prompt…")
        greeting = self._session.bot_greeting()
        self._append_chat("assistant", greeting)
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._save_btn.setEnabled(False)

    def _on_send(self) -> None:
        if self._session is None or self._worker is not None:
            return
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._append_chat("user", text)
        self._set_busy(True)
        self._worker = _AuthoringWorker(self._session, text)
        self._worker.finished_step.connect(self._on_step_done)
        self._worker.start()

    def _on_step_done(self, snapshot: dict) -> None:
        self._set_busy(False)
        self._worker = None
        if snapshot.get("error"):
            self._append_chat("assistant", f"Error: {snapshot['error']}")
            return
        # Last assistant message is already appended by AuthoringSession.transcript;
        # we only need the latest entry here.
        if self._session is not None:
            last = self._session.transcript[-1] if self._session.transcript else None
            if last and last["role"] == "assistant":
                self._append_chat("assistant", last["content"])
        if snapshot.get("prompt_preview"):
            self._preview_box.setPlainText(snapshot["prompt_preview"])
        # Save unlocks at PREVIEW state and stays unlocked through DONE
        if snapshot.get("state") in (AuthoringState.PREVIEW.value,
                                       AuthoringState.DONE.value):
            self._save_btn.setEnabled(True)

    def _on_save(self) -> None:
        if self._session is None:
            return
        name, ok = QInputDialog.getText(
            self, "Name this prompt", "Prompt name:",
            text=self._session.name or "Custom report",
        )
        if not ok or not name.strip():
            return
        description, _ = QInputDialog.getText(
            self, "Description", "Short description (optional):",
            text=self._session.description or "",
        )
        record = self._session.finalize(name.strip(), (description or "").strip())
        try:
            self.db.save_prompt(record)
        except Exception as exc:
            QMessageBox.warning(self, "Save error", str(exc))
            return
        self.refresh_prompt_list()
        self.prompt_saved.emit(name.strip())
        self._save_btn.setText("Saved!")

    def _on_prompt_clicked(self, item: QListWidgetItem) -> None:
        prompt_id = item.data(Qt.UserRole)
        if prompt_id is None:
            return
        self.prompt_selected.emit(int(prompt_id))

    # ── helpers ────────────────────────────────────────────────────

    def _maybe_build_llm(self):
        """Return a callable that runs the wizard via Claude, or None."""
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task("prompt_authoring")
            if client is None:
                return None
        except Exception:
            return None

        def _call(messages: list[dict]) -> dict:
            # client.generate is sync — wizard runs on a worker thread
            try:
                user_msg = messages[-1]["content"] if messages else ""
                system = "\n".join(m["content"] for m in messages
                                    if m["role"] == "system")
                resp = client.generate(user_msg, system_prompt=system, timeout=60)
                # Try to parse JSON; fall back to bot_reply only
                import json as _json
                try:
                    return _json.loads(resp)
                except Exception:
                    return {"bot_reply": resp}
            except Exception:
                return {}
        return _call

    def _append_chat(self, role: str, text: str) -> None:
        marker = "You" if role == "user" else "Assistant"
        self._chat_log.appendPlainText(f"{marker}: {text}\n")

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)


# ──────────────────────────────────────────────────────────────────────
# Internal helpers — list rendering + qss
# ──────────────────────────────────────────────────────────────────────

def _section_item(label: str) -> QListWidgetItem:
    item = QListWidgetItem(label)
    item.setFlags(Qt.NoItemFlags)
    item.setData(Qt.UserRole, None)
    item.setForeground(Qt.gray)
    return item


def _prompt_item(prompt_row: dict) -> QListWidgetItem:
    name = prompt_row.get("name") or "(unnamed)"
    desc = prompt_row.get("description") or ""
    text = name if not desc else f"{name}\n  {desc}"
    item = QListWidgetItem(text)
    item.setData(Qt.UserRole, prompt_row.get("prompt_id"))
    return item


def _list_qss() -> str:
    return (
        f"QListWidget {{ background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER_LIGHT};"
        f" border-radius: 8px; padding: 4px; }}"
        f"QListWidget::item {{ padding: 8px 6px; margin-bottom: 2px;"
        f" border-radius: 6px; color: {ALMA_TEXT_DARK}; }}"
        f"QListWidget::item:selected {{ background: {ALMA_GREEN_SUBTLE};"
        f" color: {ALMA_GREEN_DARK}; }}"
    )


def _chat_qss() -> str:
    return (
        f"QPlainTextEdit {{ background: {ALMA_CREAM}; color: {ALMA_TEXT_DARK};"
        f" border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;"
        f" padding: 12px; font-size: 12px; }}"
    )


def _input_qss() -> str:
    return (
        f"QLineEdit {{ background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};"
        f" border: 1px solid {ALMA_BORDER_LIGHT}; border-radius: 8px;"
        f" padding: 8px 10px; font-size: 12px; }}"
        f"QLineEdit:focus {{ border-color: {ALMA_GREEN_LIGHT}; }}"
    )


def _primary_button_qss(*, filled: bool = False) -> str:
    if filled:
        return (
            f"QPushButton {{ background: {ALMA_GREEN_DARK}; color: white;"
            f" border: none; border-radius: 8px;"
            f" padding: 8px 16px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}"
            f"QPushButton:disabled {{ background: {ALMA_BORDER}; }}"
        )
    return (
        f"QPushButton {{ background: {ALMA_WHITE}; color: {ALMA_GREEN_DARK};"
        f" border: 1px solid {ALMA_GREEN_DARK}; border-radius: 8px;"
        f" padding: 6px 12px; font-size: 12px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {ALMA_GREEN_SUBTLE}; }}"
        f"QPushButton:disabled {{ color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER}; }}"
    )
