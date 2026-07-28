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
full table markup — so a pulled Zendesk article renders
the way an end user would see it. It still removes scripts, event handlers
and ``javascript:``/``data:`` URLs, and it REPORTS what it removed so callers
can warn only when something genuinely could not be displayed. Never use it
for stored content and never for the clipboard.

**The preview is NOT a disclosure control (2026-07-27).** It was treated as
one for seven rounds, and every round something the preview could not model
walked past it — a CSS property nobody had enumerated, a presentational
attribute that needs no CSS at all, a tag whose children the engine never
paints. The disclosure for markup now lives entirely in the native copy
confirm in ``src/services/zendesk_web.py``, which displays the exact bytes;
everything below is HONESTY about how faithful the render is, not a gate.
Nothing here is load-bearing for whether bytes may reach the clipboard.

**The preview profile still fails in ONE direction: it may show MORE than the
real render, never less.** It is the surface a specialist actually looks at,
so an author who can make text invisible in it wastes the reader's attention
even though they can no longer smuggle anything past the clipboard gate:

    No CSS surviving into the preview can reduce what the reader sees
    relative to the source bytes.

It is enforced by a CLOSED CSS allowlist, not by a list of known tricks.
Only properties that provably cannot remove content from view survive
(typography, colour, non-negative spacing/borders, table scaffolding), each
value-gated; everything else — ``display``, ``position``, ``overflow``,
``opacity``, ``transform``, ``clip-path``, ``filter``, ``zoom`` and every
property CSS has not invented yet — is DROPPED and REPORTED by default. The
earlier design enumerated twelve hiding constructs while the allowlist
admitted hundreds of properties, and ``transform:scale(0)`` walked straight
through it with an empty report. The ``hidden`` attribute, ``width="0"``,
collapsed ``<details>`` and HTML comments are the non-CSS members of the same
family and are handled beside it. Unexpected URL schemes are reported too:
the point of this profile is DISCLOSURE, not blocking.

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
# ids, data-attributes and style declarations INERT — they can style and lay
# out, and nothing else. What is still removed is what remains dangerous or
# simply undisplayable: script bodies, event handlers, plugin/object embeds,
# and ``javascript:``/``data:`` URLs.

PREVIEW_ALLOWED_TAGS = ALLOWED_TAGS | {
    # sectioning / semantic wrappers real Help Center themes emit
    "article", "aside", "details", "footer", "header", "hgroup", "main",
    "nav", "section", "summary",
    # inline semantics
    "abbr", "bdi", "bdo", "cite", "data", "del", "dfn", "ins", "kbd", "q",
    "rt", "ruby", "samp", "time", "var", "wbr",
    # table scaffolding the strict profile drops
    "col", "colgroup",
    # legacy presentational tags Zendesk bodies still carry
    "big", "center", "font", "strike", "tt",
}

# ── TAGS WHOSE CHILDREN THE ENGINE NEVER PAINTS ──────────────────────
#
# A whole family the CSS policy above cannot see, because no CSS is involved:
# the element is on the allowlist, its attributes are clean, its style is
# empty — and Chromium still paints none of its content.
#
# * ``<iframe>X</iframe>`` — the element's own children are FALLBACK content
#   that a frame-capable engine discards outright.
# * ``<rp>X</rp>`` — ruby parenthesis, ``display:none`` in every UA stylesheet
#   that supports ruby.
#
# Both were in ``PREVIEW_ALLOWED_TAGS`` and both hid their content completely
# with an EMPTY report (measured in a real offscreen QWebEngineView). They are
# now UNWRAPPED: the container is dropped, a visible badge names it, and the
# children flow into the document as ordinary painted text. An ``<iframe>``
# was never going to play inside ``sandbox=""`` anyway (nested contexts
# inherit the flags, so no scripts), so the render loses a blank box and gains
# the bytes it was hiding.
PREVIEW_NON_PAINTING_TAGS = {"iframe", "rp"}

# Still dropped WITH their content. ``style``/``svg``/``math``/forms/plugins
# stay out — CSS smuggling, plugin surfaces and credential-shaped widgets are
# exactly the things a reviewer should be told are missing rather than shown.
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
                       "value"}

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

# ══ THE PREVIEW CSS POLICY — A CLOSED ALLOWLIST (inverted 2026-07-26) ══
#
# This was a WIDE allowlist ("the CSS surface a Help Center article actually
# uses" — whole property families by prefix, plus ``transform``, ``opacity``,
# ``display``, ``position``, ``overflow``, ``content``…) paired with a
# detector that enumerated twelve known hiding constructs. That is exactly
# backwards. The allowlist admitted ``transform``, ``clip``, ``clip-path``,
# ``filter``, ``mix-blend-mode``, ``zoom`` and every property CSS has not
# invented yet, while the detector inspected none of them: measured live,
# ``<p style="transform:scale(0)">SECRET</p>`` survived into the preview
# VERBATIM with an EMPTY report — the reviewer's primary surface painted the
# payload at zero scale, no badge, no markup notice, and the copy confirm
# omitted its markup-divergence line. Enumerating what is dangerous can only
# ever be as complete as the last attacker who was caught.
#
# THE INVARIANT NOW, stated so it can be tested:
#
#     No CSS surviving into the preview can reduce what the reader sees
#     relative to the source bytes.
#
# Enforced structurally: a declaration is honoured ONLY when its property is
# on ``_PREVIEW_SAFE_STYLE_PROPS`` — properties that change how content LOOKS
# without being able to remove it from the reader's view — AND its value
# passes that property's gate. EVERYTHING else is dropped and reported,
# including every property this file has never heard of. Dropping beats
# neutralizing per case: an unknown future property is refused by default
# instead of silently honoured, which is the only version of this that is
# still true after CSS grows a feature.
#
# The cost is a preview less pixel-faithful than the real Zendesk render: a
# dropped ``display:flex`` or ``background-image`` is reported, so the markup
# notice fires more often than it did. That is the correct trade for a
# surface whose entire purpose is that a human sees everything before pasting
# it into a public site — an over-eager warning costs a click, a silent one
# costs a phishing paste. Every drop is NAMED in the report (``prop:value``),
# so the notice is never a bare "something changed".

