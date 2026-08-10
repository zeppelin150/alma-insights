// Guru-look preview of a pending draft (WS-D-WEB M4, first increment): the
// mirror-fidelity treatment — Zendesk Garden clone → Asana task panel → now
// the Guru card — scoped to the chat review lane. RENDERING ONLY.
//
// The one rule that matters: this component NEVER touches d.publish_body.
// Those bytes are deliberately unsanitized (Guru native blocks depend on
// class=/data-ghq-* the strict sanitizer strips) and render exclusively as
// escaped text in PublishBytesPanel. What the frame shows is
// d.preview_srcdoc — produced Python-side by sanitize_html_preview, fit ONLY
// for a sandbox="" iframe — and d.preview_notes names every way that preview
// differs from the exact bytes. The bytes panel remains the approval object;
// this popup exists so style-only changes (invisible in the word-diff by
// design) have somewhere to be seen.
//
// Visual tokens are PROVISIONAL pending owner screenshots — the authority is
// ~/.claude/plans/guru-ui-dossier.md (callout RGBA palette + block contracts
// are verbatim-from-Guru; chrome metrics and fonts are approximations).

import { useState } from "react";

// Iframe-internal stylesheet. Callout backgrounds arrive as INLINE styles on
// the section element (Guru's own contract) — never double-paint them here.
const GURU_CARD_CSS = `
  body { margin: 0; padding: 20px 24px; background: #ffffff;
         font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #2E2E2E; font-size: 13px; line-height: 148%; }
  h1 { font-size: 22px; font-weight: 700; color: #1A1A1A; margin: 0 0 10px; }
  h2 { font-size: 17px; font-weight: 700; color: #1A1A1A; margin-top: 16px; }
  h3 { font-size: 14px; font-weight: 700; color: #1A1A1A; margin-top: 12px; }
  h4, h5, h6 { font-size: 13px; font-weight: 700; color: #1A1A1A; margin-top: 10px; }
  p, li { font-size: 13px; line-height: 148%; }
  a { color: #1D6FA5; text-decoration: none; }
  code { background: #F7F5F0; font-family: Consolas, monospace; font-size: 12px; }
  pre { background: #F7F5F0; padding: 8px; font-family: Consolas, monospace;
        font-size: 12px; overflow-x: auto; }
  pre code { background: transparent; }
  blockquote { color: #4A4A4A; margin: 6px 0; padding-left: 10px;
               border-left: 3px solid #0D7D72; }
  table { border-collapse: collapse; border: 1px solid #D6D2CA; }
  th { background: #F7F5F0; font-weight: 700; padding: 4px 8px; border: 1px solid #D6D2CA; }
  td { padding: 4px 8px; border: 1px solid #E8E5DE; }
  hr { border: none; border-top: 1px solid #D6D2CA; }
  ul, ol { margin: 6px 0; padding-left: 22px; }
  img { max-width: 100%; }
  del, s, strike { text-decoration: line-through; color: #4A4A4A; }
  .ghq-card-content__callout { padding: 12px 16px; margin: 10px 0; border-radius: 6px; }
  .ghq-card-content__collapsible { border: 1px solid #D6D2CA; border-radius: 6px;
                                   margin: 10px 0; padding: 6px 10px; }
  .ghq-card-content__collapsible-summary { font-weight: 700; color: #1A1A1A; cursor: pointer; }
  .ghq-card-content__collapsible-content { padding-top: 6px; }
  .ghq-card-content__guru-card { color: #0D7D72; font-weight: 600; text-decoration: none;
    border: 1px solid #D6D2CA; border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
  .ghq-card-content__guru-card::before { content: "G "; font-weight: 800; }
`;

export function guruCardDoc(body) {
  return (
    '<!doctype html><html><head><meta charset="utf-8"><style>' +
    GURU_CARD_CSS +
    "</style></head><body>" +
    (body || "") +
    "</body></html>"
  );
}

export function GuruCardPreview({ d, onClose }) {
  const has =
    typeof d.preview_srcdoc === "string" && d.preview_srcdoc.length > 0;
  const notes = Array.isArray(d.preview_notes) ? d.preview_notes : [];
  return (
    <div className="drawer-overlay gpv-overlay" onClick={onClose}>
      <div className="gpv-card" onClick={(e) => e.stopPropagation()}>
        <div className="gpv-hdr">
          <span className="gpv-title">{d.title}</span>
          {/* A pending draft is by definition unverified in Guru terms —
              never fabricate a Verified state (dossier §d.5). */}
          <span className="gpv-verify">Needs verification</span>
          <button className="gpv-close" onClick={onClose} title="Close preview">
            ×
          </button>
        </div>
        {has ? (
          <iframe
            className="gpv-frame"
            sandbox=""
            title="Guru card preview"
            srcDoc={guruCardDoc(d.preview_srcdoc)}
          />
        ) : (
          <div className="gpv-empty">
            No preview is available for this draft — read the exact bytes
            panel on the review card instead.
          </div>
        )}
        <div className={"gpv-note" + (notes.length ? " warn" : "")}>
          {notes.length
            ? "This preview differs from the exact bytes in " +
              notes.length +
              (notes.length === 1 ? " way" : " ways") +
              " — what you approve is the bytes panel, not this rendering."
            : "Rendered from the same bytes you approve. Styling is a preview approximation."}
        </div>
      </div>
    </div>
  );
}

// Button + popup pair for the DraftCard's action row: state lives here so
// ChatApp.jsx (whose source is locked frame-free by test_chat_review_panel)
// only composes components and never carries an iframe.
export function PreviewCardButton({ d }) {
  const [open, setOpen] = useState(false);
  const has =
    typeof d.preview_srcdoc === "string" && d.preview_srcdoc.length > 0;
  return (
    <>
      <button
        className="dpreview"
        disabled={!has}
        title={
          has
            ? "See this draft as it will look in Guru."
            : "No preview available — read the exact bytes panel."
        }
        onClick={() => setOpen(true)}
      >
        Preview card
      </button>
      {open ? <GuruCardPreview d={d} onClose={() => setOpen(false)} /> : null}
    </>
  );
}
