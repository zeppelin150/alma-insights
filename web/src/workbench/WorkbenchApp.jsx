import { useEffect, useMemo, useRef, useState } from "react";
import { useBridge } from "../lib/bridge.js";
import ChatDrawer from "../chat/ChatDrawer.jsx";
import { makeDemoRenn } from "../chat/demoRenn.js";
import { DEMO_CHIPS, DEMO_DIFF, DEMO_DRAFTS, isDemoMode } from "./demo.js";
import { AiEditBar, ToolsMenu } from "./EditTools.jsx";

// Enablement Workbench route (#/workbench) — M3 read views + M4 editing.
// Workspaces, the TRUE-FIDELITY sandboxed Guru preview, word-level diff,
// check badges, a markdown editor with the Qt commit-on-leave semantics, the
// AI-edit ask, and publish/import menus whose actions only ever ASK Python
// (destination allowlists + a NATIVE confirm gate live in the controller).

// The sandboxed document carries its own minimal stylesheet (it shares
// nothing with the app page — that's the point).
const FRAME_CSS = `
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         color: #1E2A22; font-size: 14px; line-height: 1.55; margin: 18px 22px;
         background: #FFFFFF; }
  h1, h2, h3 { color: #0F5132; line-height: 1.25; }
  h2 { font-size: 17px; margin: 18px 0 6px; }
  blockquote { border-left: 3px solid #0F6E56; background: #F2F7F4;
               margin: 10px 0; padding: 8px 12px; border-radius: 0 8px 8px 0; }
  table { border-collapse: collapse; margin: 10px 0; }
  th, td { border: 1px solid #E7E1D6; padding: 5px 10px; font-size: 13px; }
  th { background: #FBF8F2; text-align: left; }
  code, pre { background: #F6F3EC; border-radius: 5px; }
  pre { padding: 8px 10px; overflow-x: auto; }
  code { padding: 1px 5px; }
  img { max-width: 100%; }
  a { color: #0F6E56; }
`;

export function PreviewFrame({ html }) {
  const doc = `<!doctype html><html><head><meta charset="utf-8">` +
    `<style>${FRAME_CSS}</style></head><body>${html || ""}</body></html>`;
  // sandbox="" = fully sandboxed: no scripts, no same-origin, no forms, no
  // popups. Defense in depth behind the Python sanitizer (guardrail-enforced).
  return (
    <iframe className="wb-frame" title="Card preview (sandboxed)"
            sandbox="" srcDoc={doc} />
  );
}

export function DiffBody({ diff }) {
  if (!diff) return <div className="wb-empty">Loading diff…</div>;
  if (!diff.baseline_present) {
    return (
      <div className="wb-empty">
        <p>No linked card to compare against — this draft is new content.</p>
      </div>
    );
  }
  return (
    <div className="wb-diff">
      <div className="wb-diff-hd">{diff.change_count} changed line{diff.change_count === 1 ? "" : "s"}</div>
      {(diff.rows || []).map((r, i) => (
        <div key={i} className={"drow " + r.tag}>
          <span className="dgutter">{r.tag === "add" ? "+" : r.tag === "del" ? "−" : " "}</span>
          <span className="dtext">
            {Array.isArray(r.spans)
              ? r.spans.map((s, k) => (
                  <span key={k} className={s.tag === "equal" ? undefined : "wbspan " + s.tag}>
                    {s.text}
                  </span>
                ))
              : (r.text || " ")}
          </span>
        </div>
      ))}
    </div>
  );
}

function ChecksRow({ checks }) {
  if (!Array.isArray(checks) || checks.length === 0) return null;
  return (
    <div className="dchecks wb-checks">
      {checks.map((c, i) => (
        <span key={i} className={"dcheck " + (c.status || "ok")} title={c.detail || ""}>
          {c.check}
        </span>
      ))}
    </div>
  );
}

