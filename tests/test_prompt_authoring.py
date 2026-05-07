"""Unit tests — src/data/prompt_authoring.py (R4.4, rewritten 2026-05-07).

Coverage:
  * GREETING → INTENT advance on bot_greeting()
  * Multi-turn intent capture: 3 user replies before PREVIEW
  * `next_state: "complete"` from LLM is mapped to PREVIEW (never DONE)
  * Refinement after PREVIEW: stays in PREVIEW + appends to custom_requirements
  * `_clean_bot_reply` strips fenced ```json``` blocks
  * `_coerce_state` tolerates aliases (complete/done/finished/ready)
  * IntentBag.absorb buckets free-form replies via keyword heuristics
  * finalize() returns prompt_library record + sets DONE
  * is_complete() only true after explicit finalize()
  * Render embeds AlmaReport schema + custom_requirements
"""
from __future__ import annotations

import pytest

from src.data.prompt_authoring import (
    AuthoringSession, AuthoringState, IntentBag,
    _bulletize, _clean_bot_reply, _coerce_state, _stringify_output, _truncate,
)


# ──────────────────────────────────────────────────────────────────────
# State machine — happy path
# ──────────────────────────────────────────────────────────────────────

class TestStateMachine:
    def test_greeting_advances_to_intent(self):
        s = AuthoringSession()
        s.bot_greeting()
        assert s.state is AuthoringState.INTENT

    def test_three_turns_lands_in_preview(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("Custom bug report")
        s.step("All TRCs, all payers, last 14 days")
        s.step("Aggregate counts of bugs per area + CSAT averages")
        assert s.state is AuthoringState.PREVIEW
        assert s.prompt_preview != ""
        assert "AlmaReport" in s.prompt_preview

    def test_finalize_required_for_done(self):
        s = AuthoringSession()
        s.bot_greeting()
        for _ in range(5):
            s.step("more")
        # Even with many turns, never auto-DONE
        assert not s.is_complete()
        s.finalize("test", "")
        assert s.is_complete()
        assert s.state is AuthoringState.DONE


# ──────────────────────────────────────────────────────────────────────
# Refinement loop — the user-reported bug
# ──────────────────────────────────────────────────────────────────────

class TestRefinement:
    def test_refinement_after_preview_stays_in_preview(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("Custom bug report")
        s.step("All TRCs, last two weeks")
        s.step("Bugs + platform stability + confidence audit")
        assert s.state is AuthoringState.PREVIEW
        # User's follow-up refinement
        s.step(
            "Add aggregate counts per impacted area, CSAT and customer "
            "sentiment averages per group"
        )
        assert s.state is AuthoringState.PREVIEW, "must stay in PREVIEW"
        assert any(
            "aggregate counts" in r.lower()
            for r in s.intent.custom_requirements
        )

    def test_refinement_appears_in_rendered_prompt(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("Custom bug report scanning tickets for software bugs")
        s.step("All TRCs, all payers, two-week lookback")
        s.step("Highlight bugs + platform stability with confidence audits")
        s.step("Add CSAT and sentiment averages per group")
        assert "CSAT" in s.prompt_preview or "sentiment" in s.prompt_preview.lower()
        assert "ADDITIONAL REQUIREMENTS" in s.prompt_preview


# ──────────────────────────────────────────────────────────────────────
# LLM-emitted next_state aliases
# ──────────────────────────────────────────────────────────────────────

class TestStateAliases:
    @pytest.mark.parametrize("alias", [
        "complete", "done", "finished", "ready", "save",
    ])
    def test_aliases_map_to_preview_not_done(self, alias):
        def hook(_msgs):
            return {"bot_reply": "ok", "next_state": alias}
        s = AuthoringSession(llm_callable=hook)
        s.bot_greeting()
        s.step("anything")
        assert s.state is AuthoringState.PREVIEW
        assert not s.is_complete()

    def test_unknown_alias_falls_back_to_default(self):
        def hook(_msgs):
            return {"bot_reply": "ok", "next_state": "totally-bogus"}
        s = AuthoringSession(llm_callable=hook)
        s.bot_greeting()
        s.step("anything")
        # Default advance keeps us in INTENT (turn 1)
        assert s.state is AuthoringState.INTENT


# ──────────────────────────────────────────────────────────────────────
# bot_reply hygiene — no JSON in chat
# ──────────────────────────────────────────────────────────────────────

class TestBotReplyHygiene:
    def test_clean_bot_reply_strips_fenced_block(self):
        text = (
            "Sure, here's the structured payload.\n\n"
            "```json\n"
            '{"scope": "all", "questions": []}\n'
            "```\n\n"
            "Let me know if you want changes."
        )
        cleaned = _clean_bot_reply(text)
        assert "```" not in cleaned
        assert "{" not in cleaned
        assert "Sure" in cleaned
        assert "Let me know" in cleaned

    def test_clean_bot_reply_collapses_blank_lines(self):
        cleaned = _clean_bot_reply("a\n\n\n\nb")
        assert "\n\n\n" not in cleaned

    def test_session_chat_log_never_contains_json_braces(self):
        """The user-reported bug: LLM emitted JSON in bot_reply, wizard
        echoed it. With cleanup the chat log must not contain raw JSON."""
        def hook(_msgs):
            # Simulate the LLM emitting a fenced JSON block in bot_reply
            return {
                "bot_reply": (
                    "I have all the necessary details to define the custom "
                    "bug report prompt. I will now generate the final JSON "
                    "output.\n\n```json\n"
                    '{"scope": "...", "questions": ["..."]}\n```'
                ),
                "next_state": "complete",
            }
        s = AuthoringSession(llm_callable=hook)
        s.bot_greeting()
        s.step("Custom bug report")
        for entry in s.transcript:
            if entry["role"] == "assistant":
                assert "```" not in entry["content"]
                assert '{"scope"' not in entry["content"]


# ──────────────────────────────────────────────────────────────────────
# IntentBag absorption
# ──────────────────────────────────────────────────────────────────────

class TestIntentBag:
    def test_first_message_becomes_topic(self):
        bag = IntentBag()
        bag.absorb("Custom bug report", after_preview=False)
        assert bag.report_topic == "Custom bug report"

    def test_scope_keywords_route_to_scope(self):
        bag = IntentBag(report_topic="Foo")
        bag.absorb("All TRCs and payers, last two weeks", after_preview=False)
        assert any("TRC" in s for s in bag.scope_fragments)

    def test_question_keywords_route_to_questions(self):
        bag = IntentBag(report_topic="Foo")
        bag.absorb("How many tickets are bugs?", after_preview=False)
        assert any("how many" in q.lower() for q in bag.question_fragments)

    def test_output_keywords_route_to_output(self):
        bag = IntentBag(report_topic="Foo")
        bag.absorb("Include CSAT averages and sentiment table", after_preview=False)
        assert any("csat" in o.lower() for o in bag.output_fragments)

    def test_post_preview_appends_to_custom_requirements(self):
        bag = IntentBag(report_topic="Foo")
        bag.absorb("Add some new metric", after_preview=True)
        assert bag.custom_requirements == ["Add some new metric"]
        assert bag.scope_fragments == []  # didn't leak elsewhere

    def test_no_keyword_match_falls_through(self):
        bag = IntentBag(report_topic="Foo")
        bag.absorb("just a vague thing", after_preview=False)
        # Falls into the first empty bucket (scope)
        assert bag.scope_fragments == ["just a vague thing"]


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_coerce_state_canonical(self):
        assert _coerce_state("preview") is AuthoringState.PREVIEW
        assert _coerce_state("INTENT") is AuthoringState.INTENT

    def test_coerce_state_aliases(self):
        for alias in ("complete", "done", "finished", "ready"):
            assert _coerce_state(alias) is AuthoringState.PREVIEW

    def test_coerce_state_unknown_returns_none(self):
        assert _coerce_state("garbage") is None
        assert _coerce_state("") is None
        assert _coerce_state(None) is None

    def test_bulletize(self):
        assert _bulletize(["a", "b"]) == "- a\n- b"
        assert _bulletize([]) == ""
        assert _bulletize(["", "  "]) == ""

    def test_stringify_output(self):
        assert _stringify_output(["a", "b"]) == "a\n\nb"

    def test_truncate(self):
        assert _truncate("short", 100) == "short"
        assert _truncate("a" * 250, 200).endswith("…")


# ──────────────────────────────────────────────────────────────────────
# finalize()
# ──────────────────────────────────────────────────────────────────────

class TestFinalize:
    def test_returns_prompt_library_record(self):
        s = AuthoringSession()
        s.bot_greeting()
        s.step("Custom report")
        s.step("All TRCs")
        s.step("Use the AlmaReport schema")
        rec = s.finalize("E2E Custom", "describes E2E")
        assert rec["name"] == "E2E Custom"
        assert "AlmaReport" in rec["prompt_text"]
        assert s.is_complete()


# ──────────────────────────────────────────────────────────────────────
# parse_authoring_response — wizard widget JSON parser
# ──────────────────────────────────────────────────────────────────────

class TestParseAuthoringResponse:
    def test_pure_text_becomes_bot_reply(self):
        from src.ui.widgets.prompt_wizard import _parse_authoring_response
        result = _parse_authoring_response("Sure, what else do you want?")
        assert result == {"bot_reply": "Sure, what else do you want?"}

    def test_bare_json_returned_as_dict(self):
        from src.ui.widgets.prompt_wizard import _parse_authoring_response
        result = _parse_authoring_response('{"bot_reply": "hi", "next_state": "preview"}')
        assert result == {"bot_reply": "hi", "next_state": "preview"}

    def test_text_plus_fenced_json_extracts_both(self):
        """The user-reported scenario."""
        from src.ui.widgets.prompt_wizard import _parse_authoring_response
        resp = (
            "I have everything I need.\n\n"
            "```json\n"
            '{"scope": "all", "questions": ["q1"], "next_state": "preview"}\n'
            "```"
        )
        result = _parse_authoring_response(resp)
        assert result["scope"] == "all"
        assert result["questions"] == ["q1"]
        # Surrounding text becomes bot_reply (no JSON braces in it)
        assert "I have everything" in result["bot_reply"]
        assert "{" not in result["bot_reply"]
        assert "```" not in result["bot_reply"]

    def test_malformed_json_falls_back_to_text(self):
        from src.ui.widgets.prompt_wizard import _parse_authoring_response
        result = _parse_authoring_response(
            "Some text ```json\n{bad json}\n``` more text"
        )
        # Falls back to plain bot_reply with fences stripped
        assert "```" not in result["bot_reply"]

    def test_empty_response_returns_empty_dict(self):
        from src.ui.widgets.prompt_wizard import _parse_authoring_response
        assert _parse_authoring_response("") == {}
