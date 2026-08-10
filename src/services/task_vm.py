"""Pure viewmodel builders for the web task mirror (WS-D-WEB M2).

Flat, table-driven, Qt-free transforms of the SAME enriched task dict that
page.py hands the native ``TaskDetailPanel`` (extras + board_names + rich
subtasks injected) — native/web parity is by construction (one input), locked
by tests/test_task_web_controller.py. ``src/services`` must not import
``src/ui``, so the display formatting the native panel implements privately
is duplicated here on purpose; the parity test is the anti-drift guard.

Every builder is total: any missing/garbage key degrades to a rendered
default, never an exception — the JS side re-normalizes but must never need to.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

TEXT_CAP = 6000  # task_brief._INPUT_CHAR_CAP — the repo body-cap convention

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Deterministic per-user avatar fill from the Asana project-color palette.
_AVATAR_COLORS = ("#4186e0", "#aa62e3", "#20aaea", "#62d26f", "#ea4e9d",
                  "#fd612c", "#37c5ab", "#7a6ff0", "#e8384f", "#8da3a6")

# Asana custom-field option colors are palette NAMES ("green", "yellow-green",
# "hot-pink"…) which map 1:1 onto task.css's .tk-pill--<name> classes.
_PILL_COLORS = frozenset((
    "red", "orange", "yellow-orange", "yellow", "yellow-green", "green",
    "blue-green", "aqua", "blue", "indigo", "purple", "magenta", "hot-pink",
    "pink", "cool-gray",
))

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_TRUTHY = frozenset(("true", "checked", "yes", "1"))


def _s(value) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _lst(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def initials(name: str) -> str:
    parts = [p for p in _s(name).split() if p]
    if not parts:
        return "?"
    first = parts[0][0]
    last = parts[-1][0] if len(parts) > 1 else ""
    return (first + last).upper()


def avatar(name) -> dict | None:
    name = _s(name).strip()
    if not name:
        return None
    color = _AVATAR_COLORS[sum(ord(c) for c in name) % len(_AVATAR_COLORS)]
    return {"name": name, "initials": initials(name), "color": color}


def fmt_date(iso, *, year_always: bool = True, now=None) -> str:
    """``2025-10-17`` → ``Oct 17, 2025`` (Asana's `MMM D, YYYY`); short form
    drops the year when it matches today's. Non-dates pass through."""
    raw = _s(iso).strip()[:10]
    try:
        d = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return _s(iso).strip()
    if not year_always and d.year == (now or datetime.now()).year:
        return f"{_MONTHS[d.month - 1]} {d.day}"
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def fmt_date_range(start_iso, due_iso) -> str:
    """Asana's due display: ``Start – Due`` with a spaced en dash, or the
    single date, or empty."""
    start = fmt_date(start_iso) if _s(start_iso).strip() else ""
    due = fmt_date(due_iso) if _s(due_iso).strip() else ""
    if start and due:
        return f"{start} – {due}"
    return due or start


def fmt_story_ts(iso) -> str:
    """ISO story timestamp → local ``Jul 14, 6:22 PM`` (the native panel's
    _fmt_story_ts semantics), degrading to ``iso[:10]``."""
    raw = _s(iso).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        hour = dt.hour % 12 or 12
        ampm = "AM" if dt.hour < 12 else "PM"
        return f"{_MONTHS[dt.month - 1]} {dt.day}, {hour}:{dt.minute:02d} {ampm}"
    except ValueError:
        return raw[:10]


def rel_time(iso, *, now=None) -> str:
    """Freshness wording, mirroring the native panel: just now / 5m ago /
    3h ago / 2d ago / — ."""
    raw = _s(iso).strip()
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        secs = max(0, (ref - dt).total_seconds())
    except ValueError:
        return "—"
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


class _HtmlTokens(HTMLParser):
    """Asana ``html_text`` → typed tokens. Anchors become link tokens (or
    mention tokens when the anchor text is an @-handle); everything else is
    plain text. No markup survives — the feed renders tokens, never HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens: list[dict] = []
        self._href = ""
        self._link_text: list[str] = []
        self._skip = 0   # depth inside script/style — content is dropped

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a" and not self._href:
            href = next((v for k, v in attrs if k == "href"), "") or ""
            self._href = href.strip()
            self._link_text = []
        elif tag in ("br", "p", "li") and not self._href:
            self._text("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href:
            label = "".join(self._link_text).strip()
            href = self._href
            self._href = ""
            if label.startswith("@"):
                self.tokens.append({"t": "mention", "v": label[1:], "href": ""})
            elif href.lower().startswith(("http://", "https://")):
                self.tokens.append({"t": "link", "v": label or href, "href": href})
            elif label:
                self._text(label)

    def handle_data(self, data):
        if self._skip:
            return
        if self._href:
            self._link_text.append(data)
        else:
            self._text(data)

    def _text(self, data):
        if not data:
            return
        if self.tokens and self.tokens[-1]["t"] == "text":
            self.tokens[-1]["v"] += data
        else:
            self.tokens.append({"t": "text", "v": data, "href": ""})


def tokenize_text(text) -> list[dict]:
    """Plain story text → tokens with bare URLs auto-linked."""
    text = _s(text)[:TEXT_CAP]
    tokens, pos = [], 0
    for m in _URL_RE.finditer(text):
        if m.start() > pos:
            tokens.append({"t": "text", "v": text[pos:m.start()], "href": ""})
        tokens.append({"t": "link", "v": m.group(0), "href": m.group(0)})
        pos = m.end()
    if pos < len(text):
        tokens.append({"t": "text", "v": text[pos:], "href": ""})
    return tokens


def tokens_from_story(story: dict) -> list[dict]:
    html = _s(story.get("html"))
    if html:
        parser = _HtmlTokens()
        try:
            parser.feed(html[:TEXT_CAP])
            parser.close()
            if parser.tokens:
                return parser.tokens
        except Exception:  # noqa: BLE001 — hostile bytes degrade to plain text
            pass
    return tokenize_text(story.get("text"))


def story_kind(story: dict) -> str:
    """comment_added + human author → comment; comment_added without an
    author → rule/automation; every other subtype → system row. Missing
    subtype = pre-058 stored row, which was comments-only by construction."""
    subtype = _s(story.get("subtype")).strip() or "comment_added"
    if subtype == "comment_added":
        return "comment" if _s(story.get("author")).strip() else "automation"
    return "system"


def story_vm(story: dict) -> dict:
    story = story if isinstance(story, dict) else {}
    return {
        "gid": _s(story.get("gid")),
        "kind": story_kind(story),
        "author": avatar(story.get("author")),
        "when": fmt_story_ts(story.get("created_at")),
        "tokens": tokens_from_story(story),
    }


def _infer_kind(cf: dict) -> str:
    subtype = _s(cf.get("resource_subtype")).strip()
    if subtype in ("enum", "multi_enum", "people", "date", "number", "text",
                   "checkbox"):
        return subtype
    if cf.get("enum_value") is not None:
        return "enum"
    if cf.get("multi_enum_values"):
        return "multi_enum"
    if cf.get("people_value"):
        return "people"
    if cf.get("date_value"):
        return "date"
    if cf.get("number_value") is not None:
        return "number"
    return "text"


def _pill(option) -> dict | None:
    option = option if isinstance(option, dict) else {}
    text = _s(option.get("name")).strip()
    if not text:
        return None
    color = _s(option.get("color")).strip().lower()
    return {"text": text, "color": color if color in _PILL_COLORS else ""}


def _field_pills(cf: dict, kind: str) -> list[dict]:
    if kind == "enum":
        pill = _pill(cf.get("enum_value"))
        return [pill] if pill else []
    if kind == "multi_enum":
        return [p for p in (_pill(o) for o in cf.get("multi_enum_values") or [])
                if p]
    return []


def field_vm(cf: dict) -> dict:
    cf = cf if isinstance(cf, dict) else {}
    kind = _infer_kind(cf)
    value = _s(cf.get("display_value")).strip()
    if kind == "date" and isinstance(cf.get("date_value"), dict):
        value = fmt_date(cf["date_value"].get("date")) or value
    return {
        "gid": _s(cf.get("gid")),
        "name": _s(cf.get("name")).strip(),
        "kind": kind,
        "value": value[:TEXT_CAP],
        "pills": _field_pills(cf, kind),
        "people": [a for a in (avatar((p or {}).get("name"))
                               for p in cf.get("people_value") or []) if a],
        "checked": value.lower() in _TRUTHY,
    }


def fields_vm(extras: dict) -> list[dict]:
    fields = _lst(extras.get("custom_fields")) if isinstance(extras, dict) else []
    return [f for f in (field_vm(cf) for cf in fields) if f["name"]]


def header_vm(task: dict, extras: dict, *, now=None) -> dict:
    tf = (extras.get("task_fields") or {}) if isinstance(extras, dict) else {}
    completed = task.get("status") == "done" or bool(tf.get("completed"))
    due_iso = (_s(task.get("due_iso")).strip()
               or _s(task.get("due_date")).strip()[:10]
               or _s(tf.get("due_on")).strip())
    start_iso = _s(tf.get("start_on")).strip()
    today = (now or datetime.now()).strftime("%Y-%m-%d")
    assignee = avatar(task.get("assignee") if task.get("assignee") != "—" else "")
    collaborators = [
        a for a in (avatar(f.get("name")) for f in _lst(tf.get("followers"))
                    if isinstance(f, dict)) if a]
    if assignee:
        collaborators = [c for c in collaborators if c["name"] != assignee["name"]]
    fetched = _s((extras or {}).get("fetched_at")).strip() if isinstance(extras, dict) else ""
    return {
        "completed": completed,
        "completed_on": fmt_date(_s(tf.get("completed_at"))[:10]) if tf.get("completed_at") else "",
        "title": _s(task.get("title")),
        "status_pill": None,
        "assignee": assignee,
        "collaborators": collaborators,
        "due_display": fmt_date_range(start_iso, due_iso),
        "due_iso": due_iso,
        "overdue": bool(due_iso) and not completed and due_iso < today,
        "projects": [{"board": b, "section": ""}
                     for b in _lst(task.get("board_names")) if _s(b).strip()],
        "freshness": f"Updated {rel_time(fetched)}" if fetched else "Not synced yet",
        "permalink": _s(task.get("source_url")).strip()
                     or _s(tf.get("permalink_url")).strip(),
    }


def subtasks_vm(task: dict) -> list[dict]:
    out = []
    for s in _lst(task.get("subtasks")):
        if isinstance(s, dict):
            name, done = _s(s.get("text")), bool(s.get("done"))
            who, due = _s(s.get("assignee")).strip(), _s(s.get("due")).strip()
        else:
            try:
                name, done = _s(s[0]), bool(s[1])
            except (TypeError, IndexError):
                continue
            who = due = ""
        if not name.strip():
            continue
        out.append({
            "gid": _s(s.get("gid")).strip() if isinstance(s, dict) else "",
            "name": name,
            "done": done,
            "assignee": who,
            "due": fmt_date(due, year_always=False) if due else "",
            "promoted": bool(who or due),
        })
    return out


def attachments_vm(extras: dict) -> list[dict]:
    rows = _lst(extras.get("attachments")) if isinstance(extras, dict) else []
    return [{"gid": _s(a.get("gid")), "name": _s(a.get("name")).strip(),
             "host": _s(a.get("host")).strip().lower()}
            for a in rows if isinstance(a, dict) and _s(a.get("name")).strip()]


DESCRIPTION_MD_CAP = 60000


def description_vm(extras: dict, task: dict) -> dict:
    """The ONE html surface. html_notes is UNTRUSTED — sanitize with the
    rendering-only preview profile; the JS side adds the sandbox="" iframe
    wall. Plain-text description degrades through the same sanitizer.
    ``markdown`` feeds the edit textarea (the same html→md conversion the
    Renn lanes use); the editor round-trips md → Asana html dialect."""
    from src.data.html_sanitize import sanitize_html_preview
    html = _s((extras or {}).get("html_notes") if isinstance(extras, dict) else "").strip()
    plain = _s(task.get("description")).strip()
    markdown = ""
    if html:
        try:
            from src.data.html_markdown import html_to_markdown
            markdown = (html_to_markdown(html) or "").strip()
        except Exception:  # noqa: BLE001 — editor falls back to plain text
            markdown = ""
    if not markdown:
        markdown = plain
    if not html:
        if not plain:
            return {"srcdoc": "", "markdown": ""}
        html = "<p>" + plain.replace("&", "&amp;").replace("<", "&lt;") \
                            .replace(">", "&gt;").replace("\n", "<br>") + "</p>"
    return {"srcdoc": sanitize_html_preview(html[:TEXT_CAP]),
            "markdown": markdown[:DESCRIPTION_MD_CAP]}


def build_task_vm(task: dict, *, connected: bool = True,
                  capabilities: dict | None = None, now=None) -> dict:
    """The full #/task viewmodel from ONE enriched task dict (the exact dict
    the native TaskDetailPanel receives)."""
    task = task if isinstance(task, dict) else {}
    extras = task.get("extras") if isinstance(task.get("extras"), dict) else {}
    stories = [story_vm(s) for s in _lst(extras.get("stories"))
               if isinstance(s, dict)]
    return {
        "connected": bool(connected),
        "demo": False,
        "task_id": _s(task.get("task_id")),
        "header": header_vm(task, extras, now=now),
        "fields": fields_vm(extras),
        "description": description_vm(extras, task),
        "subtasks": subtasks_vm(task),
        "attachments": attachments_vm(extras),
        "stories": stories,
        "capabilities": {
            "complete": False, "due": False, "comment": False,
            "subtask": False, "description": False, "refresh": False,
            **(capabilities or {}),
        },
    }


def link_registry(vm: dict) -> set:
    """Every URL the viewmodel legitimately carries — the ONLY urls
    js_open_url may open (a forged url is a silent no-op)."""
    urls = set()
    permalink = vm.get("header", {}).get("permalink", "")
    if permalink:
        urls.add(permalink)
    for story in vm.get("stories") or []:
        for tok in story.get("tokens") or []:
            if tok.get("t") == "link" and tok.get("href"):
                urls.add(tok["href"])
    return urls
