"""Structured JSONL logging for chat tool execution.

Every tool dispatch writes a line to `data/logs/chat_tools/tools.jsonl`
with tool_name, args, result_size, elapsed_ms, and any error.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = os.path.join("data", "logs", "chat_tools")


class ToolLogger:
    """Append-only JSONL logger for tool call telemetry."""

    def __init__(self, log_dir: str | None = None):
        self._log_dir = Path(log_dir or _DEFAULT_LOG_DIR)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / "tools.jsonl"

    def log_call(
        self,
        tool_name: str,
        args: dict,
        result_size: int,
        elapsed_ms: float,
        error: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Write one JSONL record for a tool call."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "args": _safe_serialize(args),
            "result_size": result_size,
            "elapsed_ms": round(elapsed_ms, 1),
            "error": error,
            "session_id": session_id,
        }
        try:
            line = json.dumps(record, separators=(",", ":"))
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            logger.debug("Failed to write tool log", exc_info=True)

    def read_recent(self, n: int = 20) -> list[dict]:
        """Read the last n log entries (newest first)."""
        if not self._log_path.exists():
            return []
        lines = self._log_path.read_text(encoding="utf-8").strip().split("\n")
        entries = []
        for line in reversed(lines[-n:]):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries


# Module-level singleton for decorator usage
_default_logger: ToolLogger | None = None


def _get_logger() -> ToolLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = ToolLogger()
    return _default_logger


def log_tool_call(func: Callable) -> Callable:
    """Decorator that auto-logs tool call timing and errors."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool_name = func.__name__
        call_args = kwargs.copy()
        start = time.perf_counter()
        error = None
        result = None
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            result_size = _estimate_size(result)
            _get_logger().log_call(
                tool_name=tool_name,
                args=call_args,
                result_size=result_size,
                elapsed_ms=elapsed,
                error=error,
            )

    return wrapper


def _safe_serialize(obj: Any) -> Any:
    """Convert args to JSON-safe form, truncating large values."""
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        if len(obj) > 10:
            return [_safe_serialize(v) for v in obj[:10]] + [f"...+{len(obj)-10}"]
        return [_safe_serialize(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:200] + "..."
    return obj


def _estimate_size(result: Any) -> int:
    """Rough byte estimate of a result for telemetry."""
    if result is None:
        return 0
    try:
        return len(json.dumps(result, default=str))
    except (TypeError, ValueError):
        return 0
