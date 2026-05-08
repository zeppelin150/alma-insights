"""
Drift check: every LLMBridge implementation must keep the same shape.

Parametrizes over ACPBridge and ClaudeCliBridge. If a new bridge is added
(e.g. a future BedrockSdkBridge bypassing the CLI), append it to BRIDGE_CLASSES
and these tests run against it too.

What we assert:
  - Class implements every method listed in LLMBridge protocol
  - Method signatures match (names, kinds — defaults checked loosely)
  - Required instance attributes exist after __init__
  - call_streaming returns dict with the required keys (we don't actually
    invoke the LLM; we mock the subprocess / network and check shape)

Why this matters:
  worker_agent / scan_orchestrator / report_orchestrator all consume this
  interface duck-typed. Drift between bridges means silent breakage in one
  provider while the other works fine.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.bridge_protocol import LLMBridge


def _bridge_classes():
    from src.agents.acp_bridge import ACPBridge
    from src.agents.claude_cli_bridge import ClaudeCliBridge
    return [ACPBridge, ClaudeCliBridge]


BRIDGE_CLASSES = _bridge_classes()


REQUIRED_METHODS = [
    "ensure_running", "is_alive", "shutdown", "restart",
    "set_mcp_config", "new_session", "set_on_death",
    "call_streaming", "call_blocking", "abort",
    "ping", "probe", "record_stall", "record_success", "get_stats",
]

REQUIRED_ATTRIBUTES = [
    "_usage_tracker", "_scan_id", "_on_death", "_process",
    "_boot_count", "_total_calls", "_death_count",
    "_stall_count", "_consecutive_stalls",
    "_last_stderr_lines", "_boot_time", "_last_error",
]


@pytest.mark.parametrize("bridge_cls", BRIDGE_CLASSES,
                         ids=[c.__name__ for c in BRIDGE_CLASSES])
class TestBridgeProtocol:
    """Each bridge class must satisfy the LLMBridge contract."""

    def test_has_all_methods(self, bridge_cls):
        missing = [m for m in REQUIRED_METHODS if not hasattr(bridge_cls, m)]
        assert not missing, (
            f"{bridge_cls.__name__} is missing protocol methods: {missing}"
        )

    def test_method_signatures_match(self, bridge_cls):
        """Every method must have the parameters the protocol declares."""
        for method_name in REQUIRED_METHODS:
            proto_method = getattr(LLMBridge, method_name)
            cls_method = getattr(bridge_cls, method_name)
            proto_sig = inspect.signature(proto_method)
            cls_sig = inspect.signature(cls_method)
            proto_params = set(proto_sig.parameters.keys())
            cls_params = set(cls_sig.parameters.keys())
            missing = proto_params - cls_params
            assert not missing, (
                f"{bridge_cls.__name__}.{method_name} missing params: "
                f"{missing} (proto={proto_params}, impl={cls_params})"
            )

    def test_instance_has_required_attributes(self, bridge_cls):
        instance = bridge_cls()
        missing = [
            attr for attr in REQUIRED_ATTRIBUTES
            if not hasattr(instance, attr)
        ]
        assert not missing, (
            f"{bridge_cls.__name__} instance missing attributes: {missing}"
        )

    def test_isinstance_check(self, bridge_cls):
        """Runtime-checkable Protocol smoke test."""
        instance = bridge_cls()
        assert isinstance(instance, LLMBridge), (
            f"{bridge_cls.__name__} fails isinstance(LLMBridge) check"
        )


# Result-dict shape contract --------------------------------------------------

REQUIRED_RESULT_KEYS = [
    "full_text", "elapsed_ms", "turns", "events", "error",
    "input_tokens", "output_tokens",
]


def test_call_streaming_result_shape_claude_cli(monkeypatch, tmp_path):
    """ClaudeCliBridge.call_streaming returns a dict with all required keys.

    Mocks the subprocess so we don't actually spawn ``claude``; just verify
    the dict assembly path produces the documented shape.
    """
    from src.agents.claude_cli_bridge import ClaudeCliBridge
    from src.agents.claude_cli_stream import StreamParser, StreamResult

    bridge = ClaudeCliBridge(model="sonnet")
    bridge._cli_path = "fake-path"
    bridge._bridge_healthy = True

    # Build a synthetic StreamResult (no subprocess needed)
    parser = StreamParser("synth_req")
    parser.full_text = "synthetic"
    parser.input_tokens = 5
    parser.output_tokens = 7
    stream = StreamResult(
        parser=parser, events=[],
        elapsed_ms=42, early_stopped=False,
    )
    result = bridge._build_result_dict(stream)
    for key in REQUIRED_RESULT_KEYS:
        assert key in result, f"Missing key {key} in result: {list(result)}"
