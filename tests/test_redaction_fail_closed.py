"""PII redaction on the Claude direct-API path must fail CLOSED.

Audit finding 27: claude_client._redact_text caught any exception, logged
"sending un-redacted", and returned the raw text — so a broken pattern config
leaked un-redacted (potentially PHI-adjacent) prompts to the API. It must
instead raise so generate() aborts before anything leaves the machine.
"""

import pytest

from src.llm import claude_client as cc


def test_redaction_failure_raises_not_returns_raw(monkeypatch):
    """A pipeline failure raises RedactionError rather than returning the
    input unchanged."""
    def boom():
        raise RuntimeError("pattern config unreadable")

    # _redact_text imports _load_redaction_config lazily from gemini_client.
    import src.gemini.gemini_client as gc
    monkeypatch.setattr(gc, "_load_redaction_config", boom)

    with pytest.raises(cc.RedactionError):
        cc._redact_text("call me at 555-123-4567", aggressive=True)


def test_generate_aborts_when_redaction_fails(monkeypatch):
    """The abort propagates through generate(), so no HTTP request is made."""
    import src.gemini.gemini_client as gc
    monkeypatch.setattr(gc, "_load_redaction_config",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    sent = {"called": False}

    def fake_urlopen(*a, **k):
        sent["called"] = True
        raise AssertionError("a request was made despite redaction failing")

    monkeypatch.setattr(cc.urllib.request, "urlopen", fake_urlopen, raising=False)

    client = cc.ClaudeClient(api_key="k", pii_redaction=True)
    with pytest.raises(cc.RedactionError):
        client.generate("patient SSN 123-45-6789")
    assert sent["called"] is False, "un-redacted text was about to be sent"


def test_successful_redaction_still_returns_text(monkeypatch):
    """The happy path is unchanged — a working pipeline returns redacted text."""
    result = cc._redact_text("email me at bob@example.com", aggressive=False)
    assert "bob@example.com" not in result, "base redaction did not run"
