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

# Known binary/rich formats we cannot read locally (no auth-free extractor).
# Decoding these as text yields mojibake, which used to be modelled into a
# nonsense deck; strict callers get an error instead (finding 20).
_BINARY_EXT = {".pdf", ".doc", ".ppt", ".pptx", ".xls", ".xlsx", ".rtf",
               ".odt", ".odp", ".ods", ".pages", ".key", ".numbers",
               ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp",
               ".zip", ".gz", ".tar", ".7z", ".rar", ".mp3", ".mp4", ".mov",
               ".wav", ".bin", ".exe", ".dll", ".so", ".dylib"}


class UnsupportedDocumentError(Exception):
    """Raised by read_document(..., strict=True) when a file's format cannot be
    read locally, so a caller (e.g. deck modelling) surfaces a clear error
    instead of building output from decoded garbage."""
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


_HEX6 = re.compile(r"[0-9a-f]{6}")


def _custom_color(v) -> str | None:
    """A run/cell colour worth keeping: a 6-hex value that isn't `auto` or
    near-black body text (we don't span every default-coloured run)."""
    v = (v or "").lower()
    if not _HEX6.fullmatch(v):
        return None
    r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    return v if max(r, g, b) >= 0x40 else None


def _shade_fill(props) -> str | None:
    """The background shading fill (a callout-box colour) on a paragraph's
    pPr or a cell's tcPr — None for white/auto/no-fill."""
    if props is None:
        return None
    shd = _find(props, "shd")
    if shd is None:
        return None
    fill = (_attr(shd, "fill") or "").lower()
    return fill if (_HEX6.fullmatch(fill) and fill != "ffffff") else None


# ── public ───────────────────────────────────────────────────────────

def read_document(path: str, *, strict: bool = False) -> str:
    """Read a local document into markdown, resolving formatting where we can.

    ``strict=True`` raises :class:`UnsupportedDocumentError` for a format we
    can't read locally (a binary type, or a file whose bytes look binary),
    instead of returning a stub — so deck modelling reports "couldn't model a
    deck" rather than turning decoded garbage into slides. ``strict=False``
    (the default, used by Guru import) keeps the lenient stub so a draft still
    forms."""
    ext = os.path.splitext(path)[1].lower()
    # .docx is a zip (legitimately binary on disk) that we DO parse, so it is
    # exempt from the content sniff; everything else is refused when its
    # extension is a known binary type OR its bytes look binary (a NUL tell,
    # which also catches a mislabeled file like a PDF named .txt).
    if ext != ".docx" and (ext in _BINARY_EXT or _looks_binary(path)):
        if strict:
            raise UnsupportedDocumentError(
                f"{os.path.basename(path)} is a {ext or 'binary'} file that "
                "can't be read locally. Export it to text/markdown, or connect "
                "Google Drive to extract it.")
        return _unreadable(path)
    try:
        if ext == ".docx":
            return docx_to_markdown(path)
        if ext in (".html", ".htm"):
            from src.data.html_markdown import html_to_markdown
            return html_to_markdown(_read_text(path))
        if ext in _TEXT_EXT:
            return _read_text(path)
        # Unknown but text-looking: try as text, else a clear stub.
        return _read_text(path)
    except Exception:
        if strict:
            raise UnsupportedDocumentError(
                f"{os.path.basename(path)} could not be read.")
        return _unreadable(path)


