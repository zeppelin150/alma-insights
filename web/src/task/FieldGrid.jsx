import React from "react";
import { Avatar } from "./avatars.jsx";

// Custom-field grid: name left / typed value right, "—" for empty, and the
// "Hide custom fields" collapse link. Value rendering is a table-driven map
// keyed on the field kind — never an if/elif ladder.

const DASH = <span className="tk-empty">—</span>;

function pillsOrDash(f) {
  if (!f.pills.length) return f.value ? <span>{f.value}</span> : DASH;
  return (
    <span className="tk-field-pills">
      {f.pills.map((p, i) => (
        <span key={p.text + i} className={`tk-pill tk-pill--${p.color || "gray"}`}>
          {p.text}
        </span>
      ))}
    </span>
  );
}

function peopleOrDash(f) {
  if (!f.people.length) return DASH;
  return (
    <span className="tk-field-people">
      {f.people.map((p, i) => (
        <span key={p.name + i} className="tk-field-person">
          <Avatar person={p} size={20} />
          <span>{p.name}</span>
        </span>
      ))}
    </span>
  );
}

function checkboxGlyph(f) {
  return (
    <span className={`tk-checkbox${f.checked ? " tk-checkbox--on" : ""}`}>
      {f.checked ? "☑" : "☐"}
    </span>
  );
}

function textOrDash(f) {
  return f.value ? <span>{f.value}</span> : DASH;
}

const RENDERERS = {
  enum: pillsOrDash,
  multi_enum: pillsOrDash,
  people: peopleOrDash,
  checkbox: checkboxGlyph,
  date: textOrDash,
  number: textOrDash,
  text: textOrDash,
};

export default function FieldGrid({ fields, hidden, onToggleHidden }) {
  const list = Array.isArray(fields) ? fields : [];
  if (!list.length) return null;
  return (
    <div className="tk-section tk-fields">
      <div className="tk-section-label">Fields</div>
      {!hidden &&
        list.map((f, i) => (
          <div key={f.gid || f.name + i} className="tk-field-row">
            <span className="tk-field-name">{f.name}</span>
            <span className="tk-field-value">
              {(RENDERERS[f.kind] || textOrDash)(f)}
            </span>
          </div>
        ))}
      <button type="button" className="tk-fields-toggle" onClick={onToggleHidden}>
        {hidden ? "Show custom fields" : "Hide custom fields"}
      </button>
    </div>
  );
}
