"""Section-level targeted edits.

Rewrite ONLY the sections a change touches; keep every other section verbatim.
This makes "targeted update" actually targeted — untouched content (and its
formatting) is preserved exactly, the diff is tight, and each LLM call sees one
small section instead of the whole card (more reliable on Haiku). Falls back to
a whole-card rewrite when the card has no heading structure to target.
"""

from __future__ import annotations

import re

from src.data.content_catalog.vectorizer import tokenize

from .models import ProposedUpdate
from .write_updates import write_updates

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_MIN_MATCH = 0.4


def write_targeted_updates(llm_client, pulled, plan, source, style_block) -> ProposedUpdate:
    sections = _split_sections(pulled.current_md)
    if sum(1 for s in sections if s["level"] > 0) < 2:
        return write_updates(llm_client, pulled, plan, source, style_block)  # no structure

    per, additions, unplaceable = _match(sections, plan.changes)
    # An update/remove whose section we can't locate must NOT be appended as a
    # new section (that would duplicate content, or "add" for a removal). Hand
    # the whole card to the holistic writer, which sees every change in context.
    if unplaceable:
        return write_updates(llm_client, pulled, plan, source, style_block)
    if not any(per) and not additions:
        return write_updates(llm_client, pulled, plan, source, style_block)

    src_text = _combined(source)
    parts = []
    for i, s in enumerate(sections):
        verbatim = "\n".join(s["lines"])
        if per[i]:
            parts.append(_rewrite_section(llm_client, verbatim, per[i], src_text, style_block) or verbatim)
        else:
            parts.append(verbatim)  # untouched section preserved exactly
    body = "\n".join(parts)
    for ch in additions:
        block = _new_section(llm_client, ch, src_text, style_block)
        if block:
            body += "\n\n" + block

    content = body.strip()
    if not content:
        return ProposedUpdate(ok=False, error="empty_content")
    return ProposedUpdate(ok=True, title=(pulled.title or "Untitled card").strip(), content_md=content)


# ── helpers ──

def _split_sections(md: str) -> list[dict]:
    out: list[dict] = []
    cur: dict | None = None
    in_fence = False
    for line in (md or "").split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        # A '#'-prefixed line INSIDE a fenced code block (e.g. a bash/python
        # comment) is not a heading — treating it as one splits the code block
        # and orphans the closing fence, corrupting the card on rewrite.
        m = None if in_fence else _HEADING.match(line)
        if m:
            if cur is not None:
                out.append(cur)
            cur = {"heading": m.group(2).strip(), "level": len(m.group(1)), "lines": [line]}
        elif cur is None:
            cur = {"heading": "", "level": 0, "lines": [line]}
        else:
            cur["lines"].append(line)
    if cur is not None:
        out.append(cur)
    return out


def _match(sections, changes):
    per = [[] for _ in sections]
    additions = []
    unplaceable = []
    for ch in changes:
        idx = _best_section(sections, ch.section)
        if idx is not None:
            per[idx].append(ch)
        elif (ch.type or "").strip().lower() == "add":
            additions.append(ch)  # genuine addition → new section
        else:
            # update/remove with no matching section: cannot be a new section.
            unplaceable.append(ch)
    return per, additions, unplaceable


def _best_section(sections, name) -> int | None:
    sn = set(tokenize(name))
    if not sn:
        return None
    nm = (name or "").strip().lower()
    best, best_score, best_ht = None, 0.0, 10 ** 9
    for i, s in enumerate(sections):
        if s["level"] == 0:
            continue
        ht = set(tokenize(s["heading"]))
        if not ht:
            continue
        if (s["heading"] or "").strip().lower() == nm:
            return i  # exact heading match wins outright
        score = len(sn & ht) / len(sn)
        # Strictly-higher score wins; on a TIE prefer the tighter heading (fewer
        # extra tokens) so a single shared keyword (e.g. "Policy") binds to the
        # most specific heading rather than whichever was scanned first.
        if score > best_score or (score == best_score and score > 0 and len(ht) < best_ht):
            best, best_score, best_ht = i, score, len(ht)
    return best if best_score >= _MIN_MATCH else None


def _render(changes) -> str:
    lines = []
    for c in changes:
        lines.append(f"- [{c.type}] {c.reason}".rstrip())
        if c.evidence:
            lines.append(f"  evidence: {c.evidence}")
    return "\n".join(lines)


def _combined(source) -> str:
    from .prompts import _combined_source
    return _combined_source(source)


def _rewrite_section(llm, section_md, changes, src_text, style) -> str:
    prompt = (
        "Revise ONLY this one section of a Guru card. Apply the listed changes using the "
        "SOURCE as the source of truth. KEEP the section heading and preserve everything the "
        "changes don't touch. Return ONLY the revised section markdown (heading + body), "
        "nothing else.\n"
        f"{style}\n"
        "=== CHANGES FOR THIS SECTION ===\n" + _render(changes) + "\n\n"
        "=== SOURCE (source of truth) ===\n" + src_text[:4000] + "\n\n"
        "=== CURRENT SECTION ===\n" + section_md + "\n"
    )
    return (llm.generate(prompt) or "").strip()


def _new_section(llm, change, src_text, style) -> str:
    prompt = (
        "Write a NEW Guru card section for this addition, grounded ONLY in the SOURCE. "
        "Return ONLY the new section markdown (a '## Heading' plus body), nothing else.\n"
        f"{style}\n"
        f"=== ADDITION ===\n[{change.type}] {change.section}: {change.reason}\n"
        f"evidence: {change.evidence}\n\n"
        "=== SOURCE ===\n" + src_text[:4000] + "\n"
    )
    return (llm.generate(prompt) or "").strip()