# ── (1) TYPOGRAPHY ───────────────────────────────────────────────────
# Choosing a face, weight, slant, case, alignment or the space between
# lines/letters cannot remove a glyph from the flow. The four that carry a
# LENGTH are value-gated below, because a zero or negative length CAN
# collapse text into nothing (font-size:0, line-height:0, letter-spacing:-9px)
# — and font-family is gated too, because a dingbat face substitutes every
# glyph, which is misrepresentation even though nothing is hidden.
_PREVIEW_TYPOGRAPHY_PROPS = {
    "font-family", "font-weight", "font-style", "font-variant",
    "font-variant-caps", "font-size", "line-height", "letter-spacing",
    "word-spacing", "text-align", "text-transform", "text-decoration",
    "text-decoration-line", "text-decoration-style", "text-decoration-color",
}
# ── (2) COLOUR ───────────────────────────────────────────────────────
# Colour cannot move or collapse a box. The one hiding trick it can play —
# painting text in its own background — is caught by the value gate
# (``color:transparent``) plus the foreground/background comparison in
# :func:`_hide_reasons`, which runs on the values that would be KEPT.
_PREVIEW_COLOR_PROPS = {"color", "background-color", "background"}
# ── (3) SPACING + BORDER GEOMETRY ────────────────────────────────────
# These only ADD space around content. Their gate refuses negative lengths
# (``margin-left:-9999px`` walks a box off-screen) and absurd ones
# (``padding-top:99999px`` pushes it past anything a reviewer will scroll to),
# so what survives can separate content but never remove it.
_PREVIEW_SPACING_PROPS = {
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom",
    "padding-left", "border", "border-top", "border-right", "border-bottom",
    "border-left", "border-width", "border-top-width", "border-right-width",
    "border-bottom-width", "border-left-width", "border-radius",
    "border-top-left-radius", "border-top-right-radius",
    "border-bottom-left-radius", "border-bottom-right-radius",
    "border-spacing",
}
# ── (4) BORDER + TABLE SCAFFOLDING (keywords, no geometry) ───────────
# Line styles, line colours, table rendering modes and list markers. None of
# them carries a position or a size, so none can displace content.
# ``vertical-align`` is here only for its KEYWORD form — a length value can
# shift an inline box arbitrarily far, so the gate refuses lengths.
_PREVIEW_TABLE_PROPS = {
    "border-style", "border-top-style", "border-right-style",
    "border-bottom-style", "border-left-style", "border-color",
    "border-top-color", "border-right-color", "border-bottom-color",
    "border-left-color", "border-collapse", "table-layout", "caption-side",
    "empty-cells", "vertical-align", "list-style", "list-style-type",
    "list-style-position",
}
_PREVIEW_SAFE_STYLE_PROPS = (_PREVIEW_TYPOGRAPHY_PROPS
                             | _PREVIEW_COLOR_PROPS
                             | _PREVIEW_SPACING_PROPS
                             | _PREVIEW_TABLE_PROPS)

# Active-code smuggling. Kept as a value-level refusal on TOP of the property
# allowlist (defense in depth — neither alone is relied on).
_PREVIEW_STYLE_BANNED = re.compile(
    r"expression|javascript|vbscript|@import|behavior|\\", re.IGNORECASE)
# ``url(`` is now refused outright rather than scheme-checked: NO property on
# the allowlist above needs one, so a url() in a preview style is either a
# dropped property arriving by another name or a network beacon. The strict
# profile never allowed it either.
_PREVIEW_URL_CALL = re.compile(r"url\s*\(", re.IGNORECASE)

# ── value gates ──────────────────────────────────────────────────────
_PREVIEW_MAX_SPACE_PX = 500.0       # bigger than any legitimate article gap
_PREVIEW_MIN_LINE_RATIO = 0.5       # below this, lines overprint each other
_PREVIEW_MAX_LINE_RATIO = 10.0      # above this, the next line is off-screen
_PREVIEW_MIN_TRACKING_PX = 0.0      # negative tracking overlaps glyphs
_PREVIEW_MAX_TRACKING_PX = 100.0    # above this, a word is a page-wide smear
_PREVIEW_MIN_FONT_PCT = 30.0        # relative floor mirroring _HIDE_MIN_FONT_PX
_PREVIEW_MAX_FONT_PX = 200.0        # one glyph per viewport hides the rest
_PREVIEW_MAX_FONT_PCT = 400.0
# A CSS keyword: letters and hyphens only. Numbers, lengths and functions are
# NOT keywords, which is the whole point of _gate_words.
_CSS_WORD_RE = re.compile(r"^[a-z][a-z-]*$")
_PREVIEW_REASON_CAP = 60            # reason strings quote a value, not a novel
_PREVIEW_PROP_CAP = 40              # ...and name a property, not a paragraph
# Entries a whole report may carry before it collapses to "+N more". A report
# is a heads-up rendered in a fixed-size widget, never a document: an
# unbounded one is a way to make the reader stop reading.
_PREVIEW_REPORT_CAP = 12
# Characters the native copy confirm uses as its OWN chrome (the curly quotes
# around the row title, the em dash separating it). No CSS property or value
# needs them, and a report entry carrying them can only be trying to look like
# a line the dialog wrote.
_CHROME_CHARS = "“”—"
_GENERIC_KEYWORDS = {"inherit", "initial", "unset", "revert", "auto"}
_PREVIEW_FONT_KEYWORDS = {"xx-small", "x-small", "small", "medium", "large",
                          "x-large", "xx-large", "smaller", "larger"}
