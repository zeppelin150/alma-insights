// Revision Center: Renn's proposed drafts in status lanes (pending → ready →
// copied) with the specialist's review controls. Every button only ASKS —
// status transitions, deletes and clipboard copies are validated and gated
// Python-side (delete gets a NATIVE confirm; pending drafts can never reach
// the clipboard). Buttons here mirror those gates so the UI doesn't invite
// clicks the controller will silently refuse.
import RevisionDiff from "./RevisionDiff.jsx";
import {
  canCopyDraft, canMarkCopied, canMarkReady, canSaveDraft, filterRevisions,
  groupRevisions, keyActivate, statusInfo,
} from "./shape.js";

const FILTERS = [["open", "Open"], ["copied", "Copied"], ["all", "All"]];

function RevCard({ rev, selected, onOpen }) {
  const st = statusInfo(rev.status);
  return (
    <div className={"zd-rev-card" + (selected ? " selected" : "")}
         role="button" tabIndex={0}
         onClick={() => onOpen(rev.draft_id, rev.kind)}
         onKeyDown={keyActivate(() => onOpen(rev.draft_id, rev.kind))}>
      <div className="zd-rev-top">
        <span className={"zd-tag " + (rev.kind === "macro" ? "kale" : "blue")}>
          {rev.kind === "macro" ? "Macro" : "Article"}
        </span>
        <span className="zd-rev-title">{rev.title}</span>
        {rev.is_new && <span className="zd-tag softgreen">New content</span>}
        <span className={"zd-tag soft" + st.cls}>{st.label}</span>
      </div>
      <div className="zd-rev-target">
        {rev.target_title ? "Updates: " + rev.target_title : "Proposed new " + rev.kind}
      </div>
      {rev.rationale && <div className="zd-rev-rationale">{rev.rationale}</div>}
      {Array.isArray(rev.sources) && rev.sources.length > 0 && (
        <div className="zd-rev-sources">
          {rev.sources.map((s, i) => (
            <span key={i} className="zd-tag grey" title={s.ref}>{s.label || s.ref}</span>
          ))}
        </div>
      )}
      <div className="zd-rev-meta">
        Proposed {rev.created_display}
        {rev.copied_display ? " · copied " + rev.copied_display : ""}
      </div>
    </div>
  );
}

export function RevisionDetail({
  rev, diff, onSave, onMarkReady, onMarkCopied, onCopy, onDelete, busy,
}) {
  const st = statusInfo(rev.status);
  const copyOk = canCopyDraft(rev.status);
  const copyHint = copyOk ? "" : "Mark the draft ready first — pending drafts never reach the clipboard";
  return (
    <div className="zd-rev-detail">
      <div className="zd-rev-detail-hd">
        <span className="zd-rev-title">{rev.title}</span>
        <span className={"zd-tag soft" + st.cls}>{st.label}</span>
        <span className="zd-hdr-spacer" />
        <span className="zd-cell-meta">
          {rev.kind === "macro" ? "Macro draft " : "Article draft "}{rev.draft_id}
        </span>
      </div>
      <div className="zd-rev-detail-body">
        {canSaveDraft(rev.status) && (
          <RenameRow rev={rev} onSave={onSave} busy={busy} />
        )}
        <RevisionDiff diff={diff} />
      </div>
      <div className="zd-rev-actions">
        {canMarkReady(rev.status) && (
          <button className="zd-btn" disabled={busy}
                  onClick={() => onMarkReady(rev.kind, rev.draft_id)}>Mark ready</button>
        )}
        {canMarkCopied(rev.status) && (
          <button className="zd-btn primary" disabled={busy}
                  title="After pasting into real Zendesk by hand"
                  onClick={() => onMarkCopied(rev.kind, rev.draft_id)}>Mark as copied</button>
        )}
        {rev.kind === "article" ? (
          <>
            <button className="zd-btn" disabled={busy || !copyOk} title={copyHint}
                    onClick={() => onCopy(rev.kind, rev.draft_id, "title")}>Copy title</button>
            <button className="zd-btn" disabled={busy || !copyOk} title={copyHint}
                    onClick={() => onCopy(rev.kind, rev.draft_id, "body_html")}>Copy HTML</button>
            <button className="zd-btn" disabled={busy || !copyOk} title={copyHint}
                    onClick={() => onCopy(rev.kind, rev.draft_id, "body_rich")}>Copy rich text</button>
          </>
        ) : (
          <>
            <button className="zd-btn" disabled={busy || !copyOk} title={copyHint}
                    onClick={() => onCopy(rev.kind, rev.draft_id, "macro_name")}>Copy name</button>
            <button className="zd-btn" disabled={busy || !copyOk} title={copyHint}
                    onClick={() => onCopy(rev.kind, rev.draft_id, "macro_reply")}>Copy reply</button>
          </>
        )}
        <span className="zd-hdr-spacer" />
        <button className="zd-btn danger" disabled={busy}
                onClick={() => onDelete(rev.kind, rev.draft_id)}>Delete</button>
      </div>
    </div>
  );
}

