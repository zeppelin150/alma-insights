"""
Source Monitor page — backward-compat shim.

The original 1508-LOC implementation was decomposed on 2026-05-07 into
a package at ``src/ui/pages/source_monitor/`` with one module per tab.
This file re-exports ``SourceMonitorPage`` so any caller that still
imports from ``src.ui.pages.source_monitor_page`` continues to work
unchanged.

For new code, prefer ``from src.ui.pages.source_monitor import
SourceMonitorPage``.

See ``docs/SOURCE_MONITOR.md`` for the architecture reference.
"""

from src.ui.pages.source_monitor import SourceMonitorPage  # noqa: F401

__all__ = ["SourceMonitorPage"]
