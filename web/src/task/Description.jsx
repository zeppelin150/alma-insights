import React from "react";

// The ONLY HTML renderer on the task route. The controller sanitizes
// html_notes Python-side (sanitize_html_preview) before it ever reaches the
// viewmodel; this frame adds the second wall: sandbox="" = no scripts, no
// same-origin, no forms, no popups, no top-level navigation. Links render in
// Asana blue but cannot navigate from inside the sandbox — matching the
// Zendesk clone's accepted trade-off for preview frames.
//
// Editing: the textarea round-trips MARKDOWN (the same html↔md conversion
// the Renn lanes use); Save relays it and Python serializes to Asana's
// html_notes dialect behind the CAS-guarded, remote-first lane. No HTML is
// ever composed in JS.

const FRAME_CSS = `
  html, body { margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 14px; line-height: 20px; color: #1e1f21; background: #ffffff;
  }
  .task-notes { padding: 2px 0; }
  p { margin: 0 0 8px; }
  strong, b { font-weight: 600; }
  a { color: #4573d2; text-decoration: none; }
  a:hover { text-decoration: underline; }
  ul, ol { margin: 0 0 8px; padding-left: 22px; }
  li { margin: 2px 0; }
  h1, h2, h3, h4 { font-size: 14px; font-weight: 600; margin: 10px 0 4px; }
  img { max-width: 100%; height: auto; }
  code, pre {
    font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 12px; background: #f9f8f8; border-radius: 4px;
  }
  pre { padding: 8px; overflow-x: auto; }
  blockquote { margin: 0 0 8px; padding-left: 10px; border-left: 2px solid #edeae9; color: #6d6e6f; }
  table { border-collapse: collapse; }
  td, th { border: 1px solid #edeae9; padding: 4px 8px; }
`;

export default function Description({
  srcdoc, markdown, canEdit, editing, busy, status, onEdit, onCancel, onSave,
}) {
  if (!srcdoc && !canEdit) return null;
  const doc =
    `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta name="color-scheme" content="light">` +
    `<style>${FRAME_CSS}</style></head><body>` +
    `<div class="task-notes">${srcdoc || ""}</div></body></html>`;
  return (
    <div className="tk-section tk-description">
      <div className="tk-desc-head">
        <span className="tk-section-label">Description</span>
        {canEdit && !editing && (
          <button type="button" className="tk-desc-edit-btn" disabled={busy}
                  onClick={onEdit}>
            Edit
          </button>
        )}
      </div>
      {editing ? (
        <form
          className="tk-desc-editor"
          onSubmit={(e) => {
            e.preventDefault();
            const box = e.currentTarget.elements.desc;
            if (onSave && !busy) onSave(box && box.value != null ? box.value : "");
          }}
        >
          <textarea
            name="desc"
            className="tk-desc-input"
            defaultValue={markdown}
            rows={10}
            disabled={busy}
          />
          <div className="tk-desc-editor-bar">
            {status && <span className="tk-desc-status">{status}</span>}
            <button type="button" className="tk-desc-cancel" disabled={busy}
                    onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="tk-comment-btn" disabled={busy}>
              Save
            </button>
          </div>
        </form>
      ) : srcdoc ? (
        <iframe
          className="tk-notes-frame"
          title="Task description (sandboxed)"
          sandbox=""
          srcDoc={doc}
        />
      ) : (
        <button type="button" className="tk-desc-empty" disabled={busy}
                onClick={onEdit}>
          What is this task about?
        </button>
      )}
    </div>
  );
}
