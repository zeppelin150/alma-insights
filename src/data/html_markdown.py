"""HTML ↔ Markdown conversion with zero new dependencies.

``html_to_markdown`` prefers Qt's QTextDocument (GitHub dialect) when a
QGuiApplication exists; chat tools run in the MCP child process with no
Qt app, so a stdlib ``html.parser`` fallback covers that path (headings,
paragraphs, lists, emphasis, links, code, blockquotes, tables — the
subset Guru cards actually use). ``markdown_to_html`` uses the
``markdown`` package already pinned in requirements.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser


def markdown_to_html(md: str) -> str:
    import markdown as _md
    # The rich editor emits GitHub-dialect markdown (toMarkdown). The base
    # `markdown` package (no pymdown-extensions installed) renders tables /
    # fenced code / sane lists but NOT GFM strikethrough or task-list
    # checkboxes — a small regex post-pass closes those two gaps within the
    # installed deps so the preview matches what the WYSIWYG editor showed.
    html = _md.markdown(
        md or "", extensions=["tables", "fenced_code", "sane_lists"])
    html = _GFM_STRIKE.sub(r"<del>\1</del>", html)
    html = _gfm_task_items(html)
    return html


# ~~text~~ → <del>text</del>  (GFM strikethrough)
_GFM_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
# Leading "[ ]" / "[x]" inside a freshly-opened <li> → a real checkbox.
_GFM_TASK = re.compile(r"<li>\s*\[( |x|X)\]\s*", re.IGNORECASE)


def _gfm_task_items(html: str) -> str:
    def _repl(m: "re.Match") -> str:
        checked = " checked" if m.group(1).lower() == "x" else ""
        return (f'<li class="task-list-item">'
                f'<input type="checkbox" disabled{checked}> ')
    return _GFM_TASK.sub(_repl, html)


def html_to_markdown(html: str) -> str:
    if not (html or "").strip():
        return ""
    try:
        from PySide6.QtGui import QGuiApplication, QTextDocument
        if QGuiApplication.instance() is not None:
            doc = QTextDocument()
            doc.setHtml(html)
            return doc.toMarkdown(
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            ).strip()
    except Exception:
        pass
    parser = _MarkdownParser()
    parser.feed(html)
    parser.close()
    return parser.result()


_BLOCK_END = {"p", "div", "section", "article"}
_SKIP = {"script", "style", "head"}


class _MarkdownParser(HTMLParser):
    """Minimal HTML→Markdown for the no-Qt path."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._lists: list = []          # "ul" marker or int counter for ol
        self._href: str | None = None
        self._skip = 0
        self._pre = False
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    # ── emit helpers ────────────────────────────────────────────────

    def _emit(self, text: str):
        if self._cell is not None:
            self._cell.append(text)
        else:
            self._out.append(text)

    def _blankline(self):
        self._emit("\n\n")

    # ── parser hooks ────────────────────────────────────────────────

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "br":
            self._emit("\n")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._emit("[")
        elif tag == "ul":
            self._lists.append("ul")
        elif tag == "ol":
            self._lists.append(1)
        elif tag == "li":
            indent = "  " * max(len(self._lists) - 1, 0)
            marker = "- "
            if self._lists and isinstance(self._lists[-1], int):
                marker = f"{self._lists[-1]}. "
                self._lists[-1] += 1
            self._emit(f"\n{indent}{marker}")
        elif tag == "code" and not self._pre:
            self._emit("`")
        elif tag == "pre":
            self._pre = True
            self._emit("\n\n```\n")
        elif tag == "blockquote":
            self._emit("\n\n> ")
        elif tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(self._skip - 1, 0)
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._blankline()
        elif tag in _BLOCK_END:
            self._blankline()
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "a":
            self._emit(f"]({self._href or ''})")
            self._href = None
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
            if not self._lists:
                self._blankline()
        elif tag == "code" and not self._pre:
            self._emit("`")
        elif tag == "pre":
            self._pre = False
            self._emit("\n```\n\n")
        elif tag == "blockquote":
            self._blankline()
        elif tag in ("td", "th") and self._row is not None:
            self._row.append("".join(self._cell or []).strip())
            self._cell = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self._emit_table()
            self._table = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._pre:
            self._emit(data)
            return
        text = re.sub(r"\s+", " ", data)
        if text.strip() or (self._out and not self._out[-1].endswith("\n")):
            self._emit(text)

    # ── table assembly ──────────────────────────────────────────────

    def _emit_table(self):
        rows = [r for r in (self._table or []) if r]
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out = ["\n\n| " + " | ".join(rows[0]) + " |",
               "| " + " | ".join(["---"] * width) + " |"]
        for r in rows[1:]:
            out.append("| " + " | ".join(r) + " |")
        self._out.append("\n".join(out) + "\n\n")

    def result(self) -> str:
        text = "".join(self._out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
