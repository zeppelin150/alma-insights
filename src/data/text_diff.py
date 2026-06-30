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
