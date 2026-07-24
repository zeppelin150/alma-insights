// Pure-logic contracts for the Zendesk clone's viewmodel guards.
import { describe, expect, it } from "vitest";
import {
  actionFailureText, actionInputKind, articleDotClass, canCopyDraft,
  canMarkCopied, canMarkReady, canSaveDraft, filterArticles, filterMacros,
  filterRevisions, formatChars, groupRevisions, importFlashText, isView,
  keyActivate, macroPath, normalizeData, revisionFilterFor, statusInfo,
} from "./shape.js";

describe("normalizeData", () => {
  it("fills every zendesk_data key with safe defaults", () => {
    const d = normalizeData(null);
    expect(d.view).toBe("articles");
    expect(d.connected).toBe(false);
    expect(d.counts).toEqual({ articles: 0, macros: 0, revisions_open: 0 });
    expect(d.articles).toEqual([]);
    expect(d.macros).toEqual([]);
    expect(d.categories).toEqual([]);
    expect(d.last_pull_display).toBe("Never");
    expect(d.status).toBe("");
  });

  it("passes real payload fields through", () => {
    const d = normalizeData({ view: "macros", connected: true,
      counts: { articles: 3, macros: 1, revisions_open: 2 },
      articles: [{ id: 1 }], last_pull_display: "Jul 1" });
    expect(d.view).toBe("macros");
    expect(d.connected).toBe(true);
    expect(d.counts.revisions_open).toBe(2);
    expect(d.articles).toHaveLength(1);
    expect(d.last_pull_display).toBe("Jul 1");
  });

  it("tolerates junk without throwing", () => {
    expect(normalizeData({ articles: "nope", counts: 7 }).articles).toEqual([]);
    expect(isView("articles")).toBe(true);
    expect(isView("evil")).toBe(false);
  });

  it("carries the echoed search query (null when absent or junk)", () => {
    expect(normalizeData(null).query).toBe(null);
    expect(normalizeData({}).query).toBe(null);
    expect(normalizeData({ query: 7 }).query).toBe(null);
    expect(normalizeData({ query: "metadata XML" }).query).toBe("metadata XML");
    expect(normalizeData({ query: "" }).query).toBe("");
  });
});

describe("draft lifecycle guards (mirror of the Python gates)", () => {
  it("pending: ready-able, copy-able to status only, never clipboard", () => {
    expect(canMarkReady("pending")).toBe(true);
    expect(canMarkCopied("pending")).toBe(true);
    expect(canSaveDraft("pending")).toBe(true);
    expect(canCopyDraft("pending")).toBe(false);
  });

  it("ready: clipboard unlocked, no re-ready", () => {
    expect(canMarkReady("ready")).toBe(false);
    expect(canMarkCopied("ready")).toBe(true);
    expect(canCopyDraft("ready")).toBe(true);
  });

  it("copied/pushed: immutable, copy-only", () => {
    for (const s of ["copied", "pushed"]) {
      expect(canMarkReady(s)).toBe(false);
      expect(canMarkCopied(s)).toBe(false);
      expect(canSaveDraft(s)).toBe(false);
    }
    expect(canCopyDraft("copied")).toBe(true);
  });

  it("statusInfo maps every state to a label", () => {
    expect(statusInfo("pending").label).toBe("Pending review");
    expect(statusInfo("ready").cls).toBe("blue");
    expect(statusInfo("copied").cls).toBe("green");
    expect(statusInfo("weird").cls).toBe("grey");
  });
});