# Faces that replace every glyph with a symbol: the text is present but no
# longer readable, which is the same review failure as hiding it.
_PREVIEW_ILLEGIBLE_FONTS = ("wingding", "webding", "symbol", "zapf",
                            "dingbat", "marlett")
_BORDER_STYLE_WORDS = {"none", "hidden", "dotted", "dashed", "solid",
                       "double", "groove", "ridge", "inset", "outset"}
_VERTICAL_ALIGN_WORDS = {"baseline", "sub", "super", "text-top", "text-bottom",
                         "middle", "top", "bottom"}


def _decl_words(value: str):
    """Whitespace-separated tokens of a declaration, ``!important`` removed."""
    return [t for t in re.split(r"\s+", str(value or "")
                                .replace("!important", "").strip()) if t]


def _flat(value, cap: int) -> str:
    """One line, printable, hard-capped — the ONLY shape a report entry may
    take.

    Report entries are rendered inside a native confirm dialog whose line
    structure is the disclosure. An entry that can carry a newline can forge a
    line of that dialog, and an entry with no length bound can push the fixed
    lines off the screen. Both were live: ``_short`` collapsed the VALUE but
    the PROPERTY came straight from ``decl.partition(":")[0]`` with only
    ``.strip().lower()`` applied, so ``<iframe style="<FORGE>: red">`` grew the
    confirm heading from five lines to eight and invented its own.

    So: every whitespace run — newlines included — collapses to one space,
    every non-printable character is removed (that also takes the zero-width
    and bidi-override characters a payload could use to fake dialog chrome),
    and the result is capped."""
    val = " ".join(str(value or "").split())
    val = "".join(ch for ch in val
                  if ch.isprintable() and ch not in _CHROME_CHARS)
    cap = max(1, int(cap))
    return val if len(val) <= cap else val[:cap - 1] + "…"


def _short(value: str) -> str:
    """A value trimmed to reason-string length (reports are read by humans)."""
    return _flat(value, _PREVIEW_REASON_CAP)


def _report_entry(prop, value) -> str:
    """``prop:value``, both halves flattened and capped (see :func:`_flat`).

    THE ONLY constructor for a report entry. Nothing may build one by string
    concatenation — that is exactly how the property half escaped sanitation
    the first time."""
    return f"{_flat(prop, _PREVIEW_PROP_CAP)}:{_short(value)}"


def _gate_words(prop: str, value: str) -> bool:
    """Keyword properties (text-align, border-style, list-style, caption-side
    …) may be given KEYWORDS and colours, and nothing else.

    A number, a length or a function on a property whose grammar has no room
    for one is either invalid CSS the browser will ignore — in which case
    dropping it costs the render nothing — or a form of that property which
    did not exist when this list was written, which is exactly the case this
    profile must not honour by default. ``scale()`` / ``inset()`` /
    ``opacity()`` / ``translateX()`` all arrive wearing parentheses."""
    value = str(value or "")
    if "(" in value:
        return False
    for tok in _decl_words(value):
        low = tok.strip(",").lower()
        if not low or low in _GENERIC_KEYWORDS:
            continue
        if _css_color(low) is not None:
            continue
        if not _CSS_WORD_RE.match(low):
            return False
    return True


def _gate_font_weight(prop: str, value: str) -> bool:
    """The one keyword property with a legitimate numeric form (100–900)."""
    val = str(value or "").replace("!important", "").strip().lower()
    if _CSS_WORD_RE.match(val) or val in _GENERIC_KEYWORDS:
        return True
    try:
        return 1.0 <= float(val) <= 1000.0
    except ValueError:
        return False


def _gate_space(prop: str, value: str) -> bool:
    """Non-negative, non-absurd lengths only (colour/style words ride along
    because ``border`` is a shorthand). An unparseable token is REFUSED —
    deny by default is the point of this profile."""
    for tok in _decl_words(value):
        low = tok.lower()
        if low in _GENERIC_KEYWORDS or low in _BORDER_STYLE_WORDS:
            continue
        if _css_color(tok) is not None:
            continue
        px = _css_px(tok)
        if px is not None:
            if px < 0 or px > _PREVIEW_MAX_SPACE_PX:
                return False
            continue
        pct = _css_pct(tok)
        if pct is not None:
            if pct < 0 or pct > 100:
                return False
            continue
        return False
    return True


def _gate_font_size(prop: str, value: str) -> bool:
    """Between the legibility floor and the absurdity ceiling, in whatever
    unit it is expressed. BOTH ends hide content: ``font-size:0`` erases the
    text, ``font-size:99999px`` fills the viewport with one glyph and pushes
    everything after it past anything a reviewer will scroll to."""
    val = str(value or "").replace("!important", "").strip().lower()
    if val in _GENERIC_KEYWORDS or val in _PREVIEW_FONT_KEYWORDS:
        return True
    px = _css_px(val)
    if px is not None:
        return _HIDE_MIN_FONT_PX <= px <= _PREVIEW_MAX_FONT_PX
    pct = _css_pct(val)
    if pct is not None:
        return _PREVIEW_MIN_FONT_PCT <= pct <= _PREVIEW_MAX_FONT_PCT
    return False


def _gate_line_height(prop: str, value: str) -> bool:
    """A unitless ratio, a percentage or an absolute length — never one that
    collapses the lines onto each other, and never one that flings the next
    line off the page."""
    val = str(value or "").replace("!important", "").strip().lower()
    if val in _GENERIC_KEYWORDS or val == "normal":
        return True
    m = _LEN_RE.match(val)
    if m is None:
        return False
    unit = (m.group(2) or "").lower()
    num = float(m.group(1))
    if unit == "":
        return _PREVIEW_MIN_LINE_RATIO <= num <= _PREVIEW_MAX_LINE_RATIO
    if unit == "%":
        return (_PREVIEW_MIN_LINE_RATIO * 100.0 <= num
                <= _PREVIEW_MAX_LINE_RATIO * 100.0)
    px = _css_px(val)
    return px is not None and _HIDE_MIN_FONT_PX <= px <= _PREVIEW_MAX_FONT_PX


