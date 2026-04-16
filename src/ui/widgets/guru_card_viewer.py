"""
Alma Insights — Guru Card Viewer Widget

Renders a Guru card in-app with its original content, Guru-style chrome
(title bar, owner, collection badge, verification status), and optional
redline overlay for proposed edits.

Architecture follows MarkdownViewer: card data → HTML assembly → CSS
wrapping → QTextBrowser.setHtml().  Guru API returns card content as
HTML, so no markdown conversion is needed for the body — but markdown
fallback is supported via the shared ``_md_to_html`` converter.

Media handling: ``<img>`` and ``<video>`` tags are replaced with
styled placeholder divs (since QTextBrowser can't render them).

Usage:
    viewer = GuruCardViewer()
    viewer.set_card({
        "title": "Billing Deduction Process",
        "content": "<h2>Overview</h2><p>This card outlines...</p>",
        "collection": "Billing & Payments",
        "lastModified": "2026-02-14T10:30:00Z",
        "status": "TRUSTED",
    })

    # Optional redline overlay
    viewer.set_redlines([
        {"type": "insert", "after_selector": "Step 3",
         "html": "<strong>Step 3a (NEW):</strong> Check the ..."},
        {"type": "modify", "old": "escalate to L2", "new": "escalate to Automation Team"},
        {"type": "warning", "text": "23 tickets suggest ..."},
    ])
"""

import re
import logging
from datetime import datetime

from PySide6.QtWidgets import QTextBrowser
from PySide6.QtCore import Qt

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_CREAM, ALMA_WHITE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_BORDER, ALMA_BORDER_LIGHT, ALMA_BG_INSET,
    ALMA_SUCCESS, ALMA_WARNING, ALMA_ERROR, ALMA_INFO,
)

logger = logging.getLogger("alma.guru_card_viewer")

# Verification state → display label
_VERIFICATION_LABELS = {
    "TRUSTED": ("Verified", ALMA_SUCCESS),
    "NEEDS_VERIFICATION": ("Needs Verification", ALMA_WARNING),
    "UNVERIFIED": ("Unverified", ALMA_TEXT_LIGHT),
}

# Gap score → layman label
GAP_LABELS = {
    "critical": ("Critical Gap", ALMA_ERROR),
    "needs_update": ("Needs Update", ALMA_WARNING),
    "minor": ("Minor Gap", ALMA_TEXT_MID),
    "up_to_date": ("Up to Date", ALMA_SUCCESS),
}


