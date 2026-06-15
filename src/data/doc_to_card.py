"""Deterministic document → Guru-card conversion (pure Python, no LLM).

Turning a document's text into a structured card is a text-transformation
job, not a reasoning job — so it's the DEFAULT for uploads/imports: fast,
free, offline, and predictable. An LLM only adds value for *summarising*
or *rewriting* a long doc for an audience, which is an explicit, opt-in
"polish with AI" action (Renn's revise tool), not something to fire on
every upload.

`card_from_document(name, text) -> (title, markdown)`:
  - title from the filename (extension stripped, separators → spaces),
  - a lead paragraph as the summary,
  - the first rollout/effective-date line surfaced as a `> [!NOTE]`
    callout (which renders as a native Guru callout), and
  - the rest of the document, lightly structured into markdown
    (headings / lists preserved or inferred).
Faithful to the source — it never invents content.
"""

from __future__ import annotations

import re

# A line that reads like a rollout / effective date → surfaced as a callout.
_DATE_HINT = re.compile(
    r"\b(?:rollout|effective|launch|go[- ]?live|deadline|due|live on|starts?|ships?)\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}",
    re.IGNORECASE,
)
# Lines that are already markdown structure — passed through untouched.
_MD_LINE = re.compile(r"^\s*(?:#{1,6}\s|>\s|\d+[.)]\s|[-*+]\s|```|\|)")
_UNICODE_BULLET = re.compile(r"^\s*[•·▪◦‣]\s+(.*)")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)")


def clean_name(name: str) -> str:
    base = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", name or "Document")
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", base)).strip() or "Document"


def title_from_name(name: str) -> str:
    return f"{clean_name(name)} — Enablement Guide"


def find_date_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines()
                 if _DATE_HINT.search(ln)), "")


def _structure_body(text: str) -> str:
    """Light, faithful markdown structuring of plain document text: keep any
    existing markdown, normalise unicode bullets / numbered lists, and infer
    headings only for short ALL-CAPS or "Section:" lines (conservative — when
    unsure it stays a paragraph)."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            out.append("")
            continue
        if _MD_LINE.match(line):              # already markdown
            out.append(line)
            continue
        m = _UNICODE_BULLET.match(line)
        if m:
            out.append(f"- {m.group(1).strip()}")
            continue
        m = _NUMBERED.match(line)
        if m:
            out.append(f"{m.group(1)}. {m.group(2).strip()}")
            continue
        # Heading only when it's clearly a label, not a sentence.
        if (len(s) <= 56 and len(s.split()) <= 7
                and (s.isupper() or s.endswith(":"))
                and not s.endswith(".")):
            out.append(f"## {s.rstrip(':')}")
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def card_from_document(name: str, text: str) -> tuple[str, str]:
    """Deterministically build (title, card-markdown) from a document."""
    title = title_from_name(name)
    text = (text or "").strip()
    if not text:
        return title, (
            f"This card was created from **{clean_name(name)}**, but no readable "
            "text was found — paste the content here, or connect Google Drive so "
            "the document can be extracted."
        )

    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    lead = paras[0] if paras else text[:400]
    callout = find_date_line(text)

    parts = [lead]
    if callout and callout not in lead:
        parts += ["", "> [!NOTE]", f"> {callout}"]
    rest = [p for p in paras[1:] if p.strip() != callout]
    if rest:
        parts += ["", _structure_body("\n\n".join(rest))]
    return title, "\n".join(parts).strip()
