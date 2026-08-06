// Pure viewmodel guards + formatters for the Zendesk Garden clone
// (#/zendesk). No DOM, no window — everything here is unit-testable in the
// plain node vitest environment. The shapes mirror the frozen
// ZendeskWebController contracts (plan section 3.4) exactly; the renderer
// never invents fields the controller does not push.

export function asList(v) {
  return Array.isArray(v) ? v : [];
}

// Full zendesk_data viewmodel with safe defaults, so a partial or absent
// payload can never crash the renderer.
export function normalizeData(p) {
  const src = p && typeof p === "object" ? p : {};
  const counts = src.counts && typeof src.counts === "object" ? src.counts : {};
  return {
    view: typeof src.view === "string" ? src.view : "articles",
    connected: !!src.connected,
    demo: !!src.demo,
    counts: {
      articles: counts.articles || 0,
      macros: counts.macros || 0,
      revisions_open: counts.revisions_open || 0,
    },
    categories: asList(src.categories),
    articles: asList(src.articles),
    macros: asList(src.macros),
    last_pull_display: src.last_pull_display || "Never",
    status: src.status || "",
    // Echoed js_search query. Present ⇒ Python already filtered the lists
    // via FTS (title+body/actions), so the client must NOT re-filter them.
    query: typeof src.query === "string" ? src.query : null,
  };
}

const VIEWS = ["articles", "macros", "revisions"];

export function isView(v) {
  return VIEWS.includes(v);
}

// ── Draft lifecycle (pending → ready → copied; pushed = legacy read-only) ──

export function statusInfo(status) {
  switch (status) {
    case "pending": return { label: "Pending review", cls: "yellow" };
    case "ready": return { label: "Ready to copy", cls: "blue" };
    case "copied": return { label: "Copied", cls: "green" };
    case "pushed": return { label: "Pushed", cls: "grey" };
    default: return { label: String(status || "unknown"), cls: "grey" };
  }
}

export function canMarkReady(status) { return status === "pending"; }
export function canMarkCopied(status) { return status === "pending" || status === "ready"; }
export function canSaveDraft(status) { return status === "pending" || status === "ready"; }
// Status never gates the clipboard (owner decision 2026-08-05): any draft
// with a served review copies behind Python's native confirm. Every known
// status is copyable; only an unknown one keeps the buttons off.
export function canCopyDraft(status) {
  return status === "pending" || status === "ready" ||
    status === "copied" || status === "pushed";
}

// Ordered status lanes for the Revision Center. Pending/Ready/Copied always
// render (empty lanes included — the review flow reads left to right);
// legacy Pushed only appears when rows exist.
export function groupRevisions(revisions) {
  const rows = asList(revisions);
  const by = { pending: [], ready: [], copied: [], pushed: [], other: [] };
  rows.forEach((r) => (by[r.status] || by.other).push(r));
  const lanes = [
    { key: "pending", label: "Pending review", items: by.pending },
    { key: "ready", label: "Ready to copy", items: by.ready },
    { key: "copied", label: "Copied to Zendesk", items: by.copied },
  ];
  if (by.pushed.length) lanes.push({ key: "pushed", label: "Pushed (legacy)", items: by.pushed });
  if (by.other.length) lanes.push({ key: "other", label: "Other", items: by.other });
  return lanes;
}

// ── Specialist body edits (articles only, v1) ─────────────────────────
// The js_save_body_edit payload carries ONLY the "body" key — markdown or
// plain text, capped at 200000 chars (mirrored Python-side). The page can
// never supply HTML: body_html is ALWAYS recomputed Python-side as
// sanitize_html(markdown_to_html(body)), so the reviewed diff, the stored
// body_html, and the clipboard copy can never diverge.
export const BODY_EDIT_MAX = 200000;

export function bodyEditPayload(text) {
  return { body: String(text ?? "").slice(0, BODY_EDIT_MAX) };
}

// Only open pending ARTICLE drafts are editable in v1: ready/copied/pushed
// are immutable Python-side (silent no-op), macros are out of scope. The UI
// mirrors the gate so buttons don't invite clicks Python will refuse.
export function canEditDraftBody(rev) {
  return !!rev && rev.kind === "article" && rev.status === "pending";
}

// Origin tag from the additive list_revisions source_ref key: drafts created
// by in-workspace specialist edits carry source_ref='specialist-edit';
// everything else is a Renn proposal.
export function originInfo(sourceRef) {
  if (sourceRef === "specialist-edit") {
    return { label: "Specialist edit", specialist: true };
  }
  return { label: "Renn", specialist: false };
}

