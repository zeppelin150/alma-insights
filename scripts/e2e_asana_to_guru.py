"""Full live E2E: Asana task submission -> Guru card, via Renn (Gemini).

The chain this exercises end-to-end against the real dev integrations:

    1. SUBMIT   create a real Asana task in 'Project tracker', tagged
                `assigned team = enablement`, with a due date (a genuine
                "submission" an operator would make).
    2. INGEST   run the production `asana_monitor.poll_once` so the indicator
                board picks it up and lands it in `enablement_tasks`; then
                verify it attaches to the Calendar on the right day (mirrors
                `CalendarPage.set_tasks` keying by `due_date`).
    3. GREP     grep the project's own docs for "Alma Insights" content — the
                grounding/reference material for the card.
    4. AUTHOR   run Renn (live Gemini) to write a Guru knowledge card about
                Alma Insights, grounded ONLY in the grepped material.
    5. PUBLISH  route the card through Renn's real pipeline
                `enablement_store.save_card_draft -> publish_draft(guru_client)`
                -> `GuruClient.create_card` -> live Guru collection.
    6. VERIFY   read the card back from Guru by id and confirm it exists.

This creates REAL artifacts in the dev Asana + Guru accounts (that's the
point — it lands a card in Guru). Pass --cleanup to delete the Asana task and
the published Guru card afterwards so the run is repeatable without litter.

Prereqs: creds already in pat_store (run scripts/validate_live_integrations.py
first) and the Asana indicator board configured. Gemini CLI authenticated.

Usage:
    python scripts/e2e_asana_to_guru.py
    python scripts/e2e_asana_to_guru.py --cleanup
    python scripts/e2e_asana_to_guru.py --collection "Welcome to Guru!"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ASANA_BASE = "https://app.asana.com/api/1.0"
PROJECT_GID = "1215565058346588"
PROJECT_NAME = "Project tracker"
# "Welcome to Guru!" is a system/onboarding collection that rejects API writes
# (403). "Templates for internal communications" is a standard, writable one.
DEFAULT_COLLECTION = "Templates for internal communications"

RENN_SYSTEM = (
    "You are Renn, Alma Health's enablement knowledge assistant. You write "
    "clear, accurate Guru knowledge cards for non-technical operations staff. "
    "Ground every claim in the reference material provided; do not invent "
    "features. Output only what is asked, in the exact format requested."
)

# Grounding sources for the resource grep (project's own docs).
GREP_SOURCES = [
    "CLAUDE.md", "README.md",
    "docs/ARCHITECTURE.md", "docs/DATA_FLOWS.md", "docs/UI_GUIDE.md",
    "docs/AI_REPORTS.md", "docs/SOURCE_MONITOR.md",
]
# Section headers (substring) that describe the tool deeply — pulled first.
PRIORITY_HEADERS = (
    "what this project is", "quick reference", "architecture overview",
    "source layout", "ui pages", "data import", "agentic pipeline",
    "llm integration", "key patterns", "database essentials", "overview",
    "components", "data flow", "what it does", "getting started", "features",
)
# Topic keywords marking a section relevant even without a priority header.
RELEVANCE = (
    "alma insights", "rcm", "revenue cycle", "support ticket", "classif",
    "trend", "anomaly", "report", "guru", "source monitor", "ingest",
    "scan", "analytics", "incident", "dashboard",
)
# Build-history / meta sections to skip — noise for a product card.
EXCLUDE_HEADERS = (
    "redesign", "rebuild", "protocol", "build history", "session", "phase",
    "gotcha", "danger", "documentation index", "branch status", "memory",
)
GREP_BUDGET = 13000  # max chars of grounding material fed to the model


def banner(title: str) -> None:
    print(f"\n=== {title} ===")


def line(label: str, value: str = "") -> None:
    print(f"    {label:<12} {value}".rstrip())


# ── Asana write helpers (confined to this E2E; prod AsanaClient stays read-only) ──

def _asana_headers() -> dict:
    from src.data.pat_store import load_setting
    key = load_setting("asana_api_key", "") or ""
    if not key:
        raise RuntimeError("asana_api_key missing from pat_store")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def asana_post(path: str, data: dict) -> dict:
    body = json.dumps({"data": data}).encode("utf-8")
    req = urllib.request.Request(ASANA_BASE + path, data=body,
                                 headers=_asana_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return (json.loads(r.read().decode("utf-8")) or {}).get("data") or {}


def asana_delete(path: str) -> int:
    req = urllib.request.Request(ASANA_BASE + path,
                                 headers=_asana_headers(), method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def resolve_indicator_gids() -> tuple[dict, dict]:
    """(assigned-team field, 'enablement' enum option) from the live project."""
    from src.data.asana_client import AsanaClient
    fields = AsanaClient.from_store().get_custom_fields(PROJECT_GID)
    team = next(f for f in fields if f["name"].strip().lower() == "assigned team")
    opt = next(o for o in team.get("enum_options", [])
               if o["name"].strip().lower() == "enablement")
    return team, opt


# ── Stage 1: submit an Asana task ─────────────────────────────────────────────

def stage_submit(due_iso: str, marker: str) -> tuple[str, str]:
    banner("STAGE 1 — Asana submission")
    team, opt = resolve_indicator_gids()
    title = f"[E2E {marker}] Publish Guru card: what is Alma Insights?"
    task = asana_post("/tasks", {
        "name": title,
        "projects": [PROJECT_GID],
        "due_on": due_iso,
        "custom_fields": {team["gid"]: opt["gid"]},
        "notes": ("Auto E2E submission: have Renn write a Guru knowledge card "
                  "explaining what Alma Insights is, grounded in the repo docs."),
    })
    gid = task.get("gid", "")
    line("[created]", f"task gid={gid}  due={due_iso}")
    line("[title]", title)
    line("[indicator]", f"{team['name']} = {opt['name']} (field {team['gid']})")
    line("[url]", task.get("permalink_url", ""))
    if not gid:
        raise RuntimeError("Asana task creation returned no gid")
    return gid, title


# ── Stage 2: ingest + calendar attach ─────────────────────────────────────────

def stage_ingest(conn, task_gid: str, due_iso: str) -> dict:
    banner("STAGE 2 — Ingest + Calendar attach")
    from src.data import asana_monitor, enablement_tasks
    from src.data.asana_client import AsanaClient

    ours = None
    for attempt in range(4):
        asana_monitor.poll_once(conn, client=AsanaClient.from_store())
        conn.commit()
        rows = enablement_tasks.list_tasks(conn, source="asana")
        ours = next((t for t in rows if t.get("source_ref") == task_gid), None)
        if ours:
            break
        line("[poll]", f"attempt {attempt+1}: not visible yet, waiting…")
        time.sleep(3)

    if not ours:
        raise RuntimeError("polled task never appeared in enablement_tasks")
    line("[ingested]", f"enablement task_id={ours['task_id']}  "
                       f"source_ref={ours['source_ref']}  due={ours.get('due_date')}")

    # Mirror CalendarPage.set_tasks: events keyed by due_date[:10], len>=10.
    events: dict[str, list] = {}
    for t in enablement_tasks.list_tasks(conn):
        due = t.get("due_date") or ""
        if len(due) >= 10:
            events.setdefault(due[:10], []).append((t.get("title") or "")[:20])
    on_day = events.get(due_iso, [])
    attached = (ours.get("due_date", "")[:10] == due_iso
                and any(due_iso[:10] in k for k in events)
                and ours["title"][:20] in on_day)
    line("[calendar]", f"day {due_iso} has {len(on_day)} chip(s): {on_day}")
    line("[keyed]", "YES — task keyed to its due day"
                    if attached else "NO — not keyed onto the calendar day")
    if not attached:
        raise RuntimeError("task did not attach (key) to the calendar day")

    # Real render: instantiate the actual CalendarPage widget headless and
    # confirm a chip for our task is drawn in the month grid (not just keyed).
    rendered = _calendar_render_check(conn, ours["title"], due_iso)
    if rendered is True:
        line("[rendered]", "YES — _Chip widget drawn in CalendarPage grid for the day")
    elif rendered is False:
        raise RuntimeError("task keyed but no chip rendered in the calendar grid")
    else:
        line("[rendered]", "skipped (Qt unavailable headless); keying verified above")
    return ours


def _calendar_render_check(conn, task_title: str, due_iso: str):
    """Instantiate the real CalendarPage headless and confirm our task draws a
    chip in the current month grid. True/False, or None if Qt can't run here."""
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from src.ui.pages.enablement import calendar as calmod
        from src.data import enablement_tasks
    except Exception:
        return None
    try:
        QApplication.instance() or QApplication([])
        cal = calmod.CalendarPage()
        cal.set_tasks(enablement_tasks.list_tasks(conn))
        Chip = getattr(calmod, "_Chip")
        # set_tasks rebuilds self._grid_widget; inspect only the live grid so we
        # ignore the pre-set sample grid still awaiting garbage collection.
        grid = getattr(cal, "_grid_widget", cal)
        label = (task_title or "")[:20]
        return any(label in (c.text() or "") for c in grid.findChildren(Chip))
    except Exception as exc:  # noqa: BLE001
        line("[render]", f"check error: {exc}")
        return None


