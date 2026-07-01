"""Ask-first research runner (M6a) — the engine, decoupled + testable.

Executes an approved research plan step-by-step, writing a per-task manifest MD
into research_store and driving the agent_jobs progress. Cancellation is
cooperative: the caller supplies ``is_cancelled()`` (a cheap agent_jobs read)
which is checked at each step boundary — a running LLM call can't be interrupted
mid-request, so cancel takes effect at the next step.

DECOUPLING: the LLM call is injected as ``generate(prompt) -> str`` so the runner
holds no provider coupling and no PHI/warehouse access. Prompts are built ONLY
from the task title + plan steps + prior step output — never ticket/warehouse
text. ``default_generate()`` wires the real Claude client via
build_client_for_task('enablement_research').
"""
from __future__ import annotations

from typing import Callable, Optional

from src.data import agent_jobs, research_store


def default_generate() -> Callable[[str], str]:
    """Build the real enablement-research LLM caller (Claude, PII-safe lane)."""
    from src.gemini.client_factory import build_client_for_task
    client = build_client_for_task("enablement_research")

    def _gen(prompt: str) -> str:
        out = client.generate(prompt)
        # clients return either a str or an object with .text — normalize.
        return out if isinstance(out, str) else getattr(out, "text", str(out))
    return _gen


def _step_prompt(task_title: str, step: str, prior: list[str]) -> str:
    context = "\n".join(prior[-3:])  # last few sections for continuity, bounded
    return (
        "You are Renn, an enablement research assistant. You research a work task "
        "using ONLY general knowledge and the operator's own enablement content — "
        "never patient or ticket data.\n\n"
        f"Task: {task_title}\n"
        f"Research step: {step}\n\n"
        f"Prior findings so far:\n{context}\n\n"
        "Write a concise, well-structured markdown section that completes this step. "
        "Be specific and actionable; no preamble."
    )


def run_research_job(conn, *, job_id: str, task_id: str, research_id: str,
                     task_title: str, steps: list[str],
                     generate: Callable[[str], str],
                     is_cancelled: Optional[Callable[[], bool]] = None) -> dict:
    """Run the plan; write the manifest + drive the job. Returns a status dict.

    On cancel/error the manifest keeps whatever partial MD exists and the job +
    research row are marked accordingly; no further LLM calls are made.
    """
    md_parts = [f"# Research — {task_title or task_id}\n"]
    total = max(1, len(steps))
    for i, step in enumerate(steps):
        if is_cancelled and is_cancelled():
            research_store.update_research(conn, research_id, status="cancelled",
                                           markdown="\n".join(md_parts))
            agent_jobs.update_job(conn, job_id, status="cancelled")
            return {"status": "cancelled", "markdown": "\n".join(md_parts)}
        try:
            answer = generate(_step_prompt(task_title, step, md_parts))
        except Exception as exc:  # noqa: BLE001 — a failed step ends the job cleanly
            research_store.update_research(conn, research_id, status="error",
                                           markdown="\n".join(md_parts))
            agent_jobs.update_job(conn, job_id, status="error", summary=str(exc)[:200])
            return {"status": "error", "error": str(exc)}
        md_parts.append(f"## {step}\n\n{answer}\n")
        agent_jobs.update_job(conn, job_id, progress_pct=int((i + 1) / total * 100))
    markdown = "\n".join(md_parts)
    research_store.update_research(conn, research_id, status="complete",
                                   markdown=markdown,
                                   summary=(steps[0] if steps else task_title)[:200])
    agent_jobs.update_job(conn, job_id, status="done", progress_pct=100)
    return {"status": "complete", "markdown": markdown}
