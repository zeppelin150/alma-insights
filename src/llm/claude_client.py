"""
Alma Insights — Claude API Client (Phase P1)

Duck-typed LLM client matching GeminiClient's ``.generate()`` interface.
Uses ``urllib.request`` (stdlib) to avoid adding third-party HTTP deps.

HIPAA COMPLIANCE:
  Claude models require a BAA with Anthropic.  The Settings page enforces
  a 3-checkbox danger-zone before enabling any Claude model.  PII redaction
  uses the same shared pipeline as Gemini (mandatory base + optional
  aggressive).

  Data flow is identical to GeminiClient — only aggregated, redacted
  statistics are sent.  Full ticket text never crosses the network.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Iterator

logger = logging.getLogger("alma.claude_client")

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


# ── Error classes ────────────────────────────────────────────────

class ClaudeAuthError(Exception):
    """Raised on 401 — invalid API key."""
    pass


class ClaudeRateLimitError(Exception):
    """Raised on 429 — includes retry_after seconds."""
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# ── PII redaction (shared with GeminiClient) ─────────────────────

class RedactionError(RuntimeError):
    """Raised when PII redaction cannot be applied, so the caller aborts the
    model call rather than sending un-redacted text (fail closed)."""


def _redact_text(text: str, aggressive: bool = True) -> str:
    """Apply the same PII redaction pipeline as GeminiClient.

    Imports redaction functions from gemini_client at call time to avoid
    circular imports and to guarantee single source of truth.

    **Fails closed.** If the pipeline raises (e.g. the pattern config is
    unreadable), this does NOT return the raw text — it raises
    ``RedactionError`` so ``generate`` aborts before anything leaves the
    machine. The old behaviour logged and sent un-redacted, which on a
    PHI-adjacent path is a leak.
    """
    try:
        from src.gemini.gemini_client import _load_redaction_config
        import re

        config = _load_redaction_config()

        # Base redaction (always runs)
        for pat in config.get("patterns", []):
            if pat.get("conditional"):
                continue
            flags = re.IGNORECASE if pat.get("flags") == "i" else 0
            text = re.sub(pat["regex"], pat["replace"], text, flags=flags)

        # Aggressive redaction (name heuristic)
        if aggressive:
            skip_terms = set(config.get("aggressive_skip_terms", []))

            def _maybe_redact_name(match):
                word1 = match.group(1)
                word2 = match.group(2)
                if word1 in skip_terms or word2 in skip_terms:
                    return match.group(0)
                return "[NAME]"

            text = re.sub(
                r'\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b',
                _maybe_redact_name,
                text,
            )
    except Exception as e:
        # Fail CLOSED: never fall through to returning raw text.
        logger.error("PII redaction failed — aborting the model call rather "
                     "than sending un-redacted text: %s", e)
        raise RedactionError(f"PII redaction failed: {e}") from e

    return text


class ClaudeClient:
    """Anthropic Claude API client with PII redaction.

    Interface mirrors ``GeminiClient.generate()`` so callers are
    provider-agnostic via duck typing.
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 pii_redaction: bool = True):
        self._api_key = api_key
        self.model = model
        self.pii_redaction = pii_redaction
        self._usage_tracker = None  # Optional UsageTracker (same as Gemini)

    def is_available(self) -> bool:
        """Return True if we have an API key configured."""
        return bool(self._api_key)

    def _default_max_tokens(self) -> int:
        """The default output-token budget for a no-arg call.

        Deliberately the legacy 4096 for ALL models, so every existing caller
        that passes nothing is byte-for-byte unchanged (sending the profile's
        full ceiling here would silently double output limits app-wide — chat,
        reports, the guru pipelines). A model's larger ceiling lives in its
        ``CapabilityProfile.max_output_tokens`` (e.g. 8192 for Claude); callers
        that need headroom — card drafting, long rewrites — opt in explicitly
        with ``max_tokens=ModelRegistry.instance().profile(model).max_output_tokens``.
        Centralised here so the per-call default stays one tunable choke point.
        """
        return 4096

    def generate(self, prompt: str, system_prompt: str = "",
                 timeout: int = 120, max_tokens: int | None = None) -> str:
        """Send a prompt to Claude and return the response text.

        PII redaction is applied identically to GeminiClient:
        - Base redaction always runs (emails, phones, SSNs, cards, member IDs)
        - Aggressive name redaction controlled by ``self.pii_redaction``

        ``max_tokens=None`` (the default) resolves to 4096 — the legacy
        default — so callers that pass nothing are unchanged. A model's larger
        ceiling is opt-in via its ``CapabilityProfile.max_output_tokens``. An
        explicit value is always honored as-is.
        """
        if max_tokens is None:
            max_tokens = self._default_max_tokens()
        if not self._api_key:
            raise ClaudeAuthError("Anthropic API key not configured")

        # ── PII redaction ──
        prompt = _redact_text(prompt, aggressive=self.pii_redaction)
        if system_prompt:
            system_prompt = _redact_text(system_prompt, aggressive=self.pii_redaction)

        # ── Log outbound ──
        logger.info(
            f"Claude outbound | model={self.model} "
            f"| system_len={len(system_prompt or '')} "
            f"| prompt_len={len(prompt)} "
            f"| first_100={prompt[:100]}..."
        )

        # ── Build request ──
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt

        data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            _API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
            },
            method="POST",
        )

        # ── Execute ──
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self._handle_http_error(e)
        except urllib.error.URLError as e:
            raise RuntimeError(f"Claude API connection failed: {e.reason}") from e

        # ── Parse response ──
        response_text = self._extract_text(resp_data)

        # ── Usage tracking ──
        if self._usage_tracker:
            try:
                usage = resp_data.get("usage", {})
                tok_in = usage.get("input_tokens", 0)
                tok_out = usage.get("output_tokens", 0)
                self._usage_tracker.log_call(
                    source="ai_report",
                    tokens_in=tok_in,
                    tokens_out=tok_out,
                    model=self.model,
                )
            except Exception as _e:
                logger.debug(f"Usage tracking failed: {_e}")

        return response_text

    def generate_streaming(self, prompt: str, system_prompt: str = "",
                           max_tokens: int | None = None, timeout: int = 120) -> Iterator[str]:
        """Stream response tokens via SSE.

        Yields text delta strings as they arrive.

        ``max_tokens=None`` (the default) resolves to 4096, matching
        ``generate()``; a model's larger ceiling is opt-in via its
        ``CapabilityProfile.max_output_tokens``. An explicit value is honored.
        """
        if max_tokens is None:
            max_tokens = self._default_max_tokens()
        if not self._api_key:
            raise ClaudeAuthError("Anthropic API key not configured")

        # PII redaction
        prompt = _redact_text(prompt, aggressive=self.pii_redaction)
        if system_prompt:
            system_prompt = _redact_text(system_prompt, aggressive=self.pii_redaction)

        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt

        data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            _API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
            },
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            self._handle_http_error(e)
        except urllib.error.URLError as e:
            raise RuntimeError(f"Claude API connection failed: {e.reason}") from e

        # Parse SSE stream
        try:
            for line in resp:
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded or not decoded.startswith("data: "):
                    continue
                payload = decoded[6:]  # strip "data: "
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            yield text
                except json.JSONDecodeError:
                    continue
        finally:
            resp.close()

    # ── Internals ──────────────────────────────────────────────────

    @staticmethod
    def _extract_text(resp_data: dict) -> str:
        """Pull text from a Messages API response."""
        content = resp_data.get("content", [])
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()

    @staticmethod
    def _handle_http_error(e: urllib.error.HTTPError) -> None:
        """Convert HTTP errors to typed exceptions."""
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        if e.code == 401:
            raise ClaudeAuthError(
                f"Invalid Anthropic API key (401): {body}"
            ) from e
        elif e.code == 429:
            retry_after = None
            ra_header = e.headers.get("retry-after")
            if ra_header:
                try:
                    retry_after = float(ra_header)
                except (ValueError, TypeError):
                    pass
            raise ClaudeRateLimitError(
                f"Rate limited (429): {body}",
                retry_after=retry_after,
            ) from e
        else:
            raise RuntimeError(
                f"Claude API error {e.code}: {body}"
            ) from e
