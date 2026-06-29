"""Stage 5 — write the revised card (LLM → TITLE/---/body markdown).

Reuses ``enablement_store._parse_card`` so the output format matches the rest of
the enablement card pipeline (the same parser the chat ``revise_draft`` uses).
"""

from __future__ import annotations

from typing import Any

from .models import ProposedUpdate, PulledCard, SourceBundle, UpdatePlan
from .prompts import build_write_prompt


def write_updates(llm_client: Any, pulled: PulledCard, plan: UpdatePlan,
                  source: SourceBundle, style_block: str) -> ProposedUpdate:
    """Produce the complete revised card markdown from the approved plan."""
    from src.data.enablement_store import _parse_card
    prompt = build_write_prompt(pulled, plan, source, style_block)
    text = llm_client.generate(prompt)
    title, content = _parse_card(text, pulled.title or "Untitled card")
    content = (content or "").strip()
    if not content:
        return ProposedUpdate(ok=False, error="empty_content")
    return ProposedUpdate(ok=True, title=(title or pulled.title).strip(), content_md=content)
