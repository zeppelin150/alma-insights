import React from "react";

// Subtask checklist + composer. Promoted subtasks (mig 056) carry the ↳
// marker. The composer is an uncontrolled form — submit relays the text and
// resets; creation authority lives entirely Python-side.

export default function Subtasks({ subtasks, capabilities, busy, onAdd }) {
  const list = Array.isArray(subtasks) ? subtasks : [];
  if (!list.length && !capabilities.subtask) return null;
  return (
    <div className="tk-section tk-subtasks">
      <div className="tk-section-label">Subtasks</div>
      {list.map((s, i) => (
        <div key={s.gid || s.name + i} className="tk-subtask-row">
          <span className={`tk-subcheck${s.done ? " tk-subcheck--done" : ""}`}>✓</span>
          <span className={`tk-subtask-name${s.done ? " tk-subtask-name--done" : ""}`}>
            {s.promoted && <span className="tk-subtask-promoted">↳ </span>}
            {s.name}
          </span>
          {s.assignee && <span className="tk-subtask-meta">{s.assignee}</span>}
          {s.due && <span className="tk-subtask-meta">{s.due}</span>}
        </div>
      ))}
      {capabilities.subtask && (
        <form
          className="tk-subtask-composer"
          onSubmit={(e) => {
            e.preventDefault();
            const box = e.currentTarget.elements.subtask;
            const text = (box && box.value ? box.value : "").trim();
            if (text && onAdd && !busy) {
              onAdd(text);
              e.currentTarget.reset();
            }
          }}
        >
          <span className="tk-subcheck tk-subcheck--ghost">✓</span>
          <input
            name="subtask"
            className="tk-subtask-input"
            placeholder="Type to add a subtask…"
            disabled={busy}
            autoComplete="off"
          />
        </form>
      )}
    </div>
  );
}