class GuruCardViewer(QTextBrowser):
    """QTextBrowser subclass that renders a Guru card with chrome and redlines.

    Follows the MarkdownViewer pattern: all rendering is HTML via setHtml().
    Supports Guru card content (HTML from API), optional redline overlays,
    and media placeholders for images/videos.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            QTextBrowser {{
                background: {ALMA_WHITE};
                border: none;
                padding: 0;
                font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
        """)
        self._card: dict = {}
        self._redlines: list[dict] = []
        self._raw_content: str = ""

    # ── Public API ───────────────────────────────────────────────

    def set_card(self, card: dict):
        """Render a Guru card with its chrome and body content.

        Args:
            card: Dict with keys from GuruClient.get_card():
                  id, title, content, collection, collection_id,
                  lastModified, status
        """
        self._card = card or {}
        self._raw_content = card.get("content", "") if card else ""
        self._render()

    def set_redlines(self, redlines: list[dict]):
        """Set redline annotations and re-render.

        Each redline dict has:
            type: "insert" | "modify" | "warning"
            html:  (insert) HTML content to add
            old:   (modify) original text
            new:   (modify) replacement text
            text:  (warning) warning message
        """
        self._redlines = redlines or []
        self._render()

    def set_card_with_redlines(self, card: dict, redlines: list[dict]):
        """Set both card and redlines in one call (single render)."""
        self._card = card or {}
        self._raw_content = card.get("content", "") if card else ""
        self._redlines = redlines or []
        self._render()

    def get_card_html(self) -> str:
        """Return the raw card content HTML."""
        return self._raw_content

    def get_card_markdown(self) -> str:
        """Return card content as approximate markdown (for export).

        Converts the rendered HTML back to a rough markdown representation.
        """
        return _html_to_approximate_md(self._raw_content)

    def clear_content(self):
        """Clear the viewer."""
        self._card = {}
        self._redlines = []
        self._raw_content = ""
        self.setHtml("")

    # ── Rendering Pipeline ───────────────────────────────────────

    def _render(self):
        """Assemble full HTML and push to QTextBrowser."""
        if not self._card:
            self.setHtml("")
            return

        parts = []

        # 1. Card chrome (header bar)
        parts.append(self._build_chrome_html())

        # 2. Card body with media placeholders
        body_html = _sanitize_card_content(self._raw_content)
        parts.append(f'<div class="guru-body">{body_html}</div>')

        # 3. Redline annotations (appended after body)
        if self._redlines:
            parts.append('<div class="guru-redlines">')
            parts.append(self._build_redlines_html())
            parts.append('</div>')

            # 4. Legend
            parts.append(self._build_legend_html())

        card_html = "\n".join(parts)
        full_html = _wrap_card_styles(f'<div class="guru-card-view">{card_html}</div>')
        self.setHtml(full_html)

    def _build_chrome_html(self) -> str:
        """Build the Guru card header chrome."""
        title = _escape(self._card.get("title", "Untitled Card"))
        collection = _escape(self._card.get("collection", ""))
        last_mod = self._card.get("lastModified", "")
        status = self._card.get("status", "")
        owner = _escape(self._card.get("owner", ""))

        # Format date
        date_str = ""
        if last_mod:
            try:
                dt = datetime.fromisoformat(last_mod.replace("Z", "+00:00"))
                date_str = dt.strftime("%b %d, %Y")
            except (ValueError, TypeError):
                date_str = last_mod[:10] if len(last_mod) >= 10 else last_mod

        # Verification badge
        label, color = _VERIFICATION_LABELS.get(
            status, ("", ALMA_TEXT_LIGHT)
        )
        verification_html = ""
        if label:
            verification_html = (
                f'<span class="guru-verification" '
                f'style="color: {color}; border-color: {color};">'
                f'{label}</span>'
            )

        # Meta line
        meta_parts = []
        if date_str:
            meta_parts.append(f"Last verified: {date_str}")
        if owner:
            meta_parts.append(f"Owner: {owner}")
        meta_line = " &middot; ".join(meta_parts) if meta_parts else ""

        # Collection badge
        collection_html = ""
        if collection:
            collection_html = f'<span class="guru-collection">{collection}</span>'

        return f"""
        <div class="guru-chrome">
          <div>
            <div class="guru-chrome-title">{title}</div>
            <div class="guru-chrome-meta">{meta_line} {verification_html}</div>
          </div>
          {collection_html}
        </div>
        """

    def _build_redlines_html(self) -> str:
        """Build HTML for redline annotations."""
        parts = []
        for rl in self._redlines:
            rl_type = rl.get("type", "insert")

            if rl_type == "insert":
                content = rl.get("html", "")
                parts.append(
                    f'<div class="redline-insert">{content}</div>'
                )

            elif rl_type == "modify":
                old = _escape(rl.get("old", ""))
                new = _escape(rl.get("new", ""))
                title = _escape(rl.get("title", ""))
                title_html = f"<strong>{title}</strong><br>" if title else ""
                parts.append(
                    f'<div class="redline-modify">'
                    f'{title_html}'
                    f'<span class="redline-old">{old}</span><br>'
                    f'<span class="redline-new">{new}</span>'
                    f'</div>'
                )

            elif rl_type == "warning":
                text = rl.get("text", "")
                parts.append(
                    f'<div class="redline-warning">{text}</div>'
                )

        return "\n".join(parts)

    @staticmethod
    def _build_legend_html() -> str:
        """Build the redline legend bar."""
        return f"""
        <div class="redline-legend">
          <div class="legend-item">
            <div class="legend-swatch" style="background: {ALMA_ERROR};"></div>
            <span>Proposed addition</span>
          </div>
          <div class="legend-item">
            <div class="legend-swatch" style="background: {ALMA_INFO};"></div>
            <span>Proposed edit</span>
          </div>
          <div class="legend-item">
            <div class="legend-swatch" style="background: {ALMA_WARNING};"></div>
            <span>Unconfirmed — needs review</span>
          </div>
          <div class="legend-item">
            <span style="color: {ALMA_TEXT_MID};">Original text is unchanged</span>
          </div>
        </div>
        """


# ══════════════════════════════════════════════════════════════════════
# Content sanitization — media placeholders
# ══════════════════════════════════════════════════════════════════════

