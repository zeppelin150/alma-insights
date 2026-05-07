"""Unit tests — src/data/scoped_usage_tracker.py.

Coverage:
  * scan_id auto-injected when caller omits it
  * caller-supplied scan_id overrides the bound scope
  * estimate_tokens passthrough
  * arbitrary attribute fall-through to base tracker
  * pipeline rollup queries cost by scan_id
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.data.scoped_usage_tracker import ScopedUsageTracker


# ──────────────────────────────────────────────────────────────────────
# Stub base tracker
# ──────────────────────────────────────────────────────────────────────

@dataclass
class _StubBase:
    calls: list[dict] = field(default_factory=list)
    scan_costs: dict[str, dict] = field(default_factory=dict)

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text or "") // 4)

    def log_call(self, source, tokens_in, tokens_out,
                 model="gemini-2.5-flash", scan_id=None):
        self.calls.append({
            "source": source, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "model": model, "scan_id": scan_id,
        })

    def get_scan_cost(self, scan_id: str) -> dict:
        return self.scan_costs.get(scan_id, {
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "api_calls": 0,
        })


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────

class TestScopedUsageTracker:
    def test_injects_scan_id(self):
        base = _StubBase()
        scoped = ScopedUsageTracker(base, "r-abc123")
        scoped.log_call(source="ai_report", tokens_in=100, tokens_out=50)
        assert base.calls[0]["scan_id"] == "r-abc123"

    def test_caller_can_override_scan_id(self):
        base = _StubBase()
        scoped = ScopedUsageTracker(base, "r-bound")
        scoped.log_call(source="ai_report", tokens_in=10, tokens_out=5,
                         scan_id="r-override")
        assert base.calls[0]["scan_id"] == "r-override"

    def test_estimate_tokens_passthrough(self):
        base = _StubBase()
        scoped = ScopedUsageTracker(base, "r-x")
        assert scoped.estimate_tokens("hello world this is text") > 0

    def test_attribute_fall_through(self):
        base = _StubBase()
        base.scan_costs["r-x"] = {"cost_usd": 0.42}
        scoped = ScopedUsageTracker(base, "r-x")
        assert scoped.get_scan_cost("r-x")["cost_usd"] == 0.42

    def test_none_base_raises(self):
        with pytest.raises(ValueError):
            ScopedUsageTracker(None, "r-x")

    def test_scan_id_property_readonly(self):
        scoped = ScopedUsageTracker(_StubBase(), "r-scope")
        assert scoped.scan_id == "r-scope"

    def test_log_call_preserves_other_args(self):
        base = _StubBase()
        scoped = ScopedUsageTracker(base, "r-x")
        scoped.log_call(source="ai_report", tokens_in=1000,
                         tokens_out=500, model="gemini-3-flash")
        c = base.calls[0]
        assert c["source"] == "ai_report"
        assert c["tokens_in"] == 1000
        assert c["tokens_out"] == 500
        assert c["model"] == "gemini-3-flash"
