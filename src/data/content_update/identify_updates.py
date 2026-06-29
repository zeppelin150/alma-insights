"""Stage 4 — identify what needs updating (LLM → validated JSON plan)."""

from __future__ import annotations

from typing import Any

from .llm_json import call_llm_json
from .models import Change, PulledCard, SourceBundle, UpdatePlan
from .prompts import build_identify_prompt

_SYSTEM = "You are a precise knowledge-base editor. Output only what is asked."


def identify_updates(llm_client: Any, source: SourceBundle, pulled: PulledCard) -> UpdatePlan:
    """Ask the model what must change; return a structured, validated plan."""
    prompt = build_identify_prompt(source, pulled)
    res = call_llm_json(llm_client, _SYSTEM, prompt, _validate)
    if not res["ok"]:
        return UpdatePlan(ok=False, error=res["error"])
    data = res["data"]
    changes = [_to_change(c) for c in data.get("changes", []) if isinstance(c, dict)]
    return UpdatePlan(ok=True, changes=changes, summary=str(data.get("summary", "")))


def _validate(data: dict) -> str:
    if "changes" not in data:
        return "missing 'changes' key"
    if not isinstance(data["changes"], list):
        return "'changes' must be a list"
    return ""


def _to_change(c: dict) -> Change:
    return Change(
        type=str(c.get("type", "update")),
        section=str(c.get("section", "")),
        reason=str(c.get("reason", "")),
        evidence=str(c.get("evidence", "")),
    )
