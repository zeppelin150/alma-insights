// Article version timeline (zendesk-versions plan, section 6): the
// mirror's CURRENT state pinned on top plus every captured superseded
// version (origin / replaced_by_origin tags, captured dates), an A/B pair
// picker whose 'current' side is the live mirror row, a per-version View
// toggle over the Python-sanitized body_srcdoc, an inline RevisionDiff for
// the picked pair, and a per-captured-version "Restore as revision"
// button (never for Current — restore is non-destructive and only creates
// a pending draft Python-side).
//
// Pure presentational and HOOK-FREE by design: all state (versions_data,
// version_detail, version_diff) arrives as props from ZendeskApp (WS-V3b
// wiring); every affordance only calls a prop callback. The pair picker is
// an uncontrolled radio form (RenameRow precedent) so no local state is
// needed and tests can invoke the component as a plain function. Bodies
// render ONLY through the sandboxed ArticleBody iframe; every other
// string is an escaped React child — never live markup.
import ArticleBody from "./ArticleBody.jsx";
import RevisionDiff from "./RevisionDiff.jsx";
import {
  formatChars, normalizeVersions, versionPairPayload,
} from "./versionShape.js";
import "./versions.css";

function diffSideLabel(side) {
  return side && side.label ? side.label : "?";
}

export default function VersionsPanel({
  data, detail, diff, busy,
  onDiffPair, onViewVersion, onCloseView, onRestore, onClose,
}) {
  const vm = normalizeVersions(data);
  const cur = vm.current;
  const rows = vm.versions;
  const viewingId = detail && typeof detail === "object" ? detail.version_id : null;

  // Uncontrolled radio pair -> onDiffPair(a, b); both sides must be picked
  // and distinct. Values are digit strings or the literal 'current'.
  function submitPair(ev) {
    ev.preventDefault();
    const els = ev.currentTarget.elements;
    const a = els.zdVerA ? els.zdVerA.value : "";
    const b = els.zdVerB ? els.zdVerB.value : "";
    if (!a || !b || a === b) return;
    const pair = versionPairPayload(a, b);
    if (onDiffPair) onDiffPair(pair.a, pair.b);
  }

  return (
    <div className="zd-vp">
      <div className="zd-vp-hd">
        <span className="zd-page-title">Version history</span>
        <span className="zd-vp-sub">
          Captured automatically whenever a pull or import supersedes the
          mirrored article. Restoring never edits the mirror — it creates a
          pending revision for review.
        </span>
        <span className="zd-hdr-spacer" />
        {onClose != null && (
          <button type="button" className="zd-btn basic" onClick={onClose}>
            Back to article
          </button>
        )}
      </div>

      <form className="zd-vp-timeline" onSubmit={submitPair}>
        <div className="zd-vp-cols" aria-hidden="true">
          <span className="zd-vp-col">A</span>
          <span className="zd-vp-col">B</span>
        </div>

        {/* Current mirror state — pinned on top, never restorable. */}
        <div className="zd-vp-row current">
          <input type="radio" name="zdVerA" value="current"
                 className="zd-vp-radio" disabled={busy}
                 aria-label="Compare side A: current version" />
          <input type="radio" name="zdVerB" value="current"
                 className="zd-vp-radio" disabled={busy} defaultChecked
                 aria-label="Compare side B: current version" />
          <div className="zd-vp-main">
            <div className="zd-vp-title">
              <span className="zd-tag green">Current</span>
              <span className="zd-vp-name">{cur.title}</span>
              <span className="zd-tag grey">{cur.origin}</span>
            </div>
            <div className="zd-vp-meta">
              Updated {cur.updated_display || "—"}
              {" · "}{formatChars(cur.chars)} chars
            </div>
          </div>
        </div>

        {rows.map((v, i) => (
          <div key={v.version_id} className="zd-vp-row">
            <input type="radio" name="zdVerA" value={String(v.version_id)}
                   className="zd-vp-radio" disabled={busy}
                   defaultChecked={i === 0}
                   aria-label={"Compare side A: version " + v.version_id} />
            <input type="radio" name="zdVerB" value={String(v.version_id)}
                   className="zd-vp-radio" disabled={busy}
                   aria-label={"Compare side B: version " + v.version_id} />
            <div className="zd-vp-main">
              <div className="zd-vp-title">
                <span className="zd-vp-name">{v.title}</span>
                <span className="zd-tag grey">{v.origin}</span>
                {v.replaced_by_origin ? (
                  <span className="zd-tag softyellow"
                        title="The write that superseded this state">
                    replaced by {v.replaced_by_origin}
                  </span>
                ) : null}
              </div>
              <div className="zd-vp-meta">
                Captured {v.captured_display || "—"}
                {" · "}{formatChars(v.chars)} chars
              </div>
            </div>
            <div className="zd-vp-actions">
              {viewingId === v.version_id ? (
                <button type="button" className="zd-btn basic" disabled={busy}
                        onClick={() => { if (onCloseView) onCloseView(); }}>
                  Hide body
                </button>
              ) : (
                <button type="button" className="zd-btn basic" disabled={busy}
                        title="View this version's stored body (sandboxed)"
                        onClick={() => {
                          if (onViewVersion) onViewVersion(String(v.version_id));
                        }}>
                  View body
                </button>
              )}
              <button type="button" className="zd-btn" disabled={busy}
                      title={"Creates a pending revision from this version — " +
                             "the mirror itself is never modified"}
                      onClick={() => {
                        if (onRestore) onRestore(String(v.version_id));
                      }}>
                Restore as revision
              </button>
            </div>
          </div>
        ))}

        {rows.length === 0 ? (
          <div className="zd-vp-empty">
            No captured versions yet — history starts the first time a pull
            or import changes this article.
          </div>
        ) : (
          <div className="zd-vp-compare">
            <button type="submit" className="zd-btn" disabled={busy}>
              Compare A to B
            </button>
          </div>
        )}
      </form>

      {detail != null && (
        <div className="zd-vp-body">
          <div className="zd-vp-diff-hd">
            Version body — {detail.title || ""}
            {detail.captured_display ? " (captured " + detail.captured_display + ")" : ""}
          </div>
          {/* The ONLY HTML renderer on the route: fully sandboxed srcdoc
              iframe over the controller's sanitize_html output. */}
          <ArticleBody srcdoc={detail.body_srcdoc || ""} />
        </div>
      )}

      {diff != null && (
        <div className="zd-vp-diff">
          <div className="zd-vp-diff-hd">
            Comparing {diffSideLabel(diff.a)} with {diffSideLabel(diff.b)}
          </div>
          {/* version_diff rows are identical to diff_ready; the payload has
              no baseline_present key (both sides always exist here), so
              force it truthy for the shared renderer. */}
          <RevisionDiff diff={{ ...diff, baseline_present: true }} />
        </div>
      )}
    </div>
  );
}
