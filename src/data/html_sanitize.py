"""Allowlist HTML sanitizers for web-rendered previews and stored content.

Two independent profiles live here. They are deliberately separate objects
with separate allowlists so that widening one can never widen the other.

**STRICT** — :func:`sanitize_html`. The profile everything that MATTERS uses:
stored content at the write boundary, and the ``text/html`` clipboard mime
flavour. Any HTML that reaches a Chromium surface may also use it. Guru card
bodies, Drive doc conversions, and LLM-generated markup are all untrusted.
Defense in depth: the preview iframe is also sandboxed with scripts disabled,
so this layer and the sandbox each independently stop script execution.

**PREVIEW** — :func:`sanitize_html_preview`. Additive, and for RENDERING
ONLY (a ``sandbox=""`` iframe: no scripts, no navigation, no form
submission). It keeps presentational markup the strict profile throws away —
``class``/``id``/``data-*``/``aria-*``, a much wider ``style`` property set,
full table markup, ``<iframe>`` embeds — so a pulled Zendesk article renders
the way an end user would see it. It still removes scripts, event handlers
and ``javascript:``/``data:`` URLs, and it REPORTS what it removed so callers
can warn only when something genuinely could not be displayed. Never use it
for stored content and never for the clipboard.

Stdlib-only (``html.parser``) and Qt-free so both can run anywhere (MCP
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


# ══ PREVIEW PROFILE ═══════════════════════════════════════════════════
#
# ADDITIVE. Nothing above this line is read by anything below it except
# ``escape``; the strict allowlists are duplicated rather than derived so a
# future widening here can never leak into the stored-content / clipboard
# path. Rendering only, inside ``sandbox=""``.
#
# Why the wider set is safe there: ``sandbox=""`` (no allow-scripts, no
# allow-same-origin, no allow-forms, no allow-top-navigation) makes classes,
# ids, data-attributes, style declarations and nested frames INERT — they can
# style and lay out, and nothing else. Sandbox flags are inherited by nested
# browsing contexts, so an embedded ``<iframe>`` is script-less too. What is
# still removed is what remains dangerous or simply undisplayable: script
# bodies, event handlers, plugin/object embeds, and ``javascript:``/``data:``
# URLs.

PREVIEW_ALLOWED_TAGS = ALLOWED_TAGS | {
    # sectioning / semantic wrappers real Help Center themes emit
    "article", "aside", "details", "footer", "header", "hgroup", "main",
    "nav", "section", "summary",
    # inline semantics
    "abbr", "bdi", "bdo", "cite", "data", "del", "dfn", "ins", "kbd", "q",
    "rp", "rt", "ruby", "samp", "time", "var", "wbr",
    # table scaffolding the strict profile drops
    "col", "colgroup",
    # legacy presentational tags Zendesk bodies still carry
    "big", "center", "font", "strike", "tt",
    # media embeds (inert: sandbox flags are inherited by nested frames)
    "iframe",
}

# Still dropped WITH their content. ``iframe`` is deliberately absent (it is
# allowed above); ``style``/``svg``/``math``/forms/plugins stay out — CSS
# smuggling, plugin surfaces and credential-shaped widgets are exactly the
# things a reviewer should be told are missing rather than shown.
PREVIEW_DROP_WITH_CONTENT = {
    "script", "noscript", "template", "style", "object", "embed", "applet",
    "canvas", "svg", "math", "form", "input", "button", "select", "textarea",
    "option", "optgroup", "link", "meta", "base", "head", "title", "frame",
    "frameset", "audio", "video", "source", "track", "dialog",
}

_PREVIEW_VOID = {"br", "hr", "img", "col", "wbr"}
_PREVIEW_DROP_VOID = {"embed", "input", "source", "track", "link", "meta",
                      "base", "frame"}

# Inert-under-sandbox metadata: allowed on every tag.
_PREVIEW_GLOBAL_ATTRS = {"class", "id", "title", "lang", "dir", "role",
                         "style", "align", "valign", "name"}
_PREVIEW_ATTR_PREFIXES = ("data-", "aria-")
_PREVIEW_TAG_ATTRS = {
    "a": {"href", "target", "rel", "download"},
    "img": {"src", "alt", "width", "height", "loading"},
    "iframe": {"src", "width", "height", "allowfullscreen", "frameborder",
               "scrolling", "loading"},
    "table": {"width", "height", "border", "cellpadding", "cellspacing",
              "summary", "bgcolor"},
    "td": {"colspan", "rowspan", "width", "height", "bgcolor", "headers",
           "scope", "nowrap"},
    "th": {"colspan", "rowspan", "width", "height", "bgcolor", "headers",
           "scope", "nowrap", "abbr"},
    "tr": {"bgcolor", "height"},
    "col": {"span", "width"},
    "colgroup": {"span", "width"},
    "ol": {"start", "type", "reversed"},
    "ul": {"type"},
    "li": {"value"},
    "font": {"color", "face", "size"},
    "details": {"open"},
    "del": {"datetime", "cite"},
    "ins": {"datetime", "cite"},
    "blockquote": {"cite"},
    "q": {"cite"},
    "time": {"datetime"},
}

# Numeric-ish presentational attributes accept "12", "100%", "3em".
_PREVIEW_SIZE_RE = re.compile(r"^\d{1,5}(\.\d+)?(px|%|em|rem|pt)?$",
                              re.IGNORECASE)
_PREVIEW_SIZE_ATTRS = {"width", "height", "cellpadding", "cellspacing",
                       "border", "colspan", "rowspan", "start", "span",
                       "value", "size"}

# Link targets. Relative/anchor links cannot navigate inside ``sandbox=""``
# anyway, and keeping them preserves the article's visual structure; only
# ACTIVE schemes are refused (and reported).
_PREVIEW_SAFE_HREF = re.compile(r"^(https?://|mailto:|tel:|#|/|\.{1,2}/)",
                                re.IGNORECASE)
_PREVIEW_BAD_SCHEME = re.compile(
    r"^\s*(javascript|vbscript|data|file|about|blob)\s*:", re.IGNORECASE)
_PREVIEW_SAFE_SRC = re.compile(
    r"^(https?://|//|/|data:image/(png|jpeg|jpg|gif|webp|svg\+xml);base64,)",
    re.IGNORECASE)

# The CSS surface a Help Center article actually uses. Prefix rules keep the
# list readable without becoming an allow-anything.
_PREVIEW_STYLE_PROPS = {
    "color", "opacity", "display", "float", "clear", "width", "height",
    "min-width", "max-width", "min-height", "max-height", "overflow",
    "overflow-x", "overflow-y", "position", "top", "right", "bottom", "left",
    "box-sizing", "box-shadow", "outline", "visibility", "cursor", "content",
    "gap", "order", "transform", "transition", "z-index", "resize",
}
_PREVIEW_STYLE_PREFIXES = (
    "font", "text", "background", "border", "margin", "padding", "list",
    "line-", "letter-", "word-", "white-", "vertical-", "align", "justify",
    "flex", "grid", "column", "row-", "table-", "caption-", "empty-",
)
# ``url(`` is permitted with a scheme-checked target: a background image is
# the same network exposure as the ``<img src>`` the strict profile already
# allows, and forbidding it is the difference between a faithful preview and
# a broken one. Active-code smuggling stays banned.
_PREVIEW_STYLE_BANNED = re.compile(
    r"expression|javascript|vbscript|@import|behavior|\\", re.IGNORECASE)
_PREVIEW_URL_RE = re.compile(r"url\s*\(\s*['\"]?([^'\")]*)", re.IGNORECASE)


def _preview_style_prop_ok(prop: str) -> bool:
    return (prop in _PREVIEW_STYLE_PROPS
            or prop.startswith(_PREVIEW_STYLE_PREFIXES))


def _clean_preview_style(value: str) -> str:
    """Rebuild a style attribute for the preview profile.

    Wider than :func:`_clean_style` (whole property families, not nine
    properties) but the same shape: deny-by-default, value-checked, and every
    ``url()`` target scheme-checked."""
    kept = []
    for decl in (value or "").split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if not prop or not val or not _preview_style_prop_ok(prop):
            continue
        if _PREVIEW_STYLE_BANNED.search(val):
            continue
        if "url" in val.lower():
            targets = _PREVIEW_URL_RE.findall(val)
            if not targets or any(not _PREVIEW_SAFE_SRC.match(t.strip())
                                  for t in targets):
                continue
        kept.append(f"{prop}: {val}")
    return "; ".join(kept)


class _PreviewSanitizer(HTMLParser):
    """Streaming rebuild against the PREVIEW allowlists.

    Structurally identical to :class:`_Sanitizer` (same drop-stack discipline,
    same re-escaping of every text node and attribute value) but reads the
    ``PREVIEW_*`` sets, and additionally RECORDS what it removed so the caller
    can tell "nothing was lost" from "a script body was stripped"."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._stack: list[str] = []
        self._drop_stack: list[str] = []
        # Removals that mean the preview cannot show the real content. ONLY
        # genuinely-lost or actively-neutralized things land here: dropped
        # containers, event handlers, refused URL schemes. Unwrapping an
        # unknown tag keeps its children, and dropping an inert unknown
        # attribute changes nothing visible, so neither is reported — a
        # notice that fires on every article is a notice nobody reads.
        self.removed: set[str] = set()

    # ── attributes ──────────────────────────────────────────────────
    def _attrs(self, tag, attrs) -> str:
        allowed = _PREVIEW_GLOBAL_ATTRS | _PREVIEW_TAG_ATTRS.get(tag, set())
        out = []
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name.startswith("on"):
                self.removed.add(f"{name} handler")
                continue
            if name == "style":
                value = _clean_preview_style(value)
                if not value:
                    continue
            elif name in ("href", "src"):
                if name not in allowed:
                    continue
                stripped = value.strip()
                pattern = (_PREVIEW_SAFE_HREF if name == "href"
                           else _PREVIEW_SAFE_SRC)
                if not pattern.match(stripped):
                    if _PREVIEW_BAD_SCHEME.match(stripped):
                        self.removed.add(f"{tag}[{name}] active scheme")
                    continue
            elif name.startswith(_PREVIEW_ATTR_PREFIXES):
                pass                       # inert metadata, kept verbatim
            elif name not in allowed:
                continue
            elif name in _PREVIEW_SIZE_ATTRS:
                if not _PREVIEW_SIZE_RE.match(value.strip()):
                    continue
            out.append(f' {name}="{escape(value, quote=True)}"')
        return "".join(out)

    # ── tags ────────────────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in PREVIEW_DROP_WITH_CONTENT:
            self.removed.add(f"<{tag}>")
            if tag not in _PREVIEW_DROP_VOID:
                self._drop_stack.append(tag)
            return
        if self._drop_stack:
            return
        if tag not in PREVIEW_ALLOWED_TAGS:
            return                         # unwrap: children survive
        if tag in _PREVIEW_VOID:
            self._out.append(f"<{tag}{self._attrs(tag, attrs)}>")
            return
        self._out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        self._stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in PREVIEW_DROP_WITH_CONTENT:
            self.removed.add(f"<{tag}>")
            return
        if self._drop_stack:
            return
        if tag not in PREVIEW_ALLOWED_TAGS:
            return
        if tag in _PREVIEW_VOID:
            self._out.append(f"<{tag}{self._attrs(tag, attrs)}>")
            return
        self._out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        self._stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._drop_stack:
            if tag in self._drop_stack:
                while self._drop_stack:
                    if self._drop_stack.pop() == tag:
                        break
                return
            if tag in self._stack:
                self._drop_stack.clear()
            else:
                return
        if tag not in self._stack:
            return
        while self._stack:
            open_tag = self._stack.pop()
            self._out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    # ── text + everything else ─────────────────────────────────────
    def handle_data(self, data):
        if not self._drop_stack and data:
            self._out.append(escape(data))

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def unknown_decl(self, data):
        pass

    def result(self) -> str:
        while self._stack:
            self._out.append(f"</{self._stack.pop()}>")
        return "".join(self._out)


def sanitize_html_preview(html: str, *, report: bool = False):
    """Return ``html`` reduced to the PREVIEW allowlist — RENDERING ONLY.

    Use this for the body of a ``sandbox=""`` iframe and for NOTHING else:
    not for stored content, not for the clipboard, not for any string that
    could be pasted into a live site. :func:`sanitize_html` remains the
    profile for those.

    With ``report=True`` returns ``(html, removed)`` where ``removed`` is a
    sorted list of the things that could not be displayed — dropped
    script/style/form/plugin containers, event handlers, and refused
    ``javascript:``-style URL schemes. An EMPTY list means the preview shows
    the content faithfully, which is what lets a caller raise a markup
    warning only when one is warranted.

    Safe on any input (None/empty → ""), and fails CLOSED: a parser blow-up
    degrades to fully-escaped text and reports the failure.
    """
    if not html:
        return ("", []) if report else ""
    parser = _PreviewSanitizer()
    try:
        parser.feed(str(html))
        parser.close()
    except Exception:  # noqa: BLE001 — a parser blow-up must fail CLOSED
        return (escape(str(html)), ["unparseable markup"]) if report \
            else escape(str(html))
    out = parser.result()
    return (out, sorted(parser.removed)) if report else out
