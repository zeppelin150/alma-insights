"""AI Reports — Severity badge widget (R1.5).

Small colored chip rendering a `Severity` enum. Used by `finding_card`
and `evidence_panel`. Pure styling — no signals, no behavior. Theme
constants come from `src.ui.theme`.

Reference: 3.24.26 Updated_AI_Reports_Analysis_Canvas.png — the
HIGH IMPACT / CONFIRMED / MEDIUM badges next to each finding title.

Public API
----------
- `SeverityBadge(severity, parent=None)`
- `SeverityBadge.set_severity(severity)`

Dependencies
------------
- PySide6.QtWidgets, src.ui.theme, src.data.report_schema (Severity enum)
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget
from PySide6.QtCore import Qt

from src.data.report_schema import Severity
from src.ui.theme import (
    ALMA_ERROR, ALMA_WARNING, ALMA_INFO, ALMA_TEXT_LIGHT,
    ALMA_WHITE,
)


# ──────────────────────────────────────────────────────────────────────
# Color + label maps
# ──────────────────────────────────────────────────────────────────────

# Ordered: severity → (display_label, background_color, text_color)
# Display labels are uppercased for the chip aesthetic in the reference.
_STYLES: dict[Severity, tuple[str, str, str]] = {
    Severity.HIGH:   ("HIGH IMPACT", ALMA_ERROR,        ALMA_WHITE),
    Severity.MEDIUM: ("MEDIUM",      ALMA_WARNING,      ALMA_WHITE),
    Severity.LOW:    ("LOW",         ALMA_INFO,         ALMA_WHITE),
    Severity.INFO:   ("INFO",        ALMA_TEXT_LIGHT,   ALMA_WHITE),
}


# ──────────────────────────────────────────────────────────────────────
# Widget
# ──────────────────────────────────────────────────────────────────────

class SeverityBadge(QLabel):
    """Compact pill rendering a Severity. Defaults to INFO."""

    def __init__(self, severity: Severity = Severity.INFO,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(20)
        self._severity: Severity = Severity.INFO  # set by set_severity below
        self.set_severity(severity)

    # ── public ─────────────────────────────────────────────────────

    def set_severity(self, severity: Severity | str) -> None:
        """Update the chip to reflect a new severity. Tolerant of strings."""
        sev = severity if isinstance(severity, Severity) else Severity.from_str(severity)
        self._severity = sev
        label, bg, fg = _STYLES.get(sev, _STYLES[Severity.INFO])
        self.setText(label)
        self.setStyleSheet(_chip_style(bg, fg))

    @property
    def severity(self) -> Severity:
        return self._severity


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _chip_style(bg: str, fg: str) -> str:
    """Stylesheet fragment for the chip — extracted so tests can assert
    on properties (background, color) without re-instantiating QSS."""
    return (
        f"QLabel {{"
        f" background: {bg};"
        f" color: {fg};"
        f" border: none;"
        f" border-radius: 10px;"
        f" padding: 2px 10px;"
        f" font-size: 10px;"
        f" font-weight: 700;"
        f" letter-spacing: 0.5px;"
        f" }}"
    )