describe("revision lanes + filter", () => {
  const revs = [
    { draft_id: 1, kind: "article", status: "pending" },
    { draft_id: 2, kind: "article", status: "ready" },
    { draft_id: 3, kind: "macro", status: "copied" },
    { draft_id: 4, kind: "macro", status: "pushed" },
  ];

  it("always renders the pending/ready/copied lanes in order", () => {
    const lanes = groupRevisions([]);
    expect(lanes.map((l) => l.key)).toEqual(["pending", "ready", "copied"]);
  });

  it("pushed lane appears only when populated", () => {
    const lanes = groupRevisions(revs);
    expect(lanes.map((l) => l.key)).toEqual(["pending", "ready", "copied", "pushed"]);
    expect(lanes[0].items).toHaveLength(1);
  });

  it("filters mirror the controller: open / copied / all", () => {
    expect(filterRevisions(revs, "open").map((r) => r.draft_id)).toEqual([1, 2]);
    expect(filterRevisions(revs, "copied").map((r) => r.draft_id)).toEqual([3, 4]);
    expect(filterRevisions(revs, "all")).toHaveLength(4);
  });

  it("revisionFilterFor picks a filter the target is visible under", () => {
    // jump target copied/pushed while the default "open" filter is active
    expect(revisionFilterFor("copied", "open")).toBe("copied");
    expect(revisionFilterFor("pushed", "open")).toBe("copied");
    // jump target open while the "copied" filter is active
    expect(revisionFilterFor("pending", "copied")).toBe("open");
    expect(revisionFilterFor("ready", "copied")).toBe("open");
    // the current filter is kept whenever it already shows the target
    expect(revisionFilterFor("copied", "all")).toBe("all");
    expect(revisionFilterFor("copied", "copied")).toBe("copied");
    expect(revisionFilterFor("pending", "open")).toBe("open");
    // unknown/missing status falls back to "all" (always visible)
    expect(revisionFilterFor(undefined, "open")).toBe("all");
    expect(revisionFilterFor("weird", "copied")).toBe("all");
  });
});

describe("list filtering", () => {
  const articles = [
    { id: 1, title: "Setting up SSO", section_id: 9, section: "FAQ", labels: ["sso"] },
    { id: 2, title: "Claims intro", section_id: 11, section: "Claims", labels: [] },
  ];

  it("filters by section and by query across title/labels", () => {
    expect(filterArticles(articles, { sectionId: 9 })).toHaveLength(1);
    expect(filterArticles(articles, { query: "sso" })).toHaveLength(1);
    expect(filterArticles(articles, { query: "claims" })[0].id).toBe(2);
    expect(filterArticles(articles, {})).toHaveLength(2);
  });

  it("filters macros by name/description", () => {
    const macros = [{ id: 1, name: "Refund apology", description: "" },
                    { id: 2, name: "Assign to::Billing", description: "route" }];
    expect(filterMacros(macros, "refund")).toHaveLength(1);
    expect(filterMacros(macros, "route")).toHaveLength(1);
    expect(filterMacros(macros, "")).toHaveLength(2);
  });

  it("served lists skip the text predicate (Python's FTS covers body text " +
     "the rows don't carry) but keep the section filter", () => {
    // Body-only FTS hit: the query matches nothing visible in the row.
    const hit = { id: 1, title: "Setting up SSO", section_id: 9, section: "FAQ",
                  labels: [] };
    expect(filterArticles([hit], { query: "metadata XML" })).toHaveLength(0);
    expect(filterArticles([hit], { query: "metadata XML", served: true }))
      .toHaveLength(1);
    // section filter still applies to a served list
    expect(filterArticles([hit], { query: "metadata XML", served: true,
                                   sectionId: 11 })).toHaveLength(0);
    const macro = { id: 1, name: "Refund apology", description: "" };
    expect(filterMacros([macro], "escalate to billing")).toHaveLength(0);
    expect(filterMacros([macro], "escalate to billing", true)).toHaveLength(1);
  });
});

describe("macro helpers", () => {
  it("splits :: category nesting", () => {
    expect(macroPath("Assign to::Billing::Claim status"))
      .toEqual({ path: ["Assign to", "Billing"], leaf: "Claim status" });
    expect(macroPath("Refund apology")).toEqual({ path: [], leaf: "Refund apology" });
  });

  it("maps action fields to the documented value-input kinds", () => {
    expect(actionInputKind("comment_value")).toBe("rich");
    expect(actionInputKind("comment_value_html")).toBe("rich");
    expect(actionInputKind("status")).toBe("select");
    expect(actionInputKind("priority")).toBe("select");
    expect(actionInputKind("group_id")).toBe("select");
    expect(actionInputKind("comment_mode")).toBe("select");
    expect(actionInputKind("subject")).toBe("text");
    expect(actionInputKind("add_tags")).toBe("text");
    expect(actionInputKind("custom_field_505156")).toBe("text");
  });
});

