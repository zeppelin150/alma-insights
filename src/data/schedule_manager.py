"""
Alma Insights — Schedule Manager Service (Phase 5.5C)

Polls the `report_schedules` DB table every 60 seconds and emits
`run_triggered(dict)` when a schedule is due. Handles next-run computation
for daily / weekly / biweekly / monthly recurrence with timezone support.

The SmartReportingPage wires this to its pipeline worker so schedules
survive app restarts (persisted in SQLite, not ephemeral QTimer).

Usage:
    mgr = ScheduleManager(db)
    mgr.run_triggered.connect(my_handler)
    mgr.start()    # begins polling
    mgr.stop()     # pauses polling (schedules persist in DB)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("alma.schedule_manager")

# Attempt zoneinfo (Python 3.9+); fall back to UTC-only
try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False

# Supported timezones for the UI dropdown
SUPPORTED_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "UTC",
]

# Human-readable labels
TIMEZONE_LABELS = {
    "America/New_York": "Eastern (ET)",
    "America/Chicago": "Central (CT)",
    "America/Denver": "Mountain (MT)",
    "America/Los_Angeles": "Pacific (PT)",
    "UTC": "UTC",
}


def _now_in_tz(tz_name: str) -> datetime:
    """Return the current datetime in the given timezone (or local if unavailable)."""
    if _HAS_ZONEINFO and tz_name != "UTC":
        try:
            return datetime.now(tz=ZoneInfo(tz_name))
        except Exception:
            pass
    if tz_name == "UTC":
        from datetime import timezone
        return datetime.now(tz=timezone.utc)
    return datetime.now()


def compute_next_run(repeat_type: str, repeat_day: int,
                     repeat_time: str, tz_name: str = "UTC",
                     after: datetime | None = None) -> str:
    """Compute the next run datetime as an ISO string.

    Args:
        repeat_type: 'daily', 'weekly', 'biweekly', 'monthly'
        repeat_day: Day of week (0=Mon..6=Sun) for weekly/biweekly,
                    day of month (1-28) for monthly. Ignored for daily.
        repeat_time: 'HH:MM' in 24-hour format.
        tz_name: Timezone name (e.g., 'America/New_York').
        after: Compute next run after this datetime. Defaults to now.

    Returns:
        ISO-formatted datetime string.
    """
    now = after or _now_in_tz(tz_name)
    # Strip timezone info for arithmetic (we just need the date/time)
    if hasattr(now, 'tzinfo') and now.tzinfo:
        now = now.replace(tzinfo=None)

    hour, minute = 6, 0
    if repeat_time and ":" in repeat_time:
        parts = repeat_time.split(":")
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

    if repeat_type == "daily":
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    elif repeat_type == "weekly":
        # repeat_day: 0=Mon..6=Sun
        days_ahead = (repeat_day - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(weeks=1)
        return candidate.isoformat()

    elif repeat_type == "biweekly":
        days_ahead = (repeat_day - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(weeks=2)
        return candidate.isoformat()

    elif repeat_type == "monthly":
        # repeat_day: 1-28 (day of month)
        day = max(1, min(28, repeat_day))
        candidate = now.replace(day=day, hour=hour, minute=minute,
                                second=0, microsecond=0)
        if candidate <= now:
            # Advance to next month
            if now.month == 12:
                candidate = candidate.replace(year=now.year + 1, month=1)
            else:
                candidate = candidate.replace(month=now.month + 1)
        return candidate.isoformat()

    # Fallback: 24 hours from now
    return (now + timedelta(hours=24)).isoformat()


class ScheduleManager(QObject):
    """Polls report_schedules and triggers pipeline runs when due.

    Emits:
        run_triggered(dict): config_json + schedule metadata for the due schedule.
    """

    run_triggered = Signal(dict)

    POLL_INTERVAL_MS = 60_000  # 60 seconds

    def __init__(self, db: object, parent: object | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_schedules)

    # ── Public API ──

    def start(self) -> None:
        """Start the schedule polling loop."""
        logger.info("ScheduleManager started (poll every %ds)", self.POLL_INTERVAL_MS // 1000)
        self._timer.start(self.POLL_INTERVAL_MS)
        # Run an immediate check
        self._check_schedules()

    def stop(self) -> None:
        """Stop the schedule polling loop (schedules stay in DB)."""
        self._timer.stop()
        logger.info("ScheduleManager stopped")

    def is_running(self) -> bool:
        return self._timer.isActive()

    # ── Core ──

    def _check_schedules(self):
        """Poll for due schedules and emit run_triggered for each."""
        try:
            schedules = self.db.get_schedules(enabled_only=True)
        except Exception as e:
            logger.debug("Failed to poll schedules: %s", e)
            return

        now_str = datetime.now().isoformat()

        for sched in schedules:
            next_run = sched.get("next_run_at", "")
            if not next_run:
                continue
            if next_run <= now_str:
                self._fire_schedule(sched)

    def _fire_schedule(self, sched):
        """Process a due schedule: emit signal, update DB."""
        schedule_id = sched["schedule_id"]
        logger.info("Schedule '%s' (id=%d) is due — firing", sched["name"], schedule_id)

        # Parse config
        config_json = sched.get("config_json", "{}")
        if isinstance(config_json, str):
            try:
                config = json.loads(config_json)
            except (json.JSONDecodeError, TypeError):
                config = {}
        else:
            config = config_json

        # Add schedule metadata
        config["trigger_source"] = "scheduled"
        config["schedule_id"] = schedule_id
        config["schedule_name"] = sched.get("name", "")

        # Compute next run and update DB
        next_run = compute_next_run(
            repeat_type=sched.get("repeat_type", "weekly"),
            repeat_day=sched.get("repeat_day", 1),
            repeat_time=sched.get("repeat_time", "06:00"),
            tz_name=sched.get("timezone", "UTC"),
        )

        try:
            self.db.update_schedule_last_run(schedule_id, next_run_at=next_run)
        except Exception as e:
            logger.error("Failed to update schedule %d last_run: %s", schedule_id, e)

        # Emit the run
        self.run_triggered.emit(config)


def format_next_run(iso_str: str) -> str:
    """Format an ISO datetime string as a human-readable 'Next run' label.

    Returns e.g. 'Mon Mar 10, 9:00 AM' or empty string if invalid.
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%a %b %d, %-I:%M %p")
    except (ValueError, TypeError):
        # Windows strftime doesn't support %-I
        try:
            dt = datetime.fromisoformat(iso_str)
            return dt.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
        except Exception:
            return iso_str[:16]
