"""
Alma Insights -- Report Bridge Client (Build 7.0)

Drop-in replacement for GeminiClient that routes all generate() calls
through a persistent GeminiBridge subprocess.  Eliminates the ~17-20s
cold-start overhead per CLI fork, bringing single-call reports from
~25s to ~8s.

Interface contract:
  - .generate(prompt, system_prompt, timeout) → str   (same as GeminiClient)
  - .is_available() → bool                            (same as GeminiClient)
  - .model  (str attribute)                            (same as GeminiClient)
  - .pii_redaction (bool attribute)                    (same as GeminiClient)

PII redaction:
  - Mandatory base redaction always runs (emails, phones, SSNs, cards, etc.)
  - Optional aggressive redaction controlled by pii_redaction flag
  - Identical to GeminiClient -- reuses the same static methods + config

Thread safety:
  GeminiBridge is fully thread-safe (request queues + locks).
  ReportBridgeClient can be shared across threads (e.g., ReportWorker + ChatWorker).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("alma.report_bridge")


class ReportBridgeClient:
    """Bridge-backed Gemini client with GeminiClient-compatible interface."""

    def __init__(self, model: str = "gemini-2.5-flash", pii_redaction: bool = True,
                 cli_path: str = "", temperature: float = 0.2) -> None:
        """
        Args:
            model: Gemini model name (passed to bridge subprocess).
            pii_redaction: If True, run aggressive PII redaction on prompts.
            cli_path: Gemini CLI path (unused by bridge, kept for compat).
            temperature: Model temperature (not directly used -- bridge inherits
                         from CLI defaults, but stored for API compatibility).
        """
        self.model = model
        self.pii_redaction = pii_redaction
        self.cli_path = cli_path
        self.temperature = temperature

        self._bridge = None
        self._call_counter = 0
        self._usage_tracker = None   # Optional UsageTracker (GeminiClient compat)
        self._mcp_config: list[dict] | None = None  # MCP server config for chat tools
        self._last_tool_calls: list[dict] = []  # Tool calls from last generate()

    def set_mcp_config(self, server_config: list[dict]) -> None:
        """Set MCP server config for native tool calling.

        When set, the bridge boots with the MCP server and Gemini uses
        native function calling for tools — no TOOL_CALL regex needed.

        Args:
            server_config: List of MCP server dicts, e.g.
                [{"name": "alma-chat-tools", "command": "python",
                  "args": ["-m", "src.mcp.chat_mcp_server"],
                  "env": [{"name": "ALMA_DB_PATH", "value": "..."}]}]
        """
        self._mcp_config = server_config
        # If bridge is already running, it needs to be restarted with new config
        if self._bridge:
            logger.info("ReportBridge: MCP config set, restarting bridge")
            self.shutdown()  # Will re-boot on next generate() call

    # ═══════════════════════════════════════════════════════════════
    #  PUBLIC API (GeminiClient-compatible)
    # ═══════════════════════════════════════════════════════════════

    def is_available(self) -> bool:
        """Check if ACP bridge can be booted (gemini CLI exists)."""
        try:
            from src.agents.acp_bridge import ACPBridge
            cli_path = ACPBridge._find_gemini_cli()
            return bool(cli_path)
        except Exception:
            return False

    def generate(self, prompt: str, system_prompt: str = "",
                 timeout: int = 120) -> str:
        """
        Send a prompt through the persistent bridge and return the response.

        Drop-in replacement for GeminiClient.generate().

        Args:
            prompt: The user/analysis prompt text.
            system_prompt: Optional system instructions (prepended to prompt).
            timeout: Max seconds to wait for response.

        Returns:
            str: The full response text.

        Raises:
            RuntimeError: If the bridge call failed.
        """
        self._ensure_bridge()

        # ── MANDATORY base redaction (always runs) ──
        from src.gemini.gemini_client import GeminiClient
        prompt = GeminiClient._redact_base(None, prompt)
        if system_prompt:
            system_prompt = GeminiClient._redact_base(None, system_prompt)

        # ── Optional aggressive redaction ──
        if self.pii_redaction:
            prompt = GeminiClient._redact_aggressive(None, prompt)
            if system_prompt:
                system_prompt = GeminiClient._redact_aggressive(None, system_prompt)

        # ── Log outbound (audit trail) ──
        logger.info(
            "ReportBridge outbound | system_prompt_len=%d | prompt_len=%d "
            "| first_100=%s...",
            len(system_prompt or ""), len(prompt), prompt[:100],
        )

        # ── Build full prompt (same convention as GeminiClient) ──
        full_prompt = prompt
        if system_prompt:
            full_prompt = (
                f"[SYSTEM INSTRUCTIONS]\n{system_prompt}\n"
                f"[END SYSTEM INSTRUCTIONS]\n\n{prompt}"
            )

        # ── Bridge call (streaming to capture tool events) ──
        self._call_counter += 1
        request_id = f"report_{self._call_counter}_{int(time.time())}"
        self._last_tool_calls = []

        def _on_token(event):
            if event.type == "tool_call":
                self._last_tool_calls.append({
                    "name": event.data.get("name", ""),
                    "args": event.data.get("args", {}),
                })

        t0 = time.time()
        result = self._bridge.call_streaming(
            full_prompt, request_id,
            on_token=_on_token, timeout=timeout,
        )
        elapsed_ms = int((time.time() - t0) * 1000)

        if result.get("error"):
            raise RuntimeError(
                f"ACP call failed: {result['error']} - "
                f"{result.get('message', '')}"
            )

        response_text = result.get("full_text", "")

        logger.info(
            "ReportBridge response | request=%s | elapsed=%dms | len=%d | tools=%d",
            request_id, elapsed_ms, len(response_text or ""),
            len(self._last_tool_calls),
        )

        # ── Usage tracking (if configured) ──
        if self._usage_tracker:
            try:
                tok_in = self._usage_tracker.estimate_tokens(full_prompt)
                tok_out = self._usage_tracker.estimate_tokens(response_text or "")
                self._usage_tracker.log_call(
                    source="ai_report",
                    tokens_in=tok_in,
                    tokens_out=tok_out,
                    model=self.model,
                )
            except Exception as e:
                logger.debug("Usage tracking failed: %s", e)

        return response_text

    def shutdown(self) -> None:
        """Kill the bridge subprocess. Safe to call multiple times."""
        if self._bridge:
            try:
                self._bridge.shutdown()
                logger.info("ReportBridge: shutdown complete")
            except Exception as e:
                logger.debug("ReportBridge shutdown error: %s", e)
            self._bridge = None

    # ═══════════════════════════════════════════════════════════════
    #  INTERNAL
    # ═══════════════════════════════════════════════════════════════

    def _ensure_bridge(self):
        """Boot the ACP bridge if not already running."""
        if self._bridge and self._bridge.is_alive():
            return

        from src.agents.acp_bridge import ACPBridge

        logger.info("ReportBridge: booting ACP bridge (model=%s, mcp=%s)",
                     self.model, bool(self._mcp_config))
        self._bridge = ACPBridge(model=self.model)

        # Configure MCP servers before boot (must be set before ensure_running)
        if self._mcp_config:
            self._bridge.set_mcp_config(self._mcp_config)

        self._bridge.ensure_running()

        # Attach usage tracker if available
        if self._usage_tracker:
            self._bridge._usage_tracker = self._usage_tracker

        logger.info("ReportBridge: bridge ready")

    def __del__(self):
        """Cleanup on garbage collection."""
        try:
            self.shutdown()
        except Exception:
            pass
