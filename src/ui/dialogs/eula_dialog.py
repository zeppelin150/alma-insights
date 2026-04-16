"""
Alma Insights — First-run EULA dialog.

Shown once, before the splash, on a fresh install or after the EULA
version bumps. The user must scroll to the bottom + check a consent
box + click Accept to reach the rest of the app. Closing the dialog
without acceptance exits the process.

Acceptance is persisted to `data/settings.yaml` under:

    eula:
      version_accepted: "1"
      accepted_at: "2026-04-15T20:00:00Z"
      accepted_by: "<whoami>"

Public API:
    EULA_VERSION                 bump to force re-prompt
    is_accepted(settings) -> bool
    record_acceptance(settings) -> None
    EulaDialog(parent=None)      QDialog subclass
    ensure_accepted(parent=None) convenience: show dialog if needed,
                                  return True if app should continue
"""

from __future__ import annotations

import getpass
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.ui.theme import (
    ALMA_BORDER,
    ALMA_CREAM,
    ALMA_GREEN_DARK,
    ALMA_SUCCESS,
    ALMA_TEXT_DARK,
    ALMA_TEXT_MID,
)

EULA_VERSION = "1"


# ──────────────────────────────────────────────────────────────────
# Body text (kept short — full legal text can land later if needed)
# ──────────────────────────────────────────────────────────────────

_EULA_BODY = """\
<h2>Alma Insights — End User License Agreement</h2>
<p><b>Version {version}</b> · {today}</p>

<h3>1. Data handling</h3>
<ul>
<li><b>Local-first.</b> Tickets, conversations, embeddings, and reports
    are stored in the SQLite database at
    <code>data/alma_insights.db</code> on your machine. Data is not
    shared outside the bundled Python + Node runtimes without your
    explicit action.</li>
<li><b>PII redaction is mandatory</b> on every LLM call. See
    <code>config/redaction_patterns.json</code>.</li>
<li><b>Gemini.</b> Classification, VOC synthesis, and report drafting
    run through the Google Gemini CLI on your desktop. Alma Health
    maintains a Business Associate Agreement (BAA) with Google for the
    CLI channel used here. No PHI is sent to Google outside this BAA.</li>
<li><b>Anthropic Claude</b> is used only for operational tasks (Guru
    analysis, watchlist, meta-analytics). No ticket body text is sent
    to Anthropic.</li>
<li><b>Lightdash.</b> If configured, ticket imports reach out to your
    Lightdash tenant over HTTPS using your personal access token.</li>
</ul>

<h3>2. Updates</h3>
<p>On launch, the app may check a private GitHub repository for a
newer release. The HTTP request carries only the current version and
a User-Agent string; no user data is transmitted. You can disable
update checks in <em>Settings → Updates</em>.</p>

<h3>3. Crash reports</h3>
<p>Unhandled exceptions are written to
<code>data/crash_reports/</code> on your machine as structured JSON.
Credential-shaped strings are redacted automatically. Reports are
<b>never uploaded</b>; you can optionally export them via
<em>Settings → Support</em> for a support ticket.</p>

<h3>4. Credentials</h3>
<p>API keys and personal access tokens (Lightdash, Gemini, Anthropic,
Guru, Zendesk, GitHub update) are stored in the operating system's
native secret vault (Windows Credential Manager / macOS Keychain).
They never land in plaintext config files.</p>

<h3>5. No warranty</h3>
<p>The software is provided "as is", without warranty of any kind.
Alma Health is not liable for any damages arising from its use.</p>

<h3>6. Acceptance</h3>
<p>By clicking <b>Accept</b>, you agree to the terms above. You may
decline and exit at any time.</p>
"""


# ──────────────────────────────────────────────────────────────────
# Persistence helpers
# ──────────────────────────────────────────────────────────────────

def is_accepted(settings: dict[str, Any]) -> bool:
    """Return True iff settings record acceptance for the current version."""
    eula = (settings or {}).get("eula") or {}
    return str(eula.get("version_accepted", "")) == EULA_VERSION


def record_acceptance(settings_mutable: dict[str, Any]) -> None:
    """Mutate `settings_mutable` in-place to record acceptance. Does NOT persist."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "unknown"
    settings_mutable["eula"] = {
        "version_accepted": EULA_VERSION,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "accepted_by": user,
    }


# ──────────────────────────────────────────────────────────────────
# The dialog
# ──────────────────────────────────────────────────────────────────

class EulaDialog(QDialog):
    """Modal first-run consent dialog. Accept button is gated on scroll + checkbox."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Alma Insights — End User License Agreement")
        self.setModal(True)
        self.setMinimumSize(640, 560)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)

        header = QLabel("End User License Agreement")
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        header.setFont(font)
        header.setStyleSheet(f"color: {ALMA_TEXT_DARK};")
        root.addWidget(header)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setStyleSheet(
            f"QTextBrowser {{ background: {ALMA_CREAM}; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 6px; padding: 12px; "
            f"color: {ALMA_TEXT_DARK}; }}"
        )
        body.setHtml(_EULA_BODY.format(
            version=EULA_VERSION,
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        ))
        root.addWidget(body, 1)

        self._consent_box = QCheckBox(
            f"I have read and agree to the Alma Insights EULA (v{EULA_VERSION})."
        )
        self._consent_box.setStyleSheet(f"color: {ALMA_TEXT_DARK}; font-size: 13px;")
        self._consent_box.toggled.connect(self._update_accept_enabled)
        root.addWidget(self._consent_box)

        buttons = QHBoxLayout()
        decline = QPushButton("Decline and exit")
        decline.setStyleSheet(
            f"QPushButton {{ color: {ALMA_TEXT_MID}; background: transparent; "
            f"border: 1px solid {ALMA_BORDER}; border-radius: 4px; padding: 8px 16px; }}"
        )
        decline.clicked.connect(self.reject)
        buttons.addWidget(decline)

        buttons.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self._accept_btn = QPushButton("Accept")
        self._accept_btn.setEnabled(False)
        self._accept_btn.setStyleSheet(
            f"QPushButton {{ color: {ALMA_CREAM}; background: {ALMA_SUCCESS}; "
            "border: none; border-radius: 4px; padding: 8px 24px; font-weight: 600; }}"
            f"QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_MID}; }}"
        )
        self._accept_btn.clicked.connect(self.accept)
        buttons.addWidget(self._accept_btn)

        root.addLayout(buttons)

    def _update_accept_enabled(self) -> None:
        """Enable Accept only when the consent checkbox is ticked."""
        self._accept_btn.setEnabled(self._consent_box.isChecked())


# ──────────────────────────────────────────────────────────────────
# Convenience entry point used by main.py
# ──────────────────────────────────────────────────────────────────

def ensure_accepted(parent: QWidget | None = None) -> bool:
    """
    If the user has not yet accepted EULA_VERSION, show the dialog.
    Returns True if the app should continue (fresh accept or already
    accepted), False if the user declined.
    """
    from src.data.settings_manager import load_settings, save_settings

    settings = load_settings()
    if is_accepted(settings):
        return True

    dialog = EulaDialog(parent=parent)
    if dialog.exec() != QDialog.Accepted:
        return False

    record_acceptance(settings)
    save_settings(settings)
    return True
