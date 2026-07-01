"""Pluggable task-source registry (M4).

Each connector registers a ``TaskSourceSpec`` at import time, so ``scan_all``,
``run_monitor_now``, and (future) the Settings source list are data-driven —
adding a source is one registration, not an edit in several places. This is a
behavior-preserving refactor of the previous hardcoded asana+drive+guru block.

Registration passes ``poll`` as a small lambda that references the connector's
module-level ``poll_once`` by *name* (resolved at call time), so monkeypatching
``<module>.poll_once`` is honored by the registry — the timer, the chat tool,
and tests all share the one code path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

_REGISTRY: dict[str, "TaskSourceSpec"] = {}
_LOADED = False

# Connector modules imported so their bottom-of-file register() runs.
_CONNECTOR_MODULES = ("asana_monitor", "drive_monitor", "guru_analytics_monitor")


@dataclass
class TaskSourceSpec:
    name: str                                 # 'asana' | 'drive' | 'guru'
    poll: Callable                            # poll(conn, **kw) → the connector's poll_once
    display_name: str = ""
    kind: str = "ingest"                      # 'ingest' (creates tasks) | 'analytics'
    enabled_check: Optional[Callable] = None  # () -> bool; None = always eligible


def register(spec: "TaskSourceSpec") -> None:
    """Upsert by name (idempotent across re-imports)."""
    _REGISTRY[spec.name] = spec


def get(name: str) -> Optional["TaskSourceSpec"]:
    return _REGISTRY.get(name)


def all_specs() -> list["TaskSourceSpec"]:
    return list(_REGISTRY.values())


def ingest_specs() -> list["TaskSourceSpec"]:
    """The default scan set — ingest-kind sources (asana, drive today), matching
    the previous run_monitor_now default of ('asana', 'drive')."""
    return [s for s in _REGISTRY.values() if s.kind == "ingest"]


def ensure_sources_loaded() -> None:
    """Import the connector modules so their bottom-of-file register() runs."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True  # set first so a connector import can't re-enter this
    for mod in _CONNECTOR_MODULES:
        try:
            __import__(f"src.data.{mod}")
        except Exception:  # noqa: BLE001 — a missing/broken connector can't break scan
            pass
