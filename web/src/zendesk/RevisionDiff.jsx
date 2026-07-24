// Word-level diff renderer over the controller's diff_ready payload (rows
// identical to the workbench DiffBody shape: ctx/equal rows, change rows
// with equal/del/add spans, plus whole add/del rows). Everything renders as
// escaped React children — never live markup.

function gutterFor(tag) {
  if (tag === "add") return "+";
  if (tag === "del") return "−";
  if (tag === "change") return "±";
  return " ";
}

export default function RevisionDiff({ diff }) {
  if (!diff) return <div className="zd-empty">Loading diff…</div>;
  if (!diff.baseline_present) {
    return (
      <div className="zd-empty">
        New content — no mirrored baseline to compare against.
      </div>
    );
  }
  const rows = Array.isArray(diff.rows) ? diff.rows : [];
  const title = diff.title || {};
  return (
    <div className="zd-diff">
      <div className="zd-diff-hd">
        {diff.change_count} changed line{diff.change_count === 1 ? "" : "s"} vs the mirror
      </div>
      {title.changed && (
        <div className="zd-diff-title">
          Title: <span className="zd-dspan del">{title.old}</span>{" "}
          <span className="zd-dspan add">{title.new}</span>
        </div>
      )}
      {rows.map((r, i) => (
        <div key={i} className={"zd-drow " + (r.tag || "ctx")}>
          <span className="zd-dgutter">{gutterFor(r.tag)}</span>
          <span className="zd-dtext">
            {Array.isArray(r.spans)
              ? r.spans.map((s, k) => (
                  <span key={k}
                        className={s.tag === "equal" ? undefined : "zd-dspan " + s.tag}>
                    {s.text}
                  </span>
                ))
              : (r.text || " ")}
          </span>
        </div>
      ))}
    </div>
  );
}
