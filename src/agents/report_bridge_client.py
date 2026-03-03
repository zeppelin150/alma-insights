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

import logging
import time
from pathlib import Path

logger = logging.getLogger("alma.report_bridge")


class ReportBridgeClient:
    """Bridge-backed Gemini client with GeminiClient-compatible interface."""

    def __init__(self, model="gemini-2.5-flash", pii_redaction=True,
                 cli_path="", temperature=0.2):
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

    # ═══════════════════════════════════════════════════════════════
    #  PUBLIC API (GeminiClient-compatible)
    # ═══════════════════════════════════════════════════════════════

    def is_available(self) -> bool:
        """Check if bridge can be booted (node + bridge script exist)."""
        try:
            from src.agents.gemini_bridge_wrapper import GeminiBridge
            bridge = GeminiBridge(model=self.model)
            # Check node exists
            if not bridge._node_path:
                return False
            if not Path(bridge._bridge_script).exists():
                return False
            return True
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

        # ── Bridge call ──
        self._call_counter += 1
        request_id = f"report_{self._call_counter}_{int(time.time())}"

        t0 = time.time()
        response_text = self._bridge.call_blocking(
            full_prompt, request_id, timeout=timeout
        )
        elapsed_ms = int((time.time() - t0) * 1000)

        logger.info(
            "ReportBridge response | request=%s | elapsed=%dms | len=%d",
            request_id, elapsed_ms, len(response_text or ""),
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

    def shutdown(self):
        """Kill the bridge subprocess. Safe to call multiple times."""
        if self._bridge:
            try:
                self._bridge.kill()
                logger.info("ReportBridge: shutdown complete")
            except Exception as e:
                logger.debug("ReportBridge shutdown error: %s", e)
            self._bridge = None

    # ═══════════════════════════════════════════════════════════════
    #  INTERNAL
    # ═══════════════════════════════════════════════════════════════

    def _ensure_bridge(self):
        """Boot the bridge if not already running."""
        if self._bridge and self._bridge.is_alive():
            return

        from src.agents.gemini_bridge_wrapper import GeminiBridge

        logger.info("ReportBridge: booting bridge (model=%s)", self.model)
        self._bridge = GeminiBridge(model=self.model)
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