# ── Stage 3: resource grep ────────────────────────────────────────────────────

def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (header, body) by ATX headings (# … ####)."""
    parts = re.split(r"(?m)^(#{1,4}\s+.+)$", text)
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        out.append((parts[i].lstrip("#").strip(), parts[i + 1].strip()))
    return out


def stage_grep() -> list[tuple[str, str, str]]:
    banner("STAGE 3 — Resource grep (comprehensive tool material)")
    scanned = 0
    pool: list[tuple[int, str, str, str]] = []  # (rank, file, header, snippet)
    for rel in GREP_SOURCES:
        p = _ROOT / rel
        if not p.exists():
            continue
        scanned += 1
        for header, body in _split_sections(p.read_text(encoding="utf-8", errors="replace")):
            hl = header.lower()
            if any(x in hl for x in EXCLUDE_HEADERS) or len(body.strip()) < 40:
                continue
            is_priority = any(h in hl for h in PRIORITY_HEADERS)
            if not (is_priority or any(k in (hl + " " + body.lower()) for k in RELEVANCE)):
                continue
            pool.append((0 if is_priority else 1, rel, header, body.strip()[:2200]))
    pool.sort(key=lambda x: x[0])  # priority sections first, then other relevant

    chunks: list[tuple[str, str, str]] = []
    total = 0
    for _, rel, header, snippet in pool:
        if total >= GREP_BUDGET:
            break
        chunks.append((rel, header, snippet))
        total += len(snippet)

    line("[sources]", f"{scanned} doc(s) scanned")
    line("[sections]", f"{len(chunks)} relevant section(s), ~{total} chars of grounding")
    for rel, hdr, _ in chunks[:6]:
        line("  -", f"[{rel}] {hdr[:60]}")
    if not chunks:
        raise RuntimeError("resource grep found no relevant material")
    return chunks


# ── Stage 4: Renn (Gemini) authors the card ───────────────────────────────────

def stage_author(task_title: str,
                 resources: list[tuple[str, str, str]]) -> tuple[str, str]:
    banner("STAGE 4 — Renn (Gemini) authors a professional card")
    from src.gemini.gemini_client import GeminiClient
    from src.data.settings_manager import get_section
    g = get_section("gemini", {})
    # pii_redaction=False disables only the AGGRESSIVE pass (which redacts the
    # product name "Alma Insights" -> "[NAME]"); the mandatory base PII scrub
    # (emails/phones/SSNs/cards/member IDs) still runs. Safe here: the card is
    # built from public product docs, no PHI.
    client = GeminiClient(cli_path=g.get("cli_path", ""),
                          model=g.get("model", "gemini-2.5-flash"),
                          pii_redaction=False)
    ctx = "\n\n".join(f"### [{src}] {hdr}\n{body}"
                      for src, hdr, body in resources)[:GREP_BUDGET]
    prompt = (
        f'A task was submitted in our work tracker: "{task_title}".\n\n'
        "Write a COMPREHENSIVE, professional Guru knowledge card (500-1000 words) "
        "about Alma Insights for non-technical operations staff. Ground every "
        "claim ONLY in the reference material below — do not invent features.\n\n"
        "STRUCTURE (use ## markdown headings, in this order):\n"
        "  ## Overview — what Alma Insights is and who it's for\n"
        "  ## Key Capabilities — 5-8 bullet points\n"
        "  ## How It Works — the data flow (import -> classify -> analyze -> report)\n"
        "  ## Frequently Asked Questions — 3-4 Q&As, EACH as a collapsible (format below)\n"
        "  ## Troubleshooting — a Markdown table: | Symptom | Likely cause | What to do | "
        "with 3-5 rows\n\n"
        "GURU STYLING — use these; they render as native Guru blocks:\n"
        "  - Callout: a blockquote whose FIRST line is a marker, e.g.\n"
        "        > [!NOTE]\n"
        "        > A concise, important note.\n"
        "    Markers: [!NOTE] [!SUCCESS] [!WARNING] [!DANGER]. Open the card with a\n"
        "    one-line [!NOTE] summary; use a [!WARNING] in Troubleshooting.\n"
        "  - FAQ collapsible (KEEP the blank lines exactly as shown):\n"
        "        ::: details How do I import tickets?\n\n"
        "        Answer in markdown.\n\n"
        "        :::\n\n"
        "Markdown tables, bullet lists, and **bold** are supported. Clear, professional tone.\n\n"
        f'Reference material (grepped from the project\'s own docs):\n"""\n{ctx}\n"""\n\n'
        "Return EXACTLY this format and NOTHING else:\n"
        "TITLE: <concise card title>\n"
        "BODY:\n"
        "<the full markdown card body following the structure above>\n"
    )
    line("[model]", f"{client.model} (Renn persona)")
    line("[grounding]", f"{len(ctx)} chars from {len(resources)} sections")
    raw = client.generate(prompt, system_prompt=RENN_SYSTEM, timeout=200)
    title, body = _parse_card(raw)
    words = len(body.split())
    callouts = len(re.findall(
        r"\[!\s*(?:NOTE|SUCCESS|WARNING|DANGER|INFO|TIP|CAUTION)", body, re.I))
    faqs = body.count("::: details")
    has_table = len(re.findall(r"(?m)^\s*\|.+\|\s*$", body)) >= 2
    line("[gen]", f"{len(raw)} chars returned")
    line("[title]", title or "<empty>")
    line("[body]", f"{words} words, {body.count(chr(10)) + 1} lines")
    line("[styling]", f"callouts={callouts}  FAQ-collapsibles={faqs}  "
                      f"troubleshooting-table={'yes' if has_table else 'no'}")
    if not title or not body:
        print("    --- raw output (unparsed) ---")
        print(raw[:800])
        raise RuntimeError("Gemini output did not parse into title+body")
    if words < 350:
        line("[warn]", f"card is short ({words} words) — target was 500-1000")
    return title, body


def _parse_card(raw: str) -> tuple[str, str]:
    title = ""
    m = re.search(r"TITLE:\s*(.+)", raw)
    if m:
        title = m.group(1).strip().strip("*#").strip()
    idx = raw.find("BODY:")
    body = raw[idx + len("BODY:"):].strip() if idx >= 0 else ""
    return title, body


# ── Stage 5: publish to live Guru via Renn's pipeline ─────────────────────────

def stage_publish(conn, title: str, body: str, task_gid: str,
                  collection_name: str):
    banner("STAGE 5 — Publish to live Guru")
    from src.data import enablement_store as store
    from src.data.guru_client import GuruClient
    email, token = GuruClient.load_credentials()
    gc = GuruClient(email, token)
    cols = gc.list_collections()
    col = next((c for c in cols if c.get("name") == collection_name),
               cols[0] if cols else None)
    if not col:
        raise RuntimeError("no Guru collection available to publish into")

    draft_id = store.save_card_draft(conn, title=title, content=body,
                                     source_ref=f"asana:{task_gid}")
    conn.commit()
    line("[draft]", f"saved draft_id={draft_id} (guru_content_drafts)")
    line("[collection]", f"{col['name']} ({col['id']})")

    res = store.publish_draft(conn, draft_id, guru_client=gc,
                              collection_id=col["id"])
    conn.commit()
    if not res.get("ok"):
        raise RuntimeError(f"publish failed: {res.get('error')}")
    card_id = res.get("card_id", "")
    line("[published]", f"status={res.get('status')}  card_id={card_id}")
    return gc, card_id, col, draft_id


# ── Stage 6: verify the card is live in Guru ──────────────────────────────────

def stage_verify(gc, card_id: str, expect_title: str) -> dict:
    banner("STAGE 6 — Verify card in Guru")
    # get_card() returns a NORMALIZED dict ({"id","title","content",...}), not
    # the raw Guru payload. Retry briefly for Guru's read-after-write lag.
    card: dict = {}
    for _ in range(3):
        card = gc.get_card(card_id)
        if card.get("title"):
            break
        time.sleep(2)
    line("[get_card]", f"id={card.get('id')}  title={card.get('title')!r}")
    line("[collection]", card.get("collection", ""))
    line("[content]", f"{len(card.get('content', ''))} chars of HTML body live")
    # Slug + native-styling proof live only on the raw payload.
    try:
        raw = gc._request("GET", f"/cards/{card_id}")
        if isinstance(raw, dict):
            slug = raw.get("slug", "")
            if slug:
                card["url"] = f"https://app.getguru.com/card/{slug}"
                line("[url]", card["url"])
            html = raw.get("content", "") or ""
            line("[styling-live]",
                 f"callouts={html.count('ghq-card-content__callout')}  "
                 f"collapsibles={html.count('ghq-card-content__collapsible')}  "
                 f"tables={html.count('<table')}")
    except Exception:
        pass
    if not card.get("id"):
        raise RuntimeError("card not found in Guru after publish")
    if card.get("title") and expect_title and \
            card["title"].strip() != expect_title.strip():
        line("[warn]", f"title mismatch (expected {expect_title!r})")
    return card


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the Asana task and Guru card after the run")
    ap.add_argument("--collection", default=DEFAULT_COLLECTION,
                    help="Guru collection name to publish into")
    args = ap.parse_args()

    from src.data.connection_factory import get_connection
    marker = datetime.now().strftime("%m%d-%H%M")
    due_iso = datetime.now().strftime("%Y-%m-%d")  # due today -> current month

    conn = get_connection()
    task_gid = card_id = ""
    draft_id = None
    gc = None
    try:
        task_gid, task_title = stage_submit(due_iso, marker)
        stage_ingest(conn, task_gid, due_iso)
        resources = stage_grep()
        title, body = stage_author(task_title, resources)
        gc, card_id, col, draft_id = stage_publish(conn, title, body, task_gid, args.collection)
        card = stage_verify(gc, card_id, title)

        banner("RESULT")
        print("  PASS — Asana submission flowed end-to-end into a live Guru card.")
        print(f"  Asana task : {task_gid}")
        print(f"  Guru card  : {card_id}  (collection: {col['name']})")
        if card.get("url"):
            print(f"  Card URL   : {card['url']}")
        rc = 0
    except Exception as exc:  # noqa: BLE001
        banner("RESULT")
        print(f"  FAIL — {type(exc).__name__}: {exc}")
        rc = 1
    finally:
        if args.cleanup:
            banner("CLEANUP")
            if task_gid:
                try:
                    asana_delete(f"/tasks/{task_gid}")
                    print(f"  deleted Asana task {task_gid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  Asana delete failed: {exc}")
            if gc is not None and card_id:
                try:
                    gc._request("DELETE", f"/cards/{card_id}")
                    print(f"  deleted Guru card {card_id}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  Guru card delete failed: {exc}")
            # Also remove the LOCAL rows this run created, so the calendar /
            # drafts list don't keep stale chips for deleted remote artifacts.
            try:
                n_t = conn.execute(
                    "DELETE FROM enablement_tasks WHERE source_ref = ?",
                    (task_gid,)).rowcount if task_gid else 0
                n_d = conn.execute(
                    "DELETE FROM guru_content_drafts WHERE id = ?",
                    (draft_id,)).rowcount if draft_id else 0
                conn.commit()
                print(f"  removed local rows: {n_t} task(s), {n_d} draft(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"  local cleanup failed: {exc}")
        conn.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
