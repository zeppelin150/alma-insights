import { useEffect, useMemo, useRef, useState } from "react";
import { useBridge } from "../lib/bridge.js";
import ChatDrawer from "../chat/ChatDrawer.jsx";
import { makeDemoRenn } from "../chat/demoRenn.js";
import GardenChrome from "./GardenChrome.jsx";
import ArticleList from "./ArticleList.jsx";
import ArticleEditor from "./ArticleEditor.jsx";
import MacroList from "./MacroList.jsx";
import MacroEditor from "./MacroEditor.jsx";
import RevisionCenter from "./RevisionCenter.jsx";
import { MarkupAlert, SourcePanel } from "./RevisionDiff.jsx";
import { buildDemoZendesk, isDemoMode } from "./demo.js";
import {
  actionFailureText, applyDemoBodyEdit, bodyEditFailureText, bodyEditPayload,
  copyFieldLabel, copyFlashText, importFlashText, isView, normalizeData,
  revisionFilterFor,
} from "./shape.js";
import "./garden.css";

// Zendesk route (#/zendesk) — Garden v8 clone over the local mirror. Pure
// renderer: every viewmodel arrives from ZendeskWebController via
// zendeskBridge; every click only ASKS Python (validation, confirms, the
// clipboard and all status transitions live behind the bridge). The mirror
// is read-only toward real Zendesk by construction — nothing here can write.

