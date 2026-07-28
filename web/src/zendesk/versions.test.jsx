// WS-V3a locks: the version-history panels (VersionsPanel / HistoryPanel)
// and their local viewmodel helpers (versionShape.js — parallel-safe twin
// of shape.js while the editing lane owns that file).
//  - fixtures carry EXACTLY the controller shapes from the zendesk-versions
//    plan section 4 (versions_data / version_detail / version_diff /
//    draft_history) so the SPA and Python can never drift silently;
//  - every DB-sourced string renders as escaped React children;
//  - version bodies render ONLY through the sandboxed ArticleBody iframe;
//  - Restore never renders for Current; rollback is gated to pending
//    article drafts and never offered on the newest save.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import VersionsPanel from "./VersionsPanel.jsx";
import HistoryPanel from "./HistoryPanel.jsx";
import {
  asList, authorInfo, canRollbackDraft, formatChars, normalizeHistory,
  normalizeVersions, saveKindInfo, versionPairPayload,
} from "./versionShape.js";

const noop = () => {};

// Walk a React element tree WITHOUT rendering (both panels are hook-free
// by design, so they can be invoked as plain functions), collecting
// elements matching `pred` — lets tests assert structure and invoke
// handlers with no DOM environment. (zendesk.test.jsx precedent.)
function collectElements(node, pred, out = []) {
  if (node == null || typeof node !== "object") return out;
  if (Array.isArray(node)) {
    node.forEach((n) => collectElements(n, pred, out));
    return out;
  }
  if (pred(node)) out.push(node);
  if (node.props) collectElements(node.props.children, pred, out);
  return out;
}

// Flatten an element's children into a text string (for button labels).
function textOf(node) {
  const parts = [];
  (function walk(n) {
    if (n == null || typeof n === "boolean") return;
    if (typeof n === "string" || typeof n === "number") {
      parts.push(String(n));
      return;
    }
    if (Array.isArray(n)) {
      n.forEach(walk);
      return;
    }
    if (n.props) walk(n.props.children);
  })(node);
  return parts.join("");
}

function buttonsLabeled(tree, label) {
  return collectElements(tree,
    (n) => n.type === "button" && textOf(n).includes(label));
}

function fakeSubmit(elements) {
  return { preventDefault: noop, currentTarget: { elements } };
}

// ── Fixtures: exactly the plan section 4 shapes ───────────────────────

const VERSIONS = {
  article_id: 101,
  current: {
    title: "Setting up SSO (SAML)", updated_display: "Jul 24, 2026",
    origin: "pull", chars: 5120,
  },
  versions: [
    { version_id: 9, title: "Setting up SSO", captured_display: "Jul 20, 2026",
      origin: "pull", replaced_by_origin: "import", chars: 4980 },
    { version_id: 4, title: "Setting up SSO (draft)",
      captured_display: "Jul 10, 2026", origin: "pull",
      replaced_by_origin: "pull", chars: 4200 },
  ],
};

const DETAIL = {
  version_id: 9, article_id: 101, title: "Setting up SSO",
  captured_display: "Jul 20, 2026", origin: "pull",
  body_srcdoc: "<h2>SSO steps</h2>",
};

const VERSION_DIFF = {
  request_id: "v-3", scope: "article-version", article_id: 101,
  a: { version_id: 9, label: "Jul 20, 2026" },
  b: { version_id: null, label: "Current" },
  title: { changed: true, old: "Setting up SSO", new: "Setting up SSO (SAML)" },
  change_count: 4,
  rows: [
    { tag: "change", spans: [
      { tag: "equal", text: "Open the " },
      { tag: "del", text: "SSO" },
      { tag: "add", text: "SAML" },
      { tag: "equal", text: " page." },
    ] },
  ],
};

const HISTORY = {
  draft_id: 7, kind: "article", status: "pending",
  saves: [
    { version_id: 24, seq: 3, author: "specialist", save_kind: "rollback",
      rollback_of_seq: 1, created_display: "Jul 24, 2026",
      title: "Setting up SSO (rev)", chars: 512 },
    { version_id: 21, seq: 2, author: "renn", save_kind: "save",
      rollback_of_seq: null, created_display: "Jul 23, 2026",
      title: "Setting up SSO (rev)", chars: 640 },
    { version_id: 18, seq: 1, author: "user", save_kind: "create",
      rollback_of_seq: null, created_display: "Jul 22, 2026",
      title: "Setting up SSO", chars: 500 },
  ],
};

