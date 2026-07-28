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
// F5 (disclosure fix, 2026-07-26). Access to these bytes used to depend on
// the markup notice: the summary was a muted grey strip and the ONLY thing
// that expanded the panel for you was MarkupAlert. That notice was proven
// silent for every content-HIDING vector (display:none, font-size:0,
// off-screen positioning, white-on-white, decoy overlays, comments,
// attribute payloads), because it reports what the sanitizer REMOVED and
// never what it KEPT but will not paint. So the operator's route to the
// exact characters must not run through it:
//   * the summary is now an obvious, permanently visible control that names
//     the action ("Show source" / "Hide source") — not a decorative header;
//   * a matching "View exact source" button sits next to the copy controls
//     (CopyControls `onShowSource`), because the copy row is where the
//     decision to release bytes is actually made;
//   * a firing notice STILL raises the loud alert and auto-expands, and is
//     now also flagged on this summary so it survives a collapsed panel.
//
// Optionally controlled: pass `open` + `onToggle`. Everything renders as
// escaped React children — never markup.
export function SourcePanel({ source, notice, label, open, onToggle }) {
  const chars = String(source == null ? "" : source).length;
  return (
    <details className={"zd-source-panel" + (notice ? " noticed" : "")}
             open={!!open}
             onToggle={onToggle
               ? (ev) => onToggle(!!(ev.currentTarget && ev.currentTarget.open))
               : undefined}>
      <summary className="zd-source-summary" id="zd-source-summary">
        <span className="zd-source-summary-label">
          {label || "Exact HTML source"} — every character the clipboard delivers
        </span>
        {notice && (
          <span className="zd-source-flag" title={notice}>
            Preview is incomplete
          </span>
        )}
        <span className="zd-cell-meta">{formatChars(chars)} chars</span>
        <span className="zd-source-action">
          {open ? "Hide source" : "Show source"}
        </span>
      </summary>
      <div className="zd-source-why">
        The rendered view above is a convenience. This is the only surface
        that shows every stored character — including anything the preview
        cannot paint, and anything the stored markup hides from a reader.
      </div>
      {notice && <div className="zd-diff-warn">Warning: {notice}.</div>}
      <pre className="zd-source">{source || ""}</pre>
    </details>
  );
}

// The markup notice, promoted OUT of the collapsed disclosure so it is seen
// when it fires — and rendered only then.
//
// This notice now fires FAR more often: Python no longer drops
// content-hiding markup silently, so anything the preview cannot show
// faithfully raises it. Frequency is why the body is three separate lines
// rather than one red sentence — the specific server-supplied `notice` is
// surfaced verbatim (that is the part that differs per article and carries
// the information), wrapped in a fixed headline that says what is at stake
// and a fixed instruction that says what to do about it. A warning that
// only ever says "warning" is noise; one that names the finding and the
// next action stays worth reading on the hundredth article.
//
// `headline` / `detail` / `actionLabel` let the diff pane reuse the same
// component with wording that fits a diff rather than a preview.
export function MarkupAlert({
  notice, onShowSource, headline, detail, actionLabel,
}) {
  if (!notice) return null;
  return (
    <div className="zd-markup-alert" role="alert">
      <div className="zd-markup-alert-body">
        <div className="zd-markup-alert-hd">
          {headline || "The preview below is not a faithful view of this content"}
        </div>
        <div className="zd-markup-alert-text">{notice}.</div>
        <div className="zd-markup-alert-what">
          {detail || "Content the stored markup hides from a reader is marked "
            + "in the preview. Read the exact source before you copy or paste "
            + "— it is the only view that shows every character."}
        </div>
      </div>
      {onShowSource && (
        <button type="button" className="zd-btn danger" onClick={onShowSource}>
          {actionLabel || "Show exact source"}
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
      {/* Same alert component as the article surface, worded for a diff:
          here the exact bytes are already on screen unconditionally (the
          source rows below), so the instruction points at them rather than
          at a disclosure to open. */}
      <MarkupAlert
        notice={diff.markup_notice}
        headline="A rendered view of this content would not show all of it"
        detail={"The source rows below are the authority — they carry every "
          + "character, including anything the stored markup hides from a "
          + "reader."} />
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
