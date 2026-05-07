"""AI Reports — Conversational prompt authoring state machine (R4.1).

Backs the Manage-Prompts wizard (3.24.26 Updated_AI_Reports_Prompt_
Builder_Page.png). Walks the user through 4 steps to author a custom
prompt that the AI Reports page can run end-to-end:

    SCOPE       — what should this report cover? (TRC, payer, time…)
    QUESTIONS   — what specific questions should it answer?
    OUTPUT      — what sections / format should the report take?
    PREVIEW     — review the assembled prompt; user can refine.

Each step's transition is mediated by a small bot-style conversation. The
LLM call (Claude per task-routing) takes the user's reply + the current
authoring state and returns:

    {
      "bot_reply":   "..."  # what the user sees next in chat,
      "next_state":  "...", # optional override; defaults to next step,
      "scope":       {...}, # accumulator: payer, trc, date_range, etc.,
      "questions":   [...],
      "output_format": "...",
      "system_prompt": "...",
      "prompt_text":  "...",  # populated at PREVIEW step,
    }

The state machine is **deterministic** without an LLM (canned bot
replies advance the wizard) so unit tests don't need network access.
When `llm_callable` is provided, its response is merged with the canned
state — the LLM enriches but never blocks.

Reference: 2026-05-06 conversation, R4 spec.

Public API
----------
- `AuthoringState` enum
- `AuthoringSession(llm_callable=None)`
    - `bot_greeting()`     → str
    - `step(user_text)`    → dict (the LLM-merged state above)
    - `is_complete()`      → bool
    - `to_prompt_data()`   → dict (name/description/system_prompt/prompt_text)
    - `state`              → AuthoringState property
    - `prompt_preview`     → str property

Dependencies
------------
- stdlib only by default
- optional `llm_callable: Callable[[list[dict]], dict]` for Claude-backed
  authoring; tests inject stubs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger("alma.prompt_authoring")


# ──────────────────────────────────────────────────────────────────────
# State enum
# ──────────────────────────────────────────────────────────────────────

class AuthoringState(str, Enum):
    GREETING = "greeting"
    SCOPE = "scope"
    QUESTIONS = "questions"
    OUTPUT = "output"
    PREVIEW = "preview"
    DONE = "done"


_STATE_ORDER = (
    AuthoringState.GREETING,
    AuthoringState.SCOPE,
    AuthoringState.QUESTIONS,
    AuthoringState.OUTPUT,
    AuthoringState.PREVIEW,
    AuthoringState.DONE,
)


# ──────────────────────────────────────────────────────────────────────
# Canned bot prompts (used when LLM is absent / for tests)
# ──────────────────────────────────────────────────────────────────────

_CANNED_REPLIES: dict[AuthoringState, str] = {
    AuthoringState.SCOPE: (
        "Got it. What's the scope of this report — which TRCs, payers, providers, "
        "or time window matter? List specifics or say 'all' for unscoped."
    ),
    AuthoringState.QUESTIONS: (
        "Now the questions: what does the report need to answer? Three to five works well. "
        "(e.g. 'which TRCs spiked', 'what's driving CSAT drops')."
    ),
    AuthoringState.OUTPUT: (
        "Last step — what should the output look like? Default is the AlmaReport JSON "
        "schema with 5-10 findings. Want a different format or a specific section to highlight?"
    ),
    AuthoringState.PREVIEW: (
        "Here's the assembled prompt. Refine in the chat or save as-is."
    ),
    AuthoringState.DONE: (
        "Saved. The prompt is now available in the Analysis Canvas dropdown."
    ),
}

_GREETING = (
    "I'll help you build a custom report prompt. We'll cover scope, then questions, "
    "then output format. Ready? — describe the report you want in one sentence."
)


# ──────────────────────────────────────────────────────────────────────
# Session
# ──────────────────────────────────────────────────────────────────────

@dataclass
class AuthoringSession:
    """Stateful prompt-authoring session.

    Pass an `llm_callable(messages: list[dict]) -> dict` to enrich each
    step with Claude. Without it the session still advances using the
    canned replies — the resulting prompt is still usable, just less
    polished.
    """

    llm_callable: Callable[[list[dict]], dict] | None = None
    state: AuthoringState = AuthoringState.GREETING
    name: str = ""
    description: str = ""
    scope: dict = field(default_factory=dict)
    questions: list[str] = field(default_factory=list)
    output_format: str = ""
    system_prompt: str = ""
    prompt_preview: str = ""
    transcript: list[dict] = field(default_factory=list)

    # ── public ─────────────────────────────────────────────────────

    def bot_greeting(self) -> str:
        """Return the opening message and move out of GREETING state."""
        if self.state is AuthoringState.GREETING:
            self.state = AuthoringState.SCOPE
            self.transcript.append({"role": "assistant", "content": _GREETING})
        return _GREETING

    def step(self, user_text: str) -> dict:
        """Advance one step. Returns the merged state dict (see module doc)."""
        if self.state is AuthoringState.DONE:
            return self._snapshot()
        self.transcript.append({"role": "user", "content": user_text})
        self._absorb_user_text(user_text)

        canned_reply = _CANNED_REPLIES.get(self.state, "")
        merged = self._merge_with_llm(user_text, canned_reply)

        # Canned advance: move state forward one step unless LLM overrode
        next_state_override = merged.get("next_state")
        if next_state_override:
            self.state = AuthoringState(next_state_override)
        else:
            self.state = _next_state(self.state)
        # Apply LLM overrides over the absorbed-from-text values
        self._apply_overrides(merged)

        if self.state is AuthoringState.PREVIEW:
            self.prompt_preview = self._render_prompt()

        self.transcript.append({"role": "assistant", "content": merged.get("bot_reply", "")})
        return self._snapshot()

    def is_complete(self) -> bool:
        return self.state is AuthoringState.DONE

    def finalize(self, name: str, description: str = "") -> dict:
        """Mark the session DONE and return the prompt_library record."""
        self.name = name
        self.description = description
        if not self.prompt_preview:
            self.prompt_preview = self._render_prompt()
        self.state = AuthoringState.DONE
        return self.to_prompt_data()

    def to_prompt_data(self) -> dict:
        """Shape consumed by `db.save_prompt()` (prompt_library row).

        Returns ``{name, description, prompt_text, system_prompt}``.
        """
        return {
            "name": self.name or "Untitled custom prompt",
            "description": self.description or "Authored via the wizard",
            "prompt_text": self.prompt_preview or self._render_prompt(),
            "system_prompt": self.system_prompt or _DEFAULT_SYSTEM_PROMPT,
        }

    # ── internals ──────────────────────────────────────────────────

    def _absorb_user_text(self, text: str) -> None:
        """Capture the user's reply into the appropriate accumulator."""
        if self.state is AuthoringState.SCOPE:
            self.scope = self._parse_scope(text)
        elif self.state is AuthoringState.QUESTIONS:
            self.questions = _parse_questions(text)
        elif self.state is AuthoringState.OUTPUT:
            self.output_format = text.strip()

    def _merge_with_llm(self, user_text: str, canned_reply: str) -> dict:
        """Call the LLM hook if present and merge with canned defaults."""
        merged = {"bot_reply": canned_reply}
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
        """Let the LLM override accumulator fields if it returns them."""
        if "scope" in merged and isinstance(merged["scope"], dict):
            self.scope = merged["scope"]
        if "questions" in merged and isinstance(merged["questions"], list):
            self.questions = [str(q) for q in merged["questions"]]
        if "output_format" in merged and merged["output_format"]:
            self.output_format = str(merged["output_format"])
        if "system_prompt" in merged and merged["system_prompt"]:
            self.system_prompt = str(merged["system_prompt"])
        if "prompt_text" in merged and merged["prompt_text"]:
            self.prompt_preview = str(merged["prompt_text"])

    def _llm_payload(self, user_text: str) -> list[dict]:
        """Compact OpenAI/Claude-style messages list for the wizard LLM."""
        return [
            {"role": "system", "content": _AUTHORING_SYSTEM_PROMPT},
            {"role": "system",
             "content": f"Current state: {self.state.value}\n"
                        f"Scope: {json.dumps(self.scope)}\n"
                        f"Questions: {json.dumps(self.questions)}\n"
                        f"Output format so far: {self.output_format}"},
            {"role": "user", "content": user_text},
        ]

    def _snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "scope": dict(self.scope),
            "questions": list(self.questions),
            "output_format": self.output_format,
            "system_prompt": self.system_prompt,
            "prompt_preview": self.prompt_preview,
            "transcript": list(self.transcript),
        }

    def _render_prompt(self) -> str:
        """Assemble the final prompt text from the captured fields.

        Mirrors the structure of the canned templates in `config/prompts/`
        and embeds the AlmaReport JSON contract so the resulting prompt
        works with `report_parser.parse_report` out of the box.
        """
        from src.data.report_schema import schema_for_prompt

        scope_str = _format_scope(self.scope)
        questions_block = (
            "\n".join(f"- {q}" for q in self.questions)
            if self.questions else "- (no specific questions provided)"
        )
        output_block = self.output_format or (
            "Use the AlmaReport JSON schema below; produce 5-10 findings."
        )

        return (
            "You are a Support Analytics engine producing a custom report.\n"
            "Data is provided as pre-computed statistics. You do NOT load files or access URLs.\n\n"
            "TICKET DATA:\n{data_block}\n\n"
            "{temporal_context}\n\n"
            f"SCOPE:\n{scope_str}\n\n"
            "QUESTIONS THIS REPORT MUST ANSWER:\n"
            f"{questions_block}\n\n"
            "OUTPUT FORMAT:\n"
            f"{output_block}\n\n"
            "OUTPUT CONTRACT — emit EXACTLY ONE fenced JSON block matching the AlmaReport schema:\n\n"
            "```json\n"
            f"{schema_for_prompt()}\n"
            "```\n\n"
            "RULES:\n"
            "- 5-10 findings. Rank by impact descending.\n"
            "- Cite specific TRC codes and ticket counts; never invent numbers.\n"
            "- No commentary outside the fenced JSON block.\n"
        )

    @staticmethod
    def _parse_scope(text: str) -> dict:
        """Lightweight scope extraction — TRC, payer, date range hints."""
        scope: dict = {}
        if not text or text.strip().lower() in ("all", "none", ""):
            return scope
        lower = text.lower()
        if "payer" in lower or "insurance" in lower:
            scope["needs_payer"] = True
        if "trc" in lower or "category" in lower:
            scope["needs_trc"] = True
        if "provider" in lower:
            scope["needs_provider"] = True
        if "last" in lower and ("week" in lower or "month" in lower or "quarter" in lower):
            scope["recent_window"] = lower
        scope["raw"] = text.strip()
        return scope


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

