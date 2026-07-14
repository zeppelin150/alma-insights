"""Shared LLM generate→validate→retry primitive (renn-calendar-kb-studio plan).

Every studio/KB generator (mermaid diagrams, quizzes, deck outlines, doc
styles, task briefs, KB card summaries) runs the same loop: call Haiku via the
existing task routing, strip code fences, run a DETERMINISTIC caller-supplied
validator, and on failure retry EXACTLY ONCE with the validator's errors
appended verbatim so a weak model can self-correct. Built once here so five
call sites don't hand-roll divergent copies (the cross-cutting review's
shared-primitive #1).

Contract notes (load-bearing):
- ``client is None`` → ``{"ok": False, "error": "no_llm_client"}`` BEFORE any
  side effect — callers must not have written rows yet (offline laptop turns
  into a clean tool error, not a half-created artifact).
- The validator is code, not a model: ``validator(text) -> (ok, errors, value)``.
- Runs fine inside the MCP subprocess (proven precedent:
  enablement_tools._index_content_impl calls build_client_for_task there).
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger("alma.llm_gen")

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.DOTALL)

# Background-LLM concurrency gate (cross-cutting shared primitive #6): at most
# ONE background Haiku CLI subprocess at a time on the M1/16GB target. Every
# background caller (task briefs, KB summarize, staleness re-summarize) holds
# this around its generate loop; interactive chat turns are NOT gated (worst
# case = 1 chat + 1 background CLI process, which the target handles).
background_gate = threading.BoundedSemaphore(1)


def strip_fences(text: str) -> str:
    """Return the contents of the FIRST fenced code block, else the whole text
    stripped. Handles the classic weak-model habit of wrapping output in
    ``` fences with prose around them."""
    if not text:
        return ""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def generate_validated(prompt: str, validator, *, task_type: str = "enablement_card_gen",
                       client=None, retries: int = 1) -> dict:
    """Generate → strip fences → validate → (retry once with errors) → degrade.

    ``validator(text) -> (ok: bool, errors: list[str], value)`` — value is what
    the caller stores on success (often the cleaned text itself).

    Returns ``{"ok": True, "value", "raw", "retries"}`` or
    ``{"ok": False, "error": "no_llm_client" | "llm_error" | "validation_failed",
       "errors": [...], "retries": n}``. Never raises for LLM/validation
    trouble; only a broken validator propagates.
    """
    if client is None:
        try:
            from src.gemini.client_factory import build_client_for_task
            client = build_client_for_task(task_type)
        except Exception as exc:  # noqa: BLE001 — routing trouble == no client
            logger.warning("llm_gen: client build failed: %s", exc)
            client = None
    if client is None:
        return {"ok": False, "error": "no_llm_client", "errors": [], "retries": 0}

    attempt_prompt = prompt
    last_errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            raw = client.generate(attempt_prompt) or ""
        except Exception as exc:  # noqa: BLE001 — CLI/network death mid-call
            logger.warning("llm_gen: generate failed (attempt %d): %s", attempt, exc)
            if attempt < retries:
                continue
            return {"ok": False, "error": "llm_error",
                    "errors": [str(exc)[:200]], "retries": attempt}
        cleaned = strip_fences(raw)
        ok, errors, value = validator(cleaned)
        if ok:
            return {"ok": True, "value": value, "raw": cleaned, "retries": attempt}
        last_errors = list(errors)
        if attempt < retries:
            attempt_prompt = (
                f"{prompt}\n\nYour previous output failed validation:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\nReturn ONLY the corrected output."
            )
    return {"ok": False, "error": "validation_failed",
            "errors": last_errors, "retries": retries}