const SAVE_DIFF = {
  request_id: "v-4", scope: "draft-save", draft_id: 7,
  a: { version_id: 18, label: "Save 1" },
  b: { version_id: 24, label: "Save 3" },
  title: { changed: false, old: "", new: "" },
  change_count: 1,
  rows: [{ tag: "add", text: "New paragraph." }],
};

// ── versionShape helpers ──────────────────────────────────────────────

describe("normalizeVersions", () => {
  it("fills every versions_data key with safe defaults", () => {
    const d = normalizeVersions(null);
    expect(d.article_id).toBe(null);
    expect(d.current).toEqual(
      { title: "", updated_display: "", origin: "pull", chars: 0 });
    expect(d.versions).toEqual([]);
  });

  it("passes real payload fields through", () => {
    const d = normalizeVersions(VERSIONS);
    expect(d.article_id).toBe(101);
    expect(d.current.title).toBe("Setting up SSO (SAML)");
    expect(d.current.chars).toBe(5120);
    expect(d.versions).toHaveLength(2);
    expect(d.versions[0].replaced_by_origin).toBe("import");
  });

  it("tolerates junk without throwing", () => {
    expect(normalizeVersions({ versions: "nope", current: 7 }).versions)
      .toEqual([]);
    expect(normalizeVersions({ current: 7 }).current.origin).toBe("pull");
    expect(asList("nope")).toEqual([]);
  });
});

describe("normalizeHistory", () => {
  it("fills every draft_history key with safe defaults, failing CLOSED", () => {
    const d = normalizeHistory(null);
    expect(d.draft_id).toBe(null);
    expect(d.kind).toBe("");
    expect(d.status).toBe("");
    expect(d.saves).toEqual([]);
    // junk history must never enable rollback
    expect(canRollbackDraft(d)).toBe(false);
  });

  it("passes real payload fields through", () => {
    const d = normalizeHistory(HISTORY);
    expect(d.draft_id).toBe(7);
    expect(d.kind).toBe("article");
    expect(d.status).toBe("pending");
    expect(d.saves).toHaveLength(3);
  });

  it("tolerates junk without throwing", () => {
    expect(normalizeHistory({ saves: "nope", kind: 9 }).saves).toEqual([]);
    expect(normalizeHistory({ kind: 9 }).kind).toBe("");
  });
});

describe("canRollbackDraft (mirror of the Python rollback gate)", () => {
  it("allows only pending ARTICLE drafts", () => {
    expect(canRollbackDraft({ kind: "article", status: "pending" })).toBe(true);
  });

  it("refuses ready/copied/pushed, macros, and junk", () => {
    for (const status of ["ready", "copied", "pushed", "", "weird"]) {
      expect(canRollbackDraft({ kind: "article", status })).toBe(false);
    }
    expect(canRollbackDraft({ kind: "macro", status: "pending" })).toBe(false);
    expect(canRollbackDraft(null)).toBe(false);
    expect(canRollbackDraft(undefined)).toBe(false);
  });
});

describe("authorInfo / saveKindInfo", () => {
  it("maps the three author values", () => {
    expect(authorInfo("renn").label).toBe("Renn");
    expect(authorInfo("specialist").label).toBe("Specialist");
    expect(authorInfo("specialist").cls).toBe("softblue");
    expect(authorInfo("user").label).toBe("You");
    expect(authorInfo(undefined).label).toBe("You");
  });

  it("maps the three save kinds", () => {
    expect(saveKindInfo("create").label).toBe("Created");
    expect(saveKindInfo("save").label).toBe("Saved");
    expect(saveKindInfo("rollback").label).toBe("Rollback");
    expect(saveKindInfo("weird").label).toBe("Saved");
  });
});

describe("versionPairPayload", () => {
  it("stringifies ids and passes the literal 'current' token through", () => {
    expect(versionPairPayload(9, "current")).toEqual({ a: "9", b: "current" });
    expect(versionPairPayload("4", 9)).toEqual({ a: "4", b: "9" });
  });

  it("coerces absent/empty sides to 'current'", () => {
    expect(versionPairPayload(null, 4)).toEqual({ a: "current", b: "4" });
    expect(versionPairPayload("", undefined))
      .toEqual({ a: "current", b: "current" });
  });
});

describe("formatChars (local twin of shape.js while that file is in flight)", () => {
  it("groups thousands and defaults junk to 0", () => {
    expect(formatChars(5120)).toBe("5,120");
    expect(formatChars(0)).toBe("0");
    expect(formatChars("junk")).toBe("0");
  });
});

