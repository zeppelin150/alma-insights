"""Live trace harness — run the enablement hero loop against REAL Gemini, with
every function call's input + output recorded (ALMA_TRACE).

    python scripts/renn_live_trace_harness.py

Ingests a sample product doc → drafts a Guru card via the real Gemini CLI →
revises it via the chat tool (real Gemini again). No external writes (no Guru
push). Prints the cards and a summary of the recorded calls; the full
input/output log is written to a JSONL trace file.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ALMA_TRACE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # Windows consoles default to cp1252; Gemini output (and our arrows) is UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from src.data import call_trace  # noqa: E402

TRACE_PATH = call_trace.add_file_sink()
SUMMARY = call_trace.install_enablement_tracing()

# Imported AFTER install so bound names (dispatch_tool) are the traced versions.
from src.data import enablement_store as S          # noqa: E402
from src.data import enablement_tasks as T          # noqa: E402
from src.data.chat_tools.registry import dispatch_tool  # noqa: E402
from src.data.db_manager import DatabaseManager     # noqa: E402
from src.gemini.client_factory import build_client_for_task  # noqa: E402

_DOC = (
    "Providers can now enable SSO themselves from Settings > Security. Rollout "
    "begins June 24, 2026. Supports Okta and Azure AD. An org admin approves the "
    "connection; no Alma support ticket is needed. Existing password logins keep "
    "working during a 30-day transition window."
)


def main() -> int:
    db_path = os.path.join(tempfile.gettempdir(), "renn_live_trace.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass
    db = DatabaseManager(db_path=db_path)
    db.initialize()
    conn = db.conn

    print(f"[harness] ALMA_TRACE on - recording calls -> {TRACE_PATH}")
    print(f"[harness] instrumented: {SUMMARY}\n")

    # 1) ingest the doc into the local store
    doc_id = S.save_document(conn, source="drive", doc_id="live-doc-1",
                             name="Provider SSO Self-Serve.gdoc", full_text=_DOC)

    # 2) draft a Guru card from it via REAL Gemini
    llm = build_client_for_task("enablement_card_gen")
    print(f"[harness] card-gen client: {type(llm).__name__} model={getattr(llm, 'model', '?')}")
    try:
        draft = S.draft_card_from_document(conn, doc_id, llm, collection="Provider Enablement")
    except Exception as exc:  # noqa: BLE001
        print(f"[harness] draft failed: {exc}")
        return 1
    print(f"\n===== DRAFTED CARD (id {draft['id']}) =====")
    print(draft["title"])
    print("-" * 60)
    print((draft["content"] or "")[:1200])

    # 3) review task (as the Drive monitor would) + revise via the chat tool → REAL Gemini
    T.create_task(conn, source="drive", kind="card_review",
                  title=f"Review card: {draft['title']}", source_ref=doc_id, draft_id=draft["id"])
    rev = json.loads(dispatch_tool("revise_draft", {
        "draft_id": draft["id"],
        "instruction": "Add a short 'What changed' bullet list at the top and tighten the intro to one sentence.",
    }, conn))
    print(f"\n[harness] revise_draft -> {rev}")
    revised = S.get_draft(conn, draft["id"])
    print("\n===== REVISED CARD =====")
    print(revised["title"])
    print("-" * 60)
    print((revised["content"] or "")[:1400])

    # 4) trace summary — show the real Gemini inputs/outputs we captured
    rows = [json.loads(ln) for ln in Path(TRACE_PATH).read_text(encoding="utf-8").splitlines()]
    print(f"\n[harness] {len(rows)} calls recorded → {TRACE_PATH}")
    print("[harness] real Gemini calls captured (input/output sizes):")
    for rec in rows:
        if rec["fn"].endswith("GeminiClient.generate"):
            prompt = rec["args"][1] if len(rec["args"]) > 1 else ""
            print(f"   * generate [{rec.get('elapsed_ms')}ms]  prompt={len(prompt)}ch  "
                  f"response={len(rec.get('result', ''))}ch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
