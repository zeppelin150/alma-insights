"""AI Reports — Manage Prompts tab (R4.3 wiring shim).

Thin wrapper that hosts the conversational `PromptWizard` widget
(src/ui/widgets/prompt_wizard.py). The previous 672-LOC implementation
was replaced 2026-05-06 with this shim so the wizard logic lives in a
single, testable widget — see docs/AI_REPORTS.md "Prompt builder".

Reference: 3.24.26 Updated_AI_Reports_Prompt_Builder_Page.png.

Public API
----------
- `ManagePromptsTab(db, parent=None)` — entry point used by AIReportsPage.
- Signals re-emitted from the underlying wizard:
  - `prompt_saved(name: str)`
  - `prompt_selected(prompt_id: int)`

Dependencies
------------
- src.ui.widgets.prompt_wizard.PromptWizard
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Signal

from src.ui.widgets.prompt_wizard import PromptWizard


class ManagePromptsTab(QWidget):
    """Wraps PromptWizard. Kept as its own class so ai_reports.py and any
    legacy imports keep working without surgery."""

    prompt_saved = Signal(str)
    prompt_selected = Signal(int)

    def __init__(self, db: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self._wizard = PromptWizard(db)
        self._wizard.prompt_saved.connect(self.prompt_saved.emit)
        self._wizard.prompt_selected.connect(self.prompt_selected.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._wizard)

    def refresh(self) -> None:
        """Re-load the prompt list from disk (e.g. after import)."""
        self._wizard.refresh_prompt_list()
