"""
Source Monitor page package.

Decomposed from a 1508-LOC ``source_monitor_page.py`` god file
(2026-05-07 redesign) into one cohesive sub-module per tab. The original
public API — ``SourceMonitorPage`` — is re-exported here so any caller
that imports from ``src.ui.pages.source_monitor_page`` continues to
work unchanged.

Files in this package
─────────────────────
- ``page.py``           — top-level page shell (header, status bar, tabs)
- ``rate_tab.py``       — Live Feed tab: rate-per-hour control chart
- ``alerts_tab.py``     — Alerts tab: fired watchlist alerts
- ``watchlist_tab.py``  — Watchlist tab: rule manager (cards)
- ``connection_tab.py`` — Connection tab: Zendesk credentials form
- ``rule_dialog.py``    — Add/Edit Rule modal
- ``_styles.py``        — Shared QSS style constants

See ``docs/SOURCE_MONITOR.md`` for the full architecture reference.
"""

from src.ui.pages.source_monitor.page import SourceMonitorPage

__all__ = ["SourceMonitorPage"]