def _looks_binary(path: str) -> bool:
    """Sniff whether a file's leading bytes are binary (a NUL byte is the
    reliable tell), so a mislabeled file (a .pdf named .txt) is caught too."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(2048)
    except Exception:  # noqa: BLE001 — unreadable → let the text path try/fail
        return False
    return b"\x00" in chunk


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
                blocks.append(_table(el, rels))
        except Exception:
            continue   # loose: skip anything that won't parse
    return _assemble(_group_shaded(blocks))


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
    bold = ital = strike = False
    color = None
    if rpr is not None:
        bold = _on(_find(rpr, "b"))
        ital = _on(_find(rpr, "i"))
        strike = _on(_find(rpr, "strike"))
        c = _find(rpr, "color")
        if c is not None:
            color = _custom_color(_attr(c, "val"))
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
    if color:   # inline HTML span — markdown processes the emphasis inside it
        word = f'<span style="color:#{color}">{word}</span>'
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
        return {"li": True, "md": f"{indent}{marker} {text}" if text else "",
                "shade": None}
    if not text:
        return {"li": False, "md": "", "shade": None}
    level = _heading_level(ppr)
    md = f"{'#' * level} {text}" if level else text
    return {"li": False, "md": md, "shade": _shade_fill(ppr)}


_INLINE_HTML_SUBS = (
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"<strong><em>\1</em></strong>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])"), r"<em>\1</em>"),
    (re.compile(r"~~(.+?)~~"), r"<del>\1</del>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
)


def _inline_html(s: str) -> str:
    """Convert inline markdown (from _inline_md) to inline HTML, for use in
    raw HTML table cells where markdown isn't re-processed. Colour spans are
    already HTML and pass through."""
    for pat, repl in _INLINE_HTML_SUBS:
        s = pat.sub(repl, s)
    return s


def _table(tbl, rels) -> dict:
    rows, fills = [], []
    for tr in _findall(tbl, "tr"):
        cells, cell_fills = [], []
        for tc in _findall(tr, "tc"):
            cell_fills.append(_shade_fill(_find(tc, "tcPr")))
            cell = " ".join(_inline_md(p, rels).strip()
                            for p in _findall(tc, "p")).strip()
            cells.append(cell)
        if cells:
            rows.append(cells)
            fills.append(cell_fills)
    if not rows:
        return {"li": False, "md": "", "shade": None}
    # A single-row table is a layout/callout box, not a data table — render its
    # text (so it doesn't become a degenerate "| x |\n| --- |").
    if len(rows) == 1:
        fill = next((f for f in fills[0] if f), None)
        return {"li": False, "md": "  ".join(c for c in rows[0] if c),
                "shade": fill}
    # Shaded cells (e.g. coloured header rows) can't be carried by a GFM table,
    # so render those as a raw HTML table with inline cell colours.
    if any(any(f) for f in fills):
        out = ["<table>"]
        for cells, cfs in zip(rows, fills):
            out.append("<tr>")
            for cell, fill in zip(cells, cfs):
                style = f' style="background-color:#{fill}"' if fill else ""
                out.append(f"<td{style}>{_inline_html(cell) or '&nbsp;'}</td>")
            out.append("</tr>")
        out.append("</table>")
        return {"li": False, "md": "".join(out), "shade": None}
    ncol = max(len(r) for r in rows)
    grid = [[(c.replace("|", "\\|") or " ") for c in r] + [" "] * (ncol - len(r))
            for r in rows]
    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * ncol) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return {"li": False, "md": "\n".join(lines), "shade": None}


def _group_shaded(blocks: list[dict]) -> list[dict]:
    """Wrap runs of consecutive same-fill blocks in one exact-colour callout
    box (`<div markdown="1" style="background-color:…">`) so the document's
    custom box colours survive — md_in_html renders the inner markdown."""
    out: list[dict] = []
    i = 0
    while i < len(blocks):
        shade = blocks[i].get("shade")
        if shade and blocks[i].get("md") and not blocks[i].get("li"):
            group = []
            while (i < len(blocks) and blocks[i].get("shade") == shade
                   and blocks[i].get("md") and not blocks[i].get("li")):
                group.append(blocks[i]["md"])
                i += 1
            inner = "\n\n".join(group)
            div = (f'<div markdown="1" style="background-color:#{shade};'
                   f'padding:12px 16px;border-radius:6px;">\n\n{inner}\n\n</div>')
            out.append({"li": False, "md": div, "shade": None})
        else:
            out.append(blocks[i])
            i += 1
    return out


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