def _gate_tracking(prop: str, value: str) -> bool:
    """letter/word-spacing: NEGATIVE tracking overlaps glyphs into an
    unreadable smear and is never needed in an article body, and a huge
    positive one scatters a sentence off the page."""
    val = str(value or "").replace("!important", "").strip().lower()
    if val in _GENERIC_KEYWORDS or val == "normal":
        return True
    px = _css_px(val)
    return (px is not None
            and _PREVIEW_MIN_TRACKING_PX <= px <= _PREVIEW_MAX_TRACKING_PX)


def _gate_vertical_align(prop: str, value: str) -> bool:
    val = str(value or "").replace("!important", "").strip().lower()
    return val in _VERTICAL_ALIGN_WORDS or val in _GENERIC_KEYWORDS


def _gate_font_family(prop: str, value: str) -> bool:
    low = str(value or "").lower()
    if "(" in low:                      # no functional form exists; see _gate_any
        return False
    return not any(f in low for f in _PREVIEW_ILLEGIBLE_FONTS)


def _gate_color(prop: str, value: str) -> bool:
    """A parseable colour. ``transparent`` TEXT is invisible text, so it is
    refused for ``color``; a transparent BACKGROUND is the default and hides
    nothing."""
    val = str(value or "").replace("!important", "").strip().lower()
    if val in _GENERIC_KEYWORDS:
        return True
    parsed = _css_color(val)
    if parsed is None:
        return False
    return not (parsed == "transparent" and prop == "color")


_PREVIEW_STYLE_GATES = {p: _gate_space for p in _PREVIEW_SPACING_PROPS}
_PREVIEW_STYLE_GATES.update({
    "font-weight": _gate_font_weight,
    "font-size": _gate_font_size,
    "line-height": _gate_line_height,
    "letter-spacing": _gate_tracking,
    "word-spacing": _gate_tracking,
    "vertical-align": _gate_vertical_align,
    "font-family": _gate_font_family,
    "color": _gate_color,
    "background-color": _gate_color,
    "background": _gate_color,      # colour-only: no images, no shorthand url()
})


# ══ PRESENTATIONAL ATTRIBUTES — THE SAME POLICY, WITHOUT ANY CSS ══════
#
# The CSS policy above is worthless on markup that needs no CSS. ``<font
# color>``, ``<font face>``, ``<font size>`` and ``bgcolor`` were allowlisted
# by tag and emitted VERBATIM: the only checks were a size regex and a
# zero-test on width/height, and ``_hide_reasons`` only ever read the parsed
# ``style`` attribute, so no attribute-borne colour reached the
# foreground-vs-background comparison at all. Measured live, with an EMPTY
# report and an EMPTY markup notice:
#
#     <font color="#ffffff">…</font>                (white text, white page)
#     <td bgcolor="#000"><font color="#000">…       (black on black, nested)
#     <font face="Wingdings">…</font>               (every glyph substituted)
#
# Each attribute is therefore translated to the CSS property that means the
# same thing and run through THAT property's gate and THAT property's hide
# analysis. Failing the gate drops the attribute and NAMES it in the report,
# exactly as the CSS equivalent would be dropped and named.
_PREVIEW_PRESENTATIONAL = {
    ("font", "color"): "color",
    ("font", "face"): "font-family",
    ("font", "size"): "font-size",
    ("table", "bgcolor"): "background-color",
    ("td", "bgcolor"): "background-color",
    ("th", "bgcolor"): "background-color",
    ("tr", "bgcolor"): "background-color",
}

# ``<font size>`` is an HTML SCALE (1-7, or a signed step from the base 3),
# never a CSS length — "2" means 13px, not 2px. The px column is the standard
# UA mapping, and it exists only so the font-size gate can judge the value.
_HTML_FONT_SIZE_PX = {1: 10.0, 2: 13.0, 3: 16.0, 4: 18.0, 5: 24.0, 6: 32.0,
                      7: 48.0}


def _html_font_px(value):
    """px for an HTML ``size`` step, or None for anything off the scale.

    Browsers CLAMP an out-of-range step; this profile refuses it instead, on
    the same deny-by-default rule the CSS gates follow — a value the sanitizer
    cannot reason about is dropped and reported, never honoured."""
    val = str(value or "").strip()
    try:
        n = 3 + int(val) if val[:1] in ("+", "-") else int(val)
    except (ValueError, TypeError, IndexError):
        return None
    return _HTML_FONT_SIZE_PX.get(n)


def _presentational_value(prop: str, raw):
    """The CSS-equivalent value of a presentational attribute, or None when it
    fails the gate its CSS twin would have faced."""
    val = str(raw or "").strip()
    if not val:
        return None
    if prop == "font-size":
        px = _html_font_px(val)
        return None if px is None else f"{px:g}px"
    gate = _PREVIEW_STYLE_GATES.get(prop, _gate_words)
    return val if gate(prop, val) else None


