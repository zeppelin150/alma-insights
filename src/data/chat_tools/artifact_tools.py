"""Chat-tool handlers for the content-studio artifact spine (WS3, renn-calendar-
kb-studio plan).

Thin wrappers over ``src.data.artifact_store`` following the job_tools
precedent: new-tool modules stay OUT of the ~2200-line enablement_tools.py
(the seven-file merge-hotspot rule). Session-scoped via the same
``ALMA_CHAT_SESSION_FILE`` pointer file the MCP server uses.

Handlers follow the registry contract: ``handle_*(conn, args, filters) -> dict``.
Error shapes STEER a weak model instead of dead-ending it (the M9 lesson):
an unknown id returns the nearest recent artifacts, never a bare 'not found'.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.data import artifact_store

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "prompts"

# Exactly-one-of source args for the generate_* tools. Strict JSON schemas
# cannot express exclusivity, so handlers validate it themselves and STEER.
_SOURCE_ARGS = ("source_task_id", "source_research_id", "source_doc_id", "source_text")


def _active_session_id() -> str | None:
    path = os.environ.get("ALMA_CHAT_SESSION_FILE", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _summary_row(a: dict) -> dict:
    """The list/steer payload: enough to pick an artifact, no spec bodies."""
    return {
        "artifact_id": a.get("artifact_id"), "kind": a.get("kind"),
        "title": a.get("title"), "status": a.get("status"),
        "task_id": a.get("task_id"), "card_id": a.get("card_id"),
        "draft_id": a.get("draft_id"), "file_path": a.get("file_path"),
        "updated_at": a.get("updated_at"),
    }


def steer_unknown_artifact(conn, artifact_id: str) -> dict:
    """Shared steering error for a bad/hallucinated artifact_id — enumerates
    the most recent artifacts so the model self-corrects in one turn."""
    recent = artifact_store.list_artifacts(conn, limit=5)
    return {
        "ok": False, "error": "artifact_not_found",
        "message": (f"No artifact '{artifact_id}'. Pick one of the recent "
                    f"artifacts below, or list_artifacts for more."),
        "recent_artifacts": [_summary_row(a) for a in recent],
    }


def _load_source(conn, args: dict) -> dict:
    """Resolve the exactly-one source arg into ``{"ok", "text", "links",
    "source_ref"}`` — steering errors enumerate valid options (the M9 lesson;
    Haiku passes zero sources, several, or hallucinated ids)."""
    provided = [k for k in _SOURCE_ARGS if (args.get(k) or "").strip()]
    if len(provided) != 1:
        return {
            "ok": False, "error": "source_required",
            "message": ("Pass exactly ONE source: " + " | ".join(_SOURCE_ARGS)
                        + f" (got {len(provided)})."),
        }
    key = provided[0]
    value = str(args[key]).strip()
    if key == "source_text":
        return {"ok": True, "text": value[:8000], "links": {}, "source_ref": "inline"}
    if key == "source_task_id":
        from src.data import enablement_tasks
        task = enablement_tasks.get_task(conn, value)
        if not task:
            return {"ok": False, "error": "task_not_found",
                    "message": f"No task '{value}' — list_tasks shows valid task_ids."}
        text = "\n\n".join(filter(None, [
            f"TASK: {task.get('title', '')}",
            task.get("summary") or "", task.get("description") or ""]))
        return {"ok": True, "text": text[:8000],
                "links": {"task_id": value}, "source_ref": f"task:{value}"}
    if key == "source_research_id":
        from src.data import research_store
        res = research_store.get_research(conn, value)
        if not res:
            return {"ok": False, "error": "research_not_found",
                    "message": f"No research manifest '{value}'."}
        return {"ok": True, "text": (res.get("markdown") or "")[:8000],
                "links": {"research_id": value,
                          "task_id": res.get("task_id") or None},
                "source_ref": f"research:{value}"}
    # source_doc_id
    from src.data import enablement_store
    doc = enablement_store.get_document(conn, value)
    if not doc:
        return {"ok": False, "error": "doc_not_found",
                "message": (f"No stored document '{value}' — search_drive_docs "
                            "finds doc ids, or pass source_text.")}
    text = f"DOCUMENT: {doc.get('name', '')}\n\n{doc.get('full_text') or ''}"
    return {"ok": True, "text": text[:8000],
            "links": {"doc_id": value}, "source_ref": f"doc:{value}"}


def _mermaid_prompt(source_text: str, diagram_type: str, instruction: str) -> str:
    template_path = _PROMPTS_DIR / "enablement_mermaid.txt"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError:
        template = ("Output ONLY a fenced ```mermaid block (header: {diagram_type} "
                    "or flowchart TD). Quote all labels. No init directives, "
                    "no click lines.\nINSTRUCTION: {instruction}\nSOURCE:\n{source_text}")
    # .replace, not .format — the template legitimately contains literal braces
    # (mermaid syntax like %%{...}%% in the rules).
    return (template
            .replace("{diagram_type}", diagram_type or "flowchart TD")
            .replace("{instruction}", instruction or "(none)")
            .replace("{source_text}", source_text))


def handle_generate_diagram(conn, args: dict, filters: dict) -> dict:
    """Generate a Mermaid diagram from a task/research/doc/inline source via
    the shared llm_gen loop (lint validator, one repair retry). Stores ONLY
    lint-clean source as a kind='diagram' artifact. No preview is minted —
    in-app rendering is deferred with D-MERMAID."""
    from src.data import llm_gen, mermaid_lint

    src = _load_source(conn, args)
    if not src["ok"]:
        return src

    diagram_type = (args.get("diagram_type") or "").strip()
    instruction = (args.get("instruction") or "").strip()
    prompt = _mermaid_prompt(src["text"], diagram_type, instruction)

    def _validator(text):
        ok, errors, cleaned = mermaid_lint.lint(text)
        return ok, errors, cleaned

    out = llm_gen.generate_validated(prompt, _validator)
    if not out["ok"]:
        if out["error"] == "no_llm_client":
            return {"ok": False, "error": "no_llm_client"}
        return {"ok": False, "error": out["error"],
                "message": "Diagram generation failed validation twice — "
                           "try a simpler instruction or a shorter source.",
                "lint_errors": out.get("errors") or []}

    title = (args.get("title") or "").strip() or f"Diagram: {src['source_ref']}"
    artifact_id = artifact_store.create_artifact(
        conn, kind="diagram", title=title[:120],
        spec={"mermaid": out["value"], "diagram_type": diagram_type or "flowchart"},
        provenance={"template": "enablement_mermaid", "retries": out["retries"],
                    "source_ref": src["source_ref"]},
        session_id=_active_session_id(),
        **{k: v for k, v in src["links"].items() if v},
    )
    return {"ok": True, "artifact_id": artifact_id, "title": title[:120],
            "lint_retries": out["retries"],
            "note": "Diagram stored. It renders when previews ship; the mermaid "
                    "source is in the artifact spec."}


def handle_generate_deck(conn, args: dict, filters: dict) -> dict:
    """Generate a branded .pptx from a task/research/doc/inline source as a
    TRACKED agent_jobs job (visible live in the jobs sidebar). Cancelable at
    step boundaries; LLM outline failure falls back to the deterministic
    outline_from_markdown — the job never fails for LLM flakiness."""
    from src.data import agent_jobs, llm_gen, pptx_store

    src = _load_source(conn, args)
    if not src["ok"]:
        return src

    job_id = agent_jobs.create_job(
        conn, title=f"Deck: {src['source_ref']}", kind="deck_generation",
        session_id=_active_session_id(),
        steps=["load source", "outline", "export"])
    steps = {s["name"]: s["step_id"]
             for s in agent_jobs.list_steps(conn, job_id)}

    def _step(name, status, detail=None):
        sid = steps.get(name)
        if sid:
            fields = {"status": status}
            if detail:
                fields["detail"] = detail
            agent_jobs.update_step(conn, sid, **fields)

    def _cancelled() -> bool:
        return agent_jobs.is_cancelled(conn, job_id)

    _step("load source", "done")
    if _cancelled():
        return {"ok": False, "error": "cancelled", "job_id": job_id}

    # ── outline: LLM with deterministic fallback ─────────────────────
    _step("outline", "running")
    audience = (args.get("audience") or "the enablement team").strip()
    instruction = (args.get("instruction") or "").strip()
    outline = None
    prompt = (
        "Turn this source into a slide-deck outline for " + audience + ".\n"
        + (f"INSTRUCTION: {instruction}\n" if instruction else "")
        + 'Return STRICT JSON only: {"title": str, "slides": [{"title": str, '
          '"bullets": [str], "notes": str}]}. Max 12 slides, max 6 bullets each.\n'
        + f"SOURCE:\n{src['text']}"
    )

    def _outline_validator(text):
        parsed = pptx_store.parse_outline(text)
        if not parsed.get("slides"):
            return False, ["no slides parsed — return the JSON outline object"], None
        return True, [], parsed

    gen = llm_gen.generate_validated(prompt, _outline_validator)
    if gen["ok"]:
        outline = gen["value"]
    else:
        # Deterministic fallback: headings→slides from the raw source text.
        outline = pptx_store.outline_from_markdown(src["source_ref"], src["text"])
        _step("outline", "done", "deterministic fallback (LLM unavailable/invalid)")
    if gen["ok"]:
        _step("outline", "done")
    if _cancelled():
        agent_jobs.update_job(conn, job_id, status="cancelled")
        return {"ok": False, "error": "cancelled", "job_id": job_id}

    # ── export into the artifact's managed dir ───────────────────────
    _step("export", "running")
    title = (args.get("title") or outline.get("title") or "Deck").strip()[:120]
    deck_id = pptx_store.save_deck(conn, title=title, outline=outline,
                                   source_ref=src["source_ref"])
    artifact_id = artifact_store.create_artifact(
        conn, kind="deck", title=title,
        spec={"deck_id": deck_id, "slide_count": len(outline.get("slides") or [])},
        provenance={"source_ref": src["source_ref"],
                    "outline_via": "llm" if gen["ok"] else "deterministic",
                    "retries": gen.get("retries", 0)},
        session_id=_active_session_id(),
        deck_id=deck_id,
        **{k: v for k, v in src["links"].items() if v},
    )
    out_path = str(artifact_store.artifact_dir(artifact_id)
                   / f"{artifact_store.slugify(title)}.pptx")
    exported = pptx_store.export_pptx(conn, deck_id, out_path)
    if not exported.get("ok"):
        _step("export", "error", exported.get("error"))
        agent_jobs.update_job(conn, job_id, status="error",
                              error=exported.get("error"))
        return {"ok": False, "error": exported.get("error"), "job_id": job_id,
                "artifact_id": artifact_id}
    artifact_store.update_artifact(conn, artifact_id, status="rendered",
                                   file_path=out_path)
    _step("export", "done")
    agent_jobs.update_job(conn, job_id, status="done", progress_pct=100,
                          summary=f"{exported.get('slides', 0)} slides exported")
    return {"ok": True, "artifact_id": artifact_id, "deck_id": deck_id,
            "job_id": job_id, "file_path": out_path,
            "slides": exported.get("slides", 0)}


def _read_prompt(name: str, fallback: str) -> str:
    try:
        return (_PROMPTS_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return fallback


def handle_generate_quiz(conn, args: dict, filters: dict) -> dict:
    """Generate a knowledge-check quiz (WS3-M5, Python side — the interactive
    QuizCard ships with the deferred preview work). Stored as a kind='quiz'
    artifact holding BOTH the diffable quiz_md and the parsed JSON."""
    from src.data import llm_gen, quiz_artifacts

    src = _load_source(conn, args)
    if not src["ok"]:
        return src
    n_questions = max(2, min(int(args.get("n_questions", 5)), 12))
    template = _read_prompt("enablement_quiz.txt",
                            "Write a {n_questions}-question quiz in the "
                            "'### Qn' / '- [x]' format.\nSOURCE:\n{source_text}")
    prompt = (template.replace("{n_questions}", str(n_questions))
              .replace("{instruction}", (args.get("instruction") or "").strip()
                       or "(none)")
              .replace("{source_text}", src["text"]))

    out = llm_gen.generate_validated(prompt, quiz_artifacts.parse_quiz_md)
    if not out["ok"]:
        if out["error"] == "no_llm_client":
            return {"ok": False, "error": "no_llm_client"}
        return {"ok": False, "error": out["error"],
                "message": "Quiz generation failed validation twice — try a "
                           "shorter source or fewer questions.",
                "format_errors": out.get("errors") or []}
    quiz = out["value"]
    quiz_md = quiz_artifacts.serialize_quiz(quiz)
    title = (args.get("title") or quiz["title"]).strip()[:120]
    artifact_id = artifact_store.create_artifact(
        conn, kind="quiz", title=title,
        spec={"quiz_md": quiz_md, "quiz": quiz},
        provenance={"template": "enablement_quiz", "retries": out["retries"],
                    "source_ref": src["source_ref"]},
        session_id=_active_session_id(),
        **{k: v for k, v in src["links"].items() if v},
    )
    return {"ok": True, "artifact_id": artifact_id, "title": title,
            "questions": len(quiz["questions"]),
            "note": "Quiz stored. attach_artifact_to_draft publishes it as a "
                    "card section through the normal review/sign-off flow."}


_DOC_STYLES = {
    "one_pager": {
        "template": "enablement_one_pager.txt",
        "headers": ["## Overview", "## Why it matters", "## Key facts",
                    "## How to talk about it", "## Links"],
    },
    "battle_card": {
        "template": "enablement_battle_card.txt",
        "headers": ["## Positioning", "## Objections and responses",
                    "## Proof points", "## Landmines"],
    },
}


def handle_generate_doc(conn, args: dict, filters: dict) -> dict:
    """Generate a one-pager or battle-card (WS3-M8): rigid section skeletons
    so Haiku fills slots instead of designing structure; deterministic
    header validation with one retry. ONE tool with a style enum — each new
    style later = a prompt file + a _DOC_STYLES entry, never a new tool."""
    from src.data import llm_gen

    style = str(args.get("style") or "").strip()
    spec = _DOC_STYLES.get(style)
    if spec is None:
        return {"ok": False, "error": "unknown_style",
                "message": f"Unknown style '{style}'.",
                "allowed_styles": sorted(_DOC_STYLES)}
    src = _load_source(conn, args)
    if not src["ok"]:
        return src
    template = _read_prompt(spec["template"],
                            "Write the document with these markdown headers "
                            "verbatim: " + ", ".join(spec["headers"])
                            + "\nSOURCE:\n{source_text}")
    prompt = (template.replace("{instruction}",
                               (args.get("instruction") or "").strip() or "(none)")
              .replace("{source_text}", src["text"]))

    def _validator(text):
        missing = [h for h in spec["headers"] if h not in text]
        if missing:
            return False, [f"missing required section header: {h}"
                           for h in missing], None
        return True, [], text.strip()

    out = llm_gen.generate_validated(prompt, _validator)
    if not out["ok"]:
        if out["error"] == "no_llm_client":
            return {"ok": False, "error": "no_llm_client"}
        return {"ok": False, "error": out["error"],
                "message": "Doc generation failed validation twice.",
                "format_errors": out.get("errors") or []}
    markdown = out["value"]
    first_line = next((ln for ln in markdown.splitlines() if ln.strip()), "")
    title = (args.get("title") or first_line.lstrip("# ").strip()
             or style.replace("_", " ").title()).strip()[:120]
    artifact_id = artifact_store.create_artifact(
        conn, kind=style, title=title, spec={"markdown": markdown},
        provenance={"template": spec["template"], "retries": out["retries"],
                    "source_ref": src["source_ref"]},
        session_id=_active_session_id(),
        **{k: v for k, v in src["links"].items() if v},
    )
    return {"ok": True, "artifact_id": artifact_id, "title": title}


def handle_attach_artifact(conn, args: dict, filters: dict) -> dict:
    """Attach an artifact to a Guru card DRAFT (WS3-M4/M5/M8) — one
    kind-dispatching tool riding the EXISTING diff/sign-off rail: the draft
    still needs the operator's approval before push_guru_draft publishes.
    D-GURU deferred → diagrams attach as a fenced mermaid TEXT block (the
    seam; imagery lands with the Guru-attachment decision)."""
    import json as _json
    from src.data import enablement_store

    artifact = artifact_store.get_artifact(
        conn, str(args.get("artifact_id") or "").strip())
    if not artifact:
        return steer_unknown_artifact(conn, args.get("artifact_id"))
    try:
        spec = _json.loads(artifact.get("spec_json") or "{}")
    except (ValueError, TypeError):
        spec = {}
    kind = artifact.get("kind")

    if kind == "diagram":
        section_md = (f"\n\n## Diagram: {artifact.get('title')}\n\n"
                      f"```mermaid\n{spec.get('mermaid', '')}\n```\n")
        section_html = None
    elif kind == "quiz":
        from src.data import quiz_artifacts
        quiz = spec.get("quiz") or {}
        section_md = "\n\n" + (spec.get("quiz_md") or "")
        section_html = quiz_artifacts.to_guru_html(quiz) if quiz else None
    elif kind in ("one_pager", "battle_card"):
        section_md = "\n\n" + (spec.get("markdown") or "")
        section_html = None
    else:
        return {"ok": False, "error": "kind_not_attachable",
                "message": f"'{kind}' artifacts don't attach to cards "
                           "(decks upload via request_upload_artifact_to_drive)."}

    draft_id = args.get("draft_id")
    if draft_id:
        try:
            did = int(draft_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "draft_id_invalid"}
        draft = enablement_store.get_draft(conn, did)
        if not draft:
            return {"ok": False, "error": "draft_not_found",
                    "message": "list_pending_approvals shows open drafts, or "
                               "pass new_draft_title to start one."}
        content = (draft.get("content") or "") + section_md
        kwargs = {"title": draft.get("title"), "content": content}
        if section_html:
            kwargs["content_html"] = ((draft.get("content_html") or "")
                                      + "\n" + section_html)
        enablement_store.update_draft_content(conn, did, **kwargs)
    else:
        new_title = (args.get("new_draft_title") or "").strip()
        if not new_title:
            return {"ok": False, "error": "target_required",
                    "message": "Pass draft_id (an existing draft) or "
                               "new_draft_title (start a fresh card draft)."}
        kwargs = {"title": new_title, "content": section_md.strip(),
                  "source_ref": f"artifact:{artifact['artifact_id']}"}
        if section_html:
            kwargs["content_html"] = section_html
        did = enablement_store.save_card_draft(conn, **kwargs)

    try:
        from src.data.content_update import provenance
        provenance.record_proposal(
            conn, did, "", source_ref=f"artifact:{artifact['artifact_id']}",
            source_title=artifact.get("title") or "",
            summary=f"Attached {kind} artifact to the draft.")
    except Exception:  # noqa: BLE001 — audit is best-effort
        pass
    artifact_store.update_artifact(conn, artifact["artifact_id"],
                                   status="attached", draft_id=did)
    return {"ok": True, "draft_id": did, "artifact_id": artifact["artifact_id"],
            "note": "Attached to the draft — it publishes only after the "
                    "operator approves it in the Review panel."}


def handle_request_upload_artifact(conn, args: dict, filters: dict):
    """Propose uploading a rendered artifact to Drive → Confirm card (gated;
    non-idempotent). Target chain (cross-cutting finding #3): explicit
    target_folder_id > enablement.kb.ec_folder_id > STEER — active_folders is
    deliberately NOT a fallback (it points at PHI-adjacent product folders;
    a confirmed upload must never silently land there). The summary carries
    the folder ID, never a folder name (Drive names are PHI-adjacent)."""
    from src.data import artifact_store as A
    from src.data.chat_tools.enablement_tools import (
        _active_session_id, _emit_confirm_write)

    sid = _active_session_id()
    if not sid:
        return ("No active chat session — open the Agent chat and try again so "
                "the confirmation card can be shown.")
    artifact = A.get_artifact(conn, str(args.get("artifact_id") or "").strip())
    if not artifact:
        return steer_unknown_artifact(conn, args.get("artifact_id"))
    file_path = artifact.get("file_path") or ""
    if not file_path or not os.path.isfile(file_path):
        return {"ok": False, "error": "artifact_not_rendered",
                "message": ("That artifact has no rendered file yet — "
                            "generate/export it first (decks render on "
                            "generate_deck; diagrams render when previews ship).")}
    folder_id = str(args.get("target_folder_id") or "").strip()
    if not folder_id:
        try:
            from src.data.settings_manager import get_section
            kb_cfg = (get_section("enablement", {}) or {}).get("kb") or {}
            folder_id = str(kb_cfg.get("ec_folder_id") or "")
        except Exception:  # noqa: BLE001
            folder_id = ""
    if not folder_id:
        return {"ok": False, "error": "ec_not_bootstrapped",
                "message": ("No EC folder is set up — ask the operator to "
                            "bootstrap the knowledge base in Settings first, "
                            "or pass an explicit target_folder_id.")}
    summary = (f'Upload "{os.path.basename(file_path)}" '
               f"({artifact.get('kind')}) to Drive folder {folder_id}.")
    return _emit_confirm_write(conn, sid, "upload_artifact_to_drive", summary,
                               {"artifact_id": artifact["artifact_id"],
                                "folder_id": folder_id})


def handle_list_artifacts(conn, args: dict, filters: dict) -> dict:
    """Complete enumeration of studio artifacts with optional kind/status/
    task_id filters (LIST contract — paginates nothing, ranks nothing)."""
    kind = (args.get("kind") or "").strip() or None
    if kind and kind not in artifact_store.KINDS:
        return {
            "ok": False, "error": "unknown_kind",
            "message": f"Unknown kind '{kind}'.",
            "allowed_kinds": sorted(artifact_store.KINDS),
        }
    rows = artifact_store.list_artifacts(
        conn,
        kind=kind,
        status=(args.get("status") or "").strip() or None,
        task_id=(args.get("task_id") or "").strip() or None,
        limit=int(args.get("limit", 50)),
    )
    return {"ok": True, "artifacts": [_summary_row(a) for a in rows],
            "count": len(rows)}