// action_resolved {action:"body_edit", ok:false} → flash text. The
// controller attaches an error token string on failure.
export function bodyEditFailureText(p) {
  const src = p && typeof p === "object" ? p : {};
  if (typeof src.error === "string" && src.error) {
    return "Edit didn't save — " + src.error + ".";
  }
  return "Edit didn't save — see the app status bar.";
}

// Demo-only local simulation of js_save_body_edit (there is no Python to
// ask). Mirrors the controller's article-target rule: update the open
// pending specialist-edit draft when one already targets the article, else
// create a new pending draft. Returns the next revisions array plus the
// touched draft_id (null = silent no-op, matching the controller).
export function applyDemoBodyEdit(revisions, targetKind, targetId, body, article) {
  const rows = asList(revisions);
  if (targetKind === "draft") {
    const hit = rows.find((x) => x.kind === "article" && x.draft_id === targetId);
    if (!hit || hit.status !== "pending") return { revisions: rows, draft_id: null };
    return {
      revisions: rows.map((x) => (x === hit ? { ...x, body } : x)),
      draft_id: targetId,
    };
  }
  const existing = rows.find((x) =>
    x.kind === "article" && x.status === "pending" &&
    x.source_ref === "specialist-edit" && x.target_id === targetId);
  if (existing) {
    return {
      revisions: rows.map((x) => (x === existing ? { ...x, body } : x)),
      draft_id: existing.draft_id,
    };
  }
  const draftId = rows.reduce((m, x) => Math.max(m, Number(x.draft_id) || 0), 0) + 1;
  const title = article && article.title ? article.title : "Edited article";
  const row = {
    draft_id: draftId, kind: "article", target_id: targetId,
    target_title: title, title, status: "pending",
    rationale: "Edited in the workspace.", sources: [],
    source_ref: "specialist-edit", created_display: "Just now",
    copied_display: null, is_new: false, body,
  };
  return { revisions: rows.concat([row]), draft_id: draftId };
}

// ── Copy affordances (owner correction, 2026-07-26) ───────────────────
// The workflow copies DRAFTED CONTENT — the prose a specialist authored and
// reviewed — not verbatim HTML. So the primary clipboard button asks for
// this field and "Copy HTML" / "Copy rich text" move into an overflow.
// ONE constant, used by every surface: the copy field vocabulary is
// Python's (_COPY_FIELDS), so a rename there is a one-line change here.
export const COPY_DRAFTED_FIELD = "body_text";

const COPY_LABELS = {
  body_text: "drafted content",
  body_html: "HTML source",
  body_rich: "rich text",
  title: "title",
  macro_name: "macro name",
  macro_reply: "reply",
};

export function copyFieldLabel(field) {
  return COPY_LABELS[field] || String(field || "content");
}

// copy_resolved payload → flash text.
//
// NOTHING here may claim a copy happened before Python says so. Every copy
// of markup now runs a NATIVE confirm inside js_copy_field, and a
// cancelled/refused copy resolves with ok:false or does not resolve at all
// (the controller returns silently) — so the caller arms a self-clearing
// busy flag and this builder only ever describes a RESOLVED outcome.
export function copyFlashText(p) {
  const src = p && typeof p === "object" ? p : {};
  const what = copyFieldLabel(src.field);
  if (src.approved === false) {
    return "Copy cancelled — nothing was placed on the clipboard.";
  }
  if (!src.ok) {
    return "Copy didn't run — see the app status bar.";
  }
  let text = `Copied the ${what} — ${formatChars(src.chars)} chars` +
    (src.sanitized ? " (rich copy sanitized)" : "") + ".";
  if (typeof src.notice === "string" && src.notice) {
    text += " Warning: " + src.notice + ".";
  }
  return text;
}

// Client-side mirror of the controller's revision filter (demo mode has no
// Python to re-query; live mode re-requests but this keeps the UI instant).
export function filterRevisions(revisions, filter) {
  const rows = asList(revisions);
  if (filter === "open") return rows.filter((r) => r.status === "pending" || r.status === "ready");
  if (filter === "copied") return rows.filter((r) => r.status === "copied" || r.status === "pushed");
  return rows;
}

// Pick a Revision Center filter under which a revision with this status is
// actually visible, preferring the current one. Jump links inside the
// editors can target copied/pushed rows that the default "open" filter
// (and the controller's matching _push_revisions) would omit entirely.
export function revisionFilterFor(status, current) {
  if (filterRevisions([{ status }], current).length > 0) return current;
  if (status === "copied" || status === "pushed") return "copied";
  if (status === "pending" || status === "ready") return "open";
  return "all";
}