_AUTHORING_SYSTEM_PROMPT = (
    "You are an interactive prompt-authoring assistant for Alma Insights' AI Reports. "
    "Help the user define scope, questions, and output format for a custom report. "
    "Reply in one paragraph (≤ 80 words) per turn. When you have enough info, return "
    "JSON with keys {bot_reply, next_state, scope, questions, output_format, system_prompt}. "
    "Otherwise just return {bot_reply}."
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a Support Analytics engine. Data is provided as pre-computed statistics. "
    "Emit JSON-fenced AlmaReport output only."
)


def _next_state(current: AuthoringState) -> AuthoringState:
    try:
        idx = _STATE_ORDER.index(current)
    except ValueError:
        return AuthoringState.SCOPE
    return _STATE_ORDER[min(idx + 1, len(_STATE_ORDER) - 1)]


def _parse_questions(text: str) -> list[str]:
    """Split a multi-line / bullet-point answer into a question list."""
    if not text:
        return []
    out: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*•").strip()
        if cleaned:
            out.append(cleaned)
    if not out and text.strip():
        # No bullets — treat each sentence as a question
        for sentence in text.split("?"):
            s = sentence.strip()
            if s:
                out.append(s + "?")
    return out


def _format_scope(scope: dict) -> str:
    if not scope:
        return "All TRCs, all payers, all providers, all dates."
    lines: list[str] = []
    if scope.get("needs_payer"):
        lines.append("- Surface insurance-payer breakdowns")
    if scope.get("needs_trc"):
        lines.append("- Group by TRC code / label")
    if scope.get("needs_provider"):
        lines.append("- Surface provider-level breakdowns")
    if scope.get("recent_window"):
        lines.append(f"- Time window: {scope['recent_window']}")
    if scope.get("raw") and not lines:
        lines.append(f"- {scope['raw']}")
    return "\n".join(lines) if lines else "All TRCs, all payers, all dates."
