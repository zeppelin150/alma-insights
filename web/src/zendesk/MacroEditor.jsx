// Admin Center macro editor clone: Macro name / Description / Available for,
// then the actions stack — each row [action-type dropdown look][value
// control], with the value control kind per the documented action table
// (Comment/description = rich text with the placeholders affordances,
// enumerated ticket fields = dropdown look, subject/tags = text look).
// Everything is a read-only rendering of mirror bytes; the live controls
// are Copy exact and the revision links. Placeholders render highlighted
// but always as escaped text.
import { actionInputKind } from "./shape.js";

// Split "Hi {{ticket.requester.first_name}}," into text + placeholder spans
// (escaped React children either way — the highlight is cosmetic).
function placeholderSpans(value) {
  const text = String(value == null ? "" : value);
  const parts = text.split(/(\{\{[^}]+\}\})/g);
  return parts.map((p, i) =>
    /^\{\{[^}]+\}\}$/.test(p)
      ? <span key={i} className="ph">{p}</span>
      : <span key={i}>{p}</span>
  );
}

function ActionValue({ action }) {
  const kind = actionInputKind(action.field);
  if (kind === "rich") {
    return (
      <div>
        <div className="zd-rich">
          <div className="zd-rich-bar">
            {[["B", "b"], ["U", "u"], ["I", "i"], ["Link"], ["Image"]].map(([label, cls]) => (
              <button key={label} className={"zd-ed-tool" + (cls ? " " + cls : "")}
                      disabled>{label}</button>
            ))}
          </div>
          <div className="zd-rich-body">{placeholderSpans(action.value)}</div>
        </div>
        <div className="zd-under-note">
          <span><span className="zd-check" aria-hidden="true" /> Include plain text fallback</span>
          <button className="zd-btn basic" disabled>View available placeholders</button>
        </div>
      </div>
    );
  }
  if (kind === "select") {
    return <span className="zd-select-look">{String(action.value == null ? "" : action.value)}</span>;
  }
  return <div className="zd-static">{placeholderSpans(action.value)}</div>;
}

export default function MacroEditor({ macro, onBack, onCopy, onOpenRevision }) {
  if (!macro) return null;
  const actions = Array.isArray(macro.actions) ? macro.actions : [];
  const revisions = Array.isArray(macro.revisions) ? macro.revisions : [];
  const hasReply = actions.some((a) => String(a.field || "").includes("comment_value"));
  return (
    <div className="zd-editor">
      <div className="zd-editor-main">
        <div className="zd-editor-top">
          <button className="zd-back" onClick={onBack} title="Back to Macros">‹</button>
          <span className="zd-editor-title">{macro.name}</span>
          {macro.active
            ? <span className="zd-tag softgreen">Active</span>
            : <span className="zd-tag grey">Inactive</span>}
          <span className="zd-hdr-spacer" />
          <button className="zd-btn" onClick={() => onCopy("macro_name")}
                  title="Copy the exact stored macro name">Copy name</button>
          <button className="zd-btn" onClick={() => onCopy("macro_reply")}
                  disabled={!hasReply}
                  title="Copy the exact stored Comment/Reply text">Copy reply</button>
        </div>
        <div className="zd-form">
          <div className="zd-field">
            <div className="zd-field-label">Macro name<span className="req">*</span></div>
            <div className="zd-static">{macro.name}</div>
          </div>
          <div className="zd-field">
            <div className="zd-field-label">Description</div>
            <div className={"zd-static" + (macro.description ? "" : " zd-set-value muted")}>
              {macro.description || "Optional"}
            </div>
          </div>
          <div className="zd-field">
            <div className="zd-field-label">Available for</div>
            <span className="zd-select-look">All agents</span>
          </div>
          <div className="zd-actions-hd">Actions</div>
          {actions.length === 0 && (
            <div className="zd-empty">No actions stored for this macro.</div>
          )}
          {actions.map((a, i) => (
            <div key={i} className="zd-action-row">
              <span className="zd-select-look zd-action-type">{a.display || a.field}</span>
              <div className="zd-action-value"><ActionValue action={a} /></div>
              <button className="zd-action-x" disabled title="Read-only mirror">×</button>
            </div>
          ))}
          <button className="zd-btn" disabled
                  title="Read-only mirror — edit macros in real Zendesk">Add action</button>
          <div style={{ marginTop: 20 }}>
            <button className="zd-btn primary lg" disabled
                    title="Read-only mirror — use Copy exact and paste into real Zendesk">
              Save
            </button>
          </div>
          {revisions.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <div className="zd-actions-hd">Revisions</div>
              {revisions.map((r) => (
                <div key={r.draft_id} className="zd-set-row">
                  <button className="zd-btn basic"
                          onClick={() => onOpenRevision(r.draft_id, r.status)}>
                    {r.name || "Revision " + r.draft_id}
                  </button>
                  <span className="zd-cell-meta"> {r.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
