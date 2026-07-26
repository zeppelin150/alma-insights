// The Zendesk clone's safety-critical renderers + the demo/viewmodel key
// parity, locked as regression tests:
//  - ArticleBody must ALWAYS be a fully sandboxed srcdoc iframe (the ONLY
//    HTML renderer on the route);
//  - every DB-sourced string (titles, labels, rationale, diff spans, macro
//    values) renders as escaped React children, never live markup;
//  - the ?demo fixture carries EXACTLY the controller viewmodel keys (plan
//    section 3.4) so the SPA and Python can never drift apart silently.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ArticleBody from "./ArticleBody.jsx";
import ArticleEditor from "./ArticleEditor.jsx";
import ArticleList from "./ArticleList.jsx";
import GardenChrome from "./GardenChrome.jsx";
import MacroEditor from "./MacroEditor.jsx";
import MacroList from "./MacroList.jsx";
import RevisionCenter, { RevisionDetail } from "./RevisionCenter.jsx";
import RevisionDiff from "./RevisionDiff.jsx";
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

  it("article_detail carries exactly the contract keys", () => {
    const expected = ["author", "body_srcdoc", "category", "draft", "html_url",
      "id", "labels", "origin", "outdated", "position", "revisions", "section",
      "section_id", "source_file", "title", "updated_display"];
    Object.values(fx.article_details).forEach((d) => expect(keysOf(d)).toEqual(expected));
    const withRevs = fx.article_details[101];
    expect(keysOf(withRevs.revisions[0]))
      .toEqual(["draft_id", "status", "title", "updated_display"]);
  });

  it("macro_detail carries exactly the contract keys", () => {
    const expected = ["actions", "active", "description", "id", "name",
      "revisions", "updated_display"];
    Object.values(fx.macro_details).forEach((d) => expect(keysOf(d)).toEqual(expected));
    expect(keysOf(fx.macro_details[201].actions[0])).toEqual(["display", "field", "value"]);
    expect(keysOf(fx.macro_details[201].revisions[0])).toEqual(["draft_id", "name", "status"]);
  });

  it("revisions_data rows carry exactly the contract keys", () => {
    expect(keysOf(fx.revisions_data)).toEqual(["filter", "revisions"]);
    const expected = ["copied_display", "created_display", "draft_id", "is_new",
      "kind", "rationale", "sources", "status", "target_id", "target_title", "title"];
    fx.revisions_data.revisions.forEach((r) => expect(keysOf(r)).toEqual(expected));
    expect(keysOf(fx.revisions_data.revisions[0].sources[0])).toEqual(["label", "ref"]);
  });

  it("diff fixtures carry exactly the diff_ready contract keys", () => {
    const expected = ["baseline_present", "change_count", "draft_id", "kind",
      "request_id", "rows", "title"];
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
    // fixture: 2 pending, 1 ready, 1 copied
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
    // the three copy buttons render disabled with the explain-why tooltip
    expect(out).toContain("Mark the draft ready first");
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
