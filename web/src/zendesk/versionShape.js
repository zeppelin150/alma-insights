// Pure viewmodel guards + formatters for the Zendesk version-history
// panels (zendesk-versions plan, section 4 shapes: versions_data /
// version_detail / version_diff / draft_history). Deliberately a SEPARATE
// module from shape.js while the editing lane owns that file — WS-V3b
// decides whether to fold these helpers into shape.js once both lanes
// land. No DOM, no window; unit-tested in versions.test.jsx. The
// formatChars duplicate below is intentional for the same reason.

export function asList(v) {
  return Array.isArray(v) ? v : [];
}

// Full versions_data viewmodel with safe defaults, so a partial or absent
// payload can never crash the renderer. `current` mirrors the live mirror
// row (never restorable); `versions` are the captured superseded states,
// newest first, list rows carrying chars only (bodies travel exclusively
// as sanitized srcdocs or Python-computed diff rows).
export function normalizeVersions(p) {
  const src = p && typeof p === "object" ? p : {};
  const cur = src.current && typeof src.current === "object" ? src.current : {};
  return {
    article_id: src.article_id != null ? src.article_id : null,
    current: {
      title: typeof cur.title === "string" ? cur.title : "",
      updated_display: cur.updated_display || "",
      origin: cur.origin || "pull",
      chars: Number(cur.chars) || 0,
    },
    versions: asList(src.versions),
  };
}

// Full draft_history viewmodel with safe defaults. kind/status default to
// "" (NOT 'article'/'pending') so a junk payload fails CLOSED — the
// rollback affordance never renders off a malformed history.
export function normalizeHistory(p) {
  const src = p && typeof p === "object" ? p : {};
  return {
    draft_id: src.draft_id != null ? src.draft_id : null,
    kind: typeof src.kind === "string" ? src.kind : "",
    status: typeof src.status === "string" ? src.status : "",
    saves: asList(src.saves),
  };
}

// Mirror of the Python rollback gate (rollback_draft_to_version): only
// open pending ARTICLE drafts can be rolled back — ready/copied/pushed
// are immutable INCLUDING history rollback, macros are out of scope v1.
// The UI mirrors the gate so buttons don't invite refused clicks.
export function canRollbackDraft(rev) {
  return !!rev && rev.kind === "article" && rev.status === "pending";
}

// Per-save author tag from zendesk_draft_versions.author
// ('renn' | 'specialist' | 'user').
export function authorInfo(author) {
  if (author === "renn") return { label: "Renn", cls: "grey" };
  if (author === "specialist") return { label: "Specialist", cls: "softblue" };
  return { label: "You", cls: "softgrey" };
}

// Per-save kind tag ('create' | 'save' | 'rollback').
export function saveKindInfo(kind) {
  if (kind === "create") return { label: "Created", cls: "softgreen" };
  if (kind === "rollback") return { label: "Rollback", cls: "softyellow" };
  return { label: "Saved", cls: "grey" };
}

// js_request_version_diff sides are digit strings OR the literal
// 'current' (= the live mirror row). Absent/empty picks coerce to
// 'current'; everything else stringifies (the bridge slots are all-str).
export function versionPairPayload(a, b) {
  const tok = (v) =>
    v == null || v === "" || v === "current" ? "current" : String(v);
  return { a: tok(a), b: tok(b) };
}

export function formatChars(n) {
  const num = Number(n) || 0;
  return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