// ── VersionsPanel ─────────────────────────────────────────────────────

describe("VersionsPanel timeline", () => {
  it("pins Current on top with origin tag; captured rows carry both origin tags and dates", () => {
    const out = renderToStaticMarkup(
      <VersionsPanel data={VERSIONS} onDiffPair={noop} onViewVersion={noop}
                     onRestore={noop} />);
    expect(out).toContain("Current");
    expect(out).toContain("Setting up SSO (SAML)");
    expect(out).toContain("Updated Jul 24, 2026");
    expect(out).toContain("Captured Jul 20, 2026");
    expect(out).toContain("Captured Jul 10, 2026");
    expect(out).toContain("replaced by import");
    expect(out).toContain("5,120 chars");
    // Current must render before the captured versions
    expect(out.indexOf("Current")).toBeLessThan(out.indexOf("Captured Jul 20"));
  });

  it("tolerates absent props entirely (empty-state message)", () => {
    const out = renderToStaticMarkup(<VersionsPanel />);
    expect(out).toContain("No captured versions yet");
  });

  it("escapes hostile titles — entities, never markup", () => {
    const evil = {
      ...VERSIONS,
      current: { ...VERSIONS.current, title: "<script>alert(1)</script>" },
      versions: [{ version_id: 9, title: '<img src=x onerror="alert(1)">',
                   captured_display: "Jul 20, 2026", origin: "pull",
                   replaced_by_origin: "import", chars: 10 }],
    };
    const out = renderToStaticMarkup(
      <VersionsPanel data={evil} onDiffPair={noop} onViewVersion={noop}
                     onRestore={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;img");
    expect(out).not.toContain("<img");
  });

  it("offers Restore per captured version, NEVER for Current, wired to onRestore", () => {
    const restored = [];
    const tree = VersionsPanel({
      data: VERSIONS, onDiffPair: noop, onViewVersion: noop,
      onRestore: (id) => restored.push(id),
    });
    const buttons = buttonsLabeled(tree, "Restore as revision");
    expect(buttons).toHaveLength(2);   // one per captured version, none for Current
    buttons.forEach((b) => b.props.onClick());
    expect(restored).toEqual(["9", "4"]);
  });

  it("radio pair submit calls onDiffPair with digit/'current' string sides", () => {
    const calls = [];
    const tree = VersionsPanel({
      data: VERSIONS, onDiffPair: (a, b) => calls.push([a, b]),
      onViewVersion: noop, onRestore: noop,
    });
    const form = collectElements(tree, (n) => n.type === "form")[0];
    expect(form).toBeTruthy();
    form.props.onSubmit(fakeSubmit({
      zdVerA: { value: "9" }, zdVerB: { value: "current" } }));
    expect(calls).toEqual([["9", "current"]]);
    // same side twice and unpicked sides are no-ops
    form.props.onSubmit(fakeSubmit({
      zdVerA: { value: "9" }, zdVerB: { value: "9" } }));
    form.props.onSubmit(fakeSubmit({
      zdVerA: { value: "" }, zdVerB: { value: "current" } }));
    expect(calls).toHaveLength(1);
  });

  it("View wires onViewVersion; when the detail is open, Hide wires onCloseView", () => {
    const viewed = [];
    let closed = 0;
    const tree = VersionsPanel({
      data: VERSIONS, detail: DETAIL, onDiffPair: noop,
      onViewVersion: (id) => viewed.push(id),
      onCloseView: () => { closed += 1; }, onRestore: noop,
    });
    const views = buttonsLabeled(tree, "View body");
    const hides = buttonsLabeled(tree, "Hide body");
    expect(hides).toHaveLength(1);     // version 9 is being viewed
    expect(views).toHaveLength(1);     // version 4 is not
    views[0].props.onClick();
    expect(viewed).toEqual(["4"]);
    hides[0].props.onClick();
    expect(closed).toBe(1);
  });

  it("renders the version body ONLY through a fully sandboxed srcdoc iframe", () => {
    const out = renderToStaticMarkup(
      <VersionsPanel data={VERSIONS} detail={DETAIL} onDiffPair={noop}
                     onViewVersion={noop} onRestore={noop} />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
    expect(out).not.toContain("allow-same-origin");
    expect(out).toContain("SSO steps");
  });

  it("renders the pair diff inline with side labels (no baseline banner)", () => {
    const out = renderToStaticMarkup(
      <VersionsPanel data={VERSIONS} diff={VERSION_DIFF} onDiffPair={noop}
                     onViewVersion={noop} onRestore={noop} />);
    expect(out).toContain("Comparing Jul 20, 2026 with Current");
    expect(out).toContain("4 changed lines");
    expect(out).not.toContain("no mirrored baseline");
    // hostile-safe diff spans are RevisionDiff's contract; here just the wiring
    expect(out).toContain("SAML");
  });
});

// ── HistoryPanel ──────────────────────────────────────────────────────

describe("HistoryPanel save list", () => {
  it("renders seq, author and save_kind tags, rollback note, dates and chars", () => {
    const out = renderToStaticMarkup(
      <HistoryPanel history={HISTORY} onSaveDiff={noop} onRollback={noop} />);
    expect(out).toContain("Save 3");
    expect(out).toContain("Save 1");
    expect(out).toContain("Specialist");
    expect(out).toContain("Renn");
    expect(out).toContain("You");
    expect(out).toContain("Rollback");
    expect(out).toContain("Created");
    expect(out).toContain("Rolled back to save 1");
    expect(out).toContain("Saved Jul 24, 2026");
    expect(out).toContain("640 chars");
    expect(out).toContain("Latest");
  });

  it("tolerates absent props entirely", () => {
    const out = renderToStaticMarkup(<HistoryPanel />);
    expect(out).toContain("No recorded saves yet");
  });

  it("escapes hostile titles", () => {
    const evil = {
      ...HISTORY,
      saves: [{ ...HISTORY.saves[0], title: "<script>alert(1)</script>" }],
    };
    const out = renderToStaticMarkup(
      <HistoryPanel history={evil} onSaveDiff={noop} onRollback={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });

  it("offers rollback on NON-newest saves only (pending article), wired to onRollback", () => {
    const rolled = [];
    const tree = HistoryPanel({
      history: HISTORY, onSaveDiff: noop,
      onRollback: (id) => rolled.push(id),
    });
    const buttons = buttonsLabeled(tree, "Roll back to this save");
    expect(buttons).toHaveLength(2);   // saves 2 and 1, never the newest
    buttons.forEach((b) => b.props.onClick());
    expect(rolled).toEqual(["21", "18"]);
  });

  it("hides rollback entirely when the gate fails (copied status, macro kind)", () => {
    for (const hist of [
      { ...HISTORY, status: "copied" },
      { ...HISTORY, status: "ready" },
      { ...HISTORY, kind: "macro" },
    ]) {
      const tree = HistoryPanel({ history: hist, onSaveDiff: noop, onRollback: noop });
      expect(buttonsLabeled(tree, "Roll back to this save")).toHaveLength(0);
    }
    const out = renderToStaticMarkup(
      <HistoryPanel history={{ ...HISTORY, status: "copied" }}
                    onSaveDiff={noop} onRollback={noop} />);
    expect(out).toContain("History is read-only");
  });

  it("radio pair submit calls onSaveDiff with save version_id strings", () => {
    const calls = [];
    const tree = HistoryPanel({
      history: HISTORY, onSaveDiff: (a, b) => calls.push([a, b]),
      onRollback: noop,
    });
    const form = collectElements(tree, (n) => n.type === "form")[0];
    expect(form).toBeTruthy();
    form.props.onSubmit(fakeSubmit({
      zdSaveA: { value: "18" }, zdSaveB: { value: "24" } }));
    expect(calls).toEqual([["18", "24"]]);
    form.props.onSubmit(fakeSubmit({
      zdSaveA: { value: "24" }, zdSaveB: { value: "24" } }));
    expect(calls).toHaveLength(1);
  });

  it("renders the save diff inline with side labels (no baseline banner)", () => {
    const out = renderToStaticMarkup(
      <HistoryPanel history={HISTORY} diff={SAVE_DIFF} onSaveDiff={noop}
                    onRollback={noop} />);
    expect(out).toContain("Comparing Save 1 with Save 3");
    expect(out).toContain("1 changed line");
    expect(out).not.toContain("no mirrored baseline");
    expect(out).toContain("New paragraph.");
  });

  it("hides the compare control when fewer than two saves exist", () => {
    const one = { ...HISTORY, saves: [HISTORY.saves[0]] };
    const tree = HistoryPanel({ history: one, onSaveDiff: noop, onRollback: noop });
    expect(buttonsLabeled(tree, "Compare A to B")).toHaveLength(0);
  });
});
