// Admin Center macro list clone: "Macros" page title with the Create macro /
// Actions chrome (decorative — the mirror is read-only), the documented
// search box + Filter, and the table with :: category nesting rendered as a
// grey breadcrumb prefix. Pure renderer.
import { filterMacros, keyActivate, macroPath } from "./shape.js";

const SEARCH_ICON = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"
       strokeLinecap="round" aria-hidden="true">
    <circle cx="7" cy="7" r="4.5" />
    <path d="m10.5 10.5 3 3" />
  </svg>
);

export default function MacroList({ macros, query, onOpen, onSearch, served = false }) {
  const all = Array.isArray(macros) ? macros : [];
  // served: Python already FTS-filtered this list (actions_text hits
  // included) — no client re-filter. Demo keeps the local text filter.
  const rows = filterMacros(all, query, served);
  return (
    <div className="zd-content">
      <div className="zd-pane">
        <div className="zd-toolbar">
          <span className="zd-page-title">Macros</span>
          <span className="zd-hdr-spacer" />
          <button className="zd-btn basic" title="Manage settings, Edit order">Actions ▾</button>
          <button className="zd-btn primary" disabled
                  title="Read-only mirror — create macros in real Zendesk">Create macro</button>
        </div>
        <div className="zd-toolbar">
          <span className="zd-search">
            {SEARCH_ICON}
            <input className="zd-input" placeholder="Search by partial or complete name"
                   value={query} onChange={(e) => onSearch(e.target.value)} />
          </span>
          {query && (
            <button className="zd-btn basic" onClick={() => onSearch("")}>Clear search</button>
          )}
          <button className="zd-btn basic"
                  title="Status, Available for, Categories">Filter</button>
        </div>
        <div className="zd-scroll">
          {rows.length === 0 ? (
            <div className="zd-empty">
              {all.length === 0
                ? "The mirror has no macros yet. Pull from Zendesk or import an export."
                : "No macros match this search."}
            </div>
          ) : (
            <table className="zd-table">
              <thead>
                <tr>
                  <th style={{ width: 34 }}><span className="zd-check" aria-hidden="true" /></th>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Last updated<span className="chev">▾</span></th>
                  <th style={{ width: 90 }}>Revisions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => {
                  const { path, leaf } = macroPath(m.name);
                  return (
                    <tr key={m.id} className="zd-row" role="button" tabIndex={0}
                        onClick={() => onOpen(m.id)}
                        onKeyDown={keyActivate(() => onOpen(m.id))}>
                      <td onClick={(ev) => ev.stopPropagation()}>
                        <span className="zd-check" aria-hidden="true" />
                      </td>
                      <td>
                        <span className="zd-cell-title">
                          {path.length > 0 && (
                            <span className="zd-cell-meta">{path.join(" › ")}&nbsp;›&nbsp;</span>
                          )}
                          <span className="t">{leaf}</span>
                        </span>
                      </td>
                      <td>
                        {m.active
                          ? <span className="zd-tag softgreen">Active</span>
                          : <span className="zd-tag grey">Inactive</span>}
                      </td>
                      <td className="zd-cell-meta">{m.updated_display}</td>
                      <td>
                        {m.open_revisions > 0 && (
                          <span className="zd-tag softblue">{m.open_revisions} open</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
