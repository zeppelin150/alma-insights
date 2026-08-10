import React from "react";

// Hook-free avatar primitives shared by header, field grid, subtasks and the
// activity feed. Color arrives precomputed in the viewmodel (deterministic
// per-user hash lives Python-side); absent → neutral gray.

const FALLBACK = "#c7c4c4";

export function Avatar({ person, size = 24 }) {
  if (!person) return null;
  const style = {
    width: size,
    height: size,
    fontSize: Math.max(9, Math.round(size * 0.42)),
    background: person.color || FALLBACK,
  };
  return (
    <span className="tk-avatar" style={style} title={person.name}>
      {person.initials}
    </span>
  );
}

export function AvatarStack({ people, size = 28, max = 4 }) {
  const list = Array.isArray(people) ? people.filter(Boolean) : [];
  if (!list.length) return null;
  const shown = list.slice(0, max);
  const extra = list.length - shown.length;
  return (
    <span className="tk-avatar-stack">
      {shown.map((p, i) => (
        <Avatar key={p.name + i} person={p} size={size} />
      ))}
      {extra > 0 && (
        <span
          className="tk-avatar tk-avatar-more"
          style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.38)) }}
        >
          +{extra}
        </span>
      )}
    </span>
  );
}