def _sanitize_card_content(html: str) -> str:
    """Process Guru card HTML content for display in QTextBrowser.

    - Replaces <img> tags with styled placeholders
    - Replaces <video>/<iframe> tags with styled placeholders
    - Preserves all other HTML structure
    - Falls back to markdown conversion if content looks like markdown
    """
    if not html:
        return '<p style="color: #7A7A7A; font-style: italic;">No content</p>'

    # Detect if content is markdown (no HTML tags) → convert
    if not re.search(r"<[a-zA-Z][^>]*>", html):
        from src.ui.widgets.markdown_viewer import _md_to_html
        return _md_to_html(html)

    # Replace <img> tags with placeholder
    def _img_replacer(m):
        tag = m.group(0)
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        label = alt_m.group(1) if alt_m else "Image"
        return _media_placeholder("image", label or "Image")

    html = re.sub(r'<img\b[^>]*/?>',  _img_replacer, html, flags=re.IGNORECASE)

    # Replace <video> tags with placeholder
    html = re.sub(
        r'<video\b[^>]*>.*?</video>',
        lambda m: _media_placeholder("video", "Video"),
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Replace <iframe> tags (embedded content) with placeholder
    def _iframe_replacer(m):
        tag = m.group(0)
        title_m = re.search(r'title=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        label = title_m.group(1) if title_m else "Embedded Content"
        return _media_placeholder("embed", label or "Embedded Content")

    html = re.sub(
        r'<iframe\b[^>]*>.*?</iframe>',
        _iframe_replacer,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return html


def _media_placeholder(media_type: str, label: str) -> str:
    """Generate a styled placeholder div for media content."""
    icons = {
        "image": "🖼",
        "video": "🎬",
        "embed": "📎",
    }
    icon = icons.get(media_type, "📄")
    type_label = media_type.capitalize()
    return (
        f'<div class="media-placeholder">'
        f'<span class="media-icon">{icon}</span> '
        f'<span class="media-label">[{type_label}: {_escape(label)}]</span>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════════════════════
# HTML → approximate markdown (for export)
# ══════════════════════════════════════════════════════════════════════

def _html_to_approximate_md(html: str) -> str:
    """Best-effort HTML → markdown conversion for card export.

    Not perfect, but handles the common elements in Guru cards:
    headings, paragraphs, lists, bold, links.
    """
    if not html:
        return ""

    text = html
    # Headings
    for level in range(1, 5):
        prefix = "#" * level
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            rf"\n{prefix} \1\n",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    # Bold
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    # Italic
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    # Links
    text = re.sub(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # List items
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1", text, flags=re.DOTALL)
    # Paragraphs and line breaks
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _escape(text: str) -> str:
    """HTML-escape special characters."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ══════════════════════════════════════════════════════════════════════
# CSS wrapper — Guru card-specific styles + Alma design tokens
# ══════════════════════════════════════════════════════════════════════

def _wrap_card_styles(html_body: str) -> str:
    """Wrap card HTML with Guru-specific CSS and Alma design tokens."""
    return f"""<!DOCTYPE html>
<html><head><style>
body {{
    font-family: 'Segoe UI', 'Inter', -apple-system, sans-serif;
    font-size: 13px;
    color: {ALMA_TEXT_DARK};
    line-height: 1.6;
    margin: 0;
    padding: 0;
    background: {ALMA_WHITE};
}}

/* ── Guru Card Container ── */
.guru-card-view {{
    background: {ALMA_WHITE};
    border: none;
    border-radius: 10px;
    overflow: hidden;
}}

/* ── Card Chrome (header bar) ── */
.guru-chrome {{
    background: {ALMA_BG_INSET};
    border-bottom: 1px solid {ALMA_BORDER_LIGHT};
    padding: 12px 20px;
}}
.guru-chrome-title {{
    font-size: 16px;
    font-weight: 700;
    color: {ALMA_TEXT_DARK};
    margin-bottom: 2px;
}}
.guru-chrome-meta {{
    font-size: 11px;
    color: {ALMA_TEXT_LIGHT};
}}
.guru-collection {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.3px;
    background: rgba(3, 40, 27, 0.08);
    color: {ALMA_GREEN_DARK};
    margin-left: 8px;
}}
.guru-verification {{
    display: inline-block;
    padding: 1px 8px;
    border-radius: 8px;
    font-size: 10px;
    font-weight: 600;
    border: none;
    margin-left: 6px;
}}

/* ── Card Body ── */
.guru-body {{
    padding: 20px 24px;
    font-size: 13px;
    line-height: 1.8;
    color: {ALMA_TEXT_DARK};
}}
.guru-body h1 {{
    font-size: 17px; font-weight: 700; color: {ALMA_GREEN_DARK};
    margin: 20px 0 8px; padding-bottom: 6px;
    border-bottom: 2px solid {ALMA_BORDER_LIGHT};
}}
.guru-body h1:first-child {{ margin-top: 0; }}
.guru-body h2 {{
    font-size: 15px; font-weight: 700; color: {ALMA_GREEN_LIGHT};
    margin: 20px 0 8px; padding-bottom: 4px;
    border-bottom: 1px solid {ALMA_BORDER_LIGHT};
}}
.guru-body h2:first-child {{ margin-top: 0; }}
.guru-body h3 {{
    font-size: 14px; font-weight: 600; color: {ALMA_GREEN_LIGHT};
    margin: 16px 0 6px;
}}
.guru-body p {{ margin: 6px 0; }}
.guru-body ol, .guru-body ul {{ padding-left: 24px; margin: 6px 0; }}
.guru-body li {{ margin: 4px 0; }}
.guru-body strong {{ font-weight: 600; }}
.guru-body a {{ color: {ALMA_GREEN_LIGHT}; text-decoration: none; }}
.guru-body a:hover {{ text-decoration: underline; }}
.guru-body code {{
    background: {ALMA_CREAM};
    border: none;
    border-radius: 3px;
    padding: 1px 4px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
}}
.guru-body table {{
    border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px;
}}
.guru-body th {{
    background: {ALMA_CREAM}; color: {ALMA_GREEN_DARK};
    font-weight: 600; text-align: left;
    padding: 8px 10px; border: none;
}}
.guru-body td {{
    padding: 6px 10px; border: none;
}}
.guru-body tr:nth-child(even) td {{
    background: {ALMA_CREAM};
}}
.guru-body blockquote {{
    border-left: 3px solid {ALMA_GREEN_LIGHT};
    margin: 8px 0; padding: 6px 12px;
    color: {ALMA_TEXT_MID}; background: {ALMA_BG_INSET};
    border-radius: 0 4px 4px 0;
}}

/* ── Media Placeholders ── */
.media-placeholder {{
    display: block;
    padding: 16px 20px;
    margin: 10px 0;
    background: {ALMA_BG_INSET};
    border: 1.5px dashed {ALMA_BORDER};
    border-radius: 8px;
    text-align: center;
    color: {ALMA_TEXT_LIGHT};
    font-size: 12px;
}}
.media-icon {{
    font-size: 18px;
    margin-right: 4px;
}}
.media-label {{
    font-weight: 500;
    color: {ALMA_TEXT_MID};
}}

/* ── Redline Overlays ── */
.guru-redlines {{
    padding: 0 24px 16px;
}}

.redline-insert {{
    color: #D32F2F;
    font-weight: 600;
    background: rgba(211, 47, 47, 0.06);
    border-left: 3px solid #D32F2F;
    padding: 10px 14px;
    margin: 10px 0;
    border-radius: 0 6px 6px 0;
    font-size: 13px;
    line-height: 1.7;
}}

.redline-modify {{
    background: rgba(29, 111, 165, 0.04);
    border-left: 3px solid {ALMA_INFO};
    padding: 10px 14px;
    margin: 10px 0;
    border-radius: 0 6px 6px 0;
    font-size: 13px;
    line-height: 1.7;
}}
.redline-old {{
    text-decoration: line-through;
    color: {ALMA_TEXT_LIGHT};
    font-style: italic;
}}
.redline-new {{
    color: #D32F2F;
    font-weight: 600;
}}

.redline-warning {{
    background: rgba(180, 83, 9, 0.06);
    border-left: 3px solid {ALMA_WARNING};
    padding: 10px 14px;
    margin: 10px 0;
    border-radius: 0 6px 6px 0;
    font-size: 12px;
    color: {ALMA_TEXT_MID};
    line-height: 1.7;
}}

/* ── Redline Legend ── */
.redline-legend {{
    padding: 8px 20px;
    background: {ALMA_BG_INSET};
    border-top: 1px solid {ALMA_BORDER_LIGHT};
    font-size: 11px;
    color: {ALMA_TEXT_LIGHT};
}}
.legend-item {{
    display: inline-block;
    margin-right: 16px;
}}
.legend-swatch {{
    display: inline-block;
    width: 12px; height: 3px;
    border-radius: 2px;
    vertical-align: middle;
    margin-right: 4px;
}}
</style></head><body>
{html_body}
</body></html>"""


# ══════════════════════════════════════════════════════════════════════
# Public helpers for external use
# ══════════════════════════════════════════════════════════════════════

def guru_card_to_html(card: dict, redlines: list[dict] | None = None) -> str:
    """Convert a Guru card dict to full styled HTML string.

    Useful for DrilldownPanel callbacks, export-to-HTML, or any context
    where you need the HTML without a QTextBrowser widget.

    Same approach as ``md_to_html()`` in markdown_viewer — returns a
    self-contained HTML document with embedded CSS.
    """
    viewer = GuruCardViewer.__new__(GuruCardViewer)
    viewer._card = card or {}
    viewer._raw_content = card.get("content", "") if card else ""
    viewer._redlines = redlines or []

    parts = []
    parts.append(viewer._build_chrome_html())

    body_html = _sanitize_card_content(viewer._raw_content)
    parts.append(f'<div class="guru-body">{body_html}</div>')

    if viewer._redlines:
        parts.append('<div class="guru-redlines">')
        parts.append(viewer._build_redlines_html())
        parts.append('</div>')
        parts.append(viewer._build_legend_html())

    card_html = "\n".join(parts)
    return _wrap_card_styles(f'<div class="guru-card-view">{card_html}</div>')