// Small rename affordance — the one payload edit the surface offers. The
// keys sent are inside the js_save_draft allowlist (article: title;
// macro: name); everything else is Python's.
function RenameRow({ rev, onSave, busy }) {
  return (
    <form className="zd-rename"
          onSubmit={(ev) => {
            ev.preventDefault();
            const field = ev.currentTarget.elements.zdRename;
            const text = (field.value || "").trim();
            if (!text || text === rev.title) return;
            onSave(rev.kind, rev.draft_id,
                   rev.kind === "macro" ? { name: text } : { title: text });
          }}>
      <input className="zd-input" name="zdRename" defaultValue={rev.title}
             maxLength={255} aria-label="Revision title" />
      <button className="zd-btn" type="submit" disabled={busy}>Rename</button>
    </form>
  );
}

export default function RevisionCenter({
  revisions, filter, diff, activeDraft,
  onFilter, onOpen, onDiff, onSave, onMarkReady, onMarkCopied, onCopy,
  onDelete, busy,
}) {
  const visible = filterRevisions(revisions, filter);
  const lanes = groupRevisions(visible);
  const active = activeDraft
    ? (Array.isArray(revisions) ? revisions : []).find(
        (r) => r.draft_id === activeDraft.draft_id && r.kind === activeDraft.kind)
    : null;
  return (
    <div className="zd-content" style={{ flexDirection: "column" }}>
      <div className="zd-toolbar">
        <span className="zd-page-title">Revision Center</span>
        <span className="zd-cell-meta">
          Renn proposes; you review, copy exact, and paste into Zendesk by hand.
        </span>
        <span className="zd-hdr-spacer" />
        {FILTERS.map(([key, label]) => (
          <button key={key}
                  className={"zd-btn" + (filter === key ? " primary" : " basic")}
                  onClick={() => onFilter(key)}>{label}</button>
        ))}
      </div>
      <div className="zd-rc">
        <div className="zd-rc-lanes">
          {visible.length === 0 && (
            <div className="zd-empty">
              No revisions here yet. Ask Renn to propose an article or macro
              update — drafts land in the Pending lane.
            </div>
          )}
          {lanes.map((lane) => (
            <div key={lane.key} className="zd-lane">
              <div className="zd-lane-hd">
                {lane.label}<span className="n">{lane.items.length}</span>
              </div>
              {lane.items.length === 0
                ? <div className="zd-lane-empty">Nothing {lane.key} right now.</div>
                : lane.items.map((rev) => (
                    <RevCard key={rev.kind + ":" + rev.draft_id} rev={rev}
                             selected={!!active && active.draft_id === rev.draft_id &&
                                       active.kind === rev.kind}
                             onOpen={(id, kind) => { onOpen(id, kind); onDiff(kind, id); }} />
                  ))}
            </div>
          ))}
        </div>
        {active && (
          // Keyed by kind+draft_id so switching drafts REMOUNTS the detail:
          // RenameRow's uncontrolled input would otherwise keep the previous
          // draft's title (React ignores defaultValue on a reused node) and
          // a submit could rename the wrong draft.
          <RevisionDetail key={active.kind + ":" + active.draft_id}
                          rev={active} diff={diff} onSave={onSave}
                          onMarkReady={onMarkReady} onMarkCopied={onMarkCopied}
                          onCopy={onCopy} onDelete={onDelete} busy={busy} />
        )}
      </div>
    </div>
  );
}
