"""Phase 1 — Enablement provider toggle routing.

resolve_provider_for_task honors the operator's enablement.provider setting for
enablement_* lanes only (defaulting to Gemini), and leaves the other lanes alone.
"""

from __future__ import annotations

from unittest.mock import patch

from src.gemini import client_factory as CF


def _patched_section(enablement_provider=None):
    """Patch settings_manager.get_section name-aware: only 'enablement' carries
    a provider; everything else is empty (so default routes apply)."""
    def fake(name, default=None):
        if name == "enablement":
            return {"provider": enablement_provider} if enablement_provider is not None else {}
        return default if default is not None else {}
    return patch("src.data.settings_manager.get_section", side_effect=fake)


def test_enablement_defaults_to_gemini():
    with _patched_section(None):
        assert CF.resolve_provider_for_task("enablement_card_gen") == "gemini"
        assert CF.resolve_provider_for_task("enablement_chat") == "gemini"


def test_enablement_toggle_to_claude():
    with _patched_section("claude"):
        assert CF.resolve_provider_for_task("enablement_card_gen") == "claude"
        assert CF.resolve_provider_for_task("enablement_subtasks") == "claude"


def test_enablement_toggle_to_gemini_explicit():
    with _patched_section("gemini"):
        assert CF.resolve_provider_for_task("enablement_extract") == "gemini"


def test_enablement_toggle_ignores_garbage():
    with _patched_section("bogus"):
        # An invalid value falls through to the default route (gemini).
        assert CF.resolve_provider_for_task("enablement_triage") == "gemini"


def test_non_enablement_lanes_unaffected():
    with _patched_section("claude"):
        # The Enablement pill must not swing the NLP/VOC/Guru lanes.
        assert CF.resolve_provider_for_task("nlp_classification") == "gemini"
        assert CF.resolve_provider_for_task("guru_analysis") == "claude"


def test_enablement_claude_forces_bedrock_cli(monkeypatch):
    """Enablement Claude must use the CLI/Bedrock client, never the direct
    Anthropic API — even when an anthropic_api_key is present (BAA safety)."""
    from src.llm.claude_cli_client import ClaudeCliClient
    monkeypatch.setattr(CF, "_resolve_claude_cli_alias", lambda: "sonnet")
    monkeypatch.setattr("src.data.pat_store.load_setting",
                        lambda k, d="": "sk-ant-fake" if k == "anthropic_api_key" else d)
    with _patched_section("claude"):
        client = CF.build_client_for_task("enablement_card_gen")
    assert isinstance(client, ClaudeCliClient)


def test_enablement_claude_cli_without_key(monkeypatch):
    from src.llm.claude_cli_client import ClaudeCliClient
    monkeypatch.setattr(CF, "_resolve_claude_cli_alias", lambda: "sonnet")
    monkeypatch.setattr("src.data.pat_store.load_setting", lambda k, d="": d)
    with _patched_section("claude"):
        client = CF.build_client_for_task("enablement_chat")
    assert isinstance(client, ClaudeCliClient)


def test_enablement_llm_disables_aggressive_redaction():
    """Enablement LLM clients keep BASE redaction but skip the aggressive name pass
    (which mangles Title-Case product terms in cards). Base redaction always runs."""
    client = CF.build_client_for_task("enablement_card_gen")
    assert getattr(client, "pii_redaction", None) is False
