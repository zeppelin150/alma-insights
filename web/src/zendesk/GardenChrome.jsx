// Garden chrome for the Zendesk clone: a single compact white header row
// (~52px, Garden typography) — breadcrumb + view tabs on the left, the
// mirror actions (pull / Mirror menu / Renn) on the right. The old kale
// icon rail and tall header band are gone: inside the app tab they only
// duplicated the app's own sidebar. Pure renderer — every button only
// calls the handlers the app passes down.

const CRUMBS = {
  articles: ["Guide admin", "Manage articles"],
  macros: ["Admin Center", "Workspaces", "Agent tools", "Macros"],
  revisions: ["Content Command Center", "Revision Center"],
};

// Mirror overflow menu: file imports first, then the purge scopes
// js_purge_mirror accepts. Purge is DESTRUCTIVE and confirm-gated
// Python-side (native QMessageBox with live counts, unreachable from this
// page) — these items only ASK. Copied/pushed revisions always survive a
// purge (the audit trail of what went into real Zendesk), which the titles
// say up front.
const PURGE_ITEMS = [
  ["all", "Delete all mirrored content…",
   "Remove every mirrored article and macro and delete pending/ready revisions — copied revisions are kept as the audit record"],
  ["articles", "Delete mirrored articles…",
   "Remove every article from the local mirror — revisions are kept"],
  ["macros", "Delete mirrored macros…",
   "Remove every macro from the local mirror — revisions are kept"],
  ["imported", "Delete imported content only…",
   "Remove file-imported rows only — pulled rows and revisions are kept"],
];

// Close the <details> dropdown when an item is chosen (the native file
// dialog or confirm takes over from here).
function closeMenu(ev) {
  const d = ev && ev.currentTarget && ev.currentTarget.closest
    ? ev.currentTarget.closest("details") : null;
  if (d) d.removeAttribute("open");
}

export default function GardenChrome({
  view, counts, connected, demo, onNav, onPull, onImport, onImportFolder,
  onPurge, pullBusy, lastPull, onOpenChat,
}) {
  const crumb = CRUMBS[view] || CRUMBS.articles;
  const hasMenu = !!(onImport || onImportFolder || onPurge);
  return (
    <header className="zd-hdr">
      <div className="zd-hdr-left">
        <span className="zd-crumb">
          {crumb.map((c, i) => (
            <span key={i}>
              {i > 0 && <span className="sep">›</span>}
              {i === crumb.length - 1 ? <b>{c}</b> : c}
            </span>
          ))}
        </span>
        {demo && <span className="cal-demo-badge">SAMPLE DATA</span>}
        {!demo && !connected && (
          <span className="zd-tag softyellow" title="Local mirror only — no Zendesk credentials on this box">
            Mirror only
          </span>
        )}
        <div className="zd-tabs">
          <button className={"zd-tab" + (view === "articles" ? " active" : "")}
                  onClick={() => onNav("articles")}>
            Articles<span className="n">{counts.articles}</span>
          </button>
          <button className={"zd-tab" + (view === "macros" ? " active" : "")}
                  onClick={() => onNav("macros")}>
            Macros<span className="n">{counts.macros}</span>
          </button>
          <button className={"zd-tab" + (view === "revisions" ? " active" : "")}
                  onClick={() => onNav("revisions")}>
            Revisions<span className="n">{counts.revisions_open}</span>
          </button>
        </div>
      </div>
      <div className="zd-hdr-right">
        <span className="zd-hdr-meta" title="Last read-only pull from Zendesk">
          Last pull: {lastPull || "Never"}
        </span>
        <button className="zd-btn" onClick={onPull} disabled={pullBusy}
                title="Read-only GET pull of articles, macros, sections and categories">
          {pullBusy ? "Pulling…" : "Pull from Zendesk"}
        </button>
        {hasMenu && (
          <details className="zd-menu">
            <summary className="zd-btn basic"
                     title="Mirror maintenance — import files or remove mirrored content">
              Mirror ▾
            </summary>
            <div className="zd-menu-items" role="menu">
              <div className="zd-menu-hd">
                Local mirror only — real Zendesk is never touched.
              </div>
              {onImport && (
                <button className="zd-menu-item" role="menuitem"
                        disabled={pullBusy}
                        title="Import Zendesk-shaped JSON, HTML or document files into the mirror"
                        onClick={(ev) => { closeMenu(ev); onImport(); }}>
                  Import files…
                </button>
              )}
              {onImportFolder && (
                <button className="zd-menu-item" role="menuitem"
                        disabled={pullBusy}
                        title="Import every supported file in a folder"
                        onClick={(ev) => { closeMenu(ev); onImportFolder(); }}>
                  Import folder…
                </button>
              )}
              {(onImport || onImportFolder) && onPurge && (
                <div className="zd-menu-divider" role="separator" />
              )}
              {onPurge && PURGE_ITEMS.map(([scope, label, hint]) => (
                <button key={scope} className="zd-menu-item danger" role="menuitem"
                        disabled={pullBusy} title={hint}
                        onClick={(ev) => { closeMenu(ev); onPurge(scope); }}>
                  {label}
                </button>
              ))}
            </div>
          </details>
        )}
        {onOpenChat && (
          <button className="zd-btn primary" onClick={onOpenChat}>Renn ›</button>
        )}
      </div>
    </header>
  );
}
