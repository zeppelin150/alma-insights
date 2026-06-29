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
_MIN_MATCH = 0.4


def write_targeted_updates(llm_client, pulled, plan, source, style_block) -> ProposedUpdate:
    sections = _split_sections(pulled.current_md)
    if sum(1 for s in sections if s["level"] > 0) < 2:
        return write_updates(llm_client, pulled, plan, source, style_block)  # no structure

    per, additions = _match(sections, plan.changes)
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
    for line in (md or "").split("\n"):
        m = _HEADING.match(line)
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
    for ch in changes:
        idx = _best_section(sections, ch.section)
        if idx is None:
            additions.append(ch)
        else:
            per[idx].append(ch)
    return per, additions


def _best_section(sections, name) -> int | None:
    sn = set(tokenize(name))
    if not sn:
        return None
    best, best_score = None, 0.0
    for i, s in enumerate(sections):
        if s["level"] == 0:
            continue
        ht = set(tokenize(s["heading"]))
        if ht:
            score = len(sn & ht) / len(sn)
            if score > best_score:
                best, best_score = i, score
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