export default function ZendeskApp() {
  const bridge = useBridge("zendeskBridge");
  const renn = useBridge("almaBridge");   // shared in-page Renn drawer
  const [chatOpen, setChatOpen] = useState(false);
  const [data, setData] = useState(() => normalizeData(null));
  const [view, setView] = useState("articles");
  const [article, setArticle] = useState(null);   // article_detail | null
  const [macro, setMacro] = useState(null);       // macro_detail | null
  const [revs, setRevs] = useState(null);         // revisions_data | null
  const [revFilter, setRevFilter] = useState("open");
  const [activeDraft, setActiveDraft] = useState(null); // {draft_id, kind} | null
  const [bodyEdit, setBodyEdit] = useState(null); // "article" | "draft" | null
  const [diff, setDiff] = useState(null);
  const [section, setSection] = useState(null);   // active section id | null
  const [queries, setQueries] = useState({ articles: "", macros: "" });
  const [pullBusy, setPullBusy] = useState(false);
  // A copy request is in flight. Page-authored bytes now open a NATIVE
  // confirm inside js_copy_field, so the controls stay disabled (no second
  // prompt) and say nothing about the clipboard until Python resolves.
  const [copyBusy, setCopyBusy] = useState(false);
  // The raw-HTML disclosure under the rendered article — CLOSED by default.
  const [sourceOpen, setSourceOpen] = useState(false);
  const [flash, setFlash] = useState(null);
  const flashTimer = useRef(null);
  const pullTimer = useRef(null);
  const copyTimer = useRef(null);
  const revFilterRef = useRef("open");
  useEffect(() => { revFilterRef.current = revFilter; }, [revFilter]);

  function showFlash(text) {
    setFlash(text);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 2800);
  }

  // Success flash for a specialist body edit: the saved text now lives in a
  // pending revision, and the jump link opens it in the Revision Center.
  function showBodyEditFlash(draftId, isDemo) {
    showFlash(
      <span>
        {isDemo ? "Edit saved (demo) — " : "Edit saved as a pending revision — "}
        <button type="button" className="zd-flash-link"
                onClick={() => jumpToRevision("article", draftId, "pending")}>
          View revision
        </button>
      </span>);
  }

  // Pull/import can be silently refused Python-side (cooldown, single-winner
  // claim) with no resolving signal, so the local busy flag self-clears —
  // the controller's claims stay authoritative either way.
  function armPullBusy() {
    setPullBusy(true);
    clearTimeout(pullTimer.current);
    pullTimer.current = setTimeout(() => setPullBusy(false), 10000);
  }
  function clearPullBusy() {
    clearTimeout(pullTimer.current);
    setPullBusy(false);
  }

  // A copy that Python refuses — unreviewed bytes, a pending draft, or a
  // CANCELLED native confirm — returns silently with no copy_resolved, so
  // the in-flight flag must self-clear. It expires quietly: nothing may
  // imply the clipboard was written when Python never said so.
  function armCopyBusy() {
    setCopyBusy(true);
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopyBusy(false), 30000);
  }
  function clearCopyBusy() {
    clearTimeout(copyTimer.current);
    setCopyBusy(false);
  }

  // Opening the disclosure moves focus to it, so a keyboard user lands on
  // the source instead of hunting for it after the panel expands.
  //
  // revealSource only ever OPENS — it is what the markup alert fires, and an
  // alert whose button could close the very thing it points at is a trap.
  // toggleSource is the always-available control beside the copy buttons
  // (F5): the operator's route to the exact bytes must not run through the
  // markup notice, which is silent for every content-HIDING vector.
  function revealSource() {
    setSourceOpen(true);
    focusSourceSummary();
  }

  function toggleSource() {
    if (sourceOpen) { setSourceOpen(false); return; }
    revealSource();
  }

  function focusSourceSummary() {
    if (typeof document === "undefined") return;
    setTimeout(() => {
      const el = document.getElementById("zd-source-summary");
      if (!el) return;
      if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
      if (el.focus) el.focus();
    }, 0);
  }

  useEffect(() => {
    window.__almaZendeskMounted = true; // headless hook
  }, []);

  // Explicit demo mode (#/zendesk?demo) — sample data, dev/preview only.
  // Engages ONLY without a bridge so real data always wins in-app; the
  // header badges "SAMPLE DATA".
  const demo = isDemoMode() && !bridge;
  const fx = useMemo(() => (demo ? buildDemoZendesk() : null), [demo]);
  const rennBridge = useMemo(
    () => renn || (demo ? makeDemoRenn() : null), [renn, demo]);
  useEffect(() => {
    if (!fx) return;
    setData(normalizeData(fx.zendesk_data));
    setRevs(fx.revisions_data);
    window.__almaZdArticles = fx.zendesk_data.articles.length; // headless hooks
    window.__almaZdDemo = true;
  }, [fx]);

  useEffect(() => {
    if (!bridge) return;
    bridge.zendeskData.connect((j) => {
      try {
        const p = normalizeData(JSON.parse(j));
        setData(p);
        if (isView(p.view)) setView(p.view);
        window.__almaZdArticles = p.articles.length;  // headless hooks
        window.__almaZdMacros = p.macros.length;
      } catch (e) {}
    });
    bridge.articleDetail.connect((j) => {
      try {
        const p = JSON.parse(j);
        setArticle(p);
        setMacro(null);
        setSourceOpen(false);   // every article opens on the RENDERED body
        setView("articles");
        window.__almaZdArticle = p.title;             // headless hook
      } catch (e) {}
    });
    bridge.macroDetail.connect((j) => {
      try {
        const p = JSON.parse(j);
        setMacro(p);
        setArticle(null);
        setSourceOpen(false);
        setView("macros");
        window.__almaZdMacro = p.name;                // headless hook
      } catch (e) {}
    });
    bridge.revisionsData.connect((j) => {
      try {
        const p = JSON.parse(j);
        setRevs(p);
        window.__almaZdRevisions = (p.revisions || []).length; // headless hook
      } catch (e) {}
    });
    bridge.diffReady.connect((j) => {
      try {
        const p = JSON.parse(j);
        setDiff(p);
        window.__almaZdDiffRows = (p.rows || []).length; // headless hook
      } catch (e) {}
    });
    bridge.pullResolved.connect((j) => {
      clearPullBusy();
      try {
        const p = JSON.parse(j);
        window.__almaZdPull = p;                      // headless hook
        if (p.ok) {
          showFlash(`Pulled ${p.articles || 0} articles and ${p.macros || 0} macros.`);
        } else if (p.error === "zendesk_not_connected") {
          showFlash("Zendesk isn't connected on this machine — import files instead.");
        } else if (p.error === "not_started") {
          showFlash("Pull didn't start — see the app status bar.");
        } else {
          showFlash("Pull failed — see the app status bar.");
        }
        bridge.refresh();
      } catch (e) {}
    });
    bridge.importResolved.connect((j) => {
      clearPullBusy();
      try {
        const p = JSON.parse(j);
        window.__almaZdImport = p;                    // headless hook
        // importFlashText also surfaces per-file failures (totals.errors is
        // a count; the first error string comes from the files reports).
        showFlash(importFlashText(p));
        bridge.refresh();
      } catch (e) {}
    });
    bridge.copyResolved.connect((j) => {
      // The ONLY place a copy outcome is announced — never at click time,
      // because a native confirm may still be open (copyFlashText).
      clearCopyBusy();
      try {
        const p = JSON.parse(j);
        window.__almaZdCopy = p;                      // headless hook
        showFlash(copyFlashText(p));
      } catch (e) {}
    });
    bridge.actionResolved.connect((j) => {
      try {
        const p = JSON.parse(j);
        window.__almaZdAction = p;                    // headless hook
        if (p.action === "body_edit") {
          // The controller already re-pushes revisions_data (last filter)
          // and the open article_detail on success; refresh() only tops up
          // the header counts (revisions_open can grow by one).
          if (p.ok) {
            showBodyEditFlash(p.draft_id, false);
            bridge.refresh();
          } else {
            showFlash(bodyEditFailureText(p));
          }
          return;
        }
        if (p.ok) {
          if (p.action === "delete_revision") {
            setActiveDraft(null);
            setDiff(null);
            showFlash("Revision deleted.");
          } else if (p.action === "purge_mirror") {
            showFlash("Mirror purged.");
          } else {
            showFlash("Done.");
          }
          bridge.requestRevisions(revFilterRef.current);
          bridge.refresh();
        } else if (p.approved === false) {
          showFlash("Cancelled — nothing changed.");
        } else {
          // Approved but failed Python-side (status changed under the
          // modal, row gone, store error) — never leave the user guessing
          // after a "cannot be undone" dialog.
          showFlash(actionFailureText(p));
        }
      } catch (e) {}
    });
    bridge.statusText.connect((t) => {
      window.__almaZdStatus = t;                      // headless hook
    });
    bridge.refresh();
  }, [bridge]);

  useEffect(() => {
    window.__almaZdView = view; // headless hook
  }, [view]);

  // ── navigation ─────────────────────────────────────────────────────
  function nav(v) {
    if (!isView(v)) return;
    setView(v);
    setArticle(null);
    setMacro(null);
    setBodyEdit(null);
    if (bridge) {
      bridge.setView(v);
      if (v === "revisions") bridge.requestRevisions(revFilter);
    }
  }

  function openArticle(id) {
    setBodyEdit(null);
    setSourceOpen(false);
    if (demo) {
      setArticle(fx.article_details[id] || null);
      setMacro(null);
    } else if (bridge) bridge.openArticle(String(id));
  }

  function openMacro(id) {
    setBodyEdit(null);
    setSourceOpen(false);
    if (demo) {
      setMacro(fx.macro_details[id] || null);
      setArticle(null);
    } else if (bridge) bridge.openMacro(String(id));
  }

  function search(kind, q) {
    setQueries((m) => ({ ...m, [kind]: q }));
    if (bridge) bridge.search(q, kind);
  }

  // ── revisions ──────────────────────────────────────────────────────
  function pickRevFilter(f) {
    setRevFilter(f);
    if (bridge) bridge.requestRevisions(f);
  }

  function openRevision(draftId, kind) {
    setActiveDraft({ draft_id: draftId, kind });
    setBodyEdit(null);
  }

  function requestDiff(kind, draftId) {
    if (demo) {
      setDiff(fx.diffs[kind + ":" + draftId] || null);
      return;
    }
    setDiff(null);
    if (bridge) bridge.requestDiff(kind, String(draftId));
  }

  // A revision link inside an editor jumps to the Revision Center with the
  // draft opened and its diff requested. The filter follows the TARGET's
  // status: editors link revisions of every status, but the controller
  // serves only rows matching the requested filter, so keeping the default
  // "open" filter for a copied/pushed target would open an empty center.
  function jumpToRevision(kind, draftId, status) {
    setArticle(null);
    setMacro(null);
    setView("revisions");
    setActiveDraft({ draft_id: draftId, kind });
    setBodyEdit(null);
    const f = revisionFilterFor(status, revFilter);
    if (f !== revFilter) setRevFilter(f);
    if (bridge) {
      bridge.setView("revisions");
      bridge.requestRevisions(f);
    }
    requestDiff(kind, draftId);
  }

  // Demo-only local transition so the fixture stays interactive without a
  // controller; live mode always round-trips through Python.
  function demoTransition(kind, draftId, status) {
    setRevs((r) => ({
      ...r,
      revisions: (r ? r.revisions : []).map((x) =>
        x.kind === kind && x.draft_id === draftId
          ? { ...x, status, copied_display: status === "copied" ? "Just now" : x.copied_display }
          : x),
    }));
  }

  function markReady(kind, draftId) {
    if (demo) { demoTransition(kind, draftId, "ready"); return; }
    if (bridge) bridge.markReady(kind, String(draftId));
  }

  function markCopied(kind, draftId) {
    if (demo) { demoTransition(kind, draftId, "copied"); return; }
    if (bridge) bridge.markCopied(kind, String(draftId));
  }

  function saveDraft(kind, draftId, payload) {
    if (demo) {
      const text = payload.title || payload.name;
      setRevs((r) => ({
        ...r,
        revisions: (r ? r.revisions : []).map((x) =>
          x.kind === kind && x.draft_id === draftId ? { ...x, title: text } : x),
      }));
      return;
    }
    if (bridge) bridge.saveDraft(kind, String(draftId), JSON.stringify(payload));
  }

  // ── specialist body edits (articles only, v1) ──────────────────────
  // Both paths relay a JSON payload carrying ONLY the "body" key —
  // js_save_body_edit ignores any HTML keys, recomputes body_html
  // Python-side, and never modifies mirror article rows.
  function saveArticleBody(text) {
    if (!article) return;
    setBodyEdit(null);
    const payload = bodyEditPayload(text);
    if (demo) { demoBodyEdit("article", article.id, payload.body); return; }
    if (bridge) {
      bridge.saveBodyEdit("article", String(article.id), JSON.stringify(payload));
    }
  }

  function saveDraftBody(draftId, text) {
    setBodyEdit(null);
    const payload = bodyEditPayload(text);
    if (demo) { demoBodyEdit("draft", draftId, payload.body); return; }
    if (bridge) {
      bridge.saveBodyEdit("draft", String(draftId), JSON.stringify(payload));
    }
  }

  // Demo-only local simulation (SAMPLE DATA — nothing persists): mirrors the
  // controller's article-target rule via applyDemoBodyEdit and flashes the
  // same jump link with the (demo) caveat.
  function demoBodyEdit(targetKind, id, body) {
    const res = applyDemoBodyEdit(
      revs ? revs.revisions : [], targetKind, id, body, article);
    setRevs((r) => ({ filter: "all", ...(r || {}), revisions: res.revisions }));
    if (res.draft_id != null) showBodyEditFlash(res.draft_id, true);
  }

  function deleteRevision(kind, draftId) {
    if (demo) {
      setRevs((r) => ({
        ...r,
        revisions: (r ? r.revisions : []).filter(
          (x) => !(x.kind === kind && x.draft_id === draftId)),
      }));
      setActiveDraft(null);
      setDiff(null);
      showFlash("Revision deleted (demo).");
      return;
    }
    if (bridge) bridge.deleteRevision(kind, String(draftId));
  }

  // ── copy (Python re-reads exact DB bytes at click time) ────────────
  // Nothing below reports success. The clipboard write, the reviewed-bytes
  // gate and — for every copy of markup, mirror rows included — a NATIVE
  // confirm displaying the exact bytes all happen
  // Python-side; the outcome arrives on copy_resolved. Until then the
  // controls only go BUSY, which is also what stops a second confirm.
  function demoCopyNotice(field) {
    showFlash("Copying the " + copyFieldLabel(field) +
      " is simulated in the demo — nothing reached the clipboard.");
  }

  function copyDraftField(kind, draftId, field) {
    if (demo) { demoCopyNotice(field); return; }
    if (!bridge || copyBusy) return;
    const target = kind === "macro" ? "macro_draft" : "article_draft";
    armCopyBusy();
    bridge.copyField(target, String(draftId), field);
  }

  function copyArticleField(field) {
    if (!article) return;
    if (demo) { demoCopyNotice(field); return; }
    if (!bridge || copyBusy) return;
    armCopyBusy();
    bridge.copyField("article", String(article.id), field);
  }

  function copyMacroField(field) {
    if (!macro) return;
    if (demo) { demoCopyNotice(field); return; }
    if (!bridge || copyBusy) return;
    armCopyBusy();
    bridge.copyField("macro", String(macro.id), field);
  }

  // ── pull / import ──────────────────────────────────────────────────
  function pull() {
    if (pullBusy) return;
    if (demo) {
      armPullBusy();
      setTimeout(() => { clearPullBusy(); showFlash("Pull is disabled in the demo."); }, 600);
      return;
    }
    if (!bridge) return;
    armPullBusy();
    bridge.requestPull();
  }

  function importFiles() {
    if (pullBusy || demo || !bridge) return;
    armPullBusy();
    bridge.requestImport();
  }

  function importFolder() {
    if (pullBusy || demo || !bridge) return;
    armPullBusy();
    bridge.requestImportFolder();
  }

  // DESTRUCTIVE — but this only ASKS: js_purge_mirror validates the scope
  // and runs the native confirm (QMessageBox with live counts) Python-side,
  // unreachable from this page. Demo has no Python, so no purge.
  function purgeMirror(scope) {
    if (demo) { showFlash("Purge is disabled in the demo."); return; }
    if (bridge) bridge.purgeMirror(scope);
  }

  const connected = bridge || demo;
  const revisions = revs ? revs.revisions : [];

  let body;
  if (!connected) {
    body = (
      <div className="route-empty">
        <p>Waiting for the Zendesk bridge…</p>
        <p className="route-note">This route runs inside the app's Zendesk tab
          (<code>enablement.web_tabs</code>). Append <code>?demo</code> for sample data.</p>
      </div>
    );
  } else if (view === "revisions") {
    body = (
      <RevisionCenter revisions={revisions} filter={revFilter} diff={diff}
                      activeDraft={activeDraft}
                      onFilter={pickRevFilter} onOpen={openRevision}
                      onDiff={requestDiff} onSave={saveDraft}
                      onMarkReady={markReady} onMarkCopied={markCopied}
                      onCopy={copyDraftField} onDelete={deleteRevision}
                      busy={pullBusy} copyBusy={copyBusy}
                      bodyEditing={bodyEdit === "draft"}
                      onEditBody={() => setBodyEdit("draft")}
                      onCancelBodyEdit={() => setBodyEdit(null)}
                      onSaveBody={saveDraftBody} />
    );
  } else if (view === "macros") {
    body = macro
      ? <MacroEditor macro={macro} onBack={() => setMacro(null)}
                     onCopy={copyMacroField} copyBusy={copyBusy}
                     onShowSource={toggleSource} sourceOpen={sourceOpen}
                     onOpenRevision={(id, st) => jumpToRevision("macro", id, st)} />
      : <MacroList macros={data.macros} query={queries.macros}
                   served={data.query != null}
                   onOpen={openMacro} onSearch={(q) => search("macros", q)} />;
    if (macro) {
      // Same rule as articles: the action rows already render every value
      // verbatim, and this collapsed panel repeats them as one canonical
      // source blob so "Copy reply" can never deliver a character the
      // reviewer's view did not contain.
      body = (
        <div className="zd-article-review">
          {body}
          <SourcePanel source={macro.actions_source}
                       label="Macro action source"
                       open={sourceOpen} onToggle={setSourceOpen} />
        </div>
      );
    }
  } else {
    body = article
      // PRIMARY SURFACE = the RENDERED article (the editor's sandboxed
      // frame): it shows the specialist how the content lands for an end
      // user. The exact stored bytes still render as escaped text — what
      // Copy HTML/rich text would deliver — but as a COLLAPSED disclosure
      // beneath it, so reading the article never means scrolling past a
      // wall of markup.
      //
      // TWO independent routes into those bytes (F5). The markup alert
      // fires above the fold and auto-expands the panel when Python says
      // the preview is unfaithful — but that notice cannot be trusted to
      // fire, so it is the ADDITION, not the access path. The access path
      // is the always-visible "View exact source" control next to the copy
      // buttons (onShowSource below) plus the panel's own labelled summary.
      ? (<div className="zd-article-review">
          <MarkupAlert notice={article.markup_notice}
                       onShowSource={revealSource} />
          <ArticleEditor article={article}
                         onShowSource={toggleSource} sourceOpen={sourceOpen}
                         onBack={() => { setArticle(null); setBodyEdit(null); }}
                         onCopy={copyArticleField}
                         onOpenRevision={(id, st) => jumpToRevision("article", id, st)}
                         bodyEditing={bodyEdit === "article"}
                         onEditBody={() => setBodyEdit("article")}
                         onCancelBodyEdit={() => setBodyEdit(null)}
                         onSaveBody={saveArticleBody} busy={pullBusy}
                         copyBusy={copyBusy} />
          {bodyEdit !== "article" && (
            <SourcePanel source={article.body_source}
                         notice={article.markup_notice}
                         open={sourceOpen} onToggle={setSourceOpen} />
          )}
        </div>)
      : <ArticleList categories={data.categories} articles={data.articles}
                     activeSection={section} query={queries.articles}
                     served={data.query != null}
                     onOpen={openArticle} onSearch={(q) => search("articles", q)}
                     onSelectSection={setSection} />;
  }

  return (
    <div className="app route-zendesk zd-app">
      <GardenChrome view={view} counts={data.counts} connected={data.connected}
                    demo={demo} onNav={nav} onPull={pull} onImport={importFiles}
                    onImportFolder={importFolder} onPurge={purgeMirror}
                    pullBusy={pullBusy}
                    lastPull={data.last_pull_display}
                    onOpenChat={rennBridge ? () => setChatOpen(true) : null} />
      {body}
      {flash && <div className="cal-flash">{flash}</div>}
      <ChatDrawer bridge={rennBridge} open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  );
}
