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
pre code {{ background-color: transparent; }}
blockquote {{ color: {tokens.ALMA_TEXT_MID}; margin-left: 0px;
             padding-left: 10px; border-left: 3px solid {tokens.ALMA_ACCENT_TEAL}; }}
table {{ border: 1px solid {tokens.ALMA_BORDER}; }}
th {{ background-color: {tokens.ALMA_BG_INSET}; font-weight: 700;
     padding: 4px 8px; border: 1px solid {tokens.ALMA_BORDER}; }}
td {{ padding: 4px 8px; border: 1px solid {tokens.ALMA_BORDER_LIGHT}; }}
del, s, strike {{ text-decoration: line-through; color: {tokens.ALMA_TEXT_MID}; }}
hr {{ border: none; border-top: 1px solid {tokens.ALMA_BORDER}; }}
ul, ol {{ margin: 6px 0 6px 0; padding-left: 22px; }}
li.task-list-item {{ list-style: none; margin-left: -16px; }}
h4, h5, h6 {{ font-size: 13px; font-weight: 700; color: {tokens.ALMA_TEXT_DARK};
     margin-top: 10px; }}
img {{ max-width: 100%; }}
.ghq-card-content__callout {{ padding: 12px 16px; margin: 10px 0; }}
.ghq-card-content__collapsible {{ border: 1px solid {tokens.ALMA_BORDER};
     border-radius: 6px; margin: 10px 0; padding: 6px 10px; }}
.ghq-card-content__collapsible-summary {{ font-weight: 700;
     color: {tokens.ALMA_TEXT_DARK}; }}
.ghq-card-content__guru-card {{ color: {tokens.ALMA_ACCENT_TEAL};
     font-weight: 600; text-decoration: none; }}
"""


def render_preview(browser, content: str, content_html: str | None = None) -> None:
    """Render the draft into a QTextBrowser with the Guru-look stylesheet.

    The rendered string is ``enablement_store.publish_body`` — EXACTLY the
    bytes ``publish_draft`` sends to Guru. Callers must pass the draft's
    ``content_html`` alongside its markdown: previously this rendered only
    ``markdown_to_html(content)`` while the network sent ``content_html``, so
    an imported card's raw HTML or an attached artifact could ship without
    ever appearing on an approval surface.
    """
    from src.data.enablement_store import publish_body
    browser.document().setDefaultStyleSheet(GURU_CSS)
    browser.setHtml(publish_body({"content": content or "",
                                  "content_html": content_html}))
