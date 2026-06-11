"""Guru analytics connector — optional EnablementMonitor lane.

Module-level ``poll_once(conn)`` like asana_monitor/drive_monitor so the
timer, the run_monitor_now chat tool (separate MCP process), and tests
share one code path. Default OFF (`enablement.analytics_poll_enabled`);
the Analytics page's own Refresh button is the primary trigger.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("alma.guru_analytics")


def poll_once(conn) -> dict:
    from src.data.settings_manager import get_section
    en = get_section("enablement", {}) or {}
    if not en.get("analytics_poll_enabled", False):
        return {"skipped": "disabled"}
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    if not (email and token):
        return {"skipped": "no_credentials"}
    from src.data import guru_analytics
    try:
        return guru_analytics.sync(conn, GuruClient(email, token))
    except Exception as exc:  # noqa: BLE001 — a poll must never crash the loop
        logger.warning("guru analytics poll failed: %s", exc)
        return {"ok": False, "error": str(exc)}
