"""
Rule add / edit dialog extracted from ``source_monitor_page._on_add_rule``.

Encapsulates the QDialog, the form layout, and the read-back of values.
Tests can drive the dialog without instantiating the whole page.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)


class RuleDialog(QDialog):
    """Modal dialog to create or edit a Watchlist rule.

    Public API
    ──────────
    - constructor accepts an optional ``existing`` dict; when provided,
      the form is pre-populated for an edit flow.
    - ``get_rule_data()`` returns the form values as a dict matching the
      kwargs expected by ``WatchlistEngine.create_rule()``.

    Validation
    ──────────
    - Empty ``name`` is rejected at ``accept`` time and the dialog
      stays open with focus returned to the name field.

    Args:
        parent: Parent widget for modality. Optional.
        existing: Dict returned by ``WatchlistEngine.list_rules()`` for
            edit flows. ``None`` for the create flow.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        existing: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self._existing = existing or {}
        self.setWindowTitle("Edit Rule" if existing else "Add Watchlist Rule")
        self.setMinimumWidth(420)
        self._build_form()

    def _build_form(self) -> None:
        """Construct the QFormLayout with all field widgets."""
        form = QFormLayout(self)
        form.setSpacing(10)

        self._name_edit = QLineEdit(self._existing.get("name", ""))
        self._name_edit.setPlaceholderText("e.g. Refund Complaints")
        form.addRow("Name:", self._name_edit)

        self._type_combo = QComboBox()
        self._type_combo.addItems(["keyword", "entity", "volume", "compound"])
        if self._existing.get("rule_type"):
            self._type_combo.setCurrentText(self._existing["rule_type"])
        form.addRow("Type:", self._type_combo)

        self._sev_combo = QComboBox()
        self._sev_combo.addItems(["watch", "incident"])
        if self._existing.get("severity"):
            self._sev_combo.setCurrentText(self._existing["severity"])
        form.addRow("Severity:", self._sev_combo)

        self._source_edit = QLineEdit(self._existing.get("source_filter", ""))
        self._source_edit.setPlaceholderText("Leave blank for all sources")
        form.addRow("Source Filter:", self._source_edit)

        self._kw_edit = QLineEdit(self._existing.get("keywords", ""))
        self._kw_edit.setPlaceholderText("Comma-separated keywords")
        form.addRow("Keywords:", self._kw_edit)

        self._kw_mode_combo = QComboBox()
        self._kw_mode_combo.addItems(["any", "all"])
        if self._existing.get("keyword_mode"):
            self._kw_mode_combo.setCurrentText(self._existing["keyword_mode"])
        form.addRow("Keyword Mode:", self._kw_mode_combo)

        self._entity_combo = QComboBox()
        self._entity_combo.addItems(["", "provider", "payer", "member"])
        if self._existing.get("entity_type"):
            self._entity_combo.setCurrentText(self._existing["entity_type"])
        form.addRow("Entity Type:", self._entity_combo)

        self._vol_spin = QSpinBox()
        self._vol_spin.setRange(0, 1000)
        self._vol_spin.setValue(int(self._existing.get("volume_threshold", 0)))
        self._vol_spin.setToolTip("0 = no volume threshold")
        form.addRow("Volume Threshold:", self._vol_spin)

        self._window_spin = QSpinBox()
        self._window_spin.setRange(5, 1440)
        self._window_spin.setValue(
            int(self._existing.get("volume_window_minutes", 60))
        )
        self._window_spin.setSuffix(" min")
        form.addRow("Volume Window:", self._window_spin)

        self._cooldown_spin = QSpinBox()
        self._cooldown_spin.setRange(1, 1440)
        self._cooldown_spin.setValue(
            int(self._existing.get("cooldown_minutes", 120))
        )
        self._cooldown_spin.setSuffix(" min")
        form.addRow("Cooldown:", self._cooldown_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_accept(self) -> None:
        """Validate name on OK; bail out without closing if missing."""
        if not self._name_edit.text().strip():
            self._name_edit.setFocus()
            return
        self.accept()

    # ── Public read-back ───────────────────────────────────────────

    def get_rule_data(self) -> dict:
        """Return the form values as a kwargs dict.

        Returns:
            Dict with keys matching ``WatchlistEngine.create_rule()`` /
            ``update_rule()``. Strings are stripped; integers are
            already int (from QSpinBox.value()).
        """
        return {
            "name": self._name_edit.text().strip(),
            "rule_type": self._type_combo.currentText(),
            "severity": self._sev_combo.currentText(),
            "source_filter": self._source_edit.text().strip(),
            "keywords": self._kw_edit.text().strip(),
            "keyword_mode": self._kw_mode_combo.currentText(),
            "entity_type": self._entity_combo.currentText(),
            "volume_threshold": self._vol_spin.value(),
            "volume_window_minutes": self._window_spin.value(),
            "cooldown_minutes": self._cooldown_spin.value(),
        }
