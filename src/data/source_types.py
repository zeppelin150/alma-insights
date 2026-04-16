"""
Alma Insights — Source Abstraction Layer (Phase 3.5)

Thin ABC interfaces that define the contract for any data source.
Implementations: ZendeskClient (P3), future IntercomClient, JiraClient, etc.

Adding a new source requires:
1. Implement SourceClient ABC (~200 lines)
2. Extend SourceMonitor ABC (~220 lines)
3. Add a Connection UI section
That's it — warehouse, watchlist, and Guru effectiveness work automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal


@dataclass
class SourceConfig:
    """Describes a configured data source instance."""

    name: str              # "zendesk", "intercom", "jira"
    display_name: str      # "Zendesk Support"
    icon: str = ""         # emoji or icon path
    is_configured: bool = False
    is_connected: bool = False
    extra: dict = field(default_factory=dict)  # source-specific metadata


class SourceClient(ABC):
    """Base class for all source API clients.

    Defines the minimum contract a source client must satisfy so the
    warehouse and watchlist engine can operate source-agnostically.

    Implementations:
        ZendeskClient  (src/data/zendesk_client.py)
    """

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify credentials by calling a lightweight API endpoint.

        Returns True if authenticated, False otherwise.
        """
        ...

    @abstractmethod
    def fetch_incremental(self, cursor: str | None = None,
                          start_time: int | None = None
                          ) -> tuple[list[dict], str]:
        """Fetch records since cursor.

        Args:
            cursor: Opaque cursor from previous call. None = start fresh.
            start_time: Unix epoch fallback when cursor is None.

        Returns:
            (records, next_cursor) — list of record dicts and the cursor
            for the next call.
        """
        ...

    @abstractmethod
    def extract_trc(self, record: dict, trc_field: str = "subject") -> str:
        """Extract TRC code from a record using configured field mapping.

        Args:
            record: Raw record dict from this source's API.
            trc_field: Field mapping string (source-specific options).

        Returns:
            Extracted TRC string, or "unknown" if not found.
        """
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Return the source identifier string (e.g. 'zendesk').

        Used as the ``source`` column value in warehouse tables.
        Must be lowercase, alphanumeric, no spaces.
        """
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if all required credentials are present."""
        ...


class SourceMonitor(QObject):
    """Base class for real-time polling monitors.

    Emits source-agnostic signals consumed by SourceMonitorPage,
    SourceWarehouse, and WatchlistEngine.

    Note: Uses NotImplementedError stubs instead of ABC because
    QObject's metaclass conflicts with ABCMeta in PySide6.

    Implementations:
        ZendeskMonitor  (src/data/zendesk_monitor.py)
    """

    # ── Signals (inherited by all subclasses) ──
    records_received = Signal(list)            # raw record dicts
    spike_detected = Signal(str, int, float)   # (trc, count, delta_pct)
    alert_fired = Signal(dict)                 # watchlist alert
    status_changed = Signal(str)               # "live"|"paused"|"error"

    def start(self, interval_seconds: int = 120) -> None:
        """Begin polling at the given interval."""
        raise NotImplementedError

    def pause(self) -> None:
        """Pause polling without clearing state."""
        raise NotImplementedError

    def resume(self) -> None:
        """Resume polling."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop polling and clear state."""
        raise NotImplementedError

    @property
    def source_name(self) -> str:
        """Return the source identifier string (e.g. 'zendesk')."""
        raise NotImplementedError

    @property
    def status(self) -> str:
        """Return current status: 'live', 'paused', or 'error'."""
        raise NotImplementedError
