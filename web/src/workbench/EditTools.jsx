import { useEffect, useRef, useState } from "react";

// M4 editing chrome for the web Workbench: the AI-edit bar (slash-menu
// equivalent) and the Tools menu (Import / Push to Guru / Save to Drive).
// Pure renderers — every action only ASKS over the bridge; validation, native
// confirms, and the actual work all live in Python.

const CANNED = [
  ["Tighten", "Tighten this up — keep the meaning, cut the filler."],
  ["Fix grammar", "Fix grammar and spelling without changing the meaning."],
  ["Simplify", "Simplify the language for a new support agent."],
];

export function AiEditBar({ busy, disabled, onAsk }) {
  const [text, setText] = useState("");

  function ask(instruction) {
    const t = (instruction || "").trim();
    if (!t || busy || disabled) return;
    onAsk(t);
    setText("");
  }

  return (
    <div className="wb-ai-bar">
      <span className="wb-ai-label">{busy ? "Renn is revising…" : "Ask Renn"}</span>
      {CANNED.map(([label, instruction]) => (
        <button key={label} className="wb-btn wb-ai-canned" disabled={busy || disabled}
                onClick={() => ask(instruction)}>{label}</button>
      ))}
      <input className="wb-ai-input" value={text} disabled={busy || disabled}
             placeholder="…or describe an edit (uses your selection if any)"
             onChange={(e) => setText(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && ask(text)} />
      <button className="wb-btn primary" disabled={busy || disabled || !text.trim()}
              onClick={() => ask(text)}>Ask</button>
    </div>
  );
}

// Two-level dropdown: top-level actions plus the lazy existing-cards submenu
// (listExistingCards → existingCards push → rendered here).
export function ToolsMenu({ disabled, existingCards, onImport, onPublish,
                            onListExisting }) {
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState(null);   // "existing" | null
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
        setSub(null);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function item(label, fn, opts = {}) {
    return (
      <button key={label} className={"wb-menu-item" + (opts.sub ? " has-sub" : "")}
              onClick={() => {
                if (opts.sub) {
                  setSub(sub === opts.sub ? null : opts.sub);
                  if (opts.sub === "existing") onListExisting();
                  return;
                }
                setOpen(false);
                setSub(null);
                fn();
              }}>
        {label}{opts.sub ? <span className="wb-menu-caret">›</span> : null}
      </button>
    );
  }

  return (
    <span className="wb-menu-root" ref={rootRef}>
      <button className="wb-btn" disabled={disabled}
              onClick={() => { setOpen(!open); setSub(null); }}>Tools ▾</button>
      {open && (
        <div className="wb-menu">
          <div className="wb-menu-hd">Import</div>
          {item("Google Doc / Drive URL…", () => onImport("drive"))}
          {item("Existing Guru card…", () => onImport("guru"))}
          <div className="wb-menu-hd">Push to Guru</div>
          {item("New Guru card", () => onPublish("guru_new"))}
          {item("Existing Guru card", null, { sub: "existing" })}
          {sub === "existing" && (
            <div className="wb-submenu" data-testid="existing-cards">
              {existingCards === null && <div className="wb-menu-note">Loading cards…</div>}
              {Array.isArray(existingCards) && existingCards.length === 0 && (
                <div className="wb-menu-note">No cards found.</div>
              )}
              {Array.isArray(existingCards) && existingCards.map((c) => (
                <button key={c.key} className="wb-menu-item wb-menu-card"
                        onClick={() => {
                          setOpen(false);
                          setSub(null);
                          onPublish("guru_existing:" + c.key);
                        }}>{c.title}</button>
              ))}
            </div>
          )}
          <div className="wb-menu-hd">Save to Drive</div>
          {item("New Google Doc", () => onPublish("drive_new"))}
          {item("Update existing doc", () => onPublish("drive_update"))}
        </div>
      )}
    </span>
  );
}
