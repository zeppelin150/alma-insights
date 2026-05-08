"""
Alma Insights -- LLM Bridge Protocol

Formal contract that every streaming-LLM bridge in the codebase must
satisfy. ACPBridge (Gemini ACP) and ClaudeCliBridge both implement it;
worker_agent / scan_orchestrator / report_orchestrator depend on the
shape via duck-typing.

The contract is enforced by ``tests/test_bridge_protocol.py``, which
parametrizes over both bridge classes. Adding a new bridge means: implement
this Protocol, add the class to the test parametrization, run the tests.

Notes:
  - Method bodies in this file are intentionally empty — Protocol classes
    don't ship code, only the shape.
  - ``runtime_checkable`` lets ``isinstance(bridge, LLMBridge)`` work for
    the contract test, even though it only checks method names (not full
    signatures). The signature check lives in the test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMBridge(Protocol):
    """Streaming-LLM bridge interface.

    Required attributes (initialized in __init__, may be None):
        _usage_tracker, _scan_id, _on_death, _process

    Required counter/state attributes:
        _boot_count, _total_calls, _death_count, _stall_count,
        _consecutive_stalls, _last_stderr_lines, _boot_time, _last_error
    """

    # Lifecycle ----------------------------------------------------
    def ensure_running(self) -> None:
        """Boot the bridge (subprocess or session). Idempotent."""
        ...

    def is_alive(self) -> bool:
        """Whether the bridge is in a usable state."""
        ...

    def shutdown(self, timeout: int = 10) -> None:
        """Terminate the bridge cleanly. Idempotent."""
        ...

    def restart(self) -> None:
        """Force shutdown + ensure_running."""
        ...

    # Configuration ------------------------------------------------
    def set_mcp_config(self, server_config: list[dict]) -> None:
        """Configure MCP server registrations (if supported)."""
        ...

    def new_session(self, mcp_env: dict | None = None) -> str:
        """Open a new session. Returns session id."""
        ...

    def set_on_death(self, callback: Callable | None) -> None:
        """Register a callback fired when the bridge process dies."""
        ...

    # Calls --------------------------------------------------------
    def call_streaming(
        self,
        prompt: str,
        request_id: str,
        on_token: Callable[[Any], None] | None = None,
        timeout: int = 300,
        early_stop: Callable[[], bool] | None = None,
    ) -> dict:
        """Stream a prompt; return result dict with keys:
        full_text, elapsed_ms, turns, events, error, input_tokens,
        output_tokens (and optionally early_stopped, message, recoverable).
        """
        ...

    def call_blocking(self, prompt: str, request_id: str,
                      timeout: int = 300) -> str:
        """Blocking variant: returns full_text or raises RuntimeError."""
        ...

    def abort(self, request_id: str) -> None:
        """Cancel an in-flight call, if any."""
        ...

    # Health -------------------------------------------------------
    def ping(self, timeout: int = 10) -> dict | None:
        """Lightweight liveness check. Dict with status keys, or None."""
        ...

    def probe(self, timeout: int = 30) -> dict | None:
        """Full round-trip canary. Dict with latency_ms/status/error."""
        ...

    # Stats / health record ---------------------------------------
    def record_stall(self) -> None:
        """Increment stall counters (called by callers on slow responses)."""
        ...

    def record_success(self) -> None:
        """Reset consecutive stall counter."""
        ...

    def get_stats(self) -> dict:
        """Snapshot of internal counters for diagnostics."""
        ...