// ── List filtering (demo-only text echo; served lists are authoritative) ──
// With a bridge, js_search's FTS covers body_text/actions_text that the row
// viewmodels don't even carry, so re-filtering the served list here would
// silently drop body-only hits. `served: true` (payload carried the echoed
// 'query' key) therefore skips the text predicate; the text filter runs
// only in demo mode, where there is no Python to query.

function matches(hay, needle) {
  return String(hay || "").toLowerCase().includes(needle);
}

export function filterArticles(articles, { query = "", sectionId = null, served = false } = {}) {
  const q = String(query || "").trim().toLowerCase();
  return asList(articles).filter((a) => {
    if (sectionId != null && a.section_id !== sectionId) return false;
    if (served || !q) return true;
    return matches(a.title, q) || matches(a.section, q) ||
      asList(a.labels).some((l) => matches(l, q));
  });
}

export function filterMacros(macros, query = "", served = false) {
  const q = String(query || "").trim().toLowerCase();
  return asList(macros).filter(
    (m) => served || !q || matches(m.name, q) || matches(m.description, q));
}

// ── Macros ────────────────────────────────────────────────────────────

// Zendesk nests macro categories via "::" in the name
// ("Assign to::Billing::Claim status" → category path + leaf).
export function macroPath(name) {
  const parts = String(name || "").split("::").map((s) => s.trim()).filter(Boolean);
  if (parts.length <= 1) return { path: [], leaf: parts[0] || "" };
  return { path: parts.slice(0, -1), leaf: parts[parts.length - 1] };
}

// Which value control a macro action row renders, per the documented
// action-type table: Comment/description is a rich text editor; the
// enumerated ticket fields are dropdowns; subject/tags are text fields.
const SELECT_FIELDS = new Set([
  "status", "status_category", "priority", "type", "group_id", "assignee_id",
  "brand_id", "ticket_form_id", "comment_mode", "follower", "cc",
]);

export function actionInputKind(field) {
  const f = String(field || "");
  if (f.includes("comment_value")) return "rich";
  if (SELECT_FIELDS.has(f)) return "select";
  return "text";
}

// Article status dot per the Guide list: green = published, hollow = draft.
export function articleDotClass(article) {
  return article && article.draft ? "zd-dot draft" : "zd-dot published";
}

export function formatChars(n) {
  const num = Number(n) || 0;
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

// ── Flash wording (pure builders so the payload→message mapping is testable) ──

// import_resolved payload → flash text. Failed files are surfaced (count +
// the first per-file error string) — import_paths returns ok:true even when
// every file failed, so "Imported 0" alone would read as an empty-but-
// successful import. totals.errors is a COUNT; the strings live per-file.
export function importFlashText(p) {
  const src = p && typeof p === "object" ? p : {};
  if (src.ok === false) return "Import didn't start — see the app status bar.";
  const t = src.totals && typeof src.totals === "object" ? src.totals : {};
  let text;
  if ((t.conflicts || 0) > 0) {
    text = `Imported ${t.imported || 0}, updated ${t.updated || 0} — ` +
      `${t.conflicts} pull-origin rows kept (mirror is authoritative).`;
  } else {
    text = `Imported ${t.imported || 0}, updated ${t.updated || 0}, ` +
      `unchanged ${t.skipped_unchanged || 0}.`;
  }
  const nErr = Number(t.errors) || 0;
  if (nErr > 0) {
    const first = asList(src.files)
      .flatMap((f) => asList(f && f.errors))
      .find((e) => e);
    text += ` ${nErr} file${nErr === 1 ? "" : "s"} failed` +
      (first ? ` — ${String(first)}` : "") + ".";
  }
  return text;
}

// action_resolved with ok:false after the user APPROVED the native confirm
// (row gone, status changed under the modal, store error) → flash text. The
// approved:false cancel path is handled separately by the caller.
export function actionFailureText(p) {
  const src = p && typeof p === "object" ? p : {};
  const what = src.action === "purge_mirror" ? "Purge"
    : src.action === "delete_revision" ? "Delete" : "Action";
  if (src.error === "status_changed") {
    return what + " didn't apply — the revision changed while the dialog was open.";
  }
  return what + " didn't apply — see the app status bar.";
}

// ── Keyboard activation for clickable non-button surfaces ─────────────
// Revision cards and table rows are divs/trs with onClick; this gives them
// Enter/Space activation (with role="button" + tabIndex) without changing
// the visuals.
export function keyActivate(fn) {
  return (ev) => {
    if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
      ev.preventDefault();
      fn(ev);
    }
  };
}
