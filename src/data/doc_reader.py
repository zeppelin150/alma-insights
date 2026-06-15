"""Loose, format-resolving document reader → markdown (stdlib only).

Uploads/imports arrive as .docx / .txt / .md / .html (and Google-Doc URLs,
which route through the Drive import). Rather than flatten everything to
plain text, this resolves the source's *formatting* into markdown so the
generated card keeps the document's structure:

  - .docx  → headings (Word heading styles), **bold** / *italic* /
             ~~strike~~, bullet + numbered lists (with nesting), hyperlinks,
             and tables — parsed from the OOXML with zipfile + ElementTree.
  - .md    → passed through unchanged.
  - .html  → html_to_markdown.
  - .txt / .csv / .json / … → read as text (doc_to_card structures it).

"Loose" = tolerant: any element that won't parse is skipped, never fatal;
a format we can't read degrades to a clear note so a draft still forms.
The Drive/Google-Docs path is handled separately (it needs auth) via the
existing enablement_store.import_drive_doc.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile

_TEXT_EXT = {".txt", ".text", ".md", ".markdown", ".csv", ".json", ".rst", ".log", ""}
_ORDERED_FMTS = {"decimal", "ordinal", "lowerletter", "upperletter",
                 "lowerroman", "upperroman", "ordinaltext", "decimalzero"}


# Namespace-agnostic XML helpers — match on local tag/attribute names so the
# parser works regardless of the exact OOXML namespace URI a producer uses.

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el, name: str):
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _find(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _findall(el, name: str):
    return [c for c in el if _local(c.tag) == name]


# ── public ───────────────────────────────────────────────────────────

def read_document(path: str) -> str:
    """Read a local document into markdown, resolving formatting where we can."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            return docx_to_markdown(path)
        if ext in (".html", ".htm"):
            from src.data.html_markdown import html_to_markdown
            return html_to_markdown(_read_text(path))
        if ext in _TEXT_EXT:
            return _read_text(path)
        # Unknown: try as text, else a clear stub.
        return _read_text(path)
    except Exception:
        return _unreadable(path)


def docx_to_markdown(path: str) -> str:
    """Convert a .docx to markdown, resolving Word formatting. Stdlib only."""
    with zipfile.ZipFile(path) as z:
        doc_xml = z.read("word/document.xml")
        rels = _read_rels(z)
        numbering = _read_numbering(z)
    root = ET.fromstring(doc_xml)
    body = _find(root, "body")
    if body is None:
        return ""
    blocks: list[dict] = []
    for el in list(body):
        try:
            tag = _local(el.tag)
            if tag == "p":
                blocks.append(_paragraph(el, rels, numbering))
            elif tag == "tbl":
                blocks.append({"li": False, "md": _table(el, rels)})
        except Exception:
            continue   # loose: skip anything that won't parse
    return _assemble(blocks)


# ── docx internals ───────────────────────────────────────────────────

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _unreadable(path: str) -> str:
    return (
        f"Imported file: {os.path.basename(path)}.\n\n"
        "(This format can't be read locally — paste the content here, or "
        "connect Google Drive to extract it.)"
    )


def _read_rels(z: zipfile.ZipFile) -> dict:
    try:
        root = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError):
        return {}
    return {_attr(r, "Id"): (_attr(r, "Target") or "")
            for r in root if _local(r.tag) == "Relationship"}


def _read_numbering(z: zipfile.ZipFile) -> dict:
    """numId → is-ordered? (best effort from numbering.xml)."""
    try:
        root = ET.fromstring(z.read("word/numbering.xml"))
    except (KeyError, ET.ParseError):
        return {}
    abstract = {}
    for an in _findall(root, "abstractNum"):
        aid = _attr(an, "abstractNumId")
        lvl0 = _find(an, "lvl")
        fmt = ""
        if lvl0 is not None:
            f = _find(lvl0, "numFmt")
            fmt = (_attr(f, "val") if f is not None else "").lower()
        abstract[aid] = fmt in _ORDERED_FMTS
    num2ordered = {}
    for n in _findall(root, "num"):
        a = _find(n, "abstractNumId")
        if a is not None:
            num2ordered[_attr(n, "numId")] = abstract.get(_attr(a, "val"), False)
    return num2ordered


