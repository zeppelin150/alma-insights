"""
Alma Insights — Help & Feedback Dialog
Guru documentation link, Typeform feedback, and tool overview.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QTabWidget, QWidget
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from src.ui.theme import *


# ═══ CONFIGURATION — Update these URLs ═══
GURU_FOLDER_URL = "https://app.getguru.com/folders/YOUR_FOLDER_ID"  # Replace with actual Guru folder
TYPEFORM_URL = "https://YOUR_WORKSPACE.typeform.com/to/YOUR_FORM_ID"  # Replace with actual Typeform


class HelpDialog(QDialog):
    """Modal help dialog with documentation links, feedback form, and tool overview.

    Opened from the top bar "Help & Docs" button. Provides quick-access
    links to Guru documentation, a Typeform feedback link, and a summary
    of the app's feature set.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Help & Feedback — Alma Insights")
        self.setMinimumSize(620, 520)
        self.setMaximumSize(800, 650)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        title = QLabel("Alma Insights")
        title.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {ALMA_GREEN_DARK};")
        subtitle = QLabel("RCM Issue Analysis & AI-Assisted Reporting")
        subtitle.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_MID};")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Tabs
        tabs = QTabWidget()
        tabs.addTab(self._overview_tab(), "Overview")
        tabs.addTab(self._docs_tab(), "Documentation")
        tabs.addTab(self._feedback_tab(), "Feedback")
        tabs.addTab(self._about_tab(), "About")
        layout.addWidget(tabs)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setObjectName("SecondaryButton")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.close)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _overview_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 16, 8, 8)

        overview = QTextBrowser()
        overview.setOpenExternalLinks(False)
        overview.setStyleSheet(f"""
            QTextBrowser {{
                border: none; background: transparent;
                font-size: 13px; line-height: 1.6;
            }}
        """)
        overview.setHtml(f"""
        <div style="font-family: 'Segoe UI', Arial; color: {ALMA_TEXT_DARK}; line-height: 1.7;">
            <p style="font-size: 14px; margin-bottom: 16px;">
                <b>Alma Insights</b> gives RCM Operations, Product, and Partner Support teams
                fast, substantive visibility into what providers and clients are contacting us about.
            </p>

            <p style="font-size: 13px; color: {ALMA_TEXT_MID}; margin-bottom: 12px;">
                <b style="color: {ALMA_GREEN_DARK};">What this tool does:</b>
            </p>

            <table style="font-size: 13px; border-collapse: collapse; width: 100%;">
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT};">
                        <b>🔍 Conversation Search</b>
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT};">
                        Search and browse full rebuilt ticket conversations by keyword, TRC, date range, or CSAT score.
                    </td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT};">
                        <b>📊 TRC Analytics</b>
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT};">
                        Volume, resolution time, CSAT, and trend metrics by Ticket Reason Code.
                    </td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT};">
                        <b>📈 Trending Topics</b>
                    </td>
                    <td style="padding: 8px 12px; border-bottom: 1px solid {ALMA_BORDER_LIGHT};">
                        Auto-detected rising terms and phrases in ticket text — early warning for emerging issues.
                    </td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px;">
                        <b>🤖 AI Reports</b>
                    </td>
                    <td style="padding: 8px 12px;">
                        Generate theme summaries, sentiment analysis, and trend narratives from ticket data using Gemini.
                    </td>
                </tr>
            </table>

            <p style="font-size: 12px; color: {ALMA_TEXT_LIGHT}; margin-top: 20px;">
                Data source: Zendesk (via Lightdash). All processing runs locally.
                AI reports use Alma's approved secure Gemini CLI instance.
            </p>
        </div>
        """)
        layout.addWidget(overview)
        return w

    def _docs_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 16, 8, 8)
        layout.setSpacing(16)

        desc = QLabel(
            "Access the full documentation, SOPs, and training materials for Alma Insights "
            "in our Guru knowledge base."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_MID}; line-height: 1.6;")
        layout.addWidget(desc)

        # Guru link button
        guru_btn = QPushButton("  Open Documentation in Guru  ")
        guru_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 8px; padding: 12px 28px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        guru_btn.setCursor(Qt.PointingHandCursor)
        guru_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GURU_FOLDER_URL)))
        layout.addWidget(guru_btn)

        note = QLabel(
            "💡 If you have the Guru desktop browser extension installed, the documentation "
            "will open directly in a Guru overlay panel."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"""
            font-size: 12px; color: {ALMA_TEXT_LIGHT}; padding: 12px 16px;
            background: {ALMA_HOVER_LIGHT}; border-radius: 8px;
        """)
        layout.addWidget(note)

        layout.addStretch()
        return w

    def _feedback_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 16, 8, 8)
        layout.setSpacing(16)

        desc = QLabel(
            "We'd love your feedback! Let us know what's working, what's not, "
            "and what features you'd like to see next."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 13px; color: {ALMA_TEXT_MID}; line-height: 1.6;")
        layout.addWidget(desc)

        # Typeform link button
        feedback_btn = QPushButton("  Open Feedback Form  ")
        feedback_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_TEXT_ON_DARK};
                border: none; border-radius: 8px; padding: 12px 28px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        feedback_btn.setCursor(Qt.PointingHandCursor)
        feedback_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(TYPEFORM_URL)))
        layout.addWidget(feedback_btn)

        note = QLabel("Feedback is submitted via Typeform and reviewed by the RCM Ops team weekly.")
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        layout.addWidget(note)

        layout.addStretch()
        return w

    def _about_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 16, 8, 8)

        about = QTextBrowser()
        about.setOpenExternalLinks(False)
        about.setStyleSheet("QTextBrowser { border: none; background: transparent; }")
        about.setHtml(f"""
        <div style="font-family: 'Segoe UI', Arial; color: {ALMA_TEXT_DARK}; line-height: 1.7;">
            <p><b>Alma Insights</b> v1.0.0</p>
            <p style="color: {ALMA_TEXT_MID};">RCM Issue Analysis & AI-Assisted Reporting Tool</p>

            <br/>
            <table style="font-size: 13px;">
                <tr><td style="padding: 4px 16px 4px 0; color: {ALMA_TEXT_LIGHT};">Team:</td>
                    <td>RCM Operations</td></tr>
                <tr><td style="padding: 4px 16px 4px 0; color: {ALMA_TEXT_LIGHT};">Data Source:</td>
                    <td>Zendesk / Lightdash</td></tr>
                <tr><td style="padding: 4px 16px 4px 0; color: {ALMA_TEXT_LIGHT};">AI Engine:</td>
                    <td>Gemini (Secure CLI)</td></tr>
                <tr><td style="padding: 4px 16px 4px 0; color: {ALMA_TEXT_LIGHT};">Runtime:</td>
                    <td>Python + PySide6 (local desktop)</td></tr>
            </table>

            <br/>
            <p style="font-size: 12px; color: {ALMA_TEXT_LIGHT};">
                Internal tool — Alma Health, Inc. All ticket data is processed locally.
            </p>
        </div>
        """)
        layout.addWidget(about)
        return w
