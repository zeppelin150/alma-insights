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
        self._active_request_id: str | None = None

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
        then delegates to the CLI bridge. The (redacted) system prompt travels
        separately as the subprocess's REAL system prompt — never embedded in
        user content (see _prepare_prompt). When ``on_token`` is given, streams
        via the bridge and forwards each text delta in real time (the call still
        returns the full accumulated text, so callers are unchanged otherwise).
        """
        full_prompt, sys_prompt = self._prepare_prompt(prompt, system_prompt)
        bridge = self._ensure_bridge()
        bridge.set_system_prompt(sys_prompt)
        self._call_counter += 1
        if on_token is None:
            request_id = f"cli_client_{self._call_counter}_{int(time.time())}"
            self._active_request_id = request_id
            try:
                return bridge.call_blocking(full_prompt, request_id, timeout=timeout)
            finally:
                self._active_request_id = None

        request_id = f"cli_client_stream_{self._call_counter}_{int(time.time())}"

        def _on_token(event):
            if event.type == "content":
                delta = event.data.get("delta", "")
                if delta:
                    try:
                        on_token(delta)
                    except Exception:  # noqa: BLE001 — streaming is best-effort, never fatal
                        pass

        self._active_request_id = request_id
        try:
            result = bridge.call_streaming(
                full_prompt, request_id, on_token=_on_token, timeout=timeout,
            )
        finally:
            self._active_request_id = None
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
        full_prompt, sys_prompt = self._prepare_prompt(prompt, system_prompt)
        bridge = self._ensure_bridge()
        bridge.set_system_prompt(sys_prompt)
        self._call_counter += 1
        request_id = f"cli_client_stream_{self._call_counter}_{int(time.time())}"

        deltas: list[str] = []

        def _on_token(event):
            if event.type == "content":
                txt = event.data.get("delta", "")
                if txt:
                    deltas.append(txt)

        self._active_request_id = request_id
        try:
            result = bridge.call_streaming(
                full_prompt, request_id, on_token=_on_token, timeout=timeout,
            )
        finally:
            self._active_request_id = None
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

    def set_usage_sink(self, sink, source: str = "renn_chat") -> None:
        """Meter every CLI turn into a usage sink (carried across rebuilds).

        Same lifecycle rule as set_mcp_config: the bridge is rebuilt on death,
        so the sink is stored here and re-applied by _ensure_bridge — a mid-
        session bridge respawn must not silently stop the metering.
        """
        self._usage_sink = sink
        self._usage_source = source
        if self._bridge is not None:
            self._bridge.set_usage_sink(sink, source=source)

    def abort_active(self) -> bool:
        """Kill the in-flight CLI call, if any (safe from the main thread).

        The generate paths record the request_id just before each bridge call
        and clear it in a ``finally``; ``ClaudeCliBridge.abort`` is thread-safe
        (``_active_proc_lock``) and only kills the subprocess whose id still
        matches, so a late abort against a finished call is a no-op. The killed
        call surfaces to the caller as the normal error path.

        Returns True only when the bridge actually matched + killed the
        subprocess; False when there is no bridge, no recorded request, the
        bridge no longer tracks the id (the call already finished), or the
        abort raised — so ``ChatEngine.stop()`` never reports a stop it
        cannot prove.
        """
        bridge = self._bridge
        request_id = self._active_request_id
        if bridge is None or not request_id:
            return False
        try:
            return bool(bridge.abort(request_id))
        except Exception as e:  # noqa: BLE001 — an abort must never crash the caller
            logger.debug("ClaudeCliClient abort_active failed: %s", e)
            return False

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
            if getattr(self, "_usage_sink", None) is not None:
                self._bridge.set_usage_sink(
                    self._usage_sink,
                    source=getattr(self, "_usage_source", "renn_chat"))
        return self._bridge

    def _prepare_prompt(self, prompt: str, system_prompt: str) -> tuple[str, str]:
        """Apply PII redaction; return (user_prompt, system_prompt) SEPARATELY.

        Reuses GeminiClient's static-shaped redaction methods (the project's
        single-source-of-truth redaction layer at config/redaction_patterns.json).

        The system prompt must NOT be embedded in the user prompt on this path.
        The CLI subprocess has its own "you are Claude Code" identity, so an
        embedded ``[SYSTEM INSTRUCTIONS]`` block + replayed transcript reads as
        a prompt injection — Sonnet refused exactly that (2026-07-21), narrating
        tool calls as text instead of invoking native MCP, then breaking
        character. The callers hand the system part to the bridge, which
        delivers it as a real ``--system-prompt-file`` (replacing the Claude
        Code identity). The ``[SYSTEM INSTRUCTIONS]`` convention remains
        correct for Gemini/report-bridge paths, which have no system channel.
        """
        from src.gemini.gemini_client import GeminiClient
        prompt = GeminiClient._redact_base(None, prompt)
        if system_prompt:
            system_prompt = GeminiClient._redact_base(None, system_prompt)
        if self.pii_redaction:
            prompt = GeminiClient._redact_aggressive(None, prompt)
            if system_prompt:
                system_prompt = GeminiClient._redact_aggressive(None, system_prompt)
        return prompt, (system_prompt or "")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
