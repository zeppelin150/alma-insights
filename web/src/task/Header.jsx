import React from "react";
import { Avatar, AvatarStack } from "./avatars.jsx";

// Task header: completed banner, action row, title, meta rows. Pure renderer —
// every affordance relays through props; ids/payloads are validated Python-side.

function Pill({ pill }) {
  if (!pill) return null;
  return (
    <span className={`tk-pill tk-pill--${pill.color || "gray"}`}>{pill.text}</span>
  );
}

function MetaRow({ label, children }) {
  return (
    <div className="tk-meta-row">
      <span className="tk-meta-label">{label}</span>
      <span className="tk-meta-value">{children}</span>
    </div>
  );
}

export default function Header({
  header, capabilities, busy,
  onToggleComplete, onRefresh, onOpenUrl, onSetDue,
}) {
  const h = header;
  const stack = h.assignee ? [h.assignee, ...h.collaborators] : h.collaborators;
  return (
    <div className="tk-header">
      {h.completed && <div className="tk-banner">✓ Completed</div>}
      <div className="tk-actions">
        <button
          type="button"
          className={`tk-complete-btn${h.completed ? " tk-complete-btn--done" : ""}`}
          disabled={!capabilities.complete || busy}
          onClick={() => onToggleComplete && onToggleComplete(!h.completed)}
        >
          {h.completed ? "✓ Completed" : "✓ Mark complete"}
        </button>
        <Pill pill={h.status_pill} />
        <span className="tk-actions-spacer" />
        {h.freshness && <span className="tk-freshness">{h.freshness}</span>}
        {capabilities.refresh && (
          <button
            type="button"
            className="tk-icon-btn"
            title="Refresh from Asana"
            disabled={busy}
            onClick={() => onRefresh && onRefresh()}
          >
            ↻
          </button>
        )}
        {h.permalink && (
          <a
            className="tk-permalink"
            href={h.permalink}
            onClick={(e) => {
              e.preventDefault();
              if (onOpenUrl) onOpenUrl(h.permalink);
            }}
          >
            Open in Asana ›
          </a>
        )}
        <AvatarStack people={stack} size={28} />
      </div>
      <h1 className="tk-title">{h.title || "—"}</h1>
      <div className="tk-meta">
        <MetaRow label="Assignee">
          {h.assignee ? (
            <span className="tk-assignee">
              <Avatar person={h.assignee} size={24} />
              <span className="tk-assignee-name">{h.assignee.name}</span>
              <span className="tk-assignee-status">Recently assigned ▾</span>
            </span>
          ) : (
            <span className="tk-empty">—</span>
          )}
        </MetaRow>
        <MetaRow label="Due date">
          {h.due_display ? (
            <span className={`tk-due${h.overdue ? " tk-due--overdue" : ""}`}>
              {h.due_display}
            </span>
          ) : (
            <span className="tk-empty">—</span>
          )}
          {capabilities.due && (
            <input
              key={h.due_iso}
              type="date"
              className="tk-due-input"
              defaultValue={h.due_iso}
              disabled={busy}
              onChange={(e) => onSetDue && onSetDue(e.currentTarget.value)}
              title="Change due date"
            />
          )}
        </MetaRow>
        {h.projects.length > 0 && (
          <MetaRow label="Projects">
            <span className="tk-projects">
              {h.projects.map((p, i) => (
                <span key={p.board + i} className="tk-project">
                  <span className="tk-project-board">{p.board}</span>
                  {p.section && (
                    <span className="tk-project-section"> · {p.section} ▾</span>
                  )}
                </span>
              ))}
            </span>
          </MetaRow>
        )}
      </div>
    </div>
  );
}
