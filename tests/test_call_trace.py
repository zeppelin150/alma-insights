"""Verify the call_trace infra records inputs + outputs (deterministic; no real LLM)."""

from __future__ import annotations

import types

import pytest

from src.data import call_trace


@pytest.fixture
def captured(monkeypatch):
    monkeypatch.setenv("ALMA_TRACE", "1")
    call_trace.reset_sinks()
    records: list[dict] = []
    call_trace.add_sink(records.append)
    yield records
    call_trace.reset_sinks()


def test_records_input_and_output(captured):
    @call_trace.trace
    def add(a, b, *, label="x"):
        return {"sum": a + b, "label": label}

    assert add(2, 3, label="hi") == {"sum": 5, "label": "hi"}
    rec = [r for r in captured if r["fn"].endswith("add")][-1]
    assert rec["args"] == ["2", "3"]                 # inputs recorded (ints repr'd)
    assert rec["kwargs"] == {"label": "hi"}          # strings stored raw (readable prompts)
    assert "'sum': 5" in rec["result"]               # output recorded
    assert isinstance(rec["elapsed_ms"], (int, float))


def test_records_exceptions(captured):
    @call_trace.trace
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()
    rec = [r for r in captured if r["fn"].endswith("boom")][-1]
    assert "ValueError: nope" in rec["error"]


def test_truncates_huge_values(captured, monkeypatch):
    monkeypatch.setenv("ALMA_TRACE_MAXLEN", "50")

    @call_trace.trace
    def echo(s):
        return s

    echo("x" * 500)
    rec = [r for r in captured if r["fn"].endswith("echo")][-1]
    assert "+450 chars" in rec["result"]


def test_disabled_is_passthrough(monkeypatch):
    monkeypatch.delenv("ALMA_TRACE", raising=False)
    call_trace.reset_sinks()
    seen: list = []
    call_trace.add_sink(seen.append)

    @call_trace.trace
    def f(x):
        return x * 2

    assert f(21) == 42
    assert seen == []                                # nothing recorded when disabled
    call_trace.reset_sinks()


def test_instrument_module(captured):
    mod = types.ModuleType("dummymod")

    def foo(x):
        return x + 1
    foo.__module__ = "dummymod"
    mod.foo = foo

    assert call_trace.instrument_module(mod) == 1
    assert mod.foo(41) == 42
    assert any(r["fn"].endswith("foo") for r in captured)


def test_install_enablement_tracing_returns_summary(monkeypatch):
    monkeypatch.setenv("ALMA_TRACE", "1")
    summary = call_trace.install_enablement_tracing()
    assert summary.get("GeminiClient", 0) >= 1                    # .generate wrapped
    assert any(k.endswith("enablement_store") for k in summary)
    assert summary.get("registry.dispatch_tool", 0) >= 1
