// The copy affordance shared by the article editor and the Revision Center.
//
// OWNER CORRECTION (2026-07-26): content specialists and managers copy the
// DRAFTED CONTENT — the prose as authored and reviewed — not verbatim HTML.
// So the primary, visually-dominant button copies drafted content and the
// HTML/rich/title flavours live in a secondary overflow. They stay one click
// away for the cases that want them; they are simply no longer co-equal.
//
// Every button only ASKS Python. The clipboard, the review gate and (for
// bytes this page authored) a NATIVE confirm all live behind js_copy_field,
// so nothing here may report success: `pending` says a request is in flight
// and disables the controls, which is also what stops a second click from
// raising a second confirm. A refused or cancelled copy never resolves, so
// the caller's busy flag self-clears — never this component's business.
import { COPY_DRAFTED_FIELD } from "./shape.js";

// Close the <details> overflow when an item is chosen (the native confirm,
// if any, takes over from here).
function closeMenu(ev) {
  const d = ev && ev.currentTarget && ev.currentTarget.closest
    ? ev.currentTarget.closest("details") : null;
  if (d) d.removeAttribute("open");
}

export default function CopyControls({
  primaryLabel, primaryField, primaryTitle, items, onCopy, disabled, pending,
  menuTitle,
}) {
  const secondary = Array.isArray(items) ? items : [];
  const off = !!disabled || !!pending;
  return (
    <span className="zd-copy-controls">
      <button type="button" className="zd-btn primary" disabled={off}
              title={primaryTitle}
              onClick={() => onCopy(primaryField || COPY_DRAFTED_FIELD)}>
        {primaryLabel || "Copy content"}
      </button>
      {secondary.length > 0 && (
        <details className="zd-menu zd-copy-menu">
          <summary className="zd-btn basic"
                   title={menuTitle ||
                     "Other copy formats — the exact stored HTML and the title"}>
            More ▾
          </summary>
          <div className="zd-menu-items" role="menu">
            <div className="zd-menu-hd">
              Copies the exact stored bytes, not the drafted prose.
            </div>
            {secondary.map((it) => (
              <button key={it.field} type="button" className="zd-menu-item"
                      role="menuitem" disabled={off} title={it.title}
                      onClick={(ev) => { closeMenu(ev); onCopy(it.field); }}>
                {it.label}
              </button>
            ))}
          </div>
        </details>
      )}
      {pending && (
        // Honest in-flight wording: a copy of page-authored bytes opens a
        // native confirm, and until it resolves NOTHING is on the clipboard.
        <span className="zd-copy-pending" role="status">
          Waiting for the copy confirmation in the app window…
        </span>
      )}
    </span>
  );
}