def _on(el) -> bool:
    """A toggle run-property is on unless explicitly w:val=false/0."""
    if el is None:
        return False
    return (_attr(el, "val") or "true") not in ("false", "0", "none", "off")


def _heading_level(ppr) -> int:
    if ppr is None:
        return 0
    pstyle = _find(ppr, "pStyle")
    val = (_attr(pstyle, "val") if pstyle is not None else "") or ""
    key = val.lower().replace(" ", "")
    if key == "title":
        return 1
    if key == "subtitle":
        return 2
    m = re.match(r"heading(\d)", key)
    if m:
        return min(int(m.group(1)), 6)
    return 0


def _list_info(ppr, numbering) -> tuple:
    if ppr is None:
        return (False, False, 0)
    numpr = _find(ppr, "numPr")
    if numpr is None:
        return (False, False, 0)
    ilvl_el = _find(numpr, "ilvl")
    numid_el = _find(numpr, "numId")
    ilvl = int(_attr(ilvl_el, "val") or 0) if ilvl_el is not None else 0
    numid = _attr(numid_el, "val") if numid_el is not None else None
    return (True, bool(numbering.get(numid, False)), ilvl)


def _run_text(r) -> str:
    out = []
    for t in r:
        name = _local(t.tag)
        if name == "t":
            out.append(t.text or "")
        elif name == "tab":
            out.append("\t")
        elif name in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def _run_md(r) -> str:
    text = _run_text(r)
    if not text:
        return ""
    rpr = _find(r, "rPr")
    bold = rpr is not None and _on(_find(rpr, "b"))
    ital = rpr is not None and _on(_find(rpr, "i"))
    strike = rpr is not None and _on(_find(rpr, "strike"))
    if not text.strip():
        return text   # don't wrap pure whitespace
    lead = text[:len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    word = text.strip()
    if strike:
        word = f"~~{word}~~"
    if bold and ital:
        word = f"***{word}***"
    elif bold:
        word = f"**{word}**"
    elif ital:
        word = f"*{word}*"
    return f"{lead}{word}{trail}"


def _inline_md(parent, rels) -> str:
    parts = []
    for child in parent:
        name = _local(child.tag)
        if name == "r":
            parts.append(_run_md(child))
        elif name == "hyperlink":
            inner = "".join(_run_md(r) for r in _findall(child, "r")).strip()
            url = rels.get(_attr(child, "id"), "")
            parts.append(f"[{inner}]({url})" if (inner and url) else inner)
    return "".join(parts)


def _paragraph(p, rels, numbering) -> dict:
    ppr = _find(p, "pPr")
    text = _inline_md(p, rels).strip()
    is_list, ordered, ilvl = _list_info(ppr, numbering)
    if is_list:
        indent = "  " * max(0, ilvl)
        marker = "1." if ordered else "-"
        return {"li": True, "md": f"{indent}{marker} {text}" if text else ""}
    if not text:
        return {"li": False, "md": ""}
    level = _heading_level(ppr)
    if level:
        return {"li": False, "md": f"{'#' * level} {text}"}
    return {"li": False, "md": text}


def _table(tbl, rels) -> str:
    rows = []
    for tr in _findall(tbl, "tr"):
        cells = []
        for tc in _findall(tr, "tc"):
            cell = " ".join(_inline_md(p, rels).strip()
                            for p in _findall(tc, "p")).strip()
            cells.append(cell.replace("|", "\\|") or " ")
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [" "] * (ncol - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join(["---"] * ncol) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _assemble(blocks: list[dict]) -> str:
    """Join blocks: list items stay contiguous; other blocks get a blank
    line of separation."""
    out: list[str] = []
    for b in blocks:
        md = b.get("md", "")
        if b.get("li"):
            if md:
                out.append(md)
        else:
            if md:
                if out and out[-1] != "":
                    out.append("")
                out.append(md)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return text.strip("\n")   # drop blank lines, keep content indentation
