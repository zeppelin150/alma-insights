"""Markdown → Asana ``html_notes`` dialect (WS-D-WEB description editing).

Asana's rich-text PUT accepts a STRICT XML-ish subset wrapped in <body>:
h1 h2 strong em u s code pre blockquote ol ul li a[href] hr br — no <p>
(paragraph breaks are newlines), no <div>, no attributes beyond a@href.
Anything outside the subset makes the whole PUT fail with "XML is invalid",
so this serializer maps or unwraps every tag rather than passing them
through: p→text+newlines, h3..h6→h2, b/i→strong/em, unknown→unwrapped
children. Text is XML-escaped; hrefs are kept only for http(s).

Flat + Qt-free; the writeback lane is the only production caller.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

_PASS = {"strong", "em", "u", "s", "code", "pre", "blockquote",
         "ol", "ul", "li", "h1", "h2"}
_MAP = {"b": "strong", "i": "em", "del": "s", "strike": "s",
        "h3": "h2", "h4": "h2", "h5": "h2", "h6": "h2"}
_VOID = {"br": "<br>", "hr": "<hr>"}
_DROP_WITH_CONTENT = {"script", "style", "head", "iframe"}
_BLOCK_BREAK = {"p", "div", "section", "article"}


class _AsanaSerializer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._open: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if self._skip or tag in _DROP_WITH_CONTENT:
            if tag in _DROP_WITH_CONTENT:
                self._skip += 1
            return
        if tag in _VOID:
            self.out.append(_VOID[tag])
            return
        tag = _MAP.get(tag, tag)
        if tag in _PASS:
            self.out.append(f"<{tag}>")
            self._open.append(tag)
        elif tag == "a":
            href = next((v for k, v in attrs if k == "href"), "") or ""
            if href.lower().startswith(("http://", "https://")):
                self.out.append(f'<a href="{escape(href, quote=True)}">')
                self._open.append("a")
        # everything else unwraps — children still serialize

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        tag = _MAP.get(tag, tag)
        if self._open and self._open[-1] == tag:
            self.out.append(f"</{self._open.pop()}>")
        elif tag in _BLOCK_BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip and data:
            self.out.append(escape(data))


def to_asana_html(markdown: str) -> str:
    """Markdown → ``<body>…</body>`` in Asana's html_notes dialect."""
    from src.data.html_markdown import markdown_to_html
    html = markdown_to_html(markdown or "") or ""
    ser = _AsanaSerializer()
    try:
        ser.feed(html)
        ser.close()
    except Exception:  # noqa: BLE001 — hostile input degrades to escaped text
        return "<body>" + escape(str(markdown or "")) + "</body>"
    while ser._open:  # balance anything the parser left dangling
        ser.out.append(f"</{ser._open.pop()}>")
    body = "".join(ser.out).strip()
    return f"<body>{body}</body>"
