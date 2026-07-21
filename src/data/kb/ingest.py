"""KB product-folder ingestion (WS2-M4, renn-calendar-kb-studio plan).

The "index this Drive folder" engine: enumerate a product folder (bounded
depth + docs-per-job caps), extract real text (Google Docs export, .pptx via
pptx_reader, .docx, PDF via pypdf), Haiku-summarize each doc into an EC index
card via the shared ``llm_gen`` primitive — AND persist the FULL extracted
text to ``enablement_documents`` (the pre-mortem recall safety net: a fact on
slide 37 that a 2-sentence summary omits must stay findable; cards are the
ranked layer, full text is the floor).

LLM failure degrades to a deterministic 240-char-excerpt card — indexing
NEVER fails because Haiku had a bad day. Cards land through the kb_queue →
sync push path (the allowlisted chokepoint), never a direct Drive write.

Runs in the KBWorker (main process) under the background-LLM semaphore.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.data.kb import card_format, drive_kb, store

logger = logging.getLogger("alma.kb.ingest")

MAX_DOCS_PER_JOB = 500
MAX_DEPTH = 6
_SUMMARY_INPUT_CAP = 8000

_CARD_PROMPT = """You index a product document for a healthcare-RCM enablement knowledge base.
Extract ONLY what the document says. Output STRICT JSON, nothing else:
{"summary": "<= 2 sentences: what this document is about",
 "key_facts": ["<specific fact worth finding later>", ...max 6],
 "topics": ["<short-topic-slug>", ...max 4],
 "type": "source_summary"}

DOCUMENT ({doc_name}):
{doc_text}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_card_json(text: str):
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return False, [f"not valid JSON: {exc}"], None
    if not isinstance(data, dict):
        return False, ["output must be a JSON object"], None
    errors = []
    if not isinstance(data.get("summary"), str) or not data.get("summary", "").strip():
        errors.append('"summary" must be a non-empty string')
    for key in ("key_facts", "topics"):
        if not isinstance(data.get(key), list):
            errors.append(f'"{key}" must be a JSON array')
    if errors:
        return False, errors, None
    return True, [], {
        "summary": data["summary"].strip()[:400],
        "key_facts": [str(x)[:200] for x in data["key_facts"]][:6],
        "topics": [str(x)[:40] for x in data["topics"]][:4],
        "type": "source_summary",
    }


def summarize_doc(name: str, text: str, *, llm_client=None) -> dict:
    """Haiku summarize with the deterministic-excerpt fallback."""
    from src.data import llm_gen
    prompt = (_CARD_PROMPT.replace("{doc_name}", name or "document")
              .replace("{doc_text}", (text or "")[:_SUMMARY_INPUT_CAP]))
    out = llm_gen.generate_validated(prompt, _validate_card_json,
                                     client=llm_client)
    if out["ok"]:
        return {**out["value"], "via": "llm"}
    excerpt = " ".join((text or "").split())[:240]
    return {"summary": excerpt or f"Document: {name}", "key_facts": [],
            "topics": [], "type": "source_summary", "via": "deterministic"}


def enumerate_folder(reader, folder_id: str, *, max_depth: int = MAX_DEPTH,
                     cap: int = MAX_DOCS_PER_JOB) -> tuple[list[dict], bool]:
    """Files in a product folder tree (explicit recursion — list_changed_files
    is direct-children-only). Returns (files, truncated)."""
    files: list[dict] = []
    truncated = False
    queue: list[tuple[str, int]] = [(folder_id, 0)]
    while queue:
        fid, depth = queue.pop(0)
        try:
            for f in reader.list_changed_files(fid, recursive=False) or []:
                if len(files) >= cap:
                    truncated = True
                    return files, truncated
                files.append(f)
            if depth < max_depth:
                for sub in reader.list_folders(fid) or []:
                    queue.append((sub.get("id"), depth + 1))
        except Exception as exc:  # noqa: BLE001 — one folder can't kill the job
            logger.debug("enumerate failed for %s: %s", fid, exc)
    return files, truncated


