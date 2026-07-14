"""Read .pptx decks into Markdown (WS2-M4, renn-calendar-kb-studio plan).

python-pptx (already bundled — write-only until now) parses the deck; slide
titles become headings, body placeholders bullets, tables pipe-tables, and
speaker notes blockquote lines. Best-effort by contract: a broken deck yields
'' and NEVER raises (the ingest pipeline treats '' as metadata-only).

This closes the recon finding that Drive .pptx files silently extracted ''
(they fell into the docx branch).
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger("alma.pptx_reader")


def pptx_to_markdown(data) -> str:
    """bytes | file path → Markdown text ('' on any failure)."""
    try:
        from pptx import Presentation
        src = io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data
        prs = Presentation(src)
    except Exception as exc:  # noqa: BLE001 — corrupt/foreign file
        logger.debug("pptx parse failed: %s", exc)
        return ""

    out: list[str] = []
    for idx, slide in enumerate(prs.slides, start=1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = (slide.shapes.title.text or "").strip()
        except Exception:  # noqa: BLE001
            title = ""
        out.append(f"# {title or f'Slide {idx}'}")
        for shape in slide.shapes:
            try:
                if shape is slide.shapes.title:
                    continue
                if getattr(shape, "has_table", False):
                    out.append(_table_to_md(shape.table))
                    continue
                if not getattr(shape, "has_text_frame", False):
                    continue
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs).strip() or \
                        (para.text or "").strip()
                    if text:
                        out.append(f"- {text}")
            except Exception:  # noqa: BLE001 — one shape can't kill the deck
                continue
        try:
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "").strip()
                for line in notes.splitlines():
                    if line.strip():
                        out.append(f"> {line.strip()}")
        except Exception:  # noqa: BLE001
            pass
        out.append("")
    return "\n".join(out).strip()


def _table_to_md(table) -> str:
    rows = []
    for r in table.rows:
        cells = [(c.text or "").strip().replace("|", "/") for c in r.cells]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    header_sep = "| " + " | ".join("---" for _ in table.columns) + " |"
    return "\n".join([rows[0], header_sep] + rows[1:])
