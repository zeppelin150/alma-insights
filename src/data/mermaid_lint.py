"""Deterministic Mermaid linter (WS3-M2, renn-calendar-kb-studio plan).

Pure stdlib, no mermaid runtime — catches the breakage Haiku actually emits
(prose preambles, missing headers, unquoted punctuation labels, unbalanced
brackets, the reserved ``end`` id) and, security-critically, STRIPS the
directive surface that would otherwise reach the QWebChannel-privileged
renderer: ``%%{init ...}%%`` can flip mermaid's securityLevel and ``click``
lines execute JS callbacks — LLM-authored or prompt-injected source content
must never carry either (pre-mortem finding, severity high).

A Python linter cannot guarantee mermaid parses — the renderer's
``mermaid.parse`` (securityLevel:'strict') stays the final backstop when the
in-app preview lands. ``lint`` is the ONE gate between generation and storage:
nothing that fails it may be persisted to an artifact row.
"""

from __future__ import annotations

import re

# Diagram headers we accept (first meaningful line must start with one).
_HEADERS = (
    "flowchart", "graph", "sequenceDiagram", "stateDiagram-v2", "stateDiagram",
    "classDiagram", "erDiagram", "journey", "gantt", "pie",
)

_DIRECTIVE_RE = re.compile(r"%%\{.*?\}%%", re.DOTALL)
_CLICK_RE = re.compile(r"^\s*click\s", re.IGNORECASE)
_RESERVED_END_RE = re.compile(r"(?:^|\s)end\s*[\[\(\{]")
# The classic Haiku failure: A[Submit claim (837P)] — an UNQUOTED node label
# containing parentheses breaks mermaid's parser; the label must be "quoted".
_UNQUOTED_PARENS_LABEL_RE = re.compile(r'\[(?!")([^\]"]*[()][^\]"]*)\]')


def lint(text: str) -> tuple[bool, list[str], str]:
    """Validate (and security-clean) Mermaid source.

    Returns ``(ok, errors, cleaned_source)``. Directive/click lines are
    silently removed from ``cleaned_source`` (a diagram works without them);
    ``javascript:`` / ``callback`` anywhere is a HARD error — that content has
    no honest reason to exist in a generated diagram.
    """
    errors: list[str] = []
    src = (text or "").strip()
    if not src:
        return False, ["empty output — return a fenced ```mermaid block"], ""

    # Drop prose preamble: start from the first line that begins with a header.
    lines = src.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if any(line.strip().startswith(h) for h in _HEADERS):
            start = i
            break
    else:
        return False, [
            "missing diagram header — the first line must be one of: "
            + ", ".join(_HEADERS)
        ], src
    body = "\n".join(lines[start:])

    # ── security pass ────────────────────────────────────────────────
    lowered = body.lower()
    if "javascript:" in lowered or "callback" in lowered:
        errors.append("javascript:/callback content is not allowed in diagrams")
    body = _DIRECTIVE_RE.sub("", body)                      # %%{init ...}%% etc.
    kept = [ln for ln in body.splitlines() if not _CLICK_RE.match(ln)]
    body = "\n".join(kept).strip()

    content_lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(content_lines) < 2:
        errors.append("blank diagram — add at least one node/edge line under the header")
        return False, errors, body

    header = content_lines[0].strip()
    kind = next((h for h in _HEADERS if header.startswith(h)), "")

    # ── bracket/quote balance (quoted spans are opaque) ─────────────
    depth = {"[": 0, "(": 0, "{": 0}
    close_of = {"]": "[", ")": "(", "}": "{"}
    in_quote = False
    for ch in body:
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch in depth:
            depth[ch] += 1
        elif ch in close_of:
            depth[close_of[ch]] -= 1
    if in_quote:
        errors.append('unbalanced double quotes — every "label" must close')
    for opener, d in depth.items():
        if d != 0:
            errors.append(f"unbalanced {opener}...{ {'[': ']', '(': ')', '{': '}'}[opener] } brackets")

    # ── per-type light grammar ───────────────────────────────────────
    rest = "\n".join(content_lines[1:])
    if kind in ("flowchart", "graph"):
        if _RESERVED_END_RE.search(rest):
            errors.append('node id "end" is reserved in flowcharts — rename it (e.g. finish)')
        m = _UNQUOTED_PARENS_LABEL_RE.search(rest)
        if m:
            errors.append(
                f'unquoted label with parentheses: [{m.group(1)}] — wrap the '
                f'label in double quotes: ["{m.group(1)}"]')
        has_arrow = bool(re.search(r"--?[->]|==>|-\.->", rest))
        if not has_arrow and "subgraph" not in rest and len(content_lines) > 2:
            errors.append("flowchart has multiple lines but no edges (-->)")
    elif kind == "sequenceDiagram":
        if not re.search(r"-{1,2}>>?", rest):
            errors.append("sequenceDiagram has no message arrows (->> etc.)")

    return (not errors), errors, body