describe("misc formatters", () => {
  it("status dot: green published, hollow draft", () => {
    expect(articleDotClass({ draft: false })).toBe("zd-dot published");
    expect(articleDotClass({ draft: true })).toBe("zd-dot draft");
  });

  it("formatChars groups thousands", () => {
    expect(formatChars(5120)).toBe("5,120");
    expect(formatChars(0)).toBe("0");
  });
});

describe("importFlashText (import_resolved → flash wording)", () => {
  it("not_started keeps the didn't-start wording", () => {
    expect(importFlashText({ ok: false, error: "not_started" }))
      .toBe("Import didn't start — see the app status bar.");
  });

  it("clean import keeps the counts wording, no error clause", () => {
    const p = { ok: true, files: [{ file: "a.json", errors: [] }],
      totals: { files: 1, imported: 2, updated: 1, skipped_unchanged: 3,
                conflicts: 0, errors: 0 } };
    expect(importFlashText(p)).toBe("Imported 2, updated 1, unchanged 3.");
  });

  it("failed files are surfaced: count + FIRST per-file error string", () => {
    const p = { ok: true,
      files: [{ file: "a.json", errors: [] },
              { file: "b.json", errors: ["not valid JSON: line 1"] },
              { file: "c.json", errors: ["unrecognized JSON shape"] }],
      totals: { files: 3, imported: 0, updated: 0, skipped_unchanged: 0,
                conflicts: 0, errors: 2 } };
    expect(importFlashText(p)).toBe(
      "Imported 0, updated 0, unchanged 0. 2 files failed — not valid JSON: line 1.");
  });

  it("singular wording for one failed file, and errors ride the conflict text too", () => {
    const one = { ok: true,
      files: [{ file: "a.docx", errors: ["file too large (>50MB)"] }],
      totals: { files: 1, imported: 0, updated: 0, skipped_unchanged: 0,
                conflicts: 0, errors: 1 } };
    expect(importFlashText(one)).toBe(
      "Imported 0, updated 0, unchanged 0. 1 file failed — file too large (>50MB).");
    const both = { ok: true,
      files: [{ file: "a.json", errors: ["not valid JSON: x"] }],
      totals: { files: 2, imported: 1, updated: 0, skipped_unchanged: 0,
                conflicts: 2, errors: 1 } };
    expect(importFlashText(both)).toBe(
      "Imported 1, updated 0 — 2 pull-origin rows kept (mirror is authoritative). " +
      "1 file failed — not valid JSON: x.");
  });

  it("tolerates a count with no strings and junk payloads", () => {
    expect(importFlashText({ ok: true, totals: { errors: 3 } })).toBe(
      "Imported 0, updated 0, unchanged 0. 3 files failed.");
    expect(importFlashText(null)).toBe("Imported 0, updated 0, unchanged 0.");
  });
});

describe("actionFailureText (approved-but-failed destructive actions)", () => {
  it("status_changed explains the re-verify refusal", () => {
    expect(actionFailureText({ action: "delete_revision", ok: false,
                               approved: true, error: "status_changed" }))
      .toBe("Delete didn't apply — the revision changed while the dialog was open.");
  });

  it("names the action and points at the status bar otherwise", () => {
    expect(actionFailureText({ action: "purge_mirror", ok: false,
                               approved: true, error: "store_error" }))
      .toBe("Purge didn't apply — see the app status bar.");
    expect(actionFailureText({ action: "delete_revision", ok: false,
                               approved: true, error: "draft_not_found" }))
      .toBe("Delete didn't apply — see the app status bar.");
    expect(actionFailureText({})).toBe("Action didn't apply — see the app status bar.");
  });
});

describe("keyActivate (keyboard access for clickable rows/cards)", () => {
  function fakeEvent(key) {
    const ev = { key, defaultPrevented: false,
                 preventDefault() { this.defaultPrevented = true; } };
    return ev;
  }

  it("Enter and Space activate and prevent default scrolling", () => {
    for (const key of ["Enter", " ", "Spacebar"]) {
      let fired = 0;
      const ev = fakeEvent(key);
      keyActivate(() => { fired += 1; })(ev);
      expect(fired).toBe(1);
      expect(ev.defaultPrevented).toBe(true);
    }
  });

  it("other keys pass through untouched", () => {
    let fired = 0;
    const ev = fakeEvent("Tab");
    keyActivate(() => { fired += 1; })(ev);
    expect(fired).toBe(0);
    expect(ev.defaultPrevented).toBe(false);
  });
});