export default function WorkbenchApp() {
  const bridge = useBridge("workbenchBridge");
  const renn = useBridge("almaBridge");   // the shared in-page chat (M5.5)
  const [chatOpen, setChatOpen] = useState(false);
  const [ws, setWs] = useState({ chips: [], active_id: null });
  const [draft, setDraft] = useState(null);
  const [view, setView] = useState("preview");   // preview | diff | edit
  const [diff, setDiff] = useState(null);
  const [edits, setEdits] = useState({});        // draft_id -> {text, cursor}
  const [aiBusy, setAiBusy] = useState(false);
  const [existingCards, setExistingCards] = useState(null); // null = loading
  const [flash, setFlash] = useState(null);
  const [expand, setExpand] = useState(false);
  const editorRef = useRef(null);
  const flashTimer = useRef(null);
  // Refs mirror the state the commit path needs — the bridge callbacks and
  // view switches read them without stale-closure risk.
  const draftRef = useRef(null);
  const editsRef = useRef({});
  useEffect(() => { draftRef.current = draft; }, [draft]);
  useEffect(() => { editsRef.current = edits; }, [edits]);

  function showFlash(text) {
    setFlash(text);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 2600);
  }

  useEffect(() => {
    window.__almaWorkbenchMounted = true; // headless hook
  }, []);

  useEffect(() => {
    if (!bridge) return;
    bridge.workbenchData.connect((j) => {
      try {
        const p = JSON.parse(j);
        setWs({ chips: p.chips || [], active_id: p.active_id });
        window.__almaWbChips = (p.chips || []).length;   // headless hooks
      } catch (e) {}
    });
    bridge.draftLoaded.connect((j) => {
      try {
        const p = JSON.parse(j);
        setAiBusy(false);
        // Defense in depth (the controller already skips identical re-pushes):
        // never let a host push clobber genuinely-uncommitted editor text. If
        // this draft has a pending local edit, keep the editor state and view —
        // update only the non-editor fields. commitEdit clears the pending edit
        // first, so a real revise result (post-commit) still replaces cleanly.
        const hasPendingEdit = !!editsRef.current[p.id];
        if (hasPendingEdit && draftRef.current && draftRef.current.id === p.id) {
          setDraft((d) => ({ ...d, breadcrumb: p.breadcrumb, title: p.title,
            source: p.source, preview_html: p.preview_html,
            baseline_present: p.baseline_present, checks: p.checks }));
          return;
        }
        setDraft(p);
        setDiff(null);
        setView("preview");
        setEdits((m) => {
          const n = { ...m };
          delete n[p.id];
          return n;
        });
        window.__almaWbDraft = p.title;                  // headless hook
      } catch (e) {}
    });
    bridge.diffReady.connect((j) => {
      try {
        const p = JSON.parse(j);
        setDiff(p);
        window.__almaWbDiffRows = (p.rows || []).length; // headless hook
      } catch (e) {}
    });
    if (bridge.previewUpdated) {
      bridge.previewUpdated.connect((j) => {
        try {
          const p = JSON.parse(j);
          setDraft((d) => (d && d.id === p.id
            ? { ...d, preview_html: p.preview_html, checks: p.checks }
            : d));
          window.__almaWbPreviewRev = (window.__almaWbPreviewRev || 0) + 1;
        } catch (e) {}
      });
    }
    if (bridge.existingCards) {
      bridge.existingCards.connect((j) => {
        try {
          const p = JSON.parse(j);
          setExistingCards(Array.isArray(p.items) ? p.items : []);
          window.__almaWbCards = (p.items || []).length; // headless hook
        } catch (e) {}
      });
    }
    if (bridge.publishResolved) {
      bridge.publishResolved.connect((j) => {
        try {
          const p = JSON.parse(j);
          window.__almaWbPublish = p;                    // headless hook
          if (p.cancelled) showFlash("Publish cancelled — nothing changed.");
          else if (p.dispatched) showFlash(`Publishing “${p.title}”…`);
        } catch (e) {}
      });
    }
    if (bridge.aiEditResolved) {
      // Un-busy the AI-edit bar on the revise completing — success OR failure.
      // A failed/no-op revise emits no draftLoaded, so relying on that alone
      // would wedge the bar on "Renn is revising…".
      bridge.aiEditResolved.connect((j) => {
        setAiBusy(false);
        try {
          const p = JSON.parse(j);
          if (p && p.ok === false) showFlash("Renn couldn't revise that — try again.");
          window.__almaWbAiResolved = p;                 // headless hook
        } catch (e) {}
      });
    }
    bridge.refresh();
  }, [bridge]);

  // Explicit demo mode (#/workbench?demo) — sample data, dev/preview only,
  // badged; engages ONLY without a bridge so real data always wins in-app.
  const demo = isDemoMode() && !bridge;
  // Demo gets a canned Renn so the drawer is previewable; the real almaBridge
  // always wins when present.
  const rennBridge = useMemo(
    () => renn || (demo ? makeDemoRenn() : null), [renn, demo]);
  useEffect(() => {
    if (!demo) return;
    setWs({ chips: DEMO_CHIPS, active_id: 1 });
    setDraft(DEMO_DRAFTS[1]);
    window.__almaWbChips = DEMO_CHIPS.length;
    window.__almaWbDemo = true;
  }, [demo]);

  // ── commit-on-leave: the Qt _commit_active_editor contract ────────
  function commitEdit() {
    const d = draftRef.current;
    const pending = d && editsRef.current[d.id];
    if (!bridge || !d || !pending) return;
    if (pending.text !== d.markdown) {
      bridge.contentEdited(String(d.id), pending.text);
      // reflect the commit locally so current markdown is the edited text
      setDraft((cur) => (cur && cur.id === d.id
        ? { ...cur, markdown: pending.text } : cur));
    }
    // The edit is now persisted (or was a no-op) — drop it from the pending
    // map so a later host draftLoaded (e.g. a revise result) replaces cleanly
    // and the "uncommitted edit" guard in draftLoaded only fires for real
    // in-progress typing.
    setEdits((m) => {
      if (!(d.id in m)) return m;
      const n = { ...m };
      delete n[d.id];
      return n;
    });
  }

  function pickView(v) {
    if (view === "edit" && v !== "edit") commitEdit();
    setView(v);
    if (v === "diff") {
      if (view === "edit") setDiff(null);
      if (demo) setDiff(DEMO_DIFF);
      else if (bridge) bridge.requestDiff();
    }
  }

  function switchTo(id) {
    if (view === "edit") commitEdit();       // unsaved edits persist on leave
    if (demo) {
      setWs((w) => ({ ...w, active_id: id }));
      setDraft(DEMO_DRAFTS[id] || null);
      setDiff(null);
      setView("preview");
    } else if (bridge) bridge.switchWorkspace(String(id));
  }

  function closeChip(id, ev) {
    ev.stopPropagation();
    if (view === "edit") commitEdit();
    if (demo) {
      setWs((w) => {
        const chips = w.chips.filter((c) => c.id !== id);
        const active = w.active_id === id ? (chips[0] && chips[0].id) || null : w.active_id;
        setDraft(active ? DEMO_DRAFTS[active] : null);
        return { chips, active_id: active };
      });
    } else if (bridge) bridge.closeWorkspace(String(id));
  }

  function askAi(instruction) {
    if (!bridge || !draft) return;
    commitEdit();                             // revise runs on stored content
    const el = editorRef.current;
    let selection = "";
    if (el && el.selectionStart !== el.selectionEnd) {
      selection = el.value.slice(el.selectionStart, el.selectionEnd);
    }
    setAiBusy(true);
    bridge.aiEdit(instruction, selection);
  }

  function publish(dest) {
    if (!bridge) return;
    if (view === "edit") commitEdit();        // publish what you see
    bridge.requestPublish(dest);
  }

  const editText = draft
    ? (edits[draft.id] ? edits[draft.id].text : draft.markdown)
    : "";

  const connected = bridge || demo;

  return (
    <div className="app route-workbench">
      <header className="hdr wb-hdr">
        <span className="dot" aria-hidden="true" />
        <span className="name">Workbench</span>
        {demo && <span className="cal-demo-badge">SAMPLE DATA</span>}
        <span className="wb-ws-label">WORKSPACES</span>
        <span className="wb-count">{ws.chips.length}</span>
        <span className="wb-chips">
          {ws.chips.map((c) => (
            <button key={c.id}
                    className={"wb-chip kind-" + (c.source || "drive") + (c.id === ws.active_id ? " active" : "")}
                    onClick={() => switchTo(c.id)}>
              <span className="wb-chip-title">{c.title}</span>
              <span className="wb-chip-x" title="Close workspace"
                    onClick={(ev) => closeChip(c.id, ev)}>×</span>
            </button>
          ))}
        </span>
        <span className="cal-spacer" />
        <ToolsMenu disabled={!bridge || !draft}
                   existingCards={existingCards}
                   onImport={(kind) => bridge && bridge.requestImport(kind)}
                   onPublish={publish}
                   onListExisting={() => {
                     setExistingCards(null);
                     if (bridge) bridge.listExistingCards();
                   }} />
        <button className="wb-btn" onClick={() => bridge && bridge.findTask()}
                disabled={!bridge}>+ Find a task</button>
        <button className="wb-btn primary"
                onClick={() => {
                  // The updated Renn drawer when the chat bridge is wired;
                  // graceful fallback to the Qt drilldown when it isn't.
                  if (rennBridge) setChatOpen(true);
                  else if (bridge) bridge.openChat();
                }}
                disabled={!bridge && !rennBridge}>Open Assistant ›</button>
      </header>

      {!connected ? (
        <div className="route-empty">
          <p>Waiting for the workbench bridge…</p>
          <p className="route-note">This route runs inside the app's Workbench tab
            (<code>enablement.web_tabs: all</code>). Append <code>?demo</code> for sample data.</p>
        </div>
      ) : !draft ? (
        <div className="route-empty">
          <p>No open workspaces.</p>
          <p className="route-note">Find a task, or drag a document anywhere onto this tab.</p>
        </div>
      ) : (
        <div className="wb-body">
          <div className={"wb-card" + (expand ? " expand" : "")}>
            <div className="wb-breadcrumb">{draft.breadcrumb}</div>
            <div className="wb-title">{draft.title}</div>
            <div className="wb-meta">
              <span className="dcheck warn wb-badge">Draft</span>
              <span className="wb-source">{draft.source}</span>
              <span className="cal-spacer" />
              <button className={"wb-view-btn" + (view === "preview" ? " active" : "")}
                      onClick={() => pickView("preview")}>Guru preview</button>
              <button className={"wb-view-btn" + (view === "edit" ? " active" : "")}
                      onClick={() => pickView("edit")}>Edit markdown</button>
              <button className={"wb-view-btn" + (view === "diff" ? " active" : "")}
                      onClick={() => pickView("diff")}>Review changes</button>
              <button className="wb-view-btn" title={expand ? "Restore" : "Expand"}
                      onClick={() => setExpand(!expand)}>⤢</button>
            </div>
            <ChecksRow checks={draft.checks} />
            <div className="wb-canvas">
              {view === "preview" && <PreviewFrame html={draft.preview_html} />}
              {view === "diff" && <DiffBody diff={diff} />}
              {view === "edit" && (
                <textarea ref={editorRef} className="wb-editor" value={editText}
                          spellCheck="false"
                          onChange={(e) => {
                            const text = e.target.value;
                            setEdits((m) => ({ ...m, [draft.id]: { text } }));
                          }} />
              )}
            </div>
            {view === "edit" && (
              <AiEditBar busy={aiBusy} disabled={!bridge} onAsk={askAi} />
            )}
            <div className="wb-tools">
              <button className="wb-btn" onClick={() => bridge && bridge.uploadRequested()}
                      disabled={!bridge}>Upload document</button>
              <span className="wb-drop-note">…or drag &amp; drop a file anywhere onto this tab</span>
            </div>
          </div>
        </div>
      )}
      {flash && <div className="cal-flash">{flash}</div>}
      <ChatDrawer bridge={rennBridge} open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  );
}
