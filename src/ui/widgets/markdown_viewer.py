"""
Alma Insights — Markdown Viewer Widget (Phase 5.5A)

Renders markdown text as styled HTML in a QTextBrowser.
Uses Alma design tokens for consistent branding across all report pages.

Usage:
    viewer = MarkdownViewer()
    viewer.set_markdown("## Analysis\n\n**Key finding**: ...")
"""

import logging
import re

import markdown as _markdown_lib

from PySide6.QtWidgets import QTextBrowser
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_INSET,
)

logger = logging.getLogger("alma.markdown_viewer")


class MarkdownViewer(QTextBrowser):
    """QTextBrowser subclass that renders markdown as styled HTML.

    Supports: headings (h1-h4), bold, italic, inline code, fenced code blocks,
    tables, bulleted/numbered lists, blockquotes, horizontal rules, and links.

    Uses the `markdown` library with tables, fenced_code, and nl2br extensions.
    """

    ticket_clicked = Signal(str)  # emits ticket_id when a ticket link is clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)  # handle links ourselves
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            QTextBrowser {{
                background: {ALMA_WHITE};
                border: none;
                padding: 12px 16px;
                font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
        """)
        self._raw_markdown = ""
        self.anchorClicked.connect(self._on_link_clicked)

    def _on_link_clicked(self, url: QUrl):
        """Route ticket:// links to signal, open others externally."""
        if url.scheme() == "ticket":
            self.ticket_clicked.emit(url.path().lstrip("/"))
        else:
            QDesktopServices.openUrl(url)

    # ── Public API ──

    def set_markdown(self, text: str):
        """Convert markdown to styled HTML and display it."""
        self._raw_markdown = text or ""
        if not text or not text.strip():
            self.setHtml("")
            return
        # Linkify ticket ID patterns (#12345) before conversion
        text = _linkify_tickets(text)
        html_body = _md_to_html(text)
        self.setHtml(_wrap_with_styles(html_body))

    def get_markdown(self) -> str:
        """Return the raw markdown text last set."""
        return self._raw_markdown

    def clear_content(self):
        """Clear the viewer."""
        self._raw_markdown = ""
        self.setHtml("")


# ══════════════════════════════════════════════════════════════════════
# Markdown → HTML conversion
# ══════════════════════════════════════════════════════════════════════

_MD_EXTENSIONS = ["tables", "fenced_code", "nl2br"]

# Ticket ID pattern: #12345 (4-6 digits, not inside a word)
_TICKET_RE = re.compile(r'(?<!\w)#(\d{4,6})(?!\w)')


def _linkify_tickets(text: str) -> str:
    """Replace #12345 patterns with clickable ticket:// links."""
    return _TICKET_RE.sub(r'[#\1](ticket://\1)', text)


def md_to_html(text: str) -> str:
    """Public API: Convert markdown text to styled HTML (with Alma CSS wrapper).

    Use this when you need the full styled HTML string for injection into
    QTextBrowser.setHtml() or DrilldownPanel detail callbacks.
    """
    body = _md_to_html(text)
    return _wrap_with_styles(body)


def _md_to_html(text: str) -> str:
    """Convert markdown text to HTML body content."""
    return _markdown_lib.markdown(text, extensions=_MD_EXTENSIONS)


# ══════════════════════════════════════════════════════════════════════
# CSS wrapper using Alma design tokens
# ══════════════════════════════════════════════════════════════════════

def _wrap_with_styles(html_body: str) -> str:
    """Wrap HTML body content with Alma-branded CSS."""
    return f"""<!DOCTYPE html>
<html><head><style>
body {{
    font-family: 'Segoe UI', 'Inter', -apple-system, sans-serif;
    font-size: 13px;
    color: {ALMA_TEXT_DARK};
    line-height: 1.6;
    margin: 0;
    padding: 4px 0;
}}
h1 {{
    color: {ALMA_GREEN_DARK};
    font-size: 20px;
    font-weight: 700;
    margin: 16px 0 8px 0;
    padding-bottom: 6px;
    border-bottom: 2px solid {ALMA_BORDER_LIGHT};
}}
h2 {{
    color: {ALMA_GREEN_DARK};
    font-size: 16px;
    font-weight: 700;
    margin: 14px 0 6px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid {ALMA_BORDER_LIGHT};
}}
h3 {{
    color: {ALMA_GREEN_DARK};
    font-size: 14px;
    font-weight: 600;
    margin: 12px 0 4px 0;
}}
h4 {{
    color: {ALMA_TEXT_MID};
    font-size: 13px;
    font-weight: 600;
    margin: 10px 0 4px 0;
}}
p {{
    margin: 6px 0;
}}
strong {{
    font-weight: 600;
    color: {ALMA_TEXT_DARK};
}}
em {{
    font-style: italic;
}}
a {{
    color: {ALMA_GREEN_LIGHT};
    text-decoration: none;
}}
a:hover {{
    text-decoration: underline;
}}
code {{
    background: {ALMA_CREAM};
    border: none;
    border-radius: 3px;
    padding: 1px 4px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 12px;
}}
pre {{
    background: {ALMA_BG_INSET};
    border: none;
    border-radius: 6px;
    padding: 10px 12px;
    margin: 8px 0;
    overflow-x: auto;
}}
pre code {{
    background: transparent;
    border: none;
    padding: 0;
    font-size: 12px;
    line-height: 1.5;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
    font-size: 12px;
}}
th {{
    background: {ALMA_CREAM};
    color: {ALMA_GREEN_DARK};
    font-weight: 600;
    text-align: left;
    padding: 8px 10px;
    border: none;
}}
td {{
    padding: 6px 10px;
    border: none;
    color: {ALMA_TEXT_DARK};
}}
tr:nth-child(even) td {{
    background: {ALMA_CREAM};
}}
ul, ol {{
    margin: 6px 0;
    padding-left: 24px;
}}
li {{
    margin: 3px 0;
    line-height: 1.5;
}}
blockquote {{
    border-left: 3px solid {ALMA_GREEN_LIGHT};
    margin: 8px 0;
    padding: 6px 12px;
    color: {ALMA_TEXT_MID};
    background: {ALMA_BG_INSET};
    border-radius: 0 4px 4px 0;
}}
hr {{
    border: none;
    border-top: 1px solid {ALMA_BORDER_LIGHT};
    margin: 12px 0;
}}
</style></head><body>
{html_body}
</body></html>"""
