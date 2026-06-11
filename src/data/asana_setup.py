"""Asana setup engine — let the assistant (Renn) discover GIDs and write config.

The whole point: a non-technical enablement user should NEVER hunt for Asana
custom-field GIDs or enum-value GIDs. They paste an Asana API key, ask Renn to
find their projects/fields, and Renn resolves the GIDs and writes the board
config. Renn's ONLY write is set_asana_board_config() — it touches monitor_sources
and nothing else, so the assistant can configure Asana but can't change any other
app setting.

discover() uses the live Asana API when a key is available; otherwise it returns
mock discovery so the flow is demonstrable without credentials.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger("alma.asana_setup")

# Realistic Asana shapes (numeric gids) for the demo / offline flow.
MOCK_DISCOVERY = {
    "workspace": {"gid": "120420000001", "name": "Alma Health"},
    "projects": [
        {"gid": "120420000111", "name": "Enablement Requests"},
        {"gid": "120420000222", "name": "Launch Coordination"},
        {"gid": "120420000333", "name": "Provider Onboarding"},
    ],
    "custom_fields": {
        "120420000111": [
            {"gid": "120420000901", "name": "Assigned Team", "type": "enum",
             "enum_options": [
                 {"gid": "120420000951", "name": "Enablement"},
                 {"gid": "120420000952", "name": "Support"},
                 {"gid": "120420000953", "name": "Engineering"}]},
            {"gid": "120420000902", "name": "Urgency", "type": "enum",
             "enum_options": [
                 {"gid": "120420000961", "name": "Low"},
                 {"gid": "120420000962", "name": "Medium"},
                 {"gid": "120420000963", "name": "High"},
                 {"gid": "120420000964", "name": "Urgent"}]},
            {"gid": "120420000903", "name": "Assigned People", "type": "people"},
        ],
    },
}


def is_asana_connected() -> bool:
    """True if an Asana API key is stored."""
    try:
        from src.data.pat_store import load_setting  # noqa: PLC0415
        return bool(load_setting("asana_api_key", ""))
    except Exception:
        return False


def discover(api_key: str | None = None, project_gid: str | None = None) -> dict:
    """Return projects + custom fields (with gids and enum-value gids).

    Live Asana when a key is present; otherwise the mock. If project_gid is given,
    the result is narrowed to that project's custom fields (fetched live if needed).
    """
    key = api_key
    if not key:
        try:
            from src.data.pat_store import load_setting  # noqa: PLC0415
            key = load_setting("asana_api_key", "")
        except Exception:
            key = ""

    data = MOCK_DISCOVERY
    if key:
        try:
            from src.data.asana_client import AsanaClient  # noqa: PLC0415
            client = AsanaClient(key)
            data = client.discover()
            if project_gid and project_gid not in data.get("custom_fields", {}):
                data.setdefault("custom_fields", {})[project_gid] = client.get_custom_fields(project_gid)
        except Exception as exc:  # noqa: BLE001 — fall back to mock on any client error
            logger.warning("Asana discover failed, using mock: %s", exc)
            data = MOCK_DISCOVERY

    if project_gid:
        return {
            "workspace": data.get("workspace"),
            "project": next((p for p in data.get("projects", []) if p["gid"] == project_gid), None),
            "custom_fields": data.get("custom_fields", {}).get(project_gid, []),
        }
    return data


def set_asana_board_config(
    conn: sqlite3.Connection,
    *,
    project_gid: str,
    project_name: str,
    indicator_field_gid: str,
    indicator_field_name: str,
    indicator_value_gid: str,
    indicator_value_name: str,
    priority_field_gid: str | None = None,
    assignee_field_gid: str | None = None,
) -> dict:
    """Renn's ONLY write — persist the resolved GIDs into monitor_sources.

    Scoped on purpose: this is the single mutation the assistant is allowed to make.
    It writes one Asana board's config (keyed by gid) and nothing else.
    """
    source_id = f"asana:{project_gid}"
    config = {
        "project_gid": project_gid,
        "project_name": project_name,
        "indicators": [{
            "field_gid": indicator_field_gid,
            "field_name": indicator_field_name,
            "trigger_value_gids": [indicator_value_gid],
            "trigger_value_names": [indicator_value_name],
        }],
        "mappings": {
            "priority_field_gid": priority_field_gid,
            "assignee_field_gid": assignee_field_gid,
        },
    }
    now = datetime.now(timezone.utc).isoformat()
    # Single upsert — plain execute+commit (not atomic()) so this works whether or
    # not the caller's connection is already mid-transaction (e.g. the Gemini
    # dispatch_tool path holds a transaction open for its telemetry write).
    conn.execute(
        """INSERT INTO monitor_sources
             (source_id, source_type, display_name, config_json, enabled, created_at, updated_at)
           VALUES (?, 'asana', ?, ?, 1, ?, ?)
           ON CONFLICT(source_id) DO UPDATE SET
             display_name=excluded.display_name, config_json=excluded.config_json,
             updated_at=excluded.updated_at""",
        (source_id, project_name, json.dumps(config), now, now),
    )
    conn.commit()
    return {"ok": True, "source_id": source_id, "config": config}


def get_asana_config(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT source_id, display_name, config_json FROM monitor_sources WHERE source_type='asana'"
    ).fetchall()
    return [{"source_id": r[0], "display_name": r[1], "config": json.loads(r[2] or "{}")} for r in rows]
