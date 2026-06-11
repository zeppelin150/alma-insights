"""Call tracing — record every (instrumented) function's input + output.

A TEST/DEBUG aid for validating the enablement back-end end-to-end. OFF by
default; turn on with ``ALMA_TRACE=1`` (env). When on, each instrumented call is
recorded as one JSONL row ``{ts, fn, args, kwargs, result|error, elapsed_ms}`` to
a trace file AND emitted to the ``alma.trace`` logger.

It records FULL inputs and outputs (including LLM prompts/responses and DB rows),
so keep it gated — it must never run in normal/production use.

Usage:
    from src.data import call_trace
    path = call_trace.add_file_sink()          # where rows are written
    call_trace.install_enablement_tracing()    # wrap the enablement back-end
    ...                                         # run the flow
    # → every save_document / create_task / dispatch_tool / GeminiClient.generate /
    #   GuruClient.create_card / AsanaClient.list_tasks / ... call is recorded.

Env:
    ALMA_TRACE=1            enable tracing
    ALMA_TRACE_FILE=<path>  override the trace file (default data/trace/trace_*.jsonl)
    ALMA_TRACE_MAXLEN=N     truncate each arg/result repr to N chars (default 12000)
"""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("alma.trace")

_LOCK = threading.RLock()
_SINKS: list = []


# ── enablement / config ──────────────────────────────────────────────

def trace_enabled() -> bool:
    return os.environ.get("ALMA_TRACE", "").strip().lower() in ("1", "true", "yes", "on")


def _maxlen() -> int:
    try:
        return int(os.environ.get("ALMA_TRACE_MAXLEN", "12000"))
    except ValueError:
        return 12000


def _short(value) -> str:
    """A bounded, JSON-safe repr of an arg or result."""
    try:
        s = value if isinstance(value, str) else repr(value)
    except Exception as exc:  # noqa: BLE001 — repr must never break a traced call
        s = f"<unrepr {type(value).__name__}: {exc}>"
    m = _maxlen()
    return s if len(s) <= m else s[:m] + f"…(+{len(s) - m} chars)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── sinks ────────────────────────────────────────────────────────────

def _emit(rec: dict) -> None:
    with _LOCK:
        sinks = list(_SINKS)
    for sink in sinks:
        try:
            sink(rec)
        except Exception:  # noqa: BLE001 — a sink must never break the traced call
            pass


def add_sink(fn) -> None:
    """Register a callable(record: dict) sink."""
    with _LOCK:
        _SINKS.append(fn)


def add_file_sink(path: str | None = None) -> str:
    """Append each trace record as one JSON line to ``path``. Returns the path."""
    path = path or os.environ.get("ALMA_TRACE_FILE") or _default_path()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def _sink(rec: dict) -> None:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")

    add_sink(_sink)
    return str(p)


def reset_sinks() -> None:
    with _LOCK:
        _SINKS.clear()


def _default_path() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return str(Path("data") / "trace" / f"trace_{stamp}.jsonl")


# ── the tracer ───────────────────────────────────────────────────────

def trace(fn, label: str | None = None):
    """Wrap a callable so each call records its inputs + output (when enabled).

    Pass-through with near-zero overhead while ``ALMA_TRACE`` is unset.
    """
    name = label or (
        f"{getattr(fn, '__module__', '?')}."
        f"{getattr(fn, '__qualname__', getattr(fn, '__name__', 'fn'))}"
    )

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not trace_enabled():
            return fn(*args, **kwargs)
        rec: dict = {
            "ts": _now(),
            "fn": name,
            "args": [_short(a) for a in args],
            "kwargs": {k: _short(v) for k, v in kwargs.items()},
        }
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except BaseException as exc:
            rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            rec["error"] = f"{type(exc).__name__}: {exc}"
            _emit(rec)
            logger.info("TRACE %s -> ERROR %s [%sms]", name, rec["error"], rec["elapsed_ms"])
            raise
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        rec["result"] = _short(result)
        _emit(rec)
        logger.info("TRACE %s -> %s [%sms]", name, rec["result"], rec["elapsed_ms"])
        return result

    wrapper.__alma_traced__ = True
    return wrapper


# ── instrumentation ──────────────────────────────────────────────────

def instrument_module(module, names=None, include_private: bool = False) -> int:
    """Wrap a module's functions with ``trace()``. Idempotent. Returns the count.

    With ``names=None`` only functions DEFINED in the module are wrapped (imported
    names are skipped); private (``_``-prefixed) names are skipped unless
    ``include_private``. Pass an explicit ``names`` list to wrap exactly those.
    """
    count = 0
    candidates = list(names) if names is not None else [
        n for n in vars(module) if include_private or not n.startswith("_")
    ]
    for attr in candidates:
        obj = getattr(module, attr, None)
        if not callable(obj) or isinstance(obj, type):
            continue
        if getattr(obj, "__alma_traced__", False):
            continue
        if names is None and getattr(obj, "__module__", None) != getattr(module, "__name__", None):
            continue
        setattr(module, attr, trace(obj))
        count += 1
    return count


def instrument_methods(cls, names) -> int:
    """Wrap named methods on a class with ``trace()``. Idempotent."""
    count = 0
    for attr in names:
        obj = getattr(cls, attr, None)
        if callable(obj) and not getattr(obj, "__alma_traced__", False):
            setattr(cls, attr, trace(obj, label=f"{cls.__module__}.{cls.__name__}.{attr}"))
            count += 1
    return count


def install_enablement_tracing() -> dict:
    """Instrument the whole enablement back-end + its external API clients.

    Idempotent. Returns a {target: wrapped_count} summary.
    """
    summary: dict = {}

    from src.data import (
        asana_monitor, asana_setup, drive_monitor, drive_query,
        enablement_sources, enablement_store, enablement_tasks,
    )
    from src.data.chat_tools import enablement_tools, registry

    for mod in (enablement_store, enablement_tasks, enablement_sources,
                asana_setup, drive_query, asana_monitor, drive_monitor):
        summary[mod.__name__] = instrument_module(mod)
    # tool handlers + their shared impls (both chat paths' logic)
    summary[enablement_tools.__name__] = instrument_module(enablement_tools, include_private=True)
    summary["registry.dispatch_tool"] = instrument_module(registry, names=["dispatch_tool"])

    # External API boundaries — the inputs/outputs most worth seeing (LLM prompts
    # + responses, Guru/Asana/Drive calls).
    try:
        from src.gemini.gemini_client import GeminiClient
        summary["GeminiClient"] = instrument_methods(GeminiClient, ["generate", "generate_streaming"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.llm.claude_cli_client import ClaudeCliClient
        summary["ClaudeCliClient"] = instrument_methods(ClaudeCliClient, ["generate", "generate_streaming"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.data.guru_client import GuruClient
        summary["GuruClient"] = instrument_methods(
            GuruClient, ["create_card", "update_card", "get_card", "search_cards",
                         "list_cards", "list_collections", "test_connection"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.data.asana_client import AsanaClient
        summary["AsanaClient"] = instrument_methods(
            AsanaClient, ["list_tasks", "list_workspace_users", "get_custom_fields",
                          "list_projects", "list_workspaces", "discover", "test_connection"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.data.drive_reader import DriveReader
        summary["DriveReader"] = instrument_methods(
            DriveReader, ["list_changed_files", "export_text", "search_files", "test_connection"])
    except Exception:  # noqa: BLE001
        pass

    return summary