# ── content-hiding neutralization (PREVIEW ONLY) ─────────────────────
#
# Everything below exists because a review preview that HIDES part of the
# bytes is worse than no preview: the reviewer reads it, sees nothing
# alarming, and pastes the invisible payload into a public site. The rule
# inverted here is "honour the author's CSS"; what replaces it is "render
# what the author tried to hide, mark it, and say so".
#
# Since the CSS policy above became a CLOSED allowlist, dropping a hiding
# declaration is no longer this section's job — the allowlist drops it, and
# every future one, without being told about it. What survives here is the
# part an allowlist cannot do: naming a construct in prose a human can act
# on, and the one hiding trick played entirely with ALLOWLISTED values
# (text painted in its own background colour).
#
# Markers the caller's stylesheet paints (src/services/zendesk_web.py ships
# the Help Center CSS that makes them impossible to miss).
_HIDE_CLASS = "alma-unhidden"
_HIDE_NOTE_CLASS = "alma-unhidden-note"
_COMMENT_CLASS = "alma-source-comment"
_HIDE_DATA_ATTR = "data-alma-hidden"

# Thresholds. Deliberately generous: over-reporting a legible-but-small font
# costs a badge, under-reporting one costs a phishing paste. The off-screen /
# tiny-box / negative-indent thresholds this block used to carry are gone with
# the detector that needed them — `position`, `top/left`, `width/height`,
# `overflow` and `text-indent` are simply not on the allowlist any more, so
# there is no value of theirs left to measure.
_HIDE_MIN_OPACITY = 0.15
_HIDE_MIN_FONT_PX = 4.0
_HIDE_OVERLAY_Z = 5
_HIDE_OVERLAY_PCT = 80.0
_HIDE_COLOR_DELTA = 42.0        # euclidean RGB distance treated as "same"
_HIDE_NOTE_CAP = 8              # badge/data-attr entries before "+N more"
_PREVIEW_CANVAS_RGB = (255, 255, 255)   # the preview body background
# ...and the colour text INHERITS there (zendesk_web's Help Center CSS sets
# body { color: #2f3941 }). Text that declares no colour of its own is
# painted in this, so a background declared in the same colour hides it.
_PREVIEW_CANVAS_TEXT_RGB = (47, 57, 65)
# The colour pair an element inherits when nothing above it declared one.
_PREVIEW_ROOT_COLORS = (_PREVIEW_CANVAS_TEXT_RGB, _PREVIEW_CANVAS_RGB)
# ...and the pair the ``.alma-unhidden`` rule forces (background #fff0f1,
# colour #2f3941, both !important). An element the preview has already marked
# paints in THESE, so its children inherit them — otherwise one flagged
# ancestor makes every descendant look like a second, phantom colour trick.
_PREVIEW_UNHIDDEN_COLORS = (_PREVIEW_CANVAS_TEXT_RGB, (255, 240, 241))

_LEN_RE = re.compile(
    r"^\s*(-?\d*\.?\d+)\s*(px|pt|em|rem|ex|ch|vw|vh|%)?\s*(!important)?\s*$",
    re.IGNORECASE)
_LEN_TO_PX = {"": 1.0, "px": 1.0, "pt": 4.0 / 3.0, "em": 16.0, "rem": 16.0,
              "ex": 8.0, "ch": 8.0}
_HEX_RE = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.IGNORECASE)
_RGB_RE = re.compile(r"^rgba?\(([^)]*)\)$", re.IGNORECASE)
_NAMED_RGB = {
    "white": (255, 255, 255), "snow": (255, 250, 250),
    "ivory": (255, 255, 240), "whitesmoke": (245, 245, 245),
    "ghostwhite": (248, 248, 255), "floralwhite": (255, 250, 240),
    "black": (0, 0, 0), "silver": (192, 192, 192), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "red": (255, 0, 0), "green": (0, 128, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0),
}
_SCHEME_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+.\-]*)\s*:")
# Schemes that are KEPT but still announced — the reviewer should know a link
# dials a phone or opens a mail client, even though neither is dangerous.
_PREVIEW_NOTED_SCHEMES = {"mailto", "tel"}


def _css_number(value):
    """Leading number of a length-ish CSS value ('-9999px' → -9999.0)."""
    m = _LEN_RE.match(str(value or ""))
    return None if m is None else float(m.group(1))


def _css_px(value):
    """Approximate px for an ABSOLUTE length; None for %/vw/vh/unparseable."""
    m = _LEN_RE.match(str(value or ""))
    if m is None:
        return None
    unit = (m.group(2) or "").lower()
    if unit not in _LEN_TO_PX:
        return None
    return float(m.group(1)) * _LEN_TO_PX[unit]


def _css_pct(value):
    """Percentage-ish magnitude ('100%' / '100vw' → 100.0), else None."""
    m = _LEN_RE.match(str(value or ""))
    if m is None or (m.group(2) or "").lower() not in ("%", "vw", "vh"):
        return None
    return float(m.group(1))


def _css_color(value):
    """``(r, g, b)``, the string ``'transparent'``, or None."""
    val = str(value or "").strip().lower().replace("!important", "").strip()
    if not val:
        return None
    if val == "transparent":
        return "transparent"
    if val in _NAMED_RGB:
        return _NAMED_RGB[val]
    m = _HEX_RE.match(val)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = _RGB_RE.match(val)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) >= 3:
            try:
                rgb = tuple(min(255, max(0, int(float(p.rstrip("%")))))
                            for p in parts[:3])
            except ValueError:
                return None
            if len(parts) >= 4:
                try:
                    if float(parts[3]) < _HIDE_MIN_OPACITY:
                        return "transparent"
                except ValueError:
                    pass
            return rgb
    return None


