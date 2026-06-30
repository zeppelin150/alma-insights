"""Chat-tool handlers for the Agent job spine (M4).

Thin wrappers over ``src.data.agent_jobs``. Session-scoped via the active-session
pointer file the host writes (``ALMA_CHAT_SESSION_FILE``) — the same mechanism
the MCP server uses to tag tool executions — so a job is owned by the chat that
spawned it and the Agent sidebar only ever shows its own jobs.

Handlers follow the registry contract: ``handle_*(conn, args, filters) -> dict``.
"""

from __future__ import annotations

import os

from src.data import agent_jobs


def _active_session_id() -> str | None:
    path = os.environ.get("ALMA_CHAT_SESSION_FILE", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def handle_create_job(conn, args: dict, filters: dict) -> dict:
    """Create a tracked multi-phase job. ``steps`` is an ordered list of phase
    names (or a comma-separated string)."""
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "missing_title", "message": "create_job requires a 'title'."}
    steps = args.get("steps") or []
    if isinstance(steps, str):
        steps = [s.strip() for s in steps.split(",") if s.strip()]
    job_id = agent_jobs.create_job(
        conn, title=title, kind=args.get("kind"),
        session_id=_active_session_id(), steps=list(steps))
    job = agent_jobs.get_job(conn, job_id) or {}
    return {
        "ok": True, "job_id": job_id,
        "steps": [{"step_id": s["step_id"], "ordinal": s["ordinal"], "name": s["name"]}
                  for s in job.get("steps", [])],
    }


def handle_update_job(conn, args: dict, filters: dict) -> dict:
    """Update a job's status/progress/summary and/or one step (by ordinal)."""
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return {"error": "missing_job_id", "message": "update_job requires a 'job_id'."}
    if agent_jobs.get_job(conn, job_id) is None:
        return {"error": "job_not_found", "message": f"No job '{job_id}'."}

    job_fields = {k: args[k] for k in ("status", "progress_pct", "summary",
                                       "tokens_in", "tokens_out", "cost_usd", "error")
                  if args.get(k) is not None}
    if job_fields:
        agent_jobs.update_job(conn, job_id, **job_fields)

    updated_step = None
    if args.get("step_ordinal") is not None:
        try:
            target = int(args["step_ordinal"])
        except (TypeError, ValueError):
            target = None
        if target is not None:
            match = next((s for s in agent_jobs.list_steps(conn, job_id)
                          if s["ordinal"] == target), None)
            if match:
                sf = {}
                if args.get("step_status") is not None:
                    sf["status"] = args["step_status"]
                if args.get("step_detail") is not None:
                    sf["detail"] = args["step_detail"]
                if sf:
                    agent_jobs.update_step(conn, match["step_id"], **sf)
                    updated_step = target

    job = agent_jobs.get_job(conn, job_id) or {}
    return {"ok": True, "job_id": job_id, "status": job.get("status"),
            "progress_pct": job.get("progress_pct"), "updated_step": updated_step}


def handle_list_jobs(conn, args: dict, filters: dict) -> dict:
    """List recent jobs for the active session."""
    jobs = agent_jobs.list_jobs(
        conn, session_id=_active_session_id(),
        status=args.get("status"), limit=int(args.get("limit", 20)))
    return {"jobs": jobs, "count": len(jobs)}
