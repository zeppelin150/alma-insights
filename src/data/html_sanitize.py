"""Allowlist HTML sanitizer for web-rendered card previews.

Any HTML that reaches a Chromium surface (the enablement web tabs' preview
iframes) passes through :func:`sanitize_html` first — Guru card bodies, Drive
doc conversions, and LLM-generated markup are all untrusted. Defense in depth:
the preview iframe is also sandboxed with scripts disabled, so this layer and
the sandbox each independently stop script execution.

Stdlib-only (``html.parser``) and Qt-free so it can run anywhere (MCP
subprocess, tests, workers). Everything is deny-by-default:

- Tags outside ``ALLOWED_TAGS`` are unwrapped (children kept, tag dropped);
  script-bearing containers in ``DROP_WITH_CONTENT`` lose their content too.
- Attributes outside the per-tag allowlist are dropped. ``href``/``src`` are
  scheme-checked; ``style`` is rebuilt from an allowlisted property set and
  rejects ``url(`` / ``expression`` / import smuggling.
- All emitted text and attribute values are re-escaped, so smuggled markup
  inside text nodes can never re-materialize as tags.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

# Benign structural + inline markup Guru/Zendesk card bodies actually use.
ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "caption", "code", "dd", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
    "i", "img", "li", "mark", "ol", "p", "pre", "s", "small", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "u", "ul",
}

# Elements whose CONTENT is dangerous too — dropped wholesale.
DROP_WITH_CONTENT = {
    "script", "style", "iframe", "object", "embed", "applet", "svg", "math",
    "form", "input", "button", "select", "textarea", "option", "link", "meta",
    "base", "head", "title", "noscript", "template", "canvas", "audio",
    "video", "source", "track", "dialog", "frame", "frameset",
}

_VOID = {"br", "hr", "img"}

# Dropped elements that are HTML voids: they never get a close tag, so they
# must NOT open a skip-subtree (the counter would jam and eat the document).
_DROP_VOID = {"embed", "input", "source", "track", "link", "meta", "base",
              "frame"}

# class/title are harmless without our scripts; everything else is per-tag.
_GLOBAL_ATTRS = {"class", "title"}
_TAG_ATTRS = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "ol": {"start", "type"},
}
_STYLE_TAGS = {"span", "div", "p", "td", "th", "mark", "li", "font"}

_ALLOWED_STYLE_PROPS = {
    "color", "background-color", "text-align", "font-weight", "font-style",
    "font-size", "font-family", "text-decoration", "vertical-align",
}
_STYLE_VALUE_OK = re.compile(r"^[#\w\s.,'%()-]*$")
_STYLE_BANNED = re.compile(r"url\s*\(|expression|javascript|@import|\\", re.IGNORECASE)

_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)
_SAFE_SRC = re.compile(
    r"^(https?://|data:image/(png|jpeg|jpg|gif|webp);base64,)", re.IGNORECASE)
_INT_ATTR = re.compile(r"^\d{1,4}$")
_ALIGN = {"left", "right", "center", "justify"}


def _clean_style(value: str) -> str:
    """Rebuild a style attribute from allowlisted declarations only."""
    kept = []
    for decl in (value or "").split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if prop not in _ALLOWED_STYLE_PROPS:
            continue
        if not val or _STYLE_BANNED.search(val) or not _STYLE_VALUE_OK.match(val):
            continue
        kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def _clean_attrs(tag: str, attrs) -> str:
    """Serialize the allowed subset of ``attrs`` for ``tag`` (escaped)."""
    allowed = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag, set())
    out = []
    for name, value in attrs:
        name = (name or "").lower()
        value = value or ""
        if name == "style":
            if tag not in _STYLE_TAGS:
                continue
            value = _clean_style(value)
            if not value:
                continue
        elif name not in allowed:
            continue
        elif name == "href":
            if not _SAFE_HREF.match(value.strip()):
                continue
        elif name == "src":
            if not _SAFE_SRC.match(value.strip()):
                continue
        elif name in ("colspan", "rowspan", "width", "height", "start"):
            if not _INT_ATTR.match(value.strip()):
                continue
        elif name == "align":
            if value.strip().lower() not in _ALIGN:
                continue
        out.append(f' {name}="{escape(value, quote=True)}"')
    return "".join(out)


class _Sanitizer(HTMLParser):
    """Streaming rebuild: only allowlisted tags/attrs are ever emitted, all
    text is re-escaped, and DROP_WITH_CONTENT subtrees are suppressed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._stack: list[str] = []       # open ALLOWED tags, for balanced output
        # NAMES of the open drop-with-content containers (script/style/form/…).
        # A stack, not a counter: a close tag only ends suppression when it
        # actually matches an open drop container — an unmatched close like
        # </textarea> inside an open <form> must NOT un-suppress early and leak
        # the form's content.
        self._drop_stack: list[str] = []

    # ── tags ────────────────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            if tag not in _DROP_VOID:
                self._drop_stack.append(tag)   # suppress this subtree
            return
        if self._drop_stack:
            return                     # inside a dropped container
        if tag not in ALLOWED_TAGS:
            return                     # unwrap: keep children, drop the tag
        if tag in _VOID:
            self._out.append(f"<{tag}{_clean_attrs(tag, attrs)}>")
            return
        # img-less <a> etc. — links survive only with a scheme-checked href.
        self._out.append(f"<{tag}{_clean_attrs(tag, attrs)}>")
        self._stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            return                     # self-closed drop tag: nothing to suppress
        if self._drop_stack:
            return
        if tag not in ALLOWED_TAGS:
            return
        if tag in _VOID:
            self._out.append(f"<{tag}{_clean_attrs(tag, attrs)}>")
            return
        # HTML5 ignores the self-closing slash on non-void tags (<td/> == <td>),
        # so open it rather than dropping it; result() balances any leftover.
        self._out.append(f"<{tag}{_clean_attrs(tag, attrs)}>")
        self._stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._drop_stack:
            # A matching drop-container close ends (part of) the suppression;
            # pop down to and including it (mirrors the allowed-stack balancing).
            if tag in self._drop_stack:
                while self._drop_stack:
                    if self._drop_stack.pop() == tag:
                        break
                return
            # An ALLOWED ancestor is closing. The open drop containers were
            # opened inside it, so closing it implicitly closes them — clear the
            # suppression (else an unclosed <form>/<select> eats the rest of the
            # document) and fall through to process the ancestor's close.
            if tag in self._stack:
                self._drop_stack.clear()
            else:
                return                 # unmatched close inside a drop — ignore
        if tag not in self._stack:
            return                     # stray close — drop it
        while self._stack:             # close intermediates for balance
            open_tag = self._stack.pop()
            self._out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    # ── text + everything else ─────────────────────────────────────
    def handle_data(self, data):
        if not self._drop_stack and data:
            self._out.append(escape(data))

    def handle_comment(self, data):    # comments dropped (IE conditionals etc.)
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):      # CDATA blocks dropped
        pass

    def result(self) -> str:
        while self._stack:             # close anything left open
            self._out.append(f"</{self._stack.pop()}>")
        return "".join(self._out)


def sanitize_html(html: str) -> str:
    """Return ``html`` reduced to the allowlisted, escaped, balanced subset.

    Safe on any input (None/empty → ""). The output contains no scripts, no
    event handlers, no non-http(s) link targets, and no style smuggling — fit
    to ship to a sandboxed preview iframe (and inert even if the sandbox were
    misconfigured).
    """
    if not html:
        return ""
    parser = _Sanitizer()
    try:
        parser.feed(str(html))
        parser.close()
    except Exception:  # noqa: BLE001 — a parser blow-up must fail CLOSED
        return escape(str(html))
    return parser.result()
