"""
Alma Insights — Zendesk Monitor (Phase 3 + 3.5)

Background polling service that fetches new tickets from Zendesk at a
configurable interval.  Computes TRC spike alerts by comparing recent
60-minute window against the prior 60-minute window.

Phase 3.5 additions:
    - Optional SourceWarehouse integration (cold tier persistence)
    - Optional WatchlistEngine integration (rule-based alerting)
    - alert_fired signal (from SourceMonitor base class)

Signals:
    tickets_received(list)          — new tickets from latest fetch
    records_received(list)          — source-agnostic alias
    spike_detected(str, int, float) — (trc_code, count, delta_pct)
    alert_fired(dict)               — watchlist alert
    status_changed(str)             — "live" | "paused" | "error"

Usage (from MainWindow):
    warehouse = SourceWarehouse(db)
    watchlist = WatchlistEngine(db, warehouse)
    monitor = ZendeskMonitor(db, warehouse=warehouse, watchlist=watchlist,
                             parent=self)
    monitor.tickets_received.connect(page.on_tickets_received)
    monitor.alert_fired.connect(page.on_alert_fired)
    monitor.start(interval_seconds=120)
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from threading import Thread

from PySide6.QtCore import QTimer, Signal

from src.data.source_types import SourceMonitor

logger = logging.getLogger("alma.zendesk_monitor")

_DEFAULT_INTERVAL = 120  # seconds
_SPIKE_THRESHOLD = 0.25  # 25% increase triggers spike alert
_SPIKE_WINDOW_MINUTES = 60


class ZendeskMonitor(SourceMonitor):
    """Polls Zendesk for new tickets and emits spike alerts.

    Inherits from SourceMonitor which provides:
        records_received(list), spike_detected(str, int, float),
        alert_fired(dict), status_changed(str)
    """

    # Backward-compat alias — existing UI connects to this name
    tickets_received = Signal(list)

    def __init__(self, db_manager, warehouse=None, watchlist=None,
                 parent=None):
        super().__init__(parent)
        self.db = db_manager
        self._warehouse = warehouse
        self._watchlist = watchlist
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._status = "paused"
        self._fetching = False
        self._interval = _DEFAULT_INTERVAL

        # In-memory rolling window for spike detection
        # {trc_code: [timestamp, ...]}
        self._trc_timestamps: dict[str, list[float]] = defaultdict(list)
        self._seen_ticket_ids: set[str] = set()
        self._tickets_today: int = 0
        self._last_pull: str = ""

    # ── public API ──────────────────────────────────────────

    @property
    def source_name(self) -> str:
        return "zendesk"

    @property
    def status(self) -> str:
        return self._status

    @property
    def tickets_today(self) -> int:
        return self._tickets_today

    @property
    def last_pull(self) -> str:
        return self._last_pull

    def start(self, interval_seconds: int = _DEFAULT_INTERVAL):
        """Begin polling at the given interval."""
        self._interval = max(30, interval_seconds)
        self._status = "live"
        self.status_changed.emit("live")
        self._timer.start(self._interval * 1000)
        # Do an immediate first fetch
        self._on_tick()

    def pause(self):
        """Pause polling without clearing state."""
        self._timer.stop()
        self._status = "paused"
        self.status_changed.emit("paused")

    def resume(self):
        """Resume polling."""
        self._status = "live"
        self.status_changed.emit("live")
        self._timer.start(self._interval * 1000)

    def stop(self):
        """Stop polling and clear state."""
        self._timer.stop()
        self._status = "paused"
        self.status_changed.emit("paused")

    def set_interval(self, seconds: int):
        """Update polling interval."""
        self._interval = max(30, seconds)
        if self._timer.isActive():
            self._timer.start(self._interval * 1000)

    # ── internal ────────────────────────────────────────────

    def _on_tick(self):
        """Timer callback — fetch tickets in background thread."""
        if self._fetching:
            return
        self._fetching = True
        t = Thread(target=self._do_fetch, daemon=True)
        t.start()

    def _do_fetch(self):
        """Fetch new tickets from Zendesk (runs in background thread)."""
        try:
            from src.data.zendesk_client import ZendeskClient

            sub, email, key, view_id = ZendeskClient.load_credentials()
            if not (sub and email and key):
                logger.debug("Zendesk not configured — skipping poll")
                self._fetching = False
                return

            client = ZendeskClient(sub, email, key, view_id=view_id)
            trc_field = ZendeskClient.load_trc_field()

            if view_id:
                # View-based fetch — scoped to a specific Zendesk view
                tickets = client.fetch_view_tickets()
                new_cursor = ""
            else:
                # Incremental export — full firehose
                cursor = ZendeskClient.load_cursor()
                tickets, new_cursor = client.fetch_incremental(
                    cursor=cursor or None
                )

            if new_cursor:
                ZendeskClient.save_cursor(new_cursor)

            # Filter out duplicates
            new_tickets = []
            for t in tickets:
                tid = str(t.get("id", ""))
                if tid and tid not in self._seen_ticket_ids:
                    self._seen_ticket_ids.add(tid)
                    new_tickets.append(t)

            if new_tickets:
                self._tickets_today += len(new_tickets)
                self._last_pull = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )

                # Update TRC timestamp window for spike detection
                now = time.time()
                for t in new_tickets:
                    trc = ZendeskClient.extract_trc(t, trc_field)
                    self._trc_timestamps[trc].append(now)

                # ── Persist to warehouse (cold tier, no PHI) ──
                if self._warehouse:
                    try:
                        self._warehouse.ingest_records(
                            new_tickets, source=self.source_name,
                            trc_field=trc_field, client=client
                        )
                    except Exception as wh_exc:
                        logger.warning("Warehouse ingest failed: %s", wh_exc)

                # ── Evaluate watchlist rules ──
                if self._watchlist:
                    try:
                        alerts = self._watchlist.evaluate(
                            new_tickets, source=self.source_name,
                            trc_field=trc_field, client=client
                        )
                        for alert in alerts:
                            self.alert_fired.emit(alert)
                    except Exception as wl_exc:
                        logger.warning("Watchlist evaluate failed: %s", wl_exc)

                # Emit signals (safe to call from thread — Qt queues cross-thread)
                self.tickets_received.emit(new_tickets)
                self.records_received.emit(new_tickets)

                # Check for spikes
                self._check_spikes()

            self._last_pull = datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )

        except Exception as exc:
            logger.exception("Zendesk fetch error: %s", exc)
            self._status = "error"
            self.status_changed.emit("error")

        finally:
            self._fetching = False

    def _check_spikes(self):
        """Compare TRC counts in last 60 min vs prior 60 min.

        Emits ``spike_detected`` if delta exceeds threshold.
        """
        now = time.time()
        window = _SPIKE_WINDOW_MINUTES * 60
        recent_start = now - window
        prior_start = now - (2 * window)

        for trc, timestamps in self._trc_timestamps.items():
            # Prune old entries (older than 2 hours)
            self._trc_timestamps[trc] = [
                ts for ts in timestamps if ts > prior_start
            ]

            recent = sum(1 for ts in self._trc_timestamps[trc]
                         if ts >= recent_start)
            prior = sum(1 for ts in self._trc_timestamps[trc]
                        if prior_start <= ts < recent_start)

            if prior > 0:
                delta_pct = (recent - prior) / prior
                if delta_pct >= _SPIKE_THRESHOLD:
                    logger.info(
                        "TRC spike: %s — %d vs %d (%.0f%%)",
                        trc, recent, prior, delta_pct * 100
                    )
                    self.spike_detected.emit(trc, recent, delta_pct)
            elif recent >= 3:
                # No prior data but ≥3 tickets in recent window — flag
                self.spike_detected.emit(trc, recent, 1.0)

    def get_spike_summary(self) -> list[dict]:
        """Return current TRC counts for the spike table."""
        now = time.time()
        window = _SPIKE_WINDOW_MINUTES * 60
        recent_start = now - window
        prior_start = now - (2 * window)

        results = []
        for trc, timestamps in self._trc_timestamps.items():
            recent = sum(1 for ts in timestamps if ts >= recent_start)
            prior = sum(1 for ts in timestamps
                        if prior_start <= ts < recent_start)
            delta = ((recent - prior) / prior * 100) if prior > 0 else 0
            results.append({
                "trc": trc,
                "count": recent,
                "prior": prior,
                "delta_pct": delta,
            })
        results.sort(key=lambda x: x["delta_pct"], reverse=True)
        return results