def index_folder(conn, folder_id: str, *, reader=None, exporter=None,
                 llm_client=None, topic_hint: str = "", job_id: str | None = None) -> dict:
    """Execute one index_folder job (called by the KBWorker, main process)."""
    from src.data import agent_jobs, enablement_store, llm_gen
    if reader is None:
        from src.data.drive_reader import DriveReader
        reader = DriveReader.from_settings()

    def _cancelled() -> bool:
        return bool(job_id) and agent_jobs.is_cancelled(conn, job_id)

    files, truncated = enumerate_folder(reader, folder_id)
    if truncated:
        logger.warning("index_folder %s truncated at %d docs — the rest index "
                       "on a later run", folder_id, MAX_DOCS_PER_JOB)
    indexed = skipped = 0
    with llm_gen.background_gate:
        for f in files:
            if _cancelled():
                return {"ok": False, "error": "cancelled",
                        "indexed": indexed}
            file_id = f.get("id") or ""
            name = f.get("name") or "document"
            mime = f.get("mime_type") or f.get("mimeType") or ""
            modified = f.get("modified_time") or f.get("modifiedTime") or ""
            existing = store.list_cards(conn, source_id=file_id, limit=1)
            if existing and existing[0].get("source_modified") == modified:
                skipped += 1
                continue
            try:
                text = reader.export_text(file_id, mime)
            except Exception as exc:  # noqa: BLE001
                logger.debug("extract failed for %s: %s", file_id, exc)
                text = ""
            # Recall safety net: FULL text into enablement_documents (upsert
            # by fileId) so search always has a full-text floor.
            if text:
                try:
                    enablement_store.save_document(
                        conn, source="drive", name=name, source_ref=file_id,
                        mime_type=mime, web_url=f.get("url") or "",
                        modified_time=modified, full_text=text)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("full-text save failed for %s: %s", file_id, exc)
            card = summarize_doc(name, text, llm_client=llm_client)
            topic = topic_hint or (card["topics"][0] if card["topics"] else "")
            resolved = drive_kb.resolve_topic_folder(conn, topic or "misc",
                                                     exporter=exporter)
            if not resolved.get("ok"):
                return resolved
            card_id = (existing[0]["card_id"] if existing
                       else card_format.new_card_id())
            meta = {
                "card_id": card_id, "title": name,
                "type": "source_summary", "topics": card["topics"],
                "source_id": file_id, "source_url": f.get("url") or "",
                "source_mime": mime, "source_modified": modified,
                "summary": card["summary"], "key_facts": card["key_facts"],
                "provenance": {"created_by": "renn", "via": card["via"],
                               "job_id": job_id, "created_at": _now()},
            }
            body = "" if text else "_Content could not be extracted — metadata-only card._"
            store.upsert_card(conn, meta, body,
                              topic_folder_id=resolved["folder_id"])
            _enqueue_write(conn, card_id, job_id)
            indexed += 1
    return {"ok": True, "indexed": indexed, "skipped": skipped,
            "truncated": truncated}


def enqueue_distill(conn, draft_id) -> None:
    """Queue the published-card archive (WS2-M5 hook — coalesced, non-blocking)."""
    try:
        conn.execute(
            "INSERT INTO kb_queue (kind, target, payload_json, created_at) "
            "VALUES ('distill', ?, '{}', ?)", (str(draft_id), _now()))
        conn.commit()
    except Exception:  # noqa: BLE001 — unique pending index = already queued
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass


def distill_draft(conn, draft_id, *, exporter=None) -> dict:
    """Archive a PUBLISHED Guru card into the EC folder (owner request): a
    ``published_card`` KB card whose BODY is the full published content — the
    card IS the archive, and its frontmatter summary keeps it searchable.
    Deterministic (no LLM): the content is already human-approved."""
    from src.data import enablement_store
    try:
        did = int(draft_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "draft_id_required"}
    draft = enablement_store.get_draft(conn, did)
    if not draft:
        return {"ok": False, "error": "draft_not_found"}
    title = draft.get("title") or f"Published card {did}"
    content = draft.get("content") or ""
    guru_ref = (draft.get("pushed_card_id") or draft.get("guru_card_id")
                or draft.get("source_ref") or f"draft:{did}")
    source_id = f"guru:{guru_ref}"
    existing = store.list_cards(conn, source_id=source_id, limit=1)
    card_id = existing[0]["card_id"] if existing else card_format.new_card_id()
    summary = " ".join(content.split())[:240] or f"Published Guru card: {title}"
    resolved = drive_kb.resolve_topic_folder(conn, "published-cards",
                                             exporter=exporter)
    if not resolved.get("ok"):
        return resolved
    meta = {
        "card_id": card_id, "title": title, "type": "published_card",
        "topics": ["published-cards"], "source_id": source_id,
        "source_url": draft.get("source_url") or "",
        "source_modified": _now(), "summary": summary, "key_facts": [],
        "provenance": {"created_by": "renn", "via": "publish_hook",
                       "draft_id": did, "created_at": _now()},
    }
    store.upsert_card(conn, meta, content, topic_folder_id=resolved["folder_id"])
    _enqueue_write(conn, card_id)
    return {"ok": True, "card_id": card_id}


def _enqueue_write(conn, card_id: str, job_id: str | None = None) -> None:
    """Queue the card's Drive push (coalesced: one pending row per card)."""
    try:
        conn.execute(
            "INSERT INTO kb_queue (kind, target, payload_json, job_id, created_at) "
            "VALUES ('write_card', ?, '{}', ?, ?)",
            (card_id, job_id, _now()))
        conn.commit()
    except Exception:  # noqa: BLE001 — unique pending index = already queued
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
