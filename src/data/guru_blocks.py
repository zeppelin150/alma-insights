"""Guru-native block authoring — emit Guru's `ghq-card-content__*` markup.

Guru stores its richer blocks (callouts/banners, collapsible sections,
card-to-card links) as HTML with `class="ghq-card-content__…"` +
`data-ghq-card-content-type="…"` attributes — NOT an opaque JSON. A
QTextEdit can't hold arbitrary classes/data-attributes, so these blocks
are authored as a small, human-readable MARKDOWN-directive vocabulary in
the canonical draft body and expanded to Guru-native HTML at render time
(in-app preview) and at publish time (the Guru `content` payload).

Directive vocabulary (lives in the markdown body, survives all surfaces):
  - Callout:    a blockquote whose first line is [!NOTE] / [!SUCCESS] /
                [!WARNING] / [!DANGER]
                    > [!WARNING]
                    > Heads up — rollout is June 24.
  - Collapsible: a fenced container
                    ::: details Section title
                    body markdown
                    :::
  - Card link:  inline token  [[guru:<cardId>|Label]]

``expand_blocks(html)`` runs on the rendered HTML (after markdown_to_html,
or on the rich editor's captured HTML) and is idempotent.

CONTRACT NOTE: only the callout's inline-style form is documented by Guru.
The exact class / data-attribute contract for collapsible + card-link
blocks is NOT published — the templates below are best-effort against the
`ghq-card-content__*` convention and MUST be validated with a live
POST→GET round trip (see scripts/guru_style_roundtrip_probe.py). Guru may
render an unrecognized block as plain HTML. Every contract string lives
here, in one place, so it can be corrected after the probe.
"""

from __future__ import annotations

import html as _html
import re

# ── Block contracts ──────────────────────────────────────────────────────
# Reverse-engineered from a 20 MB real Guru collection export (verbatim
# markup, not guessed): callouts are <section> with a data-ghq-color keyword
# + 8-digit RGBA hex; collapsibles are native <details>/<summary>; card links
# are <a type=GURU_CARD> carrying data-ghq-guru-card-id. Guru re-parses
# submitted HTML, so emitting the block-level class + data-ghq-card-content-type
# is what makes it a native block (it fills in inner ids itself). The card-link
# green-"G" binding is the one piece needing a live POST→GET confirmation
# (it may degrade to a plain external link) — see
# scripts/guru_style_roundtrip_probe.py.

# Callout variants → Guru's (data-ghq-color keyword, 8-digit RGBA background).
# These are the only six colours Guru emits.
CALLOUT_VARIANTS = {
    "note":    ("blue", "#00bcd62b"),
    "info":    ("blue", "#00bcd62b"),
    "success": ("green", "#66a03033"),
    "warning": ("yellow", "#ffc20042"),
    "danger":  ("red", "#f7412d26"),
    "caution": ("orange", "#ff8a603b"),
    "neutral": ("gray", "#bab0bf2e"),
}
_CALLOUT_ALIASES = {
    "tip": "success", "resolved": "success", "ok": "success",
    "warn": "warning",
    "error": "danger", "restricted": "danger", "important": "danger",
}


def callout_html(body_html: str, variant: str = "note") -> str:
    variant = _CALLOUT_ALIASES.get(variant.lower(), variant.lower())
    color, bg = CALLOUT_VARIANTS.get(variant, CALLOUT_VARIANTS["note"])
    return (
        f'<section class="ghq-card-content__callout" '
        f'data-ghq-card-content-type="CALLOUT" '
        f'style="background-color: {bg};" data-ghq-color="{color}">'
        f'{body_html or ""}</section>'
    )


def collapsible_html(title: str, body_html: str) -> str:
    return (
        f'<details class="ghq-card-content__collapsible" '
        f'data-ghq-card-content-type="COLLAPSIBLE">'
        f'<summary class="ghq-card-content__collapsible-summary" '
        f'data-ghq-card-content-type="COLLAPSIBLE_SUMMARY">'
        f'<strong class="ghq-card-content__bold" data-ghq-card-content-type="BOLD">'
        f'{_html.escape(title)}</strong></summary>'
        f'<div class="ghq-card-content__collapsible-content" '
        f'data-ghq-card-content-type="COLLAPSIBLE_CONTENT">{body_html}</div>'
        f'</details>'
    )


