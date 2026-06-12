"""PowerPoint deck store — model a deck (editable JSON outline) and export
a real .pptx via python-pptx.

Mirrors the enablement card-draft lifecycle: stage a deck outline in
`pptx_decks` (status='pending'), edit it, then `export_pptx` writes the
file and marks it exported. The LLM only needs `.generate(prompt)`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.data.connection_factory import atomic

_PROMPTS = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
_DECK_PROMPT = _PROMPTS / "enablement_pptx_from_doc.txt"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_outline(outline) -> dict:
    """Coerce a parsed outline into {title, slides:[{title, bullets[]}]}."""
    if not isinstance(outline, dict):
        outline = {}
    title = str(outline.get("title") or "Untitled deck")
    slides_in = outline.get("slides") or []
    slides = []
    for s in slides_in if isinstance(slides_in, list) else []:
        if not isinstance(s, dict):
            continue
        bullets = [str(b) for b in (s.get("bullets") or []) if str(b).strip()]
        slides.append({"title": str(s.get("title") or "Slide"), "bullets": bullets})
    return {"title": title, "slides": slides}


def parse_outline(text: str) -> dict:
    """Tolerant parse of an LLM deck response → normalized outline.

    Accepts a raw JSON object or JSON embedded in prose/fences.
    """
    raw = (text or "").strip()
    try:
        return _normalize_outline(json.loads(raw))
    except (ValueError, TypeError):
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return _normalize_outline(json.loads(m.group(0)))
        except (ValueError, TypeError):
            pass
    return _normalize_outline({"title": "Untitled deck", "slides": []})


# ── CRUD ─────────────────────────────────────────────────────────────

def save_deck(conn: sqlite3.Connection, *, title: str, outline: dict,
              source_ref: str | None = None) -> int:
    outline = _normalize_outline(outline)
    now = _now()
    with atomic(conn):
        cur = conn.execute(
            "INSERT INTO pptx_decks (title, source_ref, outline_json, "
            "slide_count, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (title, source_ref, json.dumps(outline), len(outline["slides"]),
             now, now),
        )
        return int(cur.lastrowid)


def get_deck(conn: sqlite3.Connection, deck_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM pptx_decks WHERE id = ?", (deck_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["outline"] = _normalize_outline(json.loads(d.get("outline_json") or "{}"))
    except (ValueError, TypeError):
        d["outline"] = _normalize_outline({})
    return d


def list_decks(conn: sqlite3.Connection, *, status: str | None = None,
               limit: int = 100) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT id, title, status, slide_count, source_ref, file_path, "
            "updated_at FROM pptx_decks WHERE status=? ORDER BY "
            "COALESCE(updated_at, created_at) DESC LIMIT ?", (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, status, slide_count, source_ref, file_path, "
            "updated_at FROM pptx_decks ORDER BY "
            "COALESCE(updated_at, created_at) DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_deck_outline(conn: sqlite3.Connection, deck_id: int, *,
                        title: str | None = None, outline: dict | None = None) -> dict:
    deck = get_deck(conn, deck_id)
    if not deck:
        return {"ok": False, "error": "deck_not_found"}
    new_outline = _normalize_outline(outline) if outline is not None else deck["outline"]
    new_title = title if title is not None else deck["title"]
    with atomic(conn):
        conn.execute(
            "UPDATE pptx_decks SET title=?, outline_json=?, slide_count=?, "
            "updated_at=? WHERE id=?",
            (new_title, json.dumps(new_outline), len(new_outline["slides"]),
             _now(), deck_id),
        )
    return {"ok": True, "deck_id": deck_id, "slides": len(new_outline["slides"])}


# ── generation ───────────────────────────────────────────────────────

def generate_deck_from_document(conn: sqlite3.Connection, doc_id: str, llm_client,
                                *, audience: str = "the enablement team") -> dict:
    """Model a deck outline from a stored document via the LLM client."""
    from src.data.enablement_store import get_document
    doc = get_document(conn, doc_id)
    if not doc:
        return {"ok": False, "error": "document_not_found"}
    template = _DECK_PROMPT.read_text(encoding="utf-8") if _DECK_PROMPT.exists() else (
        "Turn the document into a slide deck. SOURCE: {doc_name}\n{doc_text}\n"
        "Return JSON: {{\"title\": str, \"slides\": [{{\"title\": str, "
        "\"bullets\": [str]}}]}}"
    )
    prompt = template.format(
        doc_name=doc.get("name", "Untitled"),
        doc_text=doc.get("full_text", ""),
        audience=audience,
    )
    outline = parse_outline(llm_client.generate(prompt))
    deck_id = save_deck(conn, title=outline["title"], outline=outline,
                        source_ref=doc_id)
    return {"ok": True, "deck_id": deck_id, "title": outline["title"],
            "slides": len(outline["slides"])}


def generate_deck_from_topic(conn: sqlite3.Connection, topic: str, llm_client) -> dict:
    template = _DECK_PROMPT.read_text(encoding="utf-8") if _DECK_PROMPT.exists() else (
        "Create a slide deck about: {doc_text}\nReturn JSON.")
    prompt = template.format(doc_name=topic, doc_text=topic,
                             audience="the enablement team")
    outline = parse_outline(llm_client.generate(prompt))
    deck_id = save_deck(conn, title=outline["title"], outline=outline,
                        source_ref=f"topic:{topic[:80]}")
    return {"ok": True, "deck_id": deck_id, "title": outline["title"],
            "slides": len(outline["slides"])}


# ── export ───────────────────────────────────────────────────────────

def export_pptx(conn: sqlite3.Connection, deck_id: int, out_path: str) -> dict:
    """Build a real .pptx from the deck outline and mark it exported."""
    deck = get_deck(conn, deck_id)
    if not deck:
        return {"ok": False, "error": "deck_not_found"}
    try:
        from pptx import Presentation
    except ImportError:
        return {"ok": False, "error": "python-pptx not installed"}

    outline = deck["outline"]
    prs = Presentation()
    title_layout = prs.slide_layouts[0]
    content_layout = prs.slide_layouts[1]

    cover = prs.slides.add_slide(title_layout)
    cover.shapes.title.text = outline["title"]

    for s in outline["slides"]:
        slide = prs.slides.add_slide(content_layout)
        slide.shapes.title.text = s["title"]
        body = slide.placeholders[1].text_frame
        body.clear()
        bullets = s.get("bullets") or [""]
        for i, bullet in enumerate(bullets):
            para = body.paragraphs[0] if i == 0 else body.add_paragraph()
            para.text = bullet

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)
    with atomic(conn):
        conn.execute(
            "UPDATE pptx_decks SET status='exported', file_path=?, "
            "exported_at=?, updated_at=? WHERE id=?",
            (out_path, _now(), _now(), deck_id),
        )
    return {"ok": True, "deck_id": deck_id, "file_path": out_path,
            "slides": len(outline["slides"]) + 1}  # + cover
