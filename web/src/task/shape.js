// Pure viewmodel guards for the #/task surface. Every renderer downstream is
// a total function of THIS shape — normalizeData accepts any bytes the bridge
// (or a forged caller) delivers and returns a fully-defaulted viewmodel, so
// render can never throw on missing keys.

const FIELD_KINDS = new Set([
  "enum", "multi_enum", "people", "date", "number", "text", "checkbox",
]);
const STORY_KINDS = new Set(["comment", "automation", "system"]);
const TOKEN_KINDS = new Set(["text", "link", "mention"]);

function str(v) {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function arr(v) {
  return Array.isArray(v) ? v : [];
}

function obj(v) {
  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
}

export function initialsOf(name) {
  const parts = str(name).trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const first = parts[0][0] || "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] || "" : "";
  return (first + last).toUpperCase() || "?";
}

function person(v) {
  const p = obj(v);
  const name = str(p.name);
  if (!name) return null;
  return {
    name,
    initials: str(p.initials) || initialsOf(name),
    color: str(p.color),
  };
}

function pill(v) {
  const p = obj(v);
  const text = str(p.text);
  if (!text) return null;
  return { text, color: str(p.color) };
}

function field(v) {
  const f = obj(v);
  const kind = FIELD_KINDS.has(f.kind) ? f.kind : "text";
  return {
    gid: str(f.gid),
    name: str(f.name),
    kind,
    value: str(f.value),
    pills: arr(f.pills).map(pill).filter(Boolean),
    people: arr(f.people).map(person).filter(Boolean),
    checked: !!f.checked,
  };
}

function project(v) {
  const p = obj(v);
  return { board: str(p.board), section: str(p.section) };
}

function subtask(v) {
  const s = obj(v);
  return {
    gid: str(s.gid),
    name: str(s.name),
    done: !!s.done,
    assignee: str(s.assignee),
    due: str(s.due),
    promoted: !!s.promoted,
  };
}

function attachment(v) {
  const a = obj(v);
  return { gid: str(a.gid), name: str(a.name), host: str(a.host) };
}

function token(v) {
  const t = obj(v);
  const kind = TOKEN_KINDS.has(t.t) ? t.t : "text";
  return { t: kind, v: str(t.v), href: str(t.href) };
}

function story(v) {
  const s = obj(v);
  const kind = STORY_KINDS.has(s.kind) ? s.kind : "system";
  const tokens = arr(s.tokens).map(token);
  if (!tokens.length && str(s.text)) tokens.push({ t: "text", v: str(s.text), href: "" });
  return {
    gid: str(s.gid),
    kind,
    author: person(s.author),
    when: str(s.when),
    tokens,
  };
}

export function normalizeData(raw) {
  const p = obj(raw);
  const h = obj(p.header);
  const caps = obj(p.capabilities);
  return {
    connected: !!p.connected,
    demo: !!p.demo,
    task_id: str(p.task_id),
    header: {
      completed: !!h.completed,
      completed_on: str(h.completed_on),
      title: str(h.title),
      status_pill: pill(h.status_pill),
      assignee: person(h.assignee),
      collaborators: arr(h.collaborators).map(person).filter(Boolean),
      due_display: str(h.due_display),
      due_iso: str(h.due_iso),
      overdue: !!h.overdue,
      projects: arr(h.projects).map(project).filter((x) => x.board),
      freshness: str(h.freshness),
      permalink: str(h.permalink),
    },
    fields: arr(p.fields).map(field).filter((f) => f.name),
    description: { srcdoc: str(obj(p.description).srcdoc) },
    subtasks: arr(p.subtasks).map(subtask).filter((s) => s.name),
    attachments: arr(p.attachments).map(attachment).filter((a) => a.name),
    stories: arr(p.stories).map(story),
    capabilities: {
      complete: !!caps.complete,
      due: !!caps.due,
      comment: !!caps.comment,
      subtask: !!caps.subtask,
      refresh: !!caps.refresh,
    },
  };
}

// Feed helpers — the sort/tab/collapse controls are local UI state in
// TaskApp; these keep the transforms pure and testable.
export function visibleStories(stories, tab) {
  const all = arr(stories);
  return tab === "comments" ? all.filter((s) => s.kind === "comment") : all;
}

export function orderStories(stories, oldestFirst) {
  const all = arr(stories).slice();
  return oldestFirst ? all : all.reverse();
}

// Asana collapses the MIDDLE of a long feed: first item, "N more", last five.
export const COLLAPSE_HEAD = 1;
export const COLLAPSE_TAIL = 5;

export function collapseStories(stories, expanded) {
  const all = arr(stories);
  const max = COLLAPSE_HEAD + COLLAPSE_TAIL;
  if (expanded || all.length <= max + 1) {
    return { head: all, hidden: 0, tail: [] };
  }
  return {
    head: all.slice(0, COLLAPSE_HEAD),
    hidden: all.length - max,
    tail: all.slice(all.length - COLLAPSE_TAIL),
  };
}
