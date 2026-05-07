"""Unit tests — src/data/prompt_authoring.py (R4.4).

Coverage:
  * canned-mode: SCOPE → QUESTIONS → OUTPUT → PREVIEW state advance
  * scope/questions/output_format absorption from user text
  * LLM hook overrides accumulator fields
  * LLM hook crash → falls back to canned reply, never raises
  * finalize() returns prompt_library record shape
  * _render_prompt embeds AlmaReport schema
"""
from __future__ import annotations

import pytest

from src.data.prompt_authoring import (
    AuthoringSession, AuthoringState,
    _format_scope, _next_state, _parse_questions,
)


# ──────────────────────────────────────────────────────────────────────
# Basic state machine
# ──────────────────────────────────────────────────────────────────────

class TestStateMachine:
    def test_greeting_advances_to_scope(self):
        s = AuthoringSession()
        s.bot_greeting()
        assert s.state is AuthoringState.SCOPE

    def test_full_flow_lands_at_preview(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("only billing TRCs and Cigna payer")
        assert s.state is AuthoringState.QUESTIONS
        s.step("- which CSAT cohorts dropped\n- top friction drivers")
        assert s.state is AuthoringState.OUTPUT
        s.step("Use 6 findings, include trend points")
        assert s.state is AuthoringState.PREVIEW
        assert s.prompt_preview != ""
        assert "AlmaReport" in s.prompt_preview

    def test_scope_absorbed(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("show me payers and TRCs over the last quarter")
        assert s.scope.get("needs_payer") is True
        assert s.scope.get("needs_trc") is True
        assert "quarter" in s.scope.get("recent_window", "")

    def test_questions_parsed_from_bullets(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("scope")
        s.step("- Q1\n- Q2\n* Q3")
        assert s.questions == ["Q1", "Q2", "Q3"]

    def test_questions_parsed_from_sentences(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("scope")
        s.step("Which TRCs spiked? What's the CSAT drop?")
        # falls back to sentence split
        assert any("TRCs spiked" in q for q in s.questions)

    def test_output_format_absorbed(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("scope")
        s.step("questions")
        s.step("Want trend chart, sentiment table, top 5 anomalies")
        assert "trend chart" in s.output_format

    def test_already_done_returns_snapshot(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.state = AuthoringState.DONE
        snap = s.step("anything")
        assert snap["state"] == AuthoringState.DONE.value


# ──────────────────────────────────────────────────────────────────────
# LLM hook
# ──────────────────────────────────────────────────────────────────────

class TestLlmHook:
    def test_hook_overrides_accumulator(self):
        called: list[list[dict]] = []
        def hook(messages):
            called.append(messages)
            return {
                "bot_reply": "OK",
                "scope": {"forced": True},
                "questions": ["forced q"],
            }
        s = AuthoringSession(llm_callable=hook)
        s.bot_greeting()
        s.step("scope text")
        assert s.scope == {"forced": True}
        assert s.questions == ["forced q"]
        assert called  # hook ran

    def test_hook_crash_falls_back_silently(self):
        def hook(_messages):
            raise RuntimeError("simulated network error")
        s = AuthoringSession(llm_callable=hook)
        s.bot_greeting()
        result = s.step("scope text")  # must not raise
        assert "state" in result
        assert s.state is AuthoringState.QUESTIONS

    def test_hook_returns_next_state_override(self):
        def hook(_msgs):
            return {"next_state": AuthoringState.PREVIEW.value, "bot_reply": "skip ahead"}
        s = AuthoringSession(llm_callable=hook)
        s.bot_greeting()
        s.step("scope")
        assert s.state is AuthoringState.PREVIEW


# ──────────────────────────────────────────────────────────────────────
# Finalize + prompt rendering
# ──────────────────────────────────────────────────────────────────────

class TestFinalize:
    def _full_session(self) -> AuthoringSession:
        s = AuthoringSession()
        s.bot_greeting()
        s.step("scope all")
        s.step("- q1")
        s.step("Use AlmaReport schema")
        return s

    def test_finalize_returns_prompt_record(self):
        s = self._full_session()
        rec = s.finalize("Custom Foo", "describes Foo")
        assert rec["name"] == "Custom Foo"
        assert rec["description"] == "describes Foo"
        assert "prompt_text" in rec
        assert "system_prompt" in rec
        assert "AlmaReport" in rec["prompt_text"]
        assert s.is_complete()

    def test_to_prompt_data_default_name(self):
        s = AuthoringSession()
        s.bot_greeting()
        rec = s.to_prompt_data()
        assert rec["name"] == "Untitled custom prompt"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_next_state_at_terminal(self):
        assert _next_state(AuthoringState.DONE) is AuthoringState.DONE

    def test_parse_questions_bullets(self):
        assert _parse_questions("- a\n- b") == ["a", "b"]

    def test_parse_questions_empty(self):
        assert _parse_questions("") == []

    def test_format_scope_default(self):
        assert "All TRCs" in _format_scope({})

    def test_format_scope_with_flags(self):
        text = _format_scope({"needs_payer": True, "needs_trc": True})
        assert "payer" in text.lower()
        assert "trc" in text.lower()