def _rgb_distance(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _hide_reasons(declared: dict, kept: dict, inherited=None):
    """``(prose_reasons, props_to_drop)`` for one element's declarations.

    Runs AFTER the closed allowlist, and only does what an allowlist cannot:

    * **the colour trick** — ``color`` and ``background-color`` are both
      allowlisted (an article needs them), so the one way colour hides text,
      painting it in its own background, has to be caught on the values that
      would otherwise be KEPT. That is the only entry here that drops a
      declaration.
    * **prose for a decoy overlay** — every property involved is already
      dropped by the allowlist, but "overlay that can cover the content"
      tells a reviewer what was attempted, where five separate
      ``position:fixed`` / ``z-index:9999`` drop lines do not. Naming is not
      the defense; the allowlist is.

    ``declared`` is everything the author wrote (pre-drop), ``kept`` is what
    survived the allowlist — BOTH now including the CSS translations of the
    presentational attributes (``<font color>``, ``bgcolor``…), which used to
    bypass this comparison entirely.

    ``inherited`` is the ``(text_rgb, background_rgb)`` pair in force on the
    PARENT element. Colour cascades, so the trick does not have to be played
    on one element: ``<td bgcolor="#000"><font color="#000">`` paints black on
    black with each half individually innocent, and comparing against the page
    canvas alone declared both of them fine."""
    reasons: list[str] = []
    drop: set[str] = set()

    in_fg, in_bg = inherited or _PREVIEW_ROOT_COLORS
    fg = _css_color(kept.get("color"))
    bg = (_css_color(kept.get("background-color"))
          or _css_color(kept.get("background")))
    if isinstance(fg, tuple):
        reference = bg if isinstance(bg, tuple) else in_bg
        if _rgb_distance(fg, reference) <= _HIDE_COLOR_DELTA:
            reasons.append("text colour "
                           f"{_short(kept.get('color'))} matches its "
                           "background")
            drop.add("color")
    elif isinstance(bg, tuple):
        # THE SAME TRICK WITH ONE DECLARATION INSTEAD OF TWO: declare no
        # colour and paint the background in the colour the text INHERITS.
        # Both properties are allowlisted, so only this comparison can catch
        # it.
        if _rgb_distance(bg, in_fg) <= _HIDE_COLOR_DELTA:
            shown = (kept.get("background-color") or kept.get("background"))
            reasons.append(f"background {_short(shown)} matches the text "
                           "colour it inherits")
            drop.update(("background-color", "background"))

    position = str(declared.get("position", "")).strip().lower().split(" ")[0]
    if position in ("absolute", "fixed"):
        z = _css_number(declared.get("z-index"))
        spread = [_css_pct(declared.get("width")),
                  _css_pct(declared.get("height"))]
        covers = any(v is not None and v >= _HIDE_OVERLAY_PCT for v in spread)
        if z is not None and z >= _HIDE_OVERLAY_Z and covers:
            reasons.append(
                f"overlay that can cover the content (position:{position}; "
                f"z-index:{_short(declared.get('z-index'))})")
    return reasons, drop


def _effective_colors(kept: dict, inherited):
    """The ``(text, background)`` pair this element's CHILDREN inherit."""
    in_fg, in_bg = inherited or _PREVIEW_ROOT_COLORS
    fg = _css_color(kept.get("color"))
    bg = (_css_color(kept.get("background-color"))
          or _css_color(kept.get("background")))
    return (fg if isinstance(fg, tuple) else in_fg,
            bg if isinstance(bg, tuple) else in_bg)


def _clean_preview_style(value: str):
    """``(kept_pairs, reasons, declared)`` for one ``style`` attribute.

    Deny-by-default at the PROPERTY level, then at the VALUE level:

    1. a declaration carrying active-code smuggling or a ``url()`` is
       refused outright (no allowlisted property needs either);
    2. a property missing from ``_PREVIEW_SAFE_STYLE_PROPS`` is dropped —
       this is where ``transform``, ``clip-path``, ``filter``, ``display``,
       ``position``, ``overflow``, ``opacity``, ``zoom`` and every property
       nobody has thought of yet go;
    3. a property on the allowlist whose VALUE fails its gate is dropped —
       ``font-size:0``, ``line-height:0``, ``margin-left:-9999px``,
       ``color:transparent``.

    The colour analysis (:func:`_hide_reasons`) is deliberately NOT run here
    any more: it has to see the element's presentational attributes and the
    colours it inherits, neither of which is in this string. The caller
    (:meth:`_PreviewSanitizer._attrs`) merges all three and runs it once.

    EVERY drop is reported through :func:`_report_entry`, so the caller can
    name what the preview refused to honour instead of warning that something
    happened — and so a crafted property name can never forge a line of the
    native confirm the report is rendered in."""
    parsed: list[tuple[str, str]] = []
    dropped: list[str] = []
    for decl in (value or "").split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if not prop or not val:
            continue
        if _PREVIEW_STYLE_BANNED.search(val) or _PREVIEW_URL_CALL.search(val):
            dropped.append(_report_entry(prop, val))
            continue
        parsed.append((prop, val))

    kept: list[tuple[str, str]] = []
    for prop, val in parsed:
        # Anything without a gate of its own is a KEYWORD property: keywords
        # and colours only. Deny-by-default all the way down.
        gate = _PREVIEW_STYLE_GATES.get(prop, _gate_words)
        if prop not in _PREVIEW_SAFE_STYLE_PROPS or not gate(prop, val):
            dropped.append(_report_entry(prop, val))
            continue
        kept.append((prop, val))
    return kept, dropped, dict(parsed)


def _url_scheme(value: str) -> str:
    m = _SCHEME_RE.match(str(value or ""))
    return m.group(1).lower() if m else ""


def _note_summary(notes) -> str:
    """Notes joined for display, capped: a badge is a signal, not a dump."""
    notes = list(notes)
    head = "; ".join(str(n) for n in notes[:_HIDE_NOTE_CAP])
    extra = len(notes) - _HIDE_NOTE_CAP
    return head + (f"; +{extra} more" if extra > 0 else "")


def _mark_unhidden(kept, notes):
    """Stamp the marker class + a machine-readable note onto an element whose
    CSS the preview refused, merging with any class the author already set."""
    out, marked = [], False
    for name, value in kept:
        if name == "class" and not marked:
            value = f"{value} {_HIDE_CLASS}".strip()
            marked = True
        out.append((name, value))
    if not marked:
        out.append(("class", _HIDE_CLASS))
    out.append((_HIDE_DATA_ATTR, _note_summary(notes)))
    return out


def _unhidden_badge(notes) -> str:
    """The visible 'the preview did not honour this' badge.

    Wording covers BOTH halves of the closed-allowlist policy: a declaration
    that was actively hiding the content (``display:none``) and one that was
    merely not provably safe (``transform:rotate(2deg)``). Either way the
    content above is shown as the source wrote it, which is the fact the
    reviewer needs."""
    return (f'<span class="{_HIDE_NOTE_CLASS}">[the review preview did not '
            f"honour the source's presentation here: "
            f"{escape(_note_summary(notes))} - the content is shown plainly "
            "so nothing can be hidden]</span>")


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
        # Colour context, kept in LOCKSTEP with ``_stack`` (one frame per open
        # emitted element): the ``(text, background)`` pair in force inside
        # that element. Colour cascades, so the only way to see
        # ``<td bgcolor="#000"><font color="#000">`` is to carry the ancestor's
        # pair down to the child that completes the pair.
        self._colors: list[tuple] = []
        # Removals that mean the preview cannot show the real content:
        # dropped containers, event handlers, refused/odd URL schemes.
        # Unwrapping an unknown tag keeps its children and dropping an inert
        # unknown attribute changes nothing visible, so neither is reported.
        self.removed: set[str] = set()
        # Things the preview SHOWS that the real render would not: un-hidden
        # elements, the ``hidden`` attribute, HTML comments. Reported for the
        # same reason removals are — the reviewer must be told the preview
        # and the bytes are not the same picture, in either direction.
        self.neutralized: set[str] = set()

    # ── attributes ──────────────────────────────────────────────────
    def _current_colors(self):
        return self._colors[-1] if self._colors else _PREVIEW_ROOT_COLORS

    def _attrs(self, tag, attrs):
        """``(serialized attributes, notes, child_colors)``.

        ``notes`` are the content-hiding constructs this element carried;
        they are stripped from the emitted attributes (so the content
        paints), stamped on the element as a marker class + ``data-`` note,
        and returned so the caller can emit the visible badge.
        ``child_colors`` is the ``(text, background)`` pair this element's
        children inherit.

        The ``style`` attribute and the PRESENTATIONAL attributes (``<font
        color/face/size>``, ``bgcolor``) are gathered into ONE picture before
        anything is emitted, because they say the same things by different
        means and only the merged picture can be judged."""
        allowed = _PREVIEW_GLOBAL_ATTRS | _PREVIEW_TAG_ATTRS.get(tag, set())
        kept: list[tuple[str, str]] = []
        notes: list[str] = []
        declared: dict[str, str] = {}       # everything the author asked for
        surviving: dict[str, str] = {}      # ...that passed its gate
        style_pairs: list[tuple[str, str]] = []
        pres: list[tuple[str, str]] = []    # (attribute name, css property)
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name.startswith("on"):
                self.removed.add(f"{name} handler")
                continue
            if name == "hidden":
                # THE INVERSE LEAK, fixed honestly. Dropping this attribute
                # silently made the preview show text Zendesk hides; keeping
                # it would make the preview hide text the clipboard carries.
                # So: drop it, mark it, report it.
                notes.append("hidden attribute")
                continue
            if name == "style":
                pairs, reasons, parsed = _clean_preview_style(value)
                notes.extend(reasons)
                declared.update(parsed)
                # A style declaration BEATS the presentational attribute
                # saying the same thing, exactly as CSS specificity does —
                # which is why this ``update`` sits opposite the
                # ``setdefault`` below, whichever order they arrive in.
                surviving.update(dict(pairs))
                style_pairs.extend(pairs)   # duplicate style attrs: last wins
                continue            # serialized after the merged analysis
            prop = _PREVIEW_PRESENTATIONAL.get((tag, name))
            if prop is not None:
                # G1: gated by the CSS property it is a synonym for, and
                # NAMED in the report when it fails, exactly like the CSS.
                css_value = _presentational_value(prop, value)
                declared.setdefault(prop, value)
                if css_value is None:
                    notes.append(_report_entry(f"{tag}[{name}]", value))
                    continue
                surviving.setdefault(prop, css_value)
                pres.append((name, prop))
                kept.append((name, value))
                continue
            if name in ("href", "src"):
                if name not in allowed:
                    continue
                stripped = value.strip()
                pattern = (_PREVIEW_SAFE_HREF if name == "href"
                           else _PREVIEW_SAFE_SRC)
                scheme = _url_scheme(stripped)
                if not pattern.match(stripped):
                    if _PREVIEW_BAD_SCHEME.match(stripped):
                        self.removed.add(f"{tag}[{name}] active scheme")
                    elif scheme:
                        self.removed.add(f"{tag}[{name}] {scheme}: URL")
                    else:
                        self.removed.add(f"{tag}[{name}] unexpected URL")
                    continue
                if scheme in _PREVIEW_NOTED_SCHEMES:
                    # kept, but never silently: disclosure, not blocking
                    self.removed.add(f"{tag}[{name}] {scheme}: link")
            elif name.startswith(_PREVIEW_ATTR_PREFIXES):
                pass                       # inert metadata, kept verbatim
            elif name not in allowed:
                continue
            elif name in _PREVIEW_SIZE_ATTRS:
                if not _PREVIEW_SIZE_RE.match(value.strip()):
                    continue
                if (name in ("width", "height")
                        and _css_number(value.strip()) == 0):
                    # The same collapse, one layer down: presentational
                    # ``width="0"``/``height="0"`` needs no CSS at all, so the
                    # style policy above never sees it. Drop and report.
                    notes.append(_report_entry(f"{tag}[{name}]", value))
                    continue
            kept.append((name, value))

        # ── the ONE merged colour analysis (style + presentational +
        # inherited). Whatever it refuses is dropped from BOTH carriers.
        inherited = self._current_colors()
        prose, drop = _hide_reasons(declared, surviving, inherited)
        for reason in prose:
            if reason not in notes:
                notes.append(reason)
        if drop:
            surviving = {p: v for p, v in surviving.items() if p not in drop}
            style_pairs = [(p, v) for p, v in style_pairs if p not in drop]
            refused = {n for n, p in pres if p in drop}
            kept = [(n, v) for n, v in kept if n not in refused]
        if style_pairs:
            kept.append(("style",
                         "; ".join(f"{p}: {v}" for p, v in style_pairs)))

        if notes:
            self.neutralized.update(notes)
            kept = _mark_unhidden(kept, notes)
            # A marked element paints in the marker rule's own colours, so
            # that is what its children inherit — not the author's refused
            # pair, and not the page canvas.
            child_colors = _PREVIEW_UNHIDDEN_COLORS
        else:
            child_colors = _effective_colors(surviving, inherited)
        return ("".join(f' {n}="{escape(v, quote=True)}"' for n, v in kept),
                notes, child_colors)

    def _emit_open(self, tag, attrs, *, push):
        if tag == "details" and not any((n or "").lower() == "open"
                                        for n, _v in attrs):
            # A collapsed <details> hides its content behind a click with no
            # CSS involved at all — the style policy cannot see it. Force it
            # OPEN so a review cannot scroll past a payload. Not reported:
            # unlike display:none this content IS reachable in the real render
            # (one click), and a Help Center accordion is ordinary article
            # markup — firing the markup notice on it would put the warning
            # back on every article, which is how a warning stops being read.
            attrs = list(attrs) + [("open", "")]
        attr_str, notes, child_colors = self._attrs(tag, attrs)
        self._out.append(f"<{tag}{attr_str}>")
        if push:
            self._stack.append(tag)
            self._colors.append(child_colors)
        if notes:
            self._out.append(_unhidden_badge(notes))

    def _unwrap_non_painting(self, tag, attrs):
        """G2: a tag whose CHILDREN the engine never paints.

        The container is dropped and its children keep flowing into the
        document as ordinary text, preceded by a visible badge naming what was
        there (and, for an ``<iframe>``, where it pointed) — so an
        ``<iframe>PAYLOAD</iframe>`` reads as PAYLOAD with a marker instead of
        vanishing with an empty report."""
        note = f"<{tag}> content is not painted in a real render"
        src = next((v for n, v in attrs
                    if (n or "").lower() == "src" and v), None)
        if src:
            note += f" (src {_short(src)})"
        self.neutralized.add(note)
        self._out.append(_unhidden_badge([note]))

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
        if tag in PREVIEW_NON_PAINTING_TAGS:
            self._unwrap_non_painting(tag, attrs)
            return                         # unwrap: children become visible
        if tag not in PREVIEW_ALLOWED_TAGS:
            return                         # unwrap: children survive
        self._emit_open(tag, attrs, push=tag not in _PREVIEW_VOID)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in PREVIEW_DROP_WITH_CONTENT:
            self.removed.add(f"<{tag}>")
            return
        if self._drop_stack:
            return
        if tag in PREVIEW_NON_PAINTING_TAGS:
            self._unwrap_non_painting(tag, attrs)
            return
        if tag not in PREVIEW_ALLOWED_TAGS:
            return
        self._emit_open(tag, attrs, push=tag not in _PREVIEW_VOID)

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
            if self._colors:
                self._colors.pop()      # lockstep with _stack
            self._out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    # ── text + everything else ─────────────────────────────────────
    def handle_data(self, data):
        if not self._drop_stack and data:
            self._out.append(escape(data))

    def handle_comment(self, data):
        """Comments carrying text are SURFACED, not dropped.

        A comment is invisible in every render and survives a copy-exact
        paste verbatim, which makes it the cheapest place to park an
        instruction the reviewer never sees."""
        text = str(data or "").strip()
        if not text or self._drop_stack:
            return
        self.neutralized.add("HTML comment")
        self._out.append(f'<span class="{_COMMENT_CLASS}">&lt;!--'
                         f"{escape(text)}--&gt;</span>")

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

    With ``report=True`` returns ``(html, notes)`` where ``notes`` is a
    sorted list of every way the preview and the bytes differ, in BOTH
    directions:

    * REMOVED — dropped script/style/form/plugin containers, event handlers,
      refused ``javascript:``-style URLs, and any unexpected URL scheme
      (``ftp:``, ``mailto:``, ``tel:``, a bare unknown one);
    * NEUTRALIZED — content-hiding CSS that was overridden so the text
      actually paints, the ``hidden`` attribute, and HTML comments.

    An EMPTY list means the preview shows the content faithfully and hides
    nothing, which is what lets a caller raise a markup warning only when one
    is warranted. A non-empty list must be surfaced: it is the signal that
    the reviewer has to read the exact source bytes.

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
    return (out, _capped_report(parser.removed | parser.neutralized)) \
        if report else out


def _capped_report(notes) -> list:
    """The report as the caller may render it: sorted, and bounded by
    ``_PREVIEW_REPORT_CAP`` with an explicit ``+N more`` tail.

    Every entry is already flattened and length-capped at construction
    (:func:`_report_entry`); this bounds the COUNT, so a body carrying a
    thousand crafted declarations cannot turn a heads-up widget into a wall
    that displaces the disclosure around it."""
    out = sorted(notes)
    if len(out) <= _PREVIEW_REPORT_CAP:
        return out
    extra = len(out) - _PREVIEW_REPORT_CAP
    return out[:_PREVIEW_REPORT_CAP] + [f"+{extra} more"]
