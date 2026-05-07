"""AI Reports — Conversational prompt authoring (R4.1, rewritten 2026-05-07).

Drives the Manage-Prompts wizard. The 2026-05-07 rewrite fixes three
defects observed in user testing:

  A. Strict SCOPE → QUESTIONS → OUTPUT capture mis-bucketed free-form
     replies. The user's report description landed in SCOPE; their actual
     scope landed in QUESTIONS; their clarification landed in OUTPUT.
  B. The LLM hook returned ``next_state: "complete"`` (an undefined
     state name) and the wizard fell through to DONE, dropping any
     follow-up refinement.
  C. The LLM emitted a fenced ```json``` block; my JSON parser only
     handled bare JSON, so the entire raw structured payload landed in
     the user-visible chat as ``bot_reply``.

Fix shape:

  * **Intent accumulator** — a single `IntentBag` dataclass holds
    `report_topic`, `scope`, `questions`, `output_format`, and
    `custom_requirements`. The wizard never assigns one user turn to
    exactly one bucket — instead it appends the message to a transcript
    and lets the LLM enrich the intent across turns. Determinism is
    preserved without an LLM via the canned-reply path.
  * **State machine** — GREETING → INTENT (multi-turn) → PREVIEW →
    DONE (only via `finalize()`). Refinements after PREVIEW loop back
    through INTENT once and re-render.
  * **JSON-aware bot_reply** — fenced ```json``` blocks are stripped
    before the bot reply is shown to the user; the structured payload
    (scope/questions/output_format/etc.) is consumed silently.
  * **next_state tolerance** — "complete", "done", "finished", "ready",
    "save" all map to PREVIEW. The wizard never auto-transitions to
    DONE; only `finalize()` (called from the Save button) does.

Public API
----------
- `AuthoringState` enum: GREETING / INTENT / PREVIEW / DONE
- `AuthoringSession(llm_callable=None)`
    - `bot_greeting() -> str`
    - `step(user_text) -> dict`
    - `is_complete() -> bool`
    - `finalize(name, description="") -> dict`
    - `to_prompt_data() -> dict`

Dependencies
------------
- stdlib only by default; optional `llm_callable: Callable[[list[dict]], dict]`
  for Claude/Gemini-backed authoring (tests inject stubs).
- src.data.report_schema.schema_for_prompt (used in prompt template)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger("alma.prompt_authoring")


# ──────────────────────────────────────────────────────────────────────
# State enum
# ──────────────────────────────────────────────────────────────────────

class AuthoringState(str, Enum):
    GREETING = "greeting"
    INTENT = "intent"      # multi-turn capture; refinement loops here
    PREVIEW = "preview"
    DONE = "done"


# Aliases the LLM may emit instead of canonical state names.
_STATE_ALIASES: dict[str, AuthoringState] = {
    "scope":     AuthoringState.INTENT,
    "questions": AuthoringState.INTENT,
    "output":    AuthoringState.INTENT,
    "intent":    AuthoringState.INTENT,
    "preview":   AuthoringState.PREVIEW,
    "complete":  AuthoringState.PREVIEW,   # never auto-DONE
    "done":      AuthoringState.PREVIEW,
    "finished":  AuthoringState.PREVIEW,
    "ready":     AuthoringState.PREVIEW,
    "save":      AuthoringState.PREVIEW,
    "greeting":  AuthoringState.GREETING,
}


# ──────────────────────────────────────────────────────────────────────
# Intent accumulator
# ──────────────────────────────────────────────────────────────────────

@dataclass
class IntentBag:
    """Free-form intent capture. Each field is a list of fragments —
    later renders concatenate them. Keeps the wizard from mis-bucketing
    multi-topic replies into a single rigid slot."""

    report_topic: str = ""           # one-line headline ("custom bug report")
    scope_fragments: list[str] = field(default_factory=list)
    question_fragments: list[str] = field(default_factory=list)
    output_fragments: list[str] = field(default_factory=list)
    custom_requirements: list[str] = field(default_factory=list)

    def absorb(self, text: str, *, after_preview: bool) -> None:
        """Append a user turn to the appropriate bucket via shallow keyword
        heuristics. After preview, every refinement goes into custom_requirements
        so the user can iterate without losing prior structure."""
        text = (text or "").strip()
        if not text:
            return
        if after_preview:
            self.custom_requirements.append(text)
            return
        if not self.report_topic:
            # First substantive user message becomes the headline topic.
            self.report_topic = _truncate(text, 200)
            return
        lower = text.lower()
        looks_scope = any(kw in lower for kw in (
            "trc", "payer", "provider", "all ", "last ", "lookback", "two week",
            "month", "quarter", "ticket", "client", "customer",
        ))
        looks_question = any(kw in lower for kw in (
            "how many", "what's", "which ", "why ", "where ", "what are",
            "answer", "question", "metric", "ratio",
        ))
        looks_output = any(kw in lower for kw in (
            "output", "section", "format", "csat", "sentiment", "table",
            "chart", "ranking", "summary", "audit", "include",
        ))
        if looks_scope:
            self.scope_fragments.append(text)
        if looks_question:
            self.question_fragments.append(text)
        if looks_output:
            self.output_fragments.append(text)
        # If no keyword matched, attach to whichever bucket is empty first
        if not (looks_scope or looks_question or looks_output):
            for bucket in (self.scope_fragments, self.question_fragments,
                           self.output_fragments):
                if not bucket:
                    bucket.append(text)
                    return
            self.custom_requirements.append(text)


# ──────────────────────────────────────────────────────────────────────
# Canned bot prompts
# ──────────────────────────────────────────────────────────────────────

_GREETING = (
    "I'll help you build a custom report prompt. Describe in one sentence what "
    "you want the report to do — we'll refine the scope, questions, and output "
    "shape together."
)

_INTENT_FOLLOWUPS = (
    "Got it. Anything else to clarify on scope (TRCs, payers, providers, time "
    "window) or what specific questions the report should answer?",
    "Noted. What sections should the output have — any specific metrics, "
    "tables, or aggregations you want?",
    "I'll work that in. Anything else, or shall we preview the prompt?",
)

_PREVIEW_REPLY = (
    "Here's the assembled prompt below. Tell me what to refine or hit "
    "**Save as new prompt** when you're happy with it."
)

_REFINEMENT_REPLY = (
    "Updated. Take another look at the preview — refine again or save."
)


# ──────────────────────────────────────────────────────────────────────
# Session
# ──────────────────────────────────────────────────────────────────────

@dataclass
class AuthoringSession:
    """Stateful prompt-authoring session.

    Pass an `llm_callable(messages: list[dict]) -> dict` to enrich each
    step with Claude/Gemini. Without it the session still advances using
    canned replies — the resulting prompt is still usable, just less polished.
    """

    llm_callable: Callable[[list[dict]], dict] | None = None
    state: AuthoringState = AuthoringState.GREETING
    name: str = ""
    description: str = ""
    intent: IntentBag = field(default_factory=IntentBag)
    system_prompt: str = ""
    prompt_preview: str = ""
    transcript: list[dict] = field(default_factory=list)
    _intent_turn_count: int = 0   # tracks how many INTENT replies we've given

    # ── public ─────────────────────────────────────────────────────

    def bot_greeting(self) -> str:
        if self.state is AuthoringState.GREETING:
            self.state = AuthoringState.INTENT
            self.transcript.append({"role": "assistant", "content": _GREETING})
        return _GREETING

    def step(self, user_text: str) -> dict:
        """Advance one turn. Returns a snapshot dict; never raises."""
        if self.state is AuthoringState.DONE:
            return self._snapshot()

        self.transcript.append({"role": "user", "content": user_text})
        after_preview = self.state is AuthoringState.PREVIEW
        self.intent.absorb(user_text, after_preview=after_preview)

        merged = self._merge_with_llm(user_text)

        # Decide next state. Default: stay in INTENT for the first 2-3 turns,
        # then PREVIEW. Refinement after PREVIEW stays in PREVIEW.
        next_state_override = _coerce_state(merged.get("next_state"))
        if next_state_override is not None:
            self.state = next_state_override
        elif after_preview:
            self.state = AuthoringState.PREVIEW   # stay
        else:
            self._intent_turn_count += 1
            self.state = (AuthoringState.PREVIEW
                          if self._intent_turn_count >= 3
                          else AuthoringState.INTENT)

        self._apply_overrides(merged)

        # Always re-render preview when in PREVIEW state (initial + refinement)
        if self.state is AuthoringState.PREVIEW:
            self.prompt_preview = self._render_prompt()

        bot_reply = _clean_bot_reply(
            merged.get("bot_reply") or self._canned_reply(after_preview),
        )
        self.transcript.append({"role": "assistant", "content": bot_reply})
        return self._snapshot()

    def is_complete(self) -> bool:
        return self.state is AuthoringState.DONE

    def finalize(self, name: str, description: str = "") -> dict:
        """Mark DONE and return the prompt_library record. Save-button path."""
        self.name = name
        self.description = description
        if not self.prompt_preview:
            self.prompt_preview = self._render_prompt()
        self.state = AuthoringState.DONE
        return self.to_prompt_data()

    def to_prompt_data(self) -> dict:
        return {
            "name": self.name or "Untitled custom prompt",
            "description": self.description or "Authored via the wizard",
            "prompt_text": self.prompt_preview or self._render_prompt(),
            "system_prompt": self.system_prompt or _DEFAULT_SYSTEM_PROMPT,
        }

    # ── internals ──────────────────────────────────────────────────

    def _canned_reply(self, after_preview: bool) -> str:
        if after_preview:
            return _REFINEMENT_REPLY
        if self.state is AuthoringState.PREVIEW:
            return _PREVIEW_REPLY
        idx = min(self._intent_turn_count, len(_INTENT_FOLLOWUPS) - 1)
        return _INTENT_FOLLOWUPS[idx]

    def _merge_with_llm(self, user_text: str) -> dict:
        merged: dict = {}
        if self.llm_callable is None:
            return merged
        try:
            resp = self.llm_callable(self._llm_payload(user_text))
        except Exception as exc:
            logger.debug("authoring LLM call failed: %s", exc)
            return merged
        if isinstance(resp, dict):
            merged.update(resp)
        return merged

    def _apply_overrides(self, merged: dict) -> None:
        """Let the LLM enrich the intent without overwriting prior state."""
        if "scope" in merged:
            v = merged["scope"]
            if isinstance(v, str) and v:
                self.intent.scope_fragments.append(v)
            elif isinstance(v, list):
                self.intent.scope_fragments.extend(str(x) for x in v if x)
        if "questions" in merged and isinstance(merged["questions"], list):
            self.intent.question_fragments.extend(
                str(q) for q in merged["questions"] if q
            )
        if "output_format" in merged and merged["output_format"]:
            self.intent.output_fragments.append(str(merged["output_format"]))
        if "custom_requirements" in merged:
            v = merged["custom_requirements"]
            items = v if isinstance(v, list) else [v]
            self.intent.custom_requirements.extend(str(x) for x in items if x)
        if "system_prompt" in merged and merged["system_prompt"]:
            self.system_prompt = str(merged["system_prompt"])
        if "report_topic" in merged and merged["report_topic"]:
            self.intent.report_topic = str(merged["report_topic"])

    def _llm_payload(self, user_text: str) -> list[dict]:
        intent_summary = (
            f"Topic: {self.intent.report_topic or '(empty)'}\n"
            f"Scope: {self.intent.scope_fragments}\n"
            f"Questions: {self.intent.question_fragments}\n"
            f"Output: {self.intent.output_fragments}\n"
            f"Custom: {self.intent.custom_requirements}"
        )
        return [
            {"role": "system", "content": _AUTHORING_SYSTEM_PROMPT},
            {"role": "system",
             "content": f"State: {self.state.value} (turn "
                        f"{self._intent_turn_count})\n\n{intent_summary}"},
            {"role": "user", "content": user_text},
        ]

    def _snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "intent": {
                "report_topic": self.intent.report_topic,
                "scope_fragments": list(self.intent.scope_fragments),
                "question_fragments": list(self.intent.question_fragments),
                "output_fragments": list(self.intent.output_fragments),
                "custom_requirements": list(self.intent.custom_requirements),
            },
            "system_prompt": self.system_prompt,
            "prompt_preview": self.prompt_preview,
            "transcript": list(self.transcript),
        }

    def _render_prompt(self) -> str:
        """Assemble the AlmaReport-shaped prompt from the intent bag."""
        from src.data.report_schema import schema_for_prompt

        topic = self.intent.report_topic or "Custom report"
        scope_block = _bulletize(self.intent.scope_fragments) or (
            "All TRCs, all payers, all providers, full date range."
        )
        question_block = _bulletize(self.intent.question_fragments) or (
            "Surface the dominant themes and rank by impact."
        )
        output_block = _stringify_output(self.intent.output_fragments) or (
            "Use the AlmaReport JSON schema below; produce 5-10 findings."
        )
        custom_block = _bulletize(self.intent.custom_requirements)
        custom_section = (
            f"\nADDITIONAL REQUIREMENTS (from refinement):\n{custom_block}\n"
            if custom_block else ""
        )

        return (
            f"You are a Support Analytics engine producing a custom report:\n"
            f"  Topic: {topic}\n\n"
            "Data is provided as pre-computed statistics. You do NOT load files or access URLs.\n\n"
            "STRICT TEXT-OUTPUT MODE:\n"
            "- Do NOT call any tools. Do NOT attempt write_file, read_file, edit, "
            "run_shell_command, web_fetch, web_search, or any other tool.\n"
            "- Do NOT delegate to sub-agents.\n"
            "- The JSON must appear INLINE in your response, wrapped in a fenced code block.\n"
            "- The ONLY acceptable output is one fenced JSON block. No preamble, no postamble.\n\n"
            "TICKET DATA:\n{data_block}\n\n"
            "{temporal_context}\n\n"
            f"SCOPE:\n{scope_block}\n\n"
            "QUESTIONS THIS REPORT MUST ANSWER:\n"
            f"{question_block}\n\n"
            "OUTPUT FORMAT:\n"
            f"{output_block}\n"
            f"{custom_section}\n"
            "OUTPUT CONTRACT — emit EXACTLY ONE fenced JSON block matching the AlmaReport schema:\n\n"
            "```json\n"
            f"{schema_for_prompt()}\n"
            "```\n\n"
            "RULES:\n"
            "- 5-10 findings. Rank by impact descending.\n"
            "- Cite specific TRC codes and ticket counts; never invent numbers.\n"
            "- No commentary outside the fenced JSON block.\n"
        )


# ──────────────────────────────────────────────────────────────────────
# Module-level helpers
# ──────────────────────────────────────────────────────────────────────

_AUTHORING_SYSTEM_PROMPT = (
    "You are an interactive prompt-authoring assistant for Alma Insights' "
    "AI Reports. Help the user refine a report prompt across scope, "
    "questions, and output format. ALWAYS reply conversationally in "
    "≤ 80 words. NEVER embed JSON in your reply text — the wizard handles "
    "structured fields separately. If you want to enrich the intent, "
    "return a JSON object with optional keys "
    "{bot_reply, next_state, scope, questions, output_format, "
    "custom_requirements, system_prompt, report_topic} as an OPTIONAL "
    "second message; otherwise just speak naturally."
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a Support Analytics engine. Data is provided as pre-computed "
    "statistics. Emit JSON-fenced AlmaReport output only."
)


# Strip any fenced code block (```...```) from bot_reply text so structured
# payloads never leak into the user-visible chat.
_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)


def _clean_bot_reply(text: str | None) -> str:
    """Strip fenced code blocks + collapse whitespace. The LLM sometimes
    embeds the structured payload in its reply; the wizard absorbs that
    payload separately, so we never want it in the chat log."""
    if not text:
        return ""
    cleaned = _FENCE_RE.sub("", text).strip()
    # Collapse runs of blank lines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _coerce_state(value) -> AuthoringState | None:
    """Tolerant state-name parser. Returns None when the value is empty
    or unrecognised — caller falls back to the default advance."""
    if not value:
        return None
    if isinstance(value, AuthoringState):
        return value
    key = str(value).strip().lower()
    if not key:
        return None
    return _STATE_ALIASES.get(key)


def _bulletize(items: list[str]) -> str:
    """Turn a fragment list into a markdown bullet block."""
    cleaned = [s.strip() for s in items if s and s.strip()]
    if not cleaned:
        return ""
    return "\n".join(f"- {s}" for s in cleaned)


def _stringify_output(items: list[str]) -> str:
    """Output format gets concatenated as paragraphs (single block)."""
    cleaned = [s.strip() for s in items if s and s.strip()]
    if not cleaned:
        return ""
    return "\n\n".join(cleaned)


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
