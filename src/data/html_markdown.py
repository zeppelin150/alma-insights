"""HTML ↔ Markdown conversion with zero new dependencies.

``html_to_markdown`` prefers Qt's QTextDocument (GitHub dialect) when a
QGuiApplication exists; chat tools run in the MCP child process with no
Qt app, so a stdlib ``html.parser`` fallback covers that path (headings,
paragraphs, lists, emphasis, links, code, blockquotes, tables — the
subset Guru cards actually use). ``markdown_to_html`` uses the
``markdown`` package already pinned in requirements.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser


def markdown_to_html(md: str) -> str:
    import markdown as _md
    # The rich editor emits GitHub-dialect markdown (toMarkdown). The base
    # `markdown` package (no pymdown-extensions installed) renders tables /
    # fenced code / sane lists but NOT GFM strikethrough or task-list
    # checkboxes — small regex post-passes close those gaps within the
    # installed deps so the preview matches what the WYSIWYG editor showed.
    # md_in_html lets the document reader's exact-colour callout boxes
    # (<div markdown="1" style="background-color:…">) render their inner
    # markdown; tables/fenced_code/sane_lists as before. All core extensions
    # (no new dependency).
    html = _md.markdown(
        md or "",
        extensions=["tables", "fenced_code", "sane_lists", "md_in_html"])
    html = _unwrap_loose_list_items(html)
    html = _GFM_STRIKE.sub(r"<del>\1</del>", html)
    html = _gfm_task_items(html)
    return html


# ~~text~~ → <del>text</del>  (GFM strikethrough)
_GFM_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
# Leading "[ ]" / "[x]" inside a freshly-opened <li> (tight, or still wrapped
# in the item's leading <p>) → a text glyph. Qt cannot render <input>, the
# strict sanitizer drops it, and Guru may strip it — a character survives
# every surface.
_GFM_TASK = re.compile(
    r"<li>(?P<lead>\s*(?:<p>\s*)?)\[(?P<state> |x|X)\]\s*", re.IGNORECASE)

# Loose (blank-line-separated) markdown lists render as <li><p>…</p></li>.
# Qt collapses the inner <p> while Chromium (Guru, Zendesk) honors its
# margins, so the editor shows tight items and the published card shows
# double-spaced ones. Unwrap the <p> when it is the item's only paragraph
# (the (?!</?p\b) guard keeps genuinely multi-paragraph items intact), and
# unwrap a leading <p> immediately followed by a nested list.
_LOOSE_LI = re.compile(
    r"<li>\s*<p>((?:(?!</?p\b).)*?)</p>\s*</li>", re.DOTALL)
_LOOSE_LI_NESTED = re.compile(
    r"<li>\s*<p>((?:(?!</?p\b).)*?)</p>\s*(?=<[uo]l\b)", re.DOTALL)


def _unwrap_loose_list_items(html: str) -> str:
    html = _LOOSE_LI.sub(r"<li>\1</li>", html)
    html = _LOOSE_LI_NESTED.sub("<li>\\1\n", html)
    return html


def _gfm_task_items(html: str) -> str:
    def _repl(m: "re.Match") -> str:
        glyph = "☑" if m.group("state").lower() == "x" else "☐"
        return f'<li class="task-list-item">{m.group("lead")}{glyph} '
    return _GFM_TASK.sub(_repl, html)


# ── Qt HTML → portable clean HTML (for the Guru publish payload) ──────
#
# Guru's card `content` field is HTML, and its editor stores text color /
# highlight / callouts as inline-style HTML — so publishing the rich
# editor's HTML (rather than a markdown subset) preserves color + highlight
# that markdown cannot represent. QTextEdit.toHtml() emits those styles
# faithfully but wraps them in a <!DOCTYPE>/<html><head><style>…<body>
# document full of Qt-specific `-qt-*` properties and default font/margin
# noise. This cleaner strips that wrapper + noise and keeps only the
# portable, meaningful inline styles + semantic tags.

# Style props worth keeping (everything else, incl. -qt-* and font-family,
# is dropped). These are exactly what Guru's own editor emits.
_KEEP_STYLE_PROPS = {
    "color", "background-color", "background", "text-align",
    "text-decoration", "font-weight", "font-style", "vertical-align",
}
# Whole tags whose content/markup is Qt document chrome — dropped entirely.
_DROP_TAGS = {"html", "head", "meta", "title", "body", "style", "script"}
# Structural attributes to preserve on kept tags.
_KEEP_ATTRS = {
    "href", "src", "alt", "title", "colspan", "rowspan", "border",
    "cellpadding", "cellspacing", "bgcolor", "width", "height",
}
_VOID_TAGS = {"br", "hr", "img"}


class _QtHtmlCleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._drop_depth = 0   # inside <style>/<script> → suppress text

    def handle_decl(self, decl):        # drop <!DOCTYPE …>
        pass

    def handle_comment(self, data):     # drop <!--StartFragment--> etc.
        pass

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._drop_depth += 1
            return
        if tag in _DROP_TAGS or self._drop_depth:
            return
        self.out.append(self._render_start(tag, attrs, self_close=tag in _VOID_TAGS))

    def handle_startendtag(self, tag, attrs):
        if tag in _DROP_TAGS or self._drop_depth:
            return
        self.out.append(self._render_start(tag, attrs, self_close=True))

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if tag in _DROP_TAGS or self._drop_depth or tag in _VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._drop_depth:
            return
        self.out.append(_html.escape(data, quote=False))

    def _render_start(self, tag, attrs, *, self_close):
        d = {k: (v or "") for k, v in attrs}
        # Qt emits paragraph alignment as align="center" — promote it to a
        # portable text-align style (Qt does not emit text-align itself).
        align = d.pop("align", "").lower()
        style = self._clean_style(d.pop("style", ""))
        if align in ("center", "right", "left", "justify"):
            style = (style + ";" if style else "") + f"text-align:{align}"
        kept = []
        for k, v in d.items():
            if k in _KEEP_ATTRS:
                kept.append(f'{k}="{_html.escape(v, quote=True)}"')
        if style:
            kept.append(f'style="{_html.escape(style, quote=True)}"')
        attr_str = (" " + " ".join(kept)) if kept else ""
        slash = "/" if self_close else ""
        return f"<{tag}{attr_str}{slash}>"

    @staticmethod
    def _clean_style(style: str) -> str:
        keep = []
        for decl in style.split(";"):
            if ":" not in decl:
                continue
            prop, val = decl.split(":", 1)
            prop, val = prop.strip().lower(), val.strip()
            if not prop or not val or prop.startswith("-qt-"):
                continue
            if prop in _KEEP_STYLE_PROPS:
                keep.append(f"{prop}:{val}")
        return ";".join(keep)


_FRAGMENT_MARKERS = re.compile(r"<!--\s*(?:Start|End)Fragment\s*-->")
_INTERTAG_WS = re.compile(r">\s*\n\s*<")   # collapse Qt's pretty-print newlines


def qt_html_to_clean_html(qt_html: str) -> str:
    """Convert QTextEdit.toHtml() output into portable, Guru-ready HTML:
    strip the Qt document wrapper + `-qt-*`/font noise, keep semantic tags
    and meaningful inline styles (color, background, text-align via the
    align-attr promotion, text-decoration, font-weight)."""
    if not (qt_html or "").strip():
        return ""
    html = _FRAGMENT_MARKERS.sub("", qt_html)
    parser = _QtHtmlCleaner()
    parser.feed(html)
    parser.close()
    out = "".join(parser.out)
    out = _INTERTAG_WS.sub("><", out)   # remove inter-block newline whitespace
    return out.strip()


# List items whose text starts with a task glyph map back to markdown
# checkboxes: "- ☐ Do A" → "- [ ] Do A" (inverse of _gfm_task_items).
_TASK_GLYPH_MD = re.compile(
    r"^(?P<marker>\s*(?:[-*+]|\d+[.)])\s+)(?P<glyph>[☐☑])\s*",
    re.MULTILINE)

# A fenced-code delimiter line. Backtick fences ONLY: both converter paths
# emit them exclusively (QTextMarkdownWriter writes ``` fences; the stdlib
# fallback emits literal "```"), and a line starting "~~~" is Qt
# strikethrough ("~~" + text beginning "~"), never a converter-produced
# fence — treating it as one would suppress restoration for the rest of
# the document. Leading whitespace is allowed because Qt indents fences
# inside list items. The [^`]*$ guard keeps a one-line code SPAN
# (```foo```) from reading as an opener, per the CommonMark rule that a
# backtick fence's info string cannot contain backticks.
_FENCE_DELIM = re.compile(r"^\s*(?P<fence>`{3,})(?P<info>[^`]*)$")


def _restore_task_markers(md: str) -> str:
    """Map task glyphs back to markdown checkboxes, OUTSIDE code fences.

    A line like "- ☐ item" inside a ``` fence is code someone wrote and
    must round-trip byte-identical (it seeds _article_body_text and the
    zendesk body_text clipboard flavour); the same line outside a fence is
    a rendered task item mapping back to "- [ ] item". The document is
    split on fence delimiter lines and the substitution runs only on the
    segments outside fences, so out-of-fence behavior is unchanged."""
    def _repl(m: "re.Match") -> str:
        box = "[x]" if m.group("glyph") == "☑" else "[ ]"
        return f"{m.group('marker')}{box} "

    out: list[str] = []
    plain: list[str] = []       # consecutive lines outside any fence

    def _flush():
        if plain:
            out.append(_TASK_GLYPH_MD.sub(_repl, "\n".join(plain)))
            plain.clear()

    fence_len = 0               # opening run length; >0 while inside
    for line in md.split("\n"):
        m = _FENCE_DELIM.match(line)
        # An opener is any delimiter line; a closer must be at least as
        # long as its opener and carry no info string (CommonMark).
        if m is not None and (
                fence_len == 0
                or (len(m.group("fence")) >= fence_len
                    and not m.group("info").strip())):
            _flush()
            fence_len = len(m.group("fence")) if fence_len == 0 else 0
            out.append(line)
        elif fence_len:
            out.append(line)    # inside a fence: byte-identical
        else:
            plain.append(line)
    _flush()
    return "\n".join(out)


def html_to_markdown(html: str) -> str:
    if not (html or "").strip():
        return ""
    try:
        from PySide6.QtGui import QGuiApplication, QTextDocument
        # isinstance, NOT `is not None`: QGuiApplication.instance() returns the
        # QCoreApplication singleton in a console/headless process, and
        # constructing a QTextDocument without a GUI application ABORTS the
        # process (no Python exception to catch). Only take the Qt path when a
        # real QGuiApplication is up.
        if isinstance(QGuiApplication.instance(), QGuiApplication):
            doc = QTextDocument()
            doc.setHtml(html)
            return _restore_task_markers(doc.toMarkdown(
                QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            ).strip())
    except Exception:
        pass
    parser = _MarkdownParser()
    parser.feed(html)
    parser.close()
    return _restore_task_markers(parser.result())


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
