"""Pure, Qt-free line-level text diff for the Agent's review/sign-off UI (M5).

Mirrors the classification used by ``src/ui/pages/enablement/diff_view.py`` (the
existing Qt widget) so the React review panel reads identically — but with no
PySide6 / theme import, so the data + service layers can compute a diff and ship
JSON-friendly rows over the bridge. Lines are classed via
``difflib.SequenceMatcher`` opcodes: a ``replace`` expands to its removed lines
(``del``) followed by its added lines (``add``), reading like a unified body.
"""

from __future__ import annotations

import difflib

ADD = "add"
DEL = "del"
EQUAL = "equal"


def diff_rows(current_md: str, proposed_md: str) -> list[dict]:
    """Return ``[{"tag": "add"|"del"|"equal", "text": str}, …]`` in display order."""
    a = (current_md or "").splitlines()
    b = (proposed_md or "").splitlines()
    rows: list[dict] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        removed, added = a[i1:i2], b[j1:j2]
        if tag == "equal":
            rows.extend({"tag": EQUAL, "text": ln} for ln in removed)
        elif tag == "delete":
            rows.extend({"tag": DEL, "text": ln} for ln in removed)
        elif tag == "insert":
            rows.extend({"tag": ADD, "text": ln} for ln in added)
        else:  # replace → removed (red) then added (green)
            rows.extend({"tag": DEL, "text": ln} for ln in removed)
            rows.extend({"tag": ADD, "text": ln} for ln in added)
    return rows


def change_count(rows: list[dict]) -> int:
    """Number of non-equal rows (added or removed lines)."""
    return sum(1 for r in rows if r.get("tag") != EQUAL)


def _words(line: str) -> list[str]:
    """Tokenize preserving whitespace runs, so joins reconstruct exactly."""
    import re
    return re.findall(r"\S+|\s+", line or "")


def _word_spans(old_line: str, new_line: str):
    """Intra-line spans for a replaced line pair: (del_spans, add_spans).

    Each span is ``{"tag": "equal"|"del"|"add", "text": str}``; concatenating a
    side's span texts reproduces that side's line verbatim.
    """
    a, b = _words(old_line), _words(new_line)
    del_spans: list[dict] = []
    add_spans: list[dict] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        removed, added = "".join(a[i1:i2]), "".join(b[j1:j2])
        if tag == "equal":
            if removed:
                del_spans.append({"tag": EQUAL, "text": removed})
                add_spans.append({"tag": EQUAL, "text": removed})
        else:
            if removed:
                del_spans.append({"tag": DEL, "text": removed})
            if added:
                add_spans.append({"tag": ADD, "text": added})
    return del_spans, add_spans


def diff_words(current_md: str, proposed_md: str) -> list[dict]:
    """``diff_rows`` plus intra-line word highlights (the web Workbench diff).

    Same row order and tags as :func:`diff_rows`; add/del rows gain a
    ``"spans"`` list marking which words changed. Replaced lines pair up
    positionally (line 1 of the removed block against line 1 of the added
    block, and so on); unpaired lines and pure insert/delete blocks carry a
    single whole-line span. Equal rows carry no spans (payload stays lean).
    """
    a = (current_md or "").splitlines()
    b = (proposed_md or "").splitlines()
    rows: list[dict] = []
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        removed, added = a[i1:i2], b[j1:j2]
        if tag == "equal":
            rows.extend({"tag": EQUAL, "text": ln} for ln in removed)
        elif tag == "delete":
            rows.extend({"tag": DEL, "text": ln,
                         "spans": [{"tag": DEL, "text": ln}]} for ln in removed)
        elif tag == "insert":
            rows.extend({"tag": ADD, "text": ln,
                         "spans": [{"tag": ADD, "text": ln}]} for ln in added)
        else:  # replace → pair lines positionally for word-level highlights
            paired = min(len(removed), len(added))
            span_pairs = [_word_spans(removed[k], added[k]) for k in range(paired)]
            for k, ln in enumerate(removed):
                spans = (span_pairs[k][0] if k < paired
                         else [{"tag": DEL, "text": ln}])
                rows.append({"tag": DEL, "text": ln, "spans": spans})
            for k, ln in enumerate(added):
                spans = (span_pairs[k][1] if k < paired
                         else [{"tag": ADD, "text": ln}])
                rows.append({"tag": ADD, "text": ln, "spans": spans})
    return rows
