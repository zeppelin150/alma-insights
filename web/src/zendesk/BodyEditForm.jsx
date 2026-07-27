// Plain-text body editor for specialist edits (v1: articles only — no rich
// toolbar, no HTML anywhere). Uncontrolled textarea (the RenameRow pattern)
// so the host components stay hook-free and directly invokable in tests.
// The bridge payload built from this form carries ONLY the "body" key;
// Python recomputes body_html as sanitize_html(markdown_to_html(body)), so
// nothing typed here can ever become live markup.
import { BODY_EDIT_MAX } from "./shape.js";

export default function BodyEditForm({ seed, label, onSave, onCancel, busy }) {
  return (
    <form className="zd-body-edit"
          onSubmit={(ev) => {
            ev.preventDefault();
            const field = ev.currentTarget.elements.zdBody;
            const text = field.value ?? "";
            // Unchanged or empty saves are no-ops — close the editor instead
            // of round-tripping a draft identical to its target.
            if (!text.trim() || text === (seed || "")) { onCancel(); return; }
            onSave(text);
          }}>
      <textarea className="zd-body-textarea" name="zdBody"
                defaultValue={seed || ""} maxLength={BODY_EDIT_MAX}
                aria-label={label} rows={16}
                onKeyDown={(ev) => { if (ev.key === "Escape") onCancel(); }} />
      <div className="zd-body-edit-actions">
        <button className="zd-btn primary" type="submit" disabled={busy}>Save</button>
        <button className="zd-btn basic" type="button" onClick={onCancel}>Cancel</button>
        <span className="zd-cell-meta">
          Plain text or Markdown — saving creates a pending revision; the
          mirrored content is never modified.
        </span>
      </div>
    </form>
  );
}
