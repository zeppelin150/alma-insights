"""Knowledge-check quiz format (WS3-M5, renn-calendar-kb-studio plan).

A quiz is a deterministic, diffable MD/YAML hybrid so the ReviewPanel can show
it as readable text:

    ---
    title: Aetna recheck basics
    pass_threshold: 70
    ---
    ### Q1: How often must eligibility be rechecked?
    - [ ] Every 90 days
    - [x] Every 30 days
    Explanation: Aetna moved to a 30-day cadence in July.

Supports single- and multi-select (2+ choices, 1+ correct). ``to_guru_html``
renders a static, JS-free section using <details>/<summary> answer reveals
(fallback if Guru's sanitizer strips them: the answer text is still inside the
details block as plain content — degraded but never lost). Answers being
visible in page source is inherent to static HTML and acceptable for
knowledge checks.
"""

from __future__ import annotations

import html
import re

_Q_RE = re.compile(r"^###\s*Q(\d+)\s*[:.]?\s*(.+)$")
_CHOICE_RE = re.compile(r"^-\s*\[( |x|X)\]\s*(.+)$")
_EXPL_RE = re.compile(r"^Explanation\s*:\s*(.+)$", re.IGNORECASE)
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

MAX_QUESTIONS = 12
MAX_CHOICES = 6


def parse_quiz_md(text: str) -> tuple[bool, list[str], dict | None]:
    """Strict parse → (ok, errors, quiz). Every question needs >= 2 choices
    and >= 1 correct; explanations optional."""
    import yaml
    errors: list[str] = []
    raw = (text or "").strip()
    meta: dict = {}
    m = _FM_RE.match(raw)
    if m:
        try:
            parsed = yaml.safe_load(m.group(1))
            meta = parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            errors.append("frontmatter is not valid YAML")
        raw = raw[m.end():]

    questions: list[dict] = []
    current: dict | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        qm = _Q_RE.match(line)
        if qm:
            current = {"question": qm.group(2).strip(), "choices": [],
                       "explanation": ""}
            questions.append(current)
            continue
        cm = _CHOICE_RE.match(line)
        if cm and current is not None:
            current["choices"].append({"text": cm.group(2).strip(),
                                       "correct": cm.group(1).lower() == "x"})
            continue
        em = _EXPL_RE.match(line)
        if em and current is not None:
            current["explanation"] = em.group(1).strip()

    if not questions:
        errors.append("no questions found — use '### Q1: <question>' headers")
    if len(questions) > MAX_QUESTIONS:
        errors.append(f"too many questions (max {MAX_QUESTIONS})")
    for i, q in enumerate(questions, start=1):
        if len(q["choices"]) < 2:
            errors.append(f"Q{i} needs at least 2 choices ('- [ ] text')")
        elif len(q["choices"]) > MAX_CHOICES:
            errors.append(f"Q{i} has too many choices (max {MAX_CHOICES})")
        if q["choices"] and not any(c["correct"] for c in q["choices"]):
            errors.append(f"Q{i} has no correct answer ('- [x] text')")
    if errors:
        return False, errors, None
    return True, [], {
        "title": str(meta.get("title") or "Knowledge check"),
        "pass_threshold": int(meta.get("pass_threshold") or 70),
        "questions": questions,
    }


def serialize_quiz(quiz: dict) -> str:
    """Canonical MD (the artifact's diffable form)."""
    import yaml
    fm = yaml.safe_dump({"title": quiz.get("title", "Knowledge check"),
                         "pass_threshold": quiz.get("pass_threshold", 70)},
                        sort_keys=False)
    lines = [f"---\n{fm}---", ""]
    for i, q in enumerate(quiz.get("questions") or [], start=1):
        lines.append(f"### Q{i}: {q['question']}")
        for c in q["choices"]:
            mark = "x" if c["correct"] else " "
            lines.append(f"- [{mark}] {c['text']}")
        if q.get("explanation"):
            lines.append(f"Explanation: {q['explanation']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def to_guru_html(quiz: dict) -> str:
    """Static, JS-free HTML for a Guru card section."""
    parts = [f"<h2>{html.escape(quiz.get('title', 'Knowledge check'))}</h2>"]
    for i, q in enumerate(quiz.get("questions") or [], start=1):
        parts.append(f"<p><strong>Q{i}. {html.escape(q['question'])}</strong></p>")
        items = "".join(f"<li>{html.escape(c['text'])}</li>"
                        for c in q["choices"])
        parts.append(f"<ul>{items}</ul>")
        answers = ", ".join(html.escape(c["text"])
                            for c in q["choices"] if c["correct"])
        expl = (f" — {html.escape(q['explanation'])}"
                if q.get("explanation") else "")
        parts.append("<details><summary>Show answer</summary>"
                     f"<p>Answer: {answers}{expl}</p></details>")
    return "\n".join(parts)
