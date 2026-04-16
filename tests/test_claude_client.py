"""
Tests for src/llm/claude_client.py (Phase P1-T2)

Covers: generate returns string (mock HTTP), auth error, rate limit,
PII applied, streaming yields tokens.
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from io import BytesIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llm.claude_client import ClaudeClient, ClaudeAuthError, ClaudeRateLimitError


class TestClaudeClient:
    """Core ClaudeClient tests."""

    def _mock_response(self, text: str = "Hello from Claude", status: int = 200,
                       input_tokens: int = 10, output_tokens: int = 20):
        """Create a mock urllib response."""
        body = {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_generate_returns_string(self):
        client = ClaudeClient(api_key="test-key", model="claude-sonnet-4-6")
        mock_resp = self._mock_response("Test response")

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("src.llm.claude_client._redact_text", side_effect=lambda t, **kw: t):
                result = client.generate("Hello")

        assert result == "Test response"
        assert isinstance(result, str)

    def test_generate_with_system_prompt(self):
        client = ClaudeClient(api_key="test-key")
        mock_resp = self._mock_response("With system")

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            with patch("src.llm.claude_client._redact_text", side_effect=lambda t, **kw: t):
                result = client.generate("Hello", system_prompt="Be helpful")

        assert result == "With system"
        # Verify system prompt was included in request body
        call_args = mock_open.call_args
        req = call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["system"] == "Be helpful"

    def test_auth_error_on_401(self):
        import urllib.error
        client = ClaudeClient(api_key="bad-key")

        error = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),
            fp=BytesIO(b'{"error": "invalid_api_key"}'),
        )

        with patch("urllib.request.urlopen", side_effect=error):
            with patch("src.llm.claude_client._redact_text", side_effect=lambda t, **kw: t):
                with pytest.raises(ClaudeAuthError):
                    client.generate("Hello")

    def test_rate_limit_error_on_429(self):
        import urllib.error
        client = ClaudeClient(api_key="test-key")

        headers = MagicMock()
        headers.get.return_value = "30"
        error = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=BytesIO(b'{"error": "rate_limited"}'),
        )

        with patch("urllib.request.urlopen", side_effect=error):
            with patch("src.llm.claude_client._redact_text", side_effect=lambda t, **kw: t):
                with pytest.raises(ClaudeRateLimitError) as exc_info:
                    client.generate("Hello")
                assert exc_info.value.retry_after == 30.0

    def test_missing_api_key_raises_auth_error(self):
        client = ClaudeClient(api_key="")
        with pytest.raises(ClaudeAuthError):
            client.generate("Hello")

    def test_is_available(self):
        assert ClaudeClient(api_key="key").is_available() is True
        assert ClaudeClient(api_key="").is_available() is False

    def test_pii_redaction_applied(self):
        client = ClaudeClient(api_key="test-key", pii_redaction=True)
        mock_resp = self._mock_response("Response")

        redact_calls = []
        def mock_redact(text, aggressive=True):
            redact_calls.append({"text": text, "aggressive": aggressive})
            return text

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("src.llm.claude_client._redact_text", side_effect=mock_redact):
                client.generate("Hello user@example.com", system_prompt="System")

        # Should be called for both prompt and system_prompt
        assert len(redact_calls) == 2
        assert redact_calls[0]["aggressive"] is True
        assert redact_calls[1]["aggressive"] is True

    def test_pii_redaction_not_aggressive_when_disabled(self):
        client = ClaudeClient(api_key="test-key", pii_redaction=False)
        mock_resp = self._mock_response("Response")

        redact_calls = []
        def mock_redact(text, aggressive=True):
            redact_calls.append({"aggressive": aggressive})
            return text

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("src.llm.claude_client._redact_text", side_effect=mock_redact):
                client.generate("Hello")

        assert redact_calls[0]["aggressive"] is False

    def test_multi_block_response(self):
        """Response with multiple content blocks should be concatenated."""
        client = ClaudeClient(api_key="test-key")
        body = {
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ],
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with patch("src.llm.claude_client._redact_text", side_effect=lambda t, **kw: t):
                result = client.generate("Hello")

        assert "Part 1" in result
        assert "Part 2" in result


class TestClaudeClientStreaming:
    """Streaming response tests."""

    def test_streaming_yields_tokens(self):
        client = ClaudeClient(api_key="test-key")

        sse_lines = [
            b'event: content_block_delta\n',
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}\n',
            b'\n',
            b'event: content_block_delta\n',
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " World"}}\n',
            b'\n',
            b'data: [DONE]\n',
        ]

        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(sse_lines))
        mock_resp.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("src.llm.claude_client._redact_text", side_effect=lambda t, **kw: t):
                tokens = list(client.generate_streaming("Hello"))

        assert tokens == ["Hello", " World"]

    def test_streaming_missing_key_raises(self):
        client = ClaudeClient(api_key="")
        with pytest.raises(ClaudeAuthError):
            list(client.generate_streaming("Hello"))


class TestRedactText:
    """Test the shared _redact_text function."""

    def test_redact_with_missing_config(self):
        """Should return text unchanged if redaction config not available."""
        from src.llm.claude_client import _redact_text
        # Even if the import fails, should not crash
        result = _redact_text("Hello world")
        assert isinstance(result, str)
