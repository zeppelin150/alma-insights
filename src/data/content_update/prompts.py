"""Prompt assembly for the two reasoning stages.

Templates live in ``config/prompts/`` (inline fallbacks below keep the pipeline
working in tests without the files). Placeholders use ``<<token>>`` rather than
str.format so the JSON braces inside the identify template stay literal.
"""

from __future__ import annotations

from pathlib import Path

from .models import PulledCard, SourceBundle, UpdatePlan

_PROMPT_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"
_MAX_CHARS = 12000

_IDENTIFY_FALLBACK = """You are a knowledge-base editor for a healthcare \
revenue-cycle-management (RCM) support team. Compare the NEW SOURCE against the \
EXISTING GURU CARD and decide what in the card must change so it matches the new \
source. Use ONLY the provided material — never invent facts.

Return ONLY a JSON object, fenced in ```json, with this exact shape:
{
  "summary": "<one sentence: what changed and why>",
  "changes": [
    {"type": "add|update|remove", "section": "<short heading or location>", \
"reason": "<why>", "evidence": "<short quote from the NEW SOURCE>"}
  ]
}
If nothing needs to change, return an empty "changes" list.

=== NEW SOURCE: <<source_title>> ===
<<source_text>>

=== REFERENCE MATERIAL (authoritative point-of-truth) ===
<<references>>

=== EXISTING GURU CARD: <<card_title>> ===
<<card_md>>
"""

_WRITE_FALLBACK = """You are revising a Guru knowledge-base card for a healthcare \
RCM support team. Apply the APPROVED CHANGES to the EXISTING CARD using the NEW \
SOURCE / REFERENCE as the source of truth. Preserve everything still correct; \
change only what the plan calls for. Do not invent facts beyond the material.

Return the COMPLETE revised card in this EXACT format and nothing else:
TITLE: <card title>
---
<card body in Markdown>
<<style_guide>>
=== APPROVED CHANGES ===
<<changes>>

=== NEW SOURCE / REFERENCE (source of truth) ===
<<source_text>>

=== EXISTING CARD ===
TITLE: <<card_title>>
---
<<card_md>>
"""


def build_identify_prompt(source: SourceBundle, pulled: PulledCard) -> str:
    """Prompt for stage 4 (identify what needs updating)."""
    return _fill(_load("enablement_identify_updates.txt", _IDENTIFY_FALLBACK), {
        "source_title": source.primary.title if source.primary else "",
        "source_text": _clip(source.primary.text if source.primary else ""),
        "references": _render_refs(source),
        "card_title": pulled.title,
        "card_md": _clip(pulled.current_md),
    })


def build_write_prompt(pulled: PulledCard, plan: UpdatePlan,
                       source: SourceBundle, style_guide: str) -> str:
    """Prompt for stage 5 (write the revised card)."""
    return _fill(_load("enablement_write_update.txt", _WRITE_FALLBACK), {
        "style_guide": style_guide or "",
        "changes": _render_changes(plan),
        "source_text": _clip(_combined_source(source)),
        "card_title": pulled.title,
        "card_md": _clip(pulled.current_md),
    })


def _load(filename: str, fallback: str) -> str:
    path = _PROMPT_DIR / filename
    try:
        return path.read_text(encoding="utf-8") if path.exists() else fallback
    except OSError:
        return fallback


def _fill(template: str, mapping: dict) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace(f"<<{key}>>", value)
    return out


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= _MAX_CHARS else text[:_MAX_CHARS] + "\n…[truncated]…"


def _render_refs(source: SourceBundle) -> str:
    if not source.references:
        return "(none provided)"
    parts = [f"--- {d.title} ---\n{_clip(d.text)}" for d in source.references]
    return "\n\n".join(parts)


def _combined_source(source: SourceBundle) -> str:
    blocks = []
    if source.primary:
        blocks.append(f"--- {source.primary.title} ---\n{source.primary.text}")
    blocks.extend(f"--- {d.title} ---\n{d.text}" for d in source.references)
    return "\n\n".join(blocks)


def _render_changes(plan: UpdatePlan) -> str:
    if not plan.changes:
        return "(no specific changes listed — refresh the card against the source)"
    lines = []
    for i, c in enumerate(plan.changes, 1):
        lines.append(f"{i}. [{c.type}] {c.section}: {c.reason}".rstrip())
        if c.evidence:
            lines.append(f"   evidence: {c.evidence}")
    return "\n".join(lines)
