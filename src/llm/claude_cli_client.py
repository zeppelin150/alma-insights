"""
Alma Insights -- Claude CLI Client

Drop-in replacement for ClaudeClient that uses the local ``claude`` CLI in
non-interactive mode instead of the Anthropic Messages API. Inherits the
user's existing CLI auth (login token, ANTHROPIC_API_KEY, or — once enabled
— Bedrock env vars). No anthropic_api_key required in pat_store.

When ``ai.task_routing.override_all=claude`` is set and no anthropic_api_key
is configured, the factory falls back to this client so chat / guru / VOC /
report_generation still work end-to-end through the CLI subprocess.

Architecture:
    .generate() / .generate_streaming() apply PII redaction, then delegate
    to a lazily-initialized ClaudeCliBridge. Each .generate() spawns a fresh
    ``claude -p`` subprocess (~500ms-2s startup); see ClaudeCliBridge for
    cost/latency tradeoffs.

Public surface mirrors ClaudeClient + GeminiClient: ``.generate``,
``.generate_streaming``, ``.is_available``, ``.model``, ``.pii_redaction``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

logger = logging.getLogger("alma.claude_cli_client")


class ClaudeCliClient:
    """ClaudeClient-compatible interface backed by the ``claude`` CLI."""

    def __init__(self, model: str = "sonnet", pii_redaction: bool = True) -> None:
        self.model = model
        self.pii_redaction = pii_redaction
        self._bridge = None  # lazily initialized in _ensure_bridge
        self._call_counter = 0
        self._mcp_config: list[dict] = []

    # ─── Public API (matches ClaudeClient + GeminiClient) ─────

    def is_available(self) -> bool:
        """Check whether the ``claude`` CLI binary is locatable."""
        try:
            from src.agents.claude_cli_bridge import ClaudeCliBridge
            probe = ClaudeCliBridge(model=self.model)
            return bool(probe._find_claude_cli())
        except Exception as e:
            logger.debug("ClaudeCliClient.is_available failed: %s", e)
            return False

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        timeout: int = 120,
        max_tokens: int | None = None,  # accepted for API parity, not used
        on_token=None,
    ) -> str:
        """Synchronous .generate() — returns full response text.

        Applies PII redaction (base always; aggressive if pii_redaction=True)
        then delegates to the CLI bridge. When ``on_token`` is given, streams via
        the bridge and forwards each text delta in real time (the call still
        returns the full accumulated text, so callers are unchanged otherwise).
        """
        full_prompt = self._prepare_prompt(prompt, system_prompt)
        bridge = self._ensure_bridge()
        self._call_counter += 1
        if on_token is None:
            request_id = f"cli_client_{self._call_counter}_{int(time.time())}"
            return bridge.call_blocking(full_prompt, request_id, timeout=timeout)

        request_id = f"cli_client_stream_{self._call_counter}_{int(time.time())}"

        def _on_token(event):
            if event.type == "content":
                delta = event.data.get("delta", "")
                if delta:
                    try:
                        on_token(delta)
                    except Exception:  # noqa: BLE001 — streaming is best-effort, never fatal
                        pass

        result = bridge.call_streaming(
            full_prompt, request_id, on_token=_on_token, timeout=timeout,
        )
        if result.get("error"):
            raise RuntimeError(
                f"Claude CLI streaming call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )
        return result.get("full_text", "")

    def generate_streaming(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        timeout: int = 120,
    ) -> Iterator[str]:
        """Yield text chunks as the model produces them.

        Uses the bridge's on_token callback to collect deltas; this iterator
        finishes synchronously after the subprocess exits — it does not stream
        in real time across the iterator boundary. Sufficient for callers that
        only need iterator-shaped semantics; real interleaved streaming would
        require running the call in a thread + queue.
        """
        full_prompt = self._prepare_prompt(prompt, system_prompt)
        bridge = self._ensure_bridge()
        self._call_counter += 1
        request_id = f"cli_client_stream_{self._call_counter}_{int(time.time())}"

        deltas: list[str] = []

        def _on_token(event):
            if event.type == "content":
                txt = event.data.get("delta", "")
                if txt:
                    deltas.append(txt)

        result = bridge.call_streaming(
            full_prompt, request_id, on_token=_on_token, timeout=timeout,
        )
        if result.get("error"):
            raise RuntimeError(
                f"Claude CLI streaming call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )
        for delta in deltas:
            yield delta

    def set_mcp_config(self, server_config: list[dict]) -> None:
        """Wire native MCP tools onto the CLI (carried across bridge rebuilds).

        Claude refuses text-injected tool results, so the chat's Claude path must
        expose tools natively via MCP. The page passes the chat_mcp_server config;
        this stores it and applies it to the live bridge (and to any future bridge
        created by _ensure_bridge). Pass [] to disable.
        """
        self._mcp_config = server_config or []
        if self._bridge is not None:
            self._bridge.set_mcp_config(self._mcp_config)

    def shutdown(self) -> None:
        """Tear down the underlying bridge if alive. Idempotent."""
        if self._bridge is not None:
            try:
                self._bridge.shutdown()
            except Exception as e:
                logger.debug("ClaudeCliClient shutdown error: %s", e)
            self._bridge = None

    # ─── Internal ──────────────────────────────────────────────

    def _ensure_bridge(self):
        if self._bridge is None or not self._bridge.is_alive():
            from src.agents.claude_cli_bridge import ClaudeCliBridge
            self._bridge = ClaudeCliBridge(model=self.model)
            self._bridge.ensure_running()
            if self._mcp_config:
                self._bridge.set_mcp_config(self._mcp_config)
        return self._bridge

    def _prepare_prompt(self, prompt: str, system_prompt: str) -> str:
        """Apply PII redaction and build the [SYSTEM INSTRUCTIONS] convention.

        Reuses GeminiClient's static-shaped redaction methods (the project's
        single-source-of-truth redaction layer at config/redaction_patterns.json).
        """
        from src.gemini.gemini_client import GeminiClient
        prompt = GeminiClient._redact_base(None, prompt)
        if system_prompt:
            system_prompt = GeminiClient._redact_base(None, system_prompt)
        if self.pii_redaction:
            prompt = GeminiClient._redact_aggressive(None, prompt)
            if system_prompt:
                system_prompt = GeminiClient._redact_aggressive(None, system_prompt)

        if system_prompt:
            return (
                f"[SYSTEM INSTRUCTIONS]\n{system_prompt}\n"
                f"[END SYSTEM INSTRUCTIONS]\n\n{prompt}"
            )
        return prompt

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
