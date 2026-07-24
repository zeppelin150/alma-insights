// Guide article editor clone: back arrow + title + status in the top bar,
// Preview / Save-split chrome (decorative — the mirror is read-only), the
// 21-button editor toolbar in the documented order, the sandboxed body
// frame, and the right "Article settings" sidebar. The live controls are
// the per-field Copy exact buttons (Python re-reads exact DB bytes) and the
// open-revision links.
import ArticleBody from "./ArticleBody.jsx";

// Toolbar buttons in the officially documented order (help center editor
// toolbar reference) — decorative chrome on this read-only surface.
const TOOLBAR = [
  ["Paragraph ▾", "Heading selector"],
  ["Aa ▾", "Formatting selector"],
  ["B", "Bold", "b"],
  ["U", "Underline", "u"],
  ["I", "Italic", "i"],
  ["Quote", "Block quote"],
  ["14 ▾", "Font size selector"],
  ["A", "Text color"],
  ["A+", "Text background color"],
  ["Table ▾", "Table"],
  ["Code", "Code block", "mono"],
  ["Lang ▾", "Code block language selector"],
  ["• List", "Bulleted list"],
  ["1. List", "Numbered list"],
  ["Align ▾", "Align or indent selector"],
  ["Link", "Insert or edit link"],
  ["Image", "Insert image"],
  ["Block", "Insert content block"],
  ["Enhance", "Enhance writing"],
  ["Parts ▾", "Article components"],
  ["</>", "View or edit the HTML source code", "mono"],
];

function SetRow({ label, children, muted }) {
  return (
    <div className="zd-set-row">
      <div className="zd-set-label">{label}</div>
      <div className={"zd-set-value" + (muted ? " muted" : "")}>{children}</div>
    </div>
  );
}

export default function ArticleEditor({ article, onBack, onCopy, onOpenRevision }) {
  if (!article) return null;
  const revisions = Array.isArray(article.revisions) ? article.revisions : [];
  const openRevs = revisions.filter((r) => r.status === "pending" || r.status === "ready");
  return (
    <div className="zd-editor">
      <div className="zd-editor-main">
        <div className="zd-editor-top">
          <button className="zd-back" onClick={onBack} title="Back to Manage articles">‹</button>
          <span className="zd-editor-title">{article.title}</span>
          {article.draft
            ? <span className="zd-tag grey">Draft</span>
            : <span className="zd-tag softgreen">Published</span>}
          {article.outdated && <span className="zd-tag softyellow">Outdated</span>}
          {openRevs.length > 0 && (
            <span className="zd-tag softblue"
                  title="Renn revisions awaiting review in the Revision Center">
              {openRevs.length} unpublished change{openRevs.length === 1 ? "" : "s"}
            </span>
          )}
          <span className="zd-hdr-spacer" />
          <button className="zd-btn basic" disabled
                  title="Read-only mirror — preview happens in real Zendesk">Preview</button>
          <span className="zd-split" title="Read-only mirror — edits happen in real Zendesk">
            <button className="zd-btn" disabled>Save</button>
            <button className="zd-btn" disabled>▾</button>
          </span>
          <button className="zd-btn" onClick={() => onCopy("title")}
                  title="Copy the exact stored title to the clipboard">Copy title</button>
          <button className="zd-btn" onClick={() => onCopy("body_html")}
                  title="Copy the exact stored HTML source (plain text)">Copy HTML</button>
          <button className="zd-btn" onClick={() => onCopy("body_rich")}
                  title="Copy as rich text — pasting into the Zendesk editor keeps formatting">
            Copy rich text
          </button>
        </div>
        <input className="zd-title-input" value={article.title} readOnly />
        <div className="zd-ed-toolbar">
          {TOOLBAR.map(([label, title, cls], i) => (
            <span key={title} style={{ display: "inline-flex" }}>
              {(i === 2 || i === 6 || i === 9 || i === 12 || i === 15 || i === 20) && (
                <span className="zd-ed-sep" aria-hidden="true" />
              )}
              <button className={"zd-ed-tool" + (cls ? " " + cls : "")}
                      title={title} disabled>{label}</button>
            </span>
          ))}
        </div>
        <div className="zd-frame-wrap">
          <ArticleBody srcdoc={article.body_srcdoc} />
        </div>
        <div className="zd-media-note">
          Local mirror preview — embedded media and scripts are stripped for
          safety. Copy HTML keeps the stored source byte-for-byte.
        </div>
      </div>
      <aside className="zd-settings">
        <div className="zd-settings-hd">Article settings</div>
        <SetRow label="Managed by" muted>Agents and admins</SetRow>
        <SetRow label="Owner" muted>—</SetRow>
        <SetRow label="Author">{article.author || "—"}</SetRow>
        <SetRow label="Visible to" muted>Everyone</SetRow>
        <SetRow label="Publishing" muted>Manual — copy by hand into Zendesk</SetRow>
        <SetRow label="Placement">
          {article.category ? article.category + " › " : ""}{article.section || "—"}
          {article.position != null && (
            <span className="zd-cell-meta"> · position {article.position}</span>
          )}
        </SetRow>
        <SetRow label="Labels">
          {(article.labels || []).length === 0 ? "—"
            : (article.labels || []).map((l) => <span key={l} className="zd-tag">{l}</span>)}
        </SetRow>
        <SetRow label="Content tags" muted>—</SetRow>
        <SetRow label="Attachments" muted>—</SetRow>
        <div className="zd-set-row zd-set-toggle">
          <span className="zd-toggle on" aria-hidden="true" />Turn on comments
        </div>
        <div className="zd-set-row zd-set-toggle">
          <span className="zd-toggle" aria-hidden="true" />Promote article
        </div>
        <SetRow label="Template" muted>Default</SetRow>
        <div className="zd-set-divider" />
        <div className="zd-settings-hd">Mirror</div>
        <SetRow label="Origin">
          {article.origin === "import" ? "Imported file" : "Zendesk pull"}
        </SetRow>
        {article.source_file && <SetRow label="Source file">{article.source_file}</SetRow>}
        {article.html_url && <SetRow label="Zendesk URL">{article.html_url}</SetRow>}
        <SetRow label="Last updated">{article.updated_display || "—"}</SetRow>
        {revisions.length > 0 && (
          <>
            <div className="zd-set-divider" />
            <div className="zd-settings-hd">Revisions</div>
            {revisions.map((r) => (
              <div key={r.draft_id} className="zd-set-row">
                <button className="zd-btn basic"
                        onClick={() => onOpenRevision(r.draft_id, r.status)}>
                  {r.title || "Revision " + r.draft_id}
                </button>
                <div className="zd-cell-meta">
                  {r.status} · {r.updated_display || ""}
                </div>
              </div>
            ))}
          </>
        )}
      </aside>
    </div>
  );
}
