"""Guru-look card preview for QTextBrowser.

Renders draft markdown through the `markdown` package and a document
stylesheet approximating Guru's card typography. Qt rich-text CSS is a
subset (element/class selectors, fonts, colors, margins, table borders;
no flex/grid) — the CSS below stays inside it.
"""

from src.ui.design import tokens

GURU_CSS = f"""
h1 {{ font-size: 22px; font-weight: 700; color: {tokens.ALMA_TEXT_DARK}; }}
h2 {{ font-size: 17px; font-weight: 700; color: {tokens.ALMA_TEXT_DARK};
     margin-top: 16px; }}
h3 {{ font-size: 14px; font-weight: 700; color: {tokens.ALMA_TEXT_DARK};
     margin-top: 12px; }}
p, li {{ font-size: 13px; color: #2E2E2E; line-height: 148%; }}
a {{ color: {tokens.ALMA_INFO}; text-decoration: none; }}
code {{ background-color: {tokens.ALMA_BG_INSET};
       font-family: Consolas, monospace; font-size: 12px; }}
pre {{ background-color: {tokens.ALMA_BG_INSET}; padding: 8px;
      font-family: Consolas, monospace; font-size: 12px; }}
blockquote {{ color: {tokens.ALMA_TEXT_MID}; margin-left: 0px;
             padding-left: 10px; border-left: 3px solid {tokens.ALMA_ACCENT_TEAL}; }}
table {{ border: 1px solid {tokens.ALMA_BORDER}; }}
th {{ background-color: {tokens.ALMA_BG_INSET}; font-weight: 700;
     padding: 4px 8px; border: 1px solid {tokens.ALMA_BORDER}; }}
td {{ padding: 4px 8px; border: 1px solid {tokens.ALMA_BORDER_LIGHT}; }}
"""


def render_preview(browser, md: str) -> None:
    """Render markdown into a QTextBrowser with the Guru-look stylesheet."""
    from src.data.html_markdown import markdown_to_html
    browser.document().setDefaultStyleSheet(GURU_CSS)
    browser.setHtml(markdown_to_html(md or ""))
