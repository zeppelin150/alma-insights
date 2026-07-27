// Review surfaces for the Zendesk revision flow.
//
// THE RULE THIS FILE EXISTS TO ENFORCE: the human reviews the exact bytes
// the clipboard will deliver. The controller's diff_ready payload carries
// TWO diffs — `rows` is a diff of the HTML/JSON SOURCE (authoritative: every
// attribute, style declaration, script body and zero-size span is literally
// on screen) and `text_rows` is a readable projection (SECONDARY: it drops
// things by construction and must never be the only thing shown before a
// copy). Both render here, source first and labelled as the authority.
//
// Everything renders as escaped React children — never live markup.
import { formatChars } from "./shape.js";

function gutterFor(tag) {
  if (tag === "add") return "+";
  if (tag === "del") return "−";
  if (tag === "change") return "±";
  return " ";
}

function DiffRows({ rows }) {
  const list = Array.isArray(rows) ? rows : [];
  if (list.length === 0) {
    return <div className="zd-diff-empty">(nothing to show)</div>;
  }
  return (
    <>
      {list.map((r, i) => (
        <div key={i} className={"zd-drow " + (r.tag || "ctx")}>
          <span className="zd-dgutter">{gutterFor(r.tag)}</span>
          <span className="zd-dtext">
            {Array.isArray(r.spans)
              ? r.spans.map((s, k) => (
                  <span key={k}
                        className={s.tag === "equal" ? undefined : "zd-dspan " + s.tag}>
                    {s.text}
                  </span>
                ))
              : (r.text || " ")}
          </span>
        </div>
      ))}
    </>
  );
}

function plural(n, word) {
  const count = Number(n) || 0;
  return count + " " + word + (count === 1 ? "" : "s");
}

// Escaped-text panel over the exact stored source of a MIRROR row — a
// COLLAPSED disclosure, closed by default (owner correction, 2026-07-26).
//
// The rendered article is the primary surface; these bytes are what a person
// asks for when they intend to copy HTML, not something they wade through to
// reach the article. Collapsed it costs one header row and cannot push the
// preview out of view. The security invariant is unchanged: the controller
// refuses mirror clipboard copies until the payload carrying this source has
// been SERVED (js_open_article records the review) — the record is made by
// the payload, not by the pixels, so collapsing changes nothing about it.
//
// Optionally controlled: pass `open` + `onToggle` to let the markup alert
// expand it. Everything renders as escaped React children — never markup.
export function SourcePanel({ source, notice, label, open, onToggle }) {
  const chars = String(source == null ? "" : source).length;
  return (
    <details className="zd-source-panel" open={!!open}
             onToggle={onToggle
               ? (ev) => onToggle(!!(ev.currentTarget && ev.currentTarget.open))
               : undefined}>
      <summary className="zd-source-summary" id="zd-source-summary">
        <span className="zd-source-summary-label">
          {label || "HTML source"} — what “Copy HTML” puts on the clipboard
        </span>
        <span className="zd-cell-meta">{formatChars(chars)} chars</span>
      </summary>
      {notice && <div className="zd-diff-warn">Warning: {notice}.</div>}
      <pre className="zd-source">{source || ""}</pre>
    </details>
  );
}

// The markup notice, promoted OUT of the collapsed disclosure so it is seen
// when it fires — and rendered only then. Python's preview profile now keeps
// what a sandboxed frame can safely render (classes, ids, data-attributes,
// inline styles, tables, embedded frames), so this fires when something
// genuinely cannot be displayed rather than on every pulled article. A
// warning that is always on is a warning nobody reads.
export function MarkupAlert({ notice, onShowSource }) {
  if (!notice) return null;
  return (
    <div className="zd-markup-alert" role="alert">
      <span className="zd-markup-alert-text">Warning: {notice}.</span>
      {onShowSource && (
        <button type="button" className="zd-btn danger" onClick={onShowSource}>
          Show HTML source
        </button>
      )}
    </div>
  );
}

export default function RevisionDiff({ diff }) {
  if (!diff) return <div className="zd-empty">Loading diff…</div>;
  const rows = Array.isArray(diff.rows) ? diff.rows : [];
  const textRows = Array.isArray(diff.text_rows) ? diff.text_rows : [];
  const title = diff.title || {};
  return (
    <div className="zd-diff">
      {diff.warning && <div className="zd-diff-warn">{diff.warning}</div>}
      {diff.markup_notice && (
        <div className="zd-diff-warn">Warning: {diff.markup_notice}.</div>
      )}
      <div className="zd-diff-hd">
        Source review (authoritative) —{" "}
        {diff.baseline_present
          ? plural(diff.change_count, "changed line") + " vs the mirror"
          : "new content, no mirrored baseline to compare against"}
      </div>
      <div className="zd-diff-note">
        The exact bytes the clipboard will deliver: attributes, inline
        styles and script tags included.
      </div>
      {title.changed && (
        <div className="zd-diff-title">
          Title: <span className="zd-dspan del">{title.old}</span>{" "}
          <span className="zd-dspan add">{title.new}</span>
        </div>
      )}
      <DiffRows rows={rows} />
      {textRows.length > 0 && (
        <>
          <div className="zd-diff-hd secondary">
            Readable text (secondary view — NOT what the clipboard delivers)
            {typeof diff.text_change_count === "number"
              ? " — " + plural(diff.text_change_count, "changed line")
              : ""}
          </div>
          <div className="zd-diff-note">
            A convenience projection. It drops markup on purpose — never
            approve a revision from this pane alone.
          </div>
          <DiffRows rows={textRows} />
        </>
      )}
    </div>
  );
}
