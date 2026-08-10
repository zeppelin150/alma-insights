"""Construction helper for the #/task web triple (WS-D-WEB M2).

Keeps page.py's gain to pure wiring: one lazy getter + the drilldown branch.
Anything here may raise — the caller's construction-failure fallback to the
native ``TaskDetailPanel`` is the safety net (pattern: page.py::_make_zendesk).

The triple is built ONCE per session and reused across every task open (one
Chromium render process — the M1/16GB production budget). The bridge is
constructed fully wired BEFORE WebHost registers it on the channel (cee4c86
rule: no connections on a channel-registered object after registration).
"""

from __future__ import annotations


def build_task_web_triple(*, write_fn, refresh_fn, open_url_fn,
                          open_subtask_fn=None, open_parent_fn=None):
    """(controller, bridge, host) for the task drilldown, fully wired."""
    from src.data.asana_client import AsanaClient
    from src.services.task_web import TaskWebController
    from src.ui.web.task_bridge import TaskBridge
    from src.ui.web.web_host import WebHost

    ctrl = TaskWebController(
        write_fn=write_fn, refresh_fn=refresh_fn, open_url_fn=open_url_fn,
        open_subtask_fn=open_subtask_fn, open_parent_fn=open_parent_fn,
        client_factory=AsanaClient.from_store)
    bridge = TaskBridge(
        data_signal=ctrl.task_data, status_signal=ctrl.status_text,
        resolved_signal=ctrl.action_resolved,
        refresh_fn=ctrl.js_refresh, refresh_task_fn=ctrl.js_refresh_task,
        complete_fn=ctrl.js_toggle_complete, due_fn=ctrl.js_set_due,
        comment_fn=ctrl.js_post_comment, subtask_fn=ctrl.js_add_subtask,
        subtask_toggle_fn=ctrl.js_toggle_subtask,
        subtask_open_fn=ctrl.js_open_subtask,
        parent_open_fn=ctrl.js_open_parent,
        description_fn=ctrl.js_update_description,
        attachment_fn=ctrl.js_open_attachment, url_fn=ctrl.js_open_url)
    host = WebHost(bridge=bridge, channel_name="taskBridge", route="/task",
                   log_name="alma.enablement.web.task")
    return ctrl, bridge, host
