// Guide admin "Manage articles" clone: article-list rail (fixed lists + the
// category/section tree) on the left, toolbar (search / Filter / Save search
// as list / language) and the article table on the right. Status dots per
// the real list — green = published, hollow = draft. Pure renderer; all
// text is escaped React children.
import { articleDotClass, filterArticles, keyActivate } from "./shape.js";

const SEARCH_ICON = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"
       strokeLinecap="round" aria-hidden="true">
    <circle cx="7" cy="7" r="4.5" />
    <path d="m10.5 10.5 3 3" />
  </svg>
);

export default function ArticleList({
  categories, articles, activeSection, query, onOpen, onSearch, onSelectSection,
  served = false,
}) {
  const all = Array.isArray(articles) ? articles : [];
  const drafts = all.filter((a) => a.draft);
  // served: Python already FTS-filtered this list for the query (body hits
  // included) — only the section filter applies client-side. Demo mode
  // (served=false) keeps the local text filter.
  const rows = filterArticles(all, { query, sectionId: activeSection, served });
  return (
    <div className="zd-content">
      <aside className="zd-side">
        <div className="zd-side-hd">Article lists</div>
        <button className={"zd-side-item" + (activeSection == null ? " active" : "")}
                onClick={() => onSelectSection(null)}>
          All articles<span className="n">{all.length}</span>
        </button>
        <button className="zd-side-item" disabled title="Drafts in the mirror">
          Drafts<span className="n">{drafts.length}</span>
        </button>
        <div className="zd-side-hd" style={{ marginTop: 12 }}>Knowledge base</div>
        {(categories || []).map((c) => (
          <div key={c.id}>
            <div className="zd-side-cat">{c.name}</div>
            {(c.sections || []).map((s) => (
              <button key={s.id}
                      className={"zd-side-item sub" + (activeSection === s.id ? " active" : "")}
                      onClick={() => onSelectSection(activeSection === s.id ? null : s.id)}>
                {s.name}<span className="n">{s.article_count}</span>
              </button>
            ))}
          </div>
        ))}
      </aside>
      <div className="zd-pane">
        <div className="zd-toolbar">
          <span className="zd-page-title">Manage articles</span>
          <span className="zd-search">
            {SEARCH_ICON}
            <input className="zd-input" placeholder="Search articles" value={query}
                   onChange={(e) => onSearch(e.target.value)} />
          </span>
          <button className="zd-btn basic" title="Filter by status, language and more">Filter</button>
          <button className="zd-btn basic" title="Save this search as a list">Save search as list</button>
          <span className="zd-hdr-spacer" />
          <span className="zd-select-look" title="Article language">English (US)</span>
        </div>
        <div className="zd-scroll">
          {rows.length === 0 ? (
            <div className="zd-empty">
              {all.length === 0
                ? "The mirror is empty. Pull from Zendesk or import a Help Center export."
                : "No articles match this search."}
            </div>
          ) : (
            <table className="zd-table">
              <thead>
                <tr>
                  <th style={{ width: 34 }}><span className="zd-check" aria-hidden="true" /></th>
                  <th>Article title</th>
                  <th>Section</th>
                  <th>Author</th>
                  <th>Last edited<span className="chev">▾</span></th>
                  <th>Labels</th>
                  <th style={{ width: 90 }}>Revisions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id} className="zd-row" role="button" tabIndex={0}
                      onClick={() => onOpen(a.id)}
                      onKeyDown={keyActivate(() => onOpen(a.id))}>
                    <td onClick={(ev) => ev.stopPropagation()}>
                      <span className="zd-check" aria-hidden="true" />
                    </td>
                    <td>
                      <span className="zd-cell-title">
                        <span className={articleDotClass(a)}
                              title={a.draft ? "Draft" : "Published"} />
                        <span className="t">{a.title}</span>
                      </span>
                    </td>
                    <td className="zd-cell-meta">{a.section}</td>
                    <td className="zd-cell-meta">{a.author || "—"}</td>
                    <td className="zd-cell-meta">{a.updated_display}</td>
                    <td>
                      <span className="zd-cell-tags">
                        {a.outdated && <span className="zd-tag softyellow">Outdated</span>}
                        {a.origin === "import" && <span className="zd-tag grey">Imported</span>}
                        {(a.labels || []).slice(0, 3).map((l) => (
                          <span key={l} className="zd-tag">{l}</span>
                        ))}
                      </span>
                    </td>
                    <td>
                      {a.open_revisions > 0 && (
                        <span className="zd-tag softblue">{a.open_revisions} open</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