def card_link_html(card_id: str, label: str) -> str:
    # The directive carries one id; Guru uses a short slug in the href and the
    # full UUID in data-ghq-guru-card-id. We put the given id in both — pass the
    # card UUID for the best chance the green-"G" link binds (validate live).
    cid = _html.escape(card_id, quote=True)
    return (
        f'<a class="ghq-card-content__guru-card" '
        f'data-ghq-card-content-type="GURU_CARD" target="_blank" '
        f'rel="noopener noreferrer" href="https://app.getguru.com/card/{cid}" '
        f'data-ghq-guru-card-id="{cid}">{_html.escape(label)}</a>'
    )


# ── Directive insert helpers (used by the editor toolbar) ────────────────

def callout_directive(variant: str = "note", body: str = "Your message here.") -> str:
    return f"\n> [!{variant.upper()}]\n> {body}\n"


def collapsible_directive(title: str = "Section title", body: str = "Hidden body.") -> str:
    # Blank lines so markdown renders the fence markers as separate paragraphs.
    return f"\n::: details {title}\n\n{body}\n\n:::\n"


def card_link_token(card_id: str, label: str) -> str:
    return f"[[guru:{card_id}|{label}]]"


# ── Expansion (markdown-rendered HTML → Guru-native HTML) ─────────────────

_CARD_LINK_RE = re.compile(r"\[\[guru:([^|\]]+)\|([^\]]+)\]\]")
# Callout — the [!TYPE] marker leads a block. Two rendered forms:
#  (bq) markdown blockquote → <blockquote><p>[!TYPE]<br>body</p></blockquote>
#  (p)  rich editor (Qt models a quote as an indented <p>, not <blockquote>)
#       → <p>[!TYPE]<br/>body</p>
_CALLOUT_BQ_RE = re.compile(
    r"<blockquote>\s*<p>\s*\[!\s*([A-Za-z]+)\s*\]\s*(?:<br\s*/?>)?\s*(.*?)</p>\s*</blockquote>",
    re.DOTALL | re.IGNORECASE,
)
_CALLOUT_P_RE = re.compile(
    r"<p>\s*\[!\s*([A-Za-z]+)\s*\]\s*(?:<br\s*/?>)?\s*(.*?)</p>",
    re.DOTALL | re.IGNORECASE,
)
# Collapsible — two rendered forms:
#  (multi) separate paragraphs → <p>::: details Title</p>(body)<p>:::</p>
#  (single) one paragraph (markdown collapses adjacent lines) →
#           <p>::: details Title<br/>body<br/>:::</p>  or with \n
_COLLAPSIBLE_MULTI_RE = re.compile(
    r"<p>:::\s*details\s+(.*?)</p>(.*?)<p>\s*:::\s*</p>",
    re.DOTALL | re.IGNORECASE,
)
_COLLAPSIBLE_SINGLE_RE = re.compile(
    r"<p>:::\s*details\s+(.*?)(?:<br\s*/?>|\n)(.*?)(?:<br\s*/?>|\n):::\s*</p>",
    re.DOTALL | re.IGNORECASE,
)


def expand_blocks(html: str) -> str:
    """Expand the native-block directives embedded in rendered HTML into
    Guru's `ghq-card-content__*` markup. Idempotent: HTML with no directives
    is returned unchanged."""
    if not html:
        return html or ""

    def _callout(m: "re.Match") -> str:
        body = m.group(2).strip()
        return callout_html(f"<p>{body}</p>" if body else "", m.group(1).lower())

    def _collapsible_multi(m: "re.Match") -> str:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return collapsible_html(title, m.group(2).strip())

    def _collapsible_single(m: "re.Match") -> str:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        body = m.group(2).strip().replace("\n", "<br/>")
        return collapsible_html(title, f"<p>{body}</p>" if body else "")

    html = _COLLAPSIBLE_MULTI_RE.sub(_collapsible_multi, html)
    html = _COLLAPSIBLE_SINGLE_RE.sub(_collapsible_single, html)
    html = _CALLOUT_BQ_RE.sub(_callout, html)
    html = _CALLOUT_P_RE.sub(_callout, html)
    html = _CARD_LINK_RE.sub(
        lambda m: card_link_html(m.group(1).strip(), m.group(2).strip()), html)
    return html
