// Draft save history (zendesk-versions plan, section 6): one row per
// recorded save of an article draft (seq, author tag, save_kind tag,
// "rolled back to save N" note, date, chars), an A/B pair picker over
// save version_ids, an inline RevisionDiff for the picked pair, and a
// "Roll back to this save" button per NON-newest entry — rendered only
// when canRollbackDraft(history) (article + pending), mirroring the
// Python gate so the UI doesn't invite refused clicks. Rollback is
// non-destructive: Python records it as another save (save_kind
// 'rollback'); history is never rewritten.
//
// Pure presentational and HOOK-FREE: draft_history / version_diff arrive
// as props from ZendeskApp (WS-V3b wiring); the pair picker is an
// uncontrolled radio form (RenameRow precedent). Every string renders as
// an escaped React child — never live markup, no HTML anywhere.
import RevisionDiff from "./RevisionDiff.jsx";
import {
  authorInfo, canRollbackDraft, formatChars, normalizeHistory, saveKindInfo,
} from "./versionShape.js";
import "./versions.css";

export default function HistoryPanel({
  history, diff, busy, onSaveDiff, onRollback, onClose,
}) {
  const vm = normalizeHistory(history);
  const saves = vm.saves;
  const rollbackOk = canRollbackDraft(vm);

  // Uncontrolled radio pair -> onSaveDiff(a, b); both sides must be picked
  // and distinct. Sides are save version_id digit strings only — there is
  // no 'current' token for draft saves (the newest save IS the draft).
  function submitPair(ev) {
    ev.preventDefault();
    const els = ev.currentTarget.elements;
    const a = els.zdSaveA ? els.zdSaveA.value : "";
    const b = els.zdSaveB ? els.zdSaveB.value : "";
    if (!a || !b || a === b) return;
    if (onSaveDiff) onSaveDiff(String(a), String(b));
  }

  return (
    <div className="zd-hist">
      <div className="zd-hist-hd">
        <span className="zd-page-title">Draft history</span>
        <span className="zd-hist-sub">
          Every body-changing save is recorded. Rolling back adds a new
          recorded save — nothing is ever rewritten.
        </span>
        <span className="zd-hdr-spacer" />
        {onClose != null && (
          <button type="button" className="zd-btn basic" onClick={onClose}>
            Back to revision
          </button>
        )}
      </div>

      {!rollbackOk && saves.length > 0 && (
        <div className="zd-hist-ro">
          History is read-only — only open pending article drafts can be
          rolled back.
        </div>
      )}

      <form className="zd-hist-list" onSubmit={submitPair}>
        <div className="zd-vp-cols" aria-hidden="true">
          <span className="zd-vp-col">A</span>
          <span className="zd-vp-col">B</span>
        </div>

        {saves.map((s, i) => {
          const author = authorInfo(s.author);
          const kind = saveKindInfo(s.save_kind);
          return (
            <div key={s.version_id} className="zd-hist-row">
              <input type="radio" name="zdSaveA" value={String(s.version_id)}
                     className="zd-vp-radio" disabled={busy}
                     defaultChecked={i === 1}
                     aria-label={"Compare side A: save " + s.seq} />
              <input type="radio" name="zdSaveB" value={String(s.version_id)}
                     className="zd-vp-radio" disabled={busy}
                     defaultChecked={i === 0}
                     aria-label={"Compare side B: save " + s.seq} />
              <div className="zd-hist-main">
                <div className="zd-hist-title">
                  <span className="zd-hist-seq">Save {s.seq}</span>
                  <span className={"zd-tag " + author.cls}>{author.label}</span>
                  <span className={"zd-tag " + kind.cls}>{kind.label}</span>
                  {i === 0 && <span className="zd-tag softgreen">Latest</span>}
                  <span className="zd-hist-name">{s.title}</span>
                </div>
                {s.save_kind === "rollback" && s.rollback_of_seq != null && (
                  <div className="zd-hist-note">
                    Rolled back to save {s.rollback_of_seq}
                  </div>
                )}
                <div className="zd-hist-meta">
                  Saved {s.created_display || "—"}
                  {" · "}{formatChars(s.chars)} chars
                </div>
              </div>
              <div className="zd-hist-actions">
                {rollbackOk && i !== 0 && (
                  <button type="button" className="zd-btn" disabled={busy}
                          title={"Restores this save's content as a new " +
                                 "recorded save — history is never rewritten"}
                          onClick={() => {
                            if (onRollback) onRollback(String(s.version_id));
                          }}>
                    Roll back to this save
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {saves.length === 0 && (
          <div className="zd-vp-empty">
            No recorded saves yet for this draft.
          </div>
        )}

        {saves.length >= 2 && (
          <div className="zd-hist-compare">
            <button type="submit" className="zd-btn" disabled={busy}>
              Compare A to B
            </button>
          </div>
        )}
      </form>

      {diff != null && (
        <div className="zd-hist-diff">
          <div className="zd-hist-diff-hd">
            Comparing {(diff.a && diff.a.label) || "?"} with{" "}
            {(diff.b && diff.b.label) || "?"}
          </div>
          {/* draft-save diff rows are identical to diff_ready; the payload
              has no baseline_present key (both sides always exist), so
              force it truthy for the shared renderer. */}
          <RevisionDiff diff={{ ...diff, baseline_present: true }} />
        </div>
      )}
    </div>
  );
}
