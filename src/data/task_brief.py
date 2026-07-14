"""Haiku enrichment briefs for Asana tasks (WS1-M4, renn-calendar-kb-studio).

Every Asana-sourced task gets a cached structured brief — {ask, deliverable,
links, stakeholders, effective_date} — built at intake and refreshed when the
task changes remotely. Briefs are ENRICHMENT: any failure degrades to the raw
fields (the panel simply shows nothing extra), and brief work NEVER runs on
the sync poll thread (pre-mortem: CLI latency would stall the 60s cadence the
calendar exists for) — the BriefWorker below has its own timer + thread.

Staleness is the same self-describing-queue trick as asana_task_extras:
``brief_source_modified_at`` snapshots the ``remote_modified_at`` the brief was
built from; dirty when they differ. A FAILED build still stamps the snapshot
(no retry-storm; the brief refreshes on the task's next real change). The
``no_llm_client`` case (CLI offline) skips the whole cycle WITHOUT stamping,
so an outage doesn't burn every dirty task's one chance.

Runs Haiku through the shared ``llm_gen`` primitive under its background
semaphore (max one background CLI call machine-wide).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from threading import Thread

logger = logging.getLogger("alma.task_brief")

_DEFAULT_CAP = 5
_INPUT_CHAR_CAP = 6000

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
_TAG_RE = re.compile(r"<[^>]+>")


def brief_cap() -> int:
    try:
        from src.data.settings_manager import get_section
        asana_cfg = (get_section("enablement", {}) or {}).get("asana") or {}
        return max(0, int(asana_cfg.get("brief_per_poll_cap", _DEFAULT_CAP)))
    except Exception:  # noqa: BLE001
        return _DEFAULT_CAP


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub(" ", html or "").strip()


def _brief_source_text(task: dict, extras: dict | None) -> str:
    """Assemble the brief's input: title + best body + custom fields + latest
    comments, char-capped."""
    parts = [f"TITLE: {task.get('title', '')}"]
    body = ""
    if extras and (extras.get("html_notes") or "").strip():
        body = _strip_tags(extras["html_notes"])
    body = body or (task.get("description") or "")
    if body:
        parts.append(f"BODY:\n{body}")
    if extras:
        fields = [f"- {cf.get('name')}: {cf.get('display_value')}"
                  for cf in (extras.get("custom_fields") or [])
                  if (cf.get("display_value") or "").strip()]
        if fields:
            parts.append("FIELDS:\n" + "\n".join(fields))
        stories = extras.get("stories") or []
        if stories:
            latest = [f"- {s.get('author', '')}: {s.get('text', '')}"
                      for s in stories[-5:]]
            parts.append("LATEST COMMENTS:\n" + "\n".join(latest))
    return "\n\n".join(parts)[:_INPUT_CHAR_CAP]


def _validate_brief(text: str):
    """Strict-JSON validator for llm_gen: normalized dict or errors."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return False, [f"not valid JSON: {exc}"], None
    if not isinstance(data, dict):
        return False, ["output must be a JSON object"], None
    errors = []
    for key in ("ask", "deliverable"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            errors.append(f'"{key}" must be a non-empty string')
    for key in ("links", "stakeholders"):
        if not isinstance(data.get(key), list):
            errors.append(f'"{key}" must be a JSON array (use [] when none)')
    eff = data.get("effective_date")
    if eff is not None and not (isinstance(eff, str)
                                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", eff)):
        errors.append('"effective_date" must be "YYYY-MM-DD" or null')
    if errors:
        return False, errors, None
    return True, [], {
        "ask": data["ask"].strip(),
        "deliverable": data["deliverable"].strip(),
        "links": [str(x) for x in data["links"]][:10],
        "stakeholders": [str(x) for x in data["stakeholders"]][:10],
        "effective_date": eff,
    }


def _brief_prompt(source_text: str) -> str:
    path = _PROMPTS_DIR / "enablement_task_brief.txt"
    try:
        template = path.read_text(encoding="utf-8")
    except OSError:
        template = ('Output STRICT JSON {"ask","deliverable","links",'
                    '"stakeholders","effective_date"} for this request:\n{source_text}')
    return template.replace("{source_text}", source_text)


def build_brief(conn, task_id: str, *, llm_client=None) -> dict:
    """Build + persist one task's brief. Returns {"ok"|"error", ...}.

    Failure stamps ``brief_source_modified_at`` anyway (refresh only on the
    task's next remote change) — EXCEPT ``no_llm_client``, which leaves the
    task dirty for the next cycle.
    """
    from src.data import asana_extras, enablement_tasks, llm_gen
    task = enablement_tasks.get_task(conn, task_id)
    if not task:
        return {"ok": False, "error": "task_not_found"}
    remote = task.get("remote_modified_at") or ""
    extras = None
    try:
        extras = asana_extras.get_extras(conn, task_id)
    except Exception:  # noqa: BLE001
        extras = None
    prompt = _brief_prompt(_brief_source_text(task, extras))

    out = llm_gen.generate_validated(prompt, _validate_brief, client=llm_client)
    if out.get("error") == "no_llm_client":
        return {"ok": False, "error": "no_llm_client"}
    if out["ok"]:
        enablement_tasks.update_task(
            conn, task_id, brief_json=json.dumps(out["value"]),
            brief_status="ok", brief_source_modified_at=remote)
        return {"ok": True, "brief": out["value"], "retries": out["retries"]}
    enablement_tasks.update_task(
        conn, task_id, brief_status="failed", brief_source_modified_at=remote)
    return {"ok": False, "error": out["error"], "errors": out.get("errors") or []}


def dirty_brief_task_ids(conn, *, limit: int) -> list[str]:
    """Asana tasks whose brief is missing/out of date (self-describing queue)."""
    if limit <= 0:
        return []
    rows = conn.execute(
        """SELECT task_id FROM enablement_tasks
           WHERE source='asana' AND status != 'dismissed'
             AND COALESCE(remote_modified_at,'') != ''
             AND COALESCE(brief_source_modified_at,'') != COALESCE(remote_modified_at,'')
           ORDER BY updated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def drain_briefs(conn, *, cap: int | None = None, llm_client=None) -> int:
    """Build briefs for up to ``cap`` dirty tasks under the background-LLM
    semaphore. Aborts the cycle (without stamping) when no client is available."""
    from src.data import llm_gen
    cap = brief_cap() if cap is None else cap
    dirty = dirty_brief_task_ids(conn, limit=cap)
    if not dirty:
        return 0
    if llm_client is None:
        try:
            from src.gemini.client_factory import build_client_for_task
            llm_client = build_client_for_task("enablement_card_gen")
        except Exception:  # noqa: BLE001
            llm_client = None
    if llm_client is None:
        logger.debug("brief drain skipped: no LLM client this cycle")
        return 0
    done = 0
    with llm_gen.background_gate:
        for task_id in dirty:
            try:
                res = build_brief(conn, task_id, llm_client=llm_client)
                if res.get("error") == "no_llm_client":
                    break
                done += 1
            except Exception as exc:  # noqa: BLE001 — one task can't kill the drain
                logger.debug("brief build failed for %s: %s", task_id, exc)
    return done


# ── Qt background worker (own timer — NEVER the sync poll thread) ────

class BriefWorker:
    """Timer-driven brief drain, composed into EnablementMonitor.

    Same shape as AsanaMonitor's tick discipline: single-flight latch, daemon
    thread, FRESH connection per run, close on exit. Deliberately not a
    QObject subclass — no signals needed (the panel reads briefs from the DB
    on open; list refresh already rides tasks_updated)."""

    def __init__(self, db_manager):
        from PySide6.QtCore import QTimer
        self.db = db_manager
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_tick)
        self._running = False

    def start(self, interval_seconds: int = 60):
        self._timer.start(max(30, int(interval_seconds)) * 1000)
        self._on_tick()

    def stop(self):
        self._timer.stop()

    def _on_tick(self):
        if self._running:
            return
        self._running = True
        Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        try:
            from src.data.connection_factory import get_connection
            conn = get_connection(str(self.db.db_path))
            try:
                drain_briefs(conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("brief worker drain failed: %s", exc)
        finally:
            self._running = False
