// Garden chrome for the Zendesk clone: the dark kale icon rail (Admin
// Center / agent-workspace style, 60px, #03363d) plus the white 52px header
// with the breadcrumb, view tabs, and the mirror actions (pull / import).
// Pure renderer — every button only calls the handlers the app passes down.

const ICONS = {
  articles: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 2.5h8l2.5 2.5v12.5H5z" />
      <path d="M7.5 8h5M7.5 11h5M7.5 14h3" />
    </svg>
  ),
  macros: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 2 4 11.5h5L9 18l7-9.5h-5z" />
    </svg>
  ),
  revisions: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 5.5V10l3 2" />
    </svg>
  ),
};

const CRUMBS = {
  articles: ["Guide admin", "Manage articles"],
  macros: ["Admin Center", "Workspaces", "Agent tools", "Macros"],
  revisions: ["Content Command Center", "Revision Center"],
};

const NAV = [
  ["articles", "Manage articles"],
  ["macros", "Macros"],
  ["revisions", "Revision Center"],
];

// Mirror overflow menu: the purge scopes js_purge_mirror accepts. Purge is
// DESTRUCTIVE and confirm-gated Python-side (native QMessageBox with live
// counts, unreachable from this page) — these items only ASK. Copied/pushed
// revisions always survive a purge (the audit trail of what went into real
// Zendesk), which the titles say up front.
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

// Close the <details> dropdown when an item is chosen (the native confirm
// takes over from here).
function closeMenu(ev) {
  const d = ev && ev.currentTarget && ev.currentTarget.closest
    ? ev.currentTarget.closest("details") : null;
  if (d) d.removeAttribute("open");
}

export function KaleRail({ view, counts, onNav }) {
  return (
    <nav className="zd-rail" aria-label="Zendesk mirror navigation">
      <div className="zd-rail-brand" title="Zendesk mirror (read-only)">Z</div>
      {NAV.map(([key, label]) => (
        <button key={key}
                className={"zd-rail-btn" + (view === key ? " active" : "")}
                title={label} onClick={() => onNav(key)}>
          {ICONS[key]}
          {key === "revisions" && counts.revisions_open > 0 && (
            <span className="zd-rail-count">{counts.revisions_open}</span>
          )}
        </button>
      ))}
      <span className="zd-rail-spacer" />
    </nav>
  );
}

export default function GardenChrome({
  view, counts, connected, demo, onNav, onPull, onImport, onImportFolder,
  onPurge, pullBusy, lastPull, onOpenChat,
}) {
  const crumb = CRUMBS[view] || CRUMBS.articles;
  return (
    <header className="zd-hdr">
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
      <span className="zd-hdr-spacer" />
      <span className="zd-hdr-meta" title="Last read-only pull from Zendesk">
        Last pull: {lastPull || "Never"}
      </span>
      <button className="zd-btn" onClick={onPull} disabled={pullBusy}
              title="Read-only GET pull of articles, macros, sections and categories">
        {pullBusy ? "Pulling…" : "Pull from Zendesk"}
      </button>
      <button className="zd-btn" onClick={onImport} disabled={pullBusy}
              title="Import Zendesk-shaped JSON, HTML or document files into the mirror">
        Import files…
      </button>
      <button className="zd-btn basic" onClick={onImportFolder} disabled={pullBusy}
              title="Import every supported file in a folder">
        Import folder…
      </button>
      {onPurge && (
        <details className="zd-menu">
          <summary className="zd-btn basic"
                   title="Mirror maintenance — remove mirrored content">
            Mirror ▾
          </summary>
          <div className="zd-menu-items" role="menu">
            <div className="zd-menu-hd">
              Local mirror only — real Zendesk is never touched.
            </div>
            {PURGE_ITEMS.map(([scope, label, hint]) => (
              <button key={scope} className="zd-menu-item" role="menuitem"
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
    </header>
  );
}
