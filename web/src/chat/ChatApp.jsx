import { useEffect, useRef, useState } from "react";
import { Markdown } from "../lib/markdown.jsx";
import { useBridge } from "../lib/bridge.js";
import { PreviewCardButton } from "./GuruCardPreview.jsx";

function ToolRow({ t }) {
  return (
    <div className={"tool " + (t.ok ? "ok" : "err")}>
      <span className="tick">{t.ok ? "✓" : "✗"}</span>
      <span className="tname">{t.name}</span>
      <span className="tmeta">
        {t.ms ? ` · ${t.ms}ms` : ""}
        {t.rows !== null && t.rows !== undefined ? ` · ${t.rows} rows` : ""}
      </span>
    </div>
  );
}

function Meter({ m }) {
  if (!m || m.tokens_in === undefined) return null;
  const tools = Array.isArray(m.tool_names) ? m.tool_names : m.tool_names ? [m.tool_names] : [];
  return (
    <div className="meter">
      {m.model_used || "model"} · {m.tokens_in || 0} in / {m.tokens_out || 0} out tokens
      {m.latency_ms ? ` · ${m.latency_ms}ms` : ""}
      {tools.length ? ` · used ${tools.join(", ")}` : ""}
    </div>
  );
}

// Clear the queued badge on the first queued bubble whose text matches the
// dispatched payload. Driven ONLY by the bridge's queuedDispatched(text) —
// never inferred from busyChanged(true): the Python queue interleaves items
// with no JS bubble ([SYSTEM] triggers, sends from the other surface), so "a
// turn started" is not proof that THIS surface's oldest queued bubble is the
// one that ran. Pure + exported so both surfaces share it and it is testable.
export function unbadgeDispatched(messages, text) {
  const i = messages.findIndex((x) => x.queued && x.text === text);
  if (i < 0) return messages;
  const next = messages.slice();
  next[i] = { ...next[i], queued: false };
  return next;
}

// One transcript bubble. Exported so the queued badge is testable: a message
// parked while a turn ran carries `queued: true` until its turn starts.
export function Bubble({ m }) {
  return (
    <div className={"msg " + m.role}>
      {m.role === "assistant" ? <Markdown text={m.text} /> : m.text}
      {m.queued ? <span className="queued-badge">queued</span> : null}
    </div>
  );
}

// The message composer. Exported so its busy contract is testable: the input
// stays ENABLED while a turn runs (extra context queues and sends when the
// engine frees up), and the Send control swaps to Stop while busy. `mic` is
// the surface's own dictation button (or null).
export function Composer({ input, onInput, onSend, onStop, busy, stopping, disabled, mic }) {
  return (
    <div className="composer">
      <input
        value={input}
        onChange={(e) => onInput(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && onSend()}
        placeholder="Message the agent…"
        autoFocus
      />
      {mic}
      {busy ? (
        <button className="send stop" onClick={onStop} disabled={stopping || disabled}
                title="Stop this response">
          Stop
        </button>
      ) : (
        <button className="send" onClick={onSend} disabled={disabled}>
          Send
        </button>
      )}
    </div>
  );
}

// One day-stamp like "Jun 29 · 2:14 PM" from an ISO timestamp (best-effort).
function when(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts.includes("Z") || ts.includes("+") ? ts : ts + "Z");
    if (isNaN(d.getTime())) return ts.slice(0, 16).replace("T", " ");
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch (e) {
    return ts.slice(0, 16).replace("T", " ");
  }
}

function RecentRow({ s, onOpen, onDelete }) {
  const title = s.title || s.first_question || "Untitled chat";
  const cost = s.total_cost ? ` · $${Number(s.total_cost).toFixed(3)}` : "";
  return (
    <div className="srow" onClick={() => onOpen(s.session_id)}>
      <div className="smain">
        <div className="stitle">{title}</div>
        <div className="smeta">
          {when(s.last_message_at || s.updated_at)}
          {s.message_count ? ` · ${s.message_count} msgs` : ""}
          {cost}
        </div>
      </div>
      <button
        className="sdel"
        title="Delete chat"
        onClick={(e) => {
          e.stopPropagation();
          onDelete(s.session_id, title);
        }}
      >
        ×
      </button>
    </div>
  );
}

function SearchRow({ r, onOpen }) {
  return (
    <div className="srow" onClick={() => onOpen(r.session_id)}>
      <div className="smain">
        <div className="stitle">{r.session_title || "Untitled chat"}</div>
        <div className="ssnippet">
          <span className="srole">{r.role === "user" ? "You" : "Renn"}:</span>{" "}
          {(r.content || "").slice(0, 140)}
        </div>
      </div>
    </div>
  );
}

function History({ bridge, open, onClose, onLoaded }) {
  const [mode, setMode] = useState("recent"); // 'recent' | 'search'
  const [items, setItems] = useState([]);
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(null); // {id, title} awaiting delete confirm

  // Wire the list/delete results once the bridge exists.
  useEffect(() => {
    if (!bridge) return;
    bridge.sessionsListed.connect((j) => {
      try {
        const p = JSON.parse(j);
        setMode(p.mode || "recent");
        setItems(Array.isArray(p.items) ? p.items : []);
        window.__almaSessions = (p.items || []).length; // headless hook
      } catch (e) {}
    });
    bridge.sessionDeleted.connect((j) => {
      try {
        const p = JSON.parse(j);
        window.__almaDeleted = !!p.ok; // headless hook
        if (p.ok) setItems((xs) => xs.filter((x) => x.session_id !== p.session_id));
      } catch (e) {}
    });
  }, [bridge]);

  // Refresh the recent list each time the drawer opens.
  useEffect(() => {
    if (open && bridge) {
      setQuery("");
      bridge.listSessions();
    }
  }, [open, bridge]);

  if (!open) return null;

  function runSearch() {
    if (!bridge) return;
    const q = query.trim();
    if (q) bridge.searchSessions(q);
    else bridge.listSessions();
  }

  function openSession(id) {
    if (!bridge) return;
    bridge.loadSession(id);
    onLoaded(); // App handles historyLoaded + closes
  }

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-hdr">
          <span className="drawer-title">Chat history</span>
          <button className="drawer-x" onClick={onClose} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-search">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            placeholder="Search past chats…"
          />
          <button onClick={runSearch}>Search</button>
        </div>
        <div className="drawer-list">
          {items.length === 0 && (
            <div className="drawer-empty">
              {mode === "search" ? "No matches." : "No past chats yet."}
            </div>
          )}
          {mode === "search"
            ? items.map((r, i) => <SearchRow key={i} r={r} onOpen={openSession} />)
            : items.map((s) => (
                <RecentRow
                  key={s.session_id}
                  s={s}
                  onOpen={openSession}
                  onDelete={(id, title) => setPending({ id, title })}
                />
              ))}
        </div>
        {pending && (
          <div className="confirm">
            <div className="confirm-q">Delete “{pending.title}”?</div>
            <div className="confirm-btns">
              <button
                className="confirm-no"
                onClick={() => setPending(null)}
              >
                Cancel
              </button>
              <button
                className="confirm-yes"
                onClick={() => {
                  bridge.deleteSession(pending.id);
                  setPending(null);
                }}
              >
                Delete
              </button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function StepRow({ s }) {
  const icon =
    s.status === "done" ? "✓" :
    s.status === "error" ? "✗" :
    s.status === "running" ? "▸" :
    s.status === "skipped" ? "–" : "○";
  return (
    <div className={"jstep " + s.status}>
      <span className="jstep-ic">{icon}</span>
      <span className="jstep-name">{s.name}</span>
      {s.detail ? <span className="jstep-detail">{s.detail}</span> : null}
    </div>
  );
}

function JobCard({ j }) {
  const pct = Math.max(0, Math.min(100, j.progress_pct || 0));
  return (
    <div className="jcard">
      <div className="jcard-hdr">
        <span className="jtitle">{j.title}</span>
        <span className={"jstatus " + (j.status || "running")}>{j.status || "running"}</span>
      </div>
      {j.summary ? <div className="jsummary">{j.summary}</div> : null}
      <div className="jbar">
        <div className="jbar-fill" style={{ width: pct + "%" }} />
      </div>
      {Array.isArray(j.steps) && j.steps.length > 0 && (
        <div className="jsteps">
          {j.steps.map((s) => (
            <StepRow key={s.step_id || s.ordinal} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}

function JobsPanel({ jobs, open, onClose }) {
  if (!open) return null;
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer jobs-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-hdr">
          <span className="drawer-title">Jobs</span>
          <button className="drawer-x" onClick={onClose} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          {jobs.length === 0 && <div className="drawer-empty">No jobs yet.</div>}
          {jobs.map((j) => (
            <JobCard key={j.job_id} j={j} />
          ))}
        </div>
      </aside>
    </div>
  );
}

function DiffRow({ r }) {
  const gutter = r.tag === "add" ? "+" : r.tag === "del" ? "−" : " ";
  return (
    <div className={"drow " + r.tag}>
      <span className="dgutter">{gutter}</span>
      <span className="dtext">{r.text || " "}</span>
    </div>
  );
}

// Inline so the notice cannot be lost to a stylesheet that was never updated —
// this is a safety surface, not decoration.
const NOTICE_STYLE = {
  margin: "8px 0 0",
  padding: "8px 10px",
  borderLeft: "3px solid #b45309",
  background: "rgba(180, 83, 9, 0.12)",
  color: "inherit",
  fontSize: "12px",
  lineHeight: 1.45,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

// Inline for the same reason as NOTICE_STYLE: this disclosure is a safety
// surface, not decoration, and must not depend on a stylesheet.
const BYTES_SUMMARY_STYLE = {
  cursor: "pointer",
  listStyle: "none",
  display: "flex",
  gap: "8px",
  alignItems: "baseline",
  padding: "6px 8px",
  border: "1px solid rgba(127,127,127,0.35)",
  borderRadius: "4px",
  fontSize: "12px",
};

const BYTES_PRE_STYLE = {
  margin: "6px 0 0",
  padding: "8px",
  maxHeight: "260px",
  overflow: "auto",
  background: "rgba(127,127,127,0.10)",
  fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
  fontSize: "11px",
  lineHeight: 1.45,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

const BYTES_WHY_STYLE = { margin: "6px 0 0", fontSize: "11px", opacity: 0.85 };

// The exact bytes this approval will send, as an ALWAYS-PRESENT collapsed
// disclosure (the Zendesk SourcePanel pattern).
//
// It is deliberately NOT conditional on `d.notice`. Every detector that ever
// gated a view of the real payload has turned out to be defeatable by whoever
// wrote the payload — most recently a word-token "already reviewed" test that
// the draft author silenced by seeding the prose with their own script's
// tokens. So the operator's route to the characters that ship exists whether or
// not anything fired, and the notice's job shrinks to saying "you should look".
//
// Rendered as escaped React children inside <pre> — never a raw-DOM HTML sink,
// never a live frame. The bytes shown here are assumed hostile.
function PublishBytesPanel({ body, available, flagged }) {
  const text = typeof body === "string" ? body : "";
  return (
    <details className="dbytes">
      <summary className="dbytes-summary" style={BYTES_SUMMARY_STYLE}>
        <span className="dbytes-label">Exact bytes that will be sent</span>
        {flagged ? (
          <span className="dbytes-flag">— the text above does not account for them</span>
        ) : null}
        <span className="dbytes-size">
          {available === false ? "unavailable" : `${text.length} chars`}
        </span>
      </summary>
      <div className="dbytes-why" style={BYTES_WHY_STYLE}>
        {available === false
          ? "This draft's publish body could not be read. Nothing on this card "
            + "represents what would be sent — do not approve it."
          : "The diff above is a readable projection. This is the only view "
            + "that shows every character Guru will receive, including markup, "
            + "attributes and script bodies the projection cannot represent."}
      </div>
      <pre className="dbytes-src" style={BYTES_PRE_STYLE}>{text}</pre>
    </details>
  );
}

// WHERE this publish lands — Python-computed (agent_chat._target_label), shown
// because the approval binds it. A re-requested push at another collection, or
// a card_id re-pointed at a different live card, used to change nothing on this
// card while changing everything about what the click did.
function DraftTarget({ d }) {
  const t = d.target || {};
  const label = d.target_label || "";
  if (!label && !t.card_id && !t.collection_id) return null;
  return (
    <div className="dtarget" style={TARGET_STYLE}>
      <span className="dtarget-label">{label}</span>
      {t.collection_id ? <span className="dtarget-id"> · collection {t.collection_id}</span> : null}
      {t.folder_id ? <span className="dtarget-id"> · folder {t.folder_id}</span> : null}
    </div>
  );
}

const TARGET_STYLE = {
  margin: "6px 0 0",
  fontSize: "11px",
  opacity: 0.9,
  wordBreak: "break-all",
};

// A draft whose bytes moved is NOT approvable until a human re-opens the
// review. `review_state` is Python's (agent_chat._bind_review): only "bound"
// and "rebound" carry a live binding, and "changed" means the row moved under
// an open panel — the case where the approve button would otherwise swap bytes
// under the cursor.
export function isApprovable(d) {
  const state = d && d.review_state;
  if (state === undefined) return true; // older payloads: unchanged behaviour
  return state === "bound" || state === "rebound";
}

export function DraftCard({ d, onApprove, onReject, onReReview, busy,
                            onSendToGuruDraft, onEditInWorkbench }) {
  const approvable = isApprovable(d) && !d.changed_under_review;
  const moved = d.review_state === "changed" || d.changed_under_review;
  return (
    <div className="dcard" data-review-state={d.review_state || ""}>
      <div className="dcard-hdr">
        <span className="dtitle">{d.title}</span>
        <span className="dchg">
          {d.change_count} change{d.change_count === 1 ? "" : "s"}
        </span>
      </div>
      <DraftTarget d={d} />
      {/* A publish that did not succeed puts the draft back here rather than
          dropping it silently. Python owns the sentence. */}
      {d.failure ? (
        <div className="dfail" role="alert" style={NOTICE_STYLE}>
          The last publish of this draft did not succeed, so nothing was sent and
          your earlier sign-off was not used: {d.failure}
        </div>
      ) : null}
      {moved ? (
        <div className="dmoved" role="alert" style={NOTICE_STYLE}>
          This draft CHANGED after it was reviewed — what is shown below is not
          what you approved, and approval is disabled. Re-open the review and
          read the new content before approving.
          <button className="dreview-again" onClick={() => onReReview && onReReview(d.draft_id)}>
            Re-review
          </button>
        </div>
      ) : null}
      {d.review_state === "rebound" ? (
        <div className="drebound" role="alert" style={NOTICE_STYLE}>
          This draft changed since you last opened this panel. What is shown now
          is the current content.
        </div>
      ) : null}
      {Array.isArray(d.checks) && d.checks.length > 0 && (
        <div className="dchecks">
          {d.checks.map((c, i) => (
            <span key={i} className={"dcheck " + (c.status || "ok")} title={c.detail || ""}>
              {c.check}
            </span>
          ))}
        </div>
      )}
      <div className="ddiff">
        {(d.diff || []).map((r, i) => (
          <DiffRow key={i} r={r} />
        ))}
      </div>
      {/* What the diff above CANNOT show of the bytes this approval sends —
          Python-computed (agent_chat.review_notice). Plain text, never HTML. */}
      {d.notice ? (
        <div className="dnotice" role="alert" style={NOTICE_STYLE}>
          {d.notice}
        </div>
      ) : null}
      {/* Unconditional — present for every draft shape, notice or no notice. */}
      <PublishBytesPanel
        body={d.publish_body}
        available={d.publish_body_available}
        flagged={!!d.notice} />
      <div className="dactions">
        {/* WS-D-WEB M4: the Guru-look rendering of the SAME bytes, read-only.
            The frame lives in GuruCardPreview.jsx and reads preview_srcdoc
            (Python-sanitized) — never publish_body; approval stays on the
            bytes panel above. */}
        <PreviewCardButton d={d} />
        {/* C1: hand edits beat typed instructions for small changes. Pure
            navigation — no authority, so it needs no review binding. */}
        {onEditInWorkbench ? (
          <button
            className="dedit"
            disabled={busy}
            title="Open this draft in the Workbench and make the change by hand."
            onClick={() => onEditInWorkbench(d.draft_id)}
          >
            Edit in Workbench
          </button>
        ) : null}
        <button className="dreject" disabled={busy} onClick={() => onReject(d.draft_id)}>
          Reject
        </button>
        {/* WS-B: same review-binding + native-confirm gate as Approve; the
            terminal act creates a Guru DRAFT (publishing happens in Guru). */}
        {onSendToGuruDraft ? (
          <button
            className="dguru-draft"
            disabled={busy || !approvable}
            title={approvable
              ? "Send to your My Drafts in Guru — review and publish it there."
              : "This draft changed since it was reviewed — re-review it first."}
            onClick={() => onSendToGuruDraft(d.draft_id)}
          >
            Send to Guru as draft
          </button>
        ) : null}
        <button
          className="dapprove"
          disabled={busy || !approvable}
          title={approvable ? "" : "This draft changed since it was reviewed — re-review it first."}
          onClick={() => onApprove(d.draft_id)}
        >
          Approve &amp; publish
        </button>
      </div>
    </div>
  );
}

// A refused approval, said out loud. Python decides the wording (agent_chat's
// STALE_REVIEW_REFUSAL and siblings); this only renders it.
//
// It has to be LOUDER than a normal error, because the failure it reports is
// the one the operator cannot see for themselves: the draft moved between the
// panel they read and the button they pressed, so nothing was published and
// what is on screen behind this banner is no longer what a retry would send.
// A bare "ok:false" would be indistinguishable from a Guru outage, and the
// natural response to an outage is to click again.
export function ResolveNotice({ note, onDismiss }) {
  if (!note || !note.message) return null;
  return (
    <div className="dresolve" role="alert" style={NOTICE_STYLE}>
      <span className="dresolve-msg">{note.message}</span>
      <button className="dresolve-x" onClick={onDismiss} title="Dismiss">
        ×
      </button>
    </div>
  );
}

export function ReviewPanel({ drafts, open, onClose, onApprove, onReject, onReReview, busyId, note, onDismissNote,
                              onSendToGuruDraft, onEditInWorkbench }) {
  if (!open) return null;
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer review-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-hdr">
          <span className="drawer-title">Review &amp; sign-off</span>
          <button className="drawer-x" onClick={onClose} title="Close">
            ×
          </button>
        </div>
        <ResolveNotice note={note} onDismiss={onDismissNote} />
        <div className="drawer-list">
          {drafts.length === 0 && <div className="drawer-empty">Nothing awaiting approval.</div>}
          {drafts.map((d) => (
            <DraftCard
              key={d.draft_id}
              d={d}
              onApprove={onApprove}
              onReject={onReject}
              onReReview={onReReview}
              onSendToGuruDraft={onSendToGuruDraft}
              onEditInWorkbench={onEditInWorkbench}
              busy={busyId === d.draft_id}
            />
          ))}
        </div>
      </aside>
    </div>
  );
}

// ActionRouter (M0) — listens for picker/connect asks the agent raised on the
// free-running action channel and renders the matching UI. The real pickers
// (ConnectGoogleCard M2, DriveFolderPicker M3, AsanaBoardPicker M4,
// GuruPublishPicker M5) are wired below; any unrecognized type falls back to a
// labeled PLACEHOLDER. The envelope carries only {id, request_id, type, payload} —
// never folder/board/collection data.
const ACTION_LABELS = {
  drive_folder_picker: "Choose a Google Drive folder",
  asana_board_picker: "Choose an Asana board",
  guru_publish_picker: "Choose a Guru publish target",
  google_connect: "Connect your Google account",
  confirm_write: "Confirm a change",
};

function ActionPlaceholder({ action, onDismiss }) {
  const label = ACTION_LABELS[action.type] || "Action requested";
  return (
    <div className="drawer-overlay" onClick={onDismiss}>
      <aside className="drawer action-drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-hdr">
          <span className="drawer-title">{label}</span>
          <button className="drawer-x" onClick={onDismiss} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          <div className="action-placeholder" data-action-type={action.type}>
            <p>
              Renn opened a picker for <strong>{label.toLowerCase()}</strong>.
            </p>
            <p className="action-note">
              The interactive picker arrives in a later update. (request{" "}
              {(action.request_id || "").slice(0, 8)})
            </p>
          </div>
        </div>
      </aside>
    </div>
  );
}

// ConnectGoogleCard (M2) — the inline "Connect your Google account" card the
// ActionRouter renders for a `google_connect` action. The state machine is
// driven ENTIRELY by the controller's googleAuthState signal
// (idle→connecting→connected|failed); the button click is the ONLY thing that
// calls bridge.connectGoogle(), exactly once, and disables while connecting. It
// must NEVER auto-invoke connectGoogle off the actionRequested/googleAuthState
// signal — a real operator click is required (pre-mortem security fix). The
// payload carries only {have_client} (non-PHI control flag): when false, no GCP
// OAuth client is configured, so we guide the operator and disable the button.
function ConnectGoogleCard({ action, state, onDismiss }) {
  const haveClient = !(action.payload && action.payload.have_client === false);
  const connecting = state === "connecting";
  const connected = state === "connected";
  const failed = state === "failed";
  // Disable while we have no client, while connecting, or once connected.
  const disabled = !haveClient || connecting || connected;

  return (
    <div className="drawer-overlay" onClick={onDismiss}>
      <aside className="drawer action-drawer connect-google" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-hdr">
          <span className="drawer-title">Connect your Google account</span>
          <button className="drawer-x" onClick={onDismiss} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          <div className="connect-card" data-action-type="google_connect" data-state={state}>
            <p>
              Authorize your own Google account so Renn can read the Drive folders
              you choose. Read-only access, active for this session only.
            </p>
            {!haveClient && (
              <p className="connect-note connect-warn" data-no-client="1">
                No Google sign-in client is set up in this build. Add a GCP desktop
                OAuth client in Settings (or ask an admin) before connecting.
              </p>
            )}
            {connected && (
              <p className="connect-note connect-ok">
                Google connected — Drive read access is active for this session.
              </p>
            )}
            {failed && (
              <p className="connect-note connect-err">
                Connection didn’t complete. You can try again.
              </p>
            )}
            <button
              className={"connect-btn" + (connecting ? " connecting" : "")}
              disabled={disabled}
              onClick={(e) => {
                e.preventDefault();
                if (disabled || !window.almaBridge) return;
                window.almaBridge.connectGoogle(); // ONLY here, on a real click
              }}
            >
              {connecting
                ? "Connecting…"
                : connected
                ? "Connected"
                : failed
                ? "Try again"
                : "Connect Google"}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

// DriveFolderPicker (M3) — a lazy tree drawer for the `drive_folder_picker`
// action. On open it asks the bridge for the roots (My Drive + each Shared
// Drive); expanding a node fetches its child folders; selecting a folder commits
// the pick ONCE via driveFolderPicked. The drive picker is name-bearing for the
// CONFIRMATION UI only (in-process) — the controller injects the folder *id*
// alone back to the model. If a payload carries needs_connect, we render an
// inline "Connect Google first" hint that reuses the connect path.
//
// Tree state is a flat map: nodeId -> { loading, error, children: [{id,name,driveId}] }.
// Root rows live under the synthetic key "" (the empty parent the bridge lists).
function DriveFolderPicker({ bridge, action, onResolved, onDismiss }) {
  const requestId = action.request_id || "";
  const [tree, setTree] = useState({}); // parentId -> {loading,error,children}
  const [expanded, setExpanded] = useState({}); // nodeId -> bool
  const [needsConnect, setNeedsConnect] = useState(false);
  const [picked, setPicked] = useState(false); // gate: select a folder ONCE

  function requestChildren(parentId) {
    if (!bridge) return;
    setTree((t) => ({ ...t, [parentId]: { ...(t[parentId] || {}), loading: true } }));
    bridge.driveListFolders(parentId || "", requestId);
  }

  // On open, load the roots (parentId "").
  useEffect(() => {
    if (!bridge) return;
    setTree({});
    setExpanded({});
    setNeedsConnect(false);
    setPicked(false);
    requestChildren("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge, requestId]);

  // Wire the listing + resolution results for THIS request.
  useEffect(() => {
    if (!bridge) return;
    const onFolders = (j) => {
      let p = {};
      try {
        p = JSON.parse(j) || {};
      } catch (e) {
        return;
      }
      if (p.request_id && requestId && p.request_id !== requestId) return; // not ours
      if (p.needs_connect) {
        setNeedsConnect(true);
        return;
      }
      const parentId = p.parent_id || "";
      const children = Array.isArray(p.folders) ? p.folders : [];
      setTree((t) => ({
        ...t,
        [parentId]: { loading: false, error: p.error || null, children },
      }));
      window.__almaDriveFolders = children.length; // headless hook
    };
    if (bridge.driveFoldersListed) bridge.driveFoldersListed.connect(onFolders);
  }, [bridge, requestId]);

  function toggle(node) {
    const open = !expanded[node.id];
    setExpanded((e) => ({ ...e, [node.id]: open }));
    if (open && !tree[node.id]) requestChildren(node.id); // lazy: fetch on first expand
  }

  function pick(node) {
    if (picked || !bridge) return;
    setPicked(true); // ONCE — the controller single-winner-resolves anyway
    bridge.driveFolderPicked(requestId, node.id, node.name || "", node.driveId || "");
  }

  function Node({ node, depth }) {
    const isOpen = !!expanded[node.id];
    const childState = tree[node.id];
    return (
      <div className="drive-node" style={{ paddingLeft: depth * 14 }}>
        <div className="drive-row">
          <button
            className={"drive-twisty" + (isOpen ? " open" : "")}
            onClick={() => toggle(node)}
            title={isOpen ? "Collapse" : "Expand"}
          >
            {isOpen ? "▾" : "▸"}
          </button>
          <span className="drive-name">{node.name || node.id}</span>
          <button className="drive-pick" disabled={picked} onClick={() => pick(node)}>
            Use this
          </button>
        </div>
        {isOpen && (
          <div className="drive-children">
            {childState && childState.loading && (
              <div className="drive-loading">Loading…</div>
            )}
            {childState && childState.error && (
              <div className="drive-error">Could not load folders.</div>
            )}
            {childState &&
              !childState.loading &&
              (childState.children || []).map((c) => (
                <Node key={c.id} node={c} depth={depth + 1} />
              ))}
            {childState &&
              !childState.loading &&
              !childState.error &&
              (childState.children || []).length === 0 && (
                <div className="drive-empty-node">No subfolders.</div>
              )}
          </div>
        )}
      </div>
    );
  }

  const roots = (tree[""] && tree[""].children) || [];
  const rootLoading = tree[""] && tree[""].loading;

  return (
    <div className="drawer-overlay" onClick={onDismiss}>
      <aside
        className="drawer action-drawer drive-picker"
        data-action-type="drive_folder_picker"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-hdr">
          <span className="drawer-title">Choose a Google Drive folder</span>
          <button className="drawer-x" onClick={onDismiss} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          {needsConnect ? (
            <div className="drive-connect-hint" data-needs-connect="1">
              <p>Connect your Google account first to browse Drive folders.</p>
              <button
                className="connect-btn"
                onClick={(e) => {
                  e.preventDefault();
                  if (window.almaBridge) window.almaBridge.connectGoogle();
                }}
              >
                Connect Google
              </button>
            </div>
          ) : (
            <div className="drive-tree">
              {rootLoading && <div className="drive-loading">Loading Drives…</div>}
              {!rootLoading && roots.length === 0 && (
                <div className="drawer-empty">No Drives available.</div>
              )}
              {roots.map((n) => (
                <Node key={n.id} node={n} depth={0} />
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

// AsanaBoardPicker (M4) — a searchable project-list drawer for the
// `asana_board_picker` action (styled like DriveFolderPicker, but flat: Asana
// projects have no tree). On open it asks the bridge for the projects the shared
// PAT can see; selecting a board commits the pick ONCE via asanaBoardPicked.
// Board names ARE operational metadata (project trackers/roadmaps), not patient
// PHI like Drive folder names — so the controller may echo the name back; we pass
// it through on the pick for the confirmation. If a payload carries
// asana_not_connected, we show an inline "connect Asana in Settings" hint.
function AsanaBoardPicker({ bridge, action, onResolved, onDismiss }) {
  const requestId = action.request_id || "";
  const [projects, setProjects] = useState(null); // null = loading; [] = loaded empty
  const [error, setError] = useState(null);
  const [notConnected, setNotConnected] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(false); // gate: select a board ONCE

  // On open, ask for the project list.
  useEffect(() => {
    if (!bridge) return;
    setProjects(null);
    setError(null);
    setNotConnected(false);
    setQuery("");
    setPicked(false);
    if (bridge.asanaListProjects) bridge.asanaListProjects(requestId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge, requestId]);

  // Wire the listing result for THIS request.
  useEffect(() => {
    if (!bridge) return;
    const onProjects = (j) => {
      let p = {};
      try {
        p = JSON.parse(j) || {};
      } catch (e) {
        return;
      }
      if (p.request_id && requestId && p.request_id !== requestId) return; // not ours
      if (p.asana_not_connected) {
        setNotConnected(true);
        setProjects([]);
        return;
      }
      const rows = Array.isArray(p.projects) ? p.projects : [];
      setProjects(rows);
      setError(p.error || null);
      window.__almaAsanaProjects = rows.length; // headless hook
    };
    if (bridge.asanaProjectsListed) bridge.asanaProjectsListed.connect(onProjects);
  }, [bridge, requestId]);

  function pick(proj) {
    if (picked || !bridge) return;
    setPicked(true); // ONCE — the controller single-winner-resolves anyway
    bridge.asanaBoardPicked(requestId, proj.gid, proj.name || "");
  }

  const q = query.trim().toLowerCase();
  const visible = (projects || []).filter(
    (p) => !q || (p.name || "").toLowerCase().includes(q)
  );

  return (
    <div className="drawer-overlay" onClick={onDismiss}>
      <aside
        className="drawer action-drawer asana-picker"
        data-action-type="asana_board_picker"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-hdr">
          <span className="drawer-title">Choose an Asana board</span>
          <button className="drawer-x" onClick={onDismiss} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          {notConnected ? (
            <div className="asana-connect-hint" data-not-connected="1">
              <p>
                Asana isn’t connected yet. Add a shared Asana access token in
                Settings to browse your boards.
              </p>
            </div>
          ) : (
            <div className="asana-board-list">
              <input
                className="asana-search"
                type="text"
                placeholder="Search boards…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {projects === null && (
                <div className="drive-loading">Loading boards…</div>
              )}
              {error && <div className="drive-error">Could not load boards.</div>}
              {projects !== null && !error && visible.length === 0 && (
                <div className="drawer-empty">No boards found.</div>
              )}
              {visible.map((p) => (
                <div key={p.gid} className="asana-board-row drive-row">
                  <span className="drive-name">{p.name || p.gid}</span>
                  <button
                    className="drive-pick"
                    disabled={picked}
                    onClick={() => pick(p)}
                  >
                    Use this
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

// ConfirmCard (M7b) — the human-gate card for a `confirm_write` action. The model
// gets NO direct-execute write tool: a request_* propose tool minted this row, so
// the ONLY path to the (non-idempotent, live-API) write is this card + a click.
// It shows action.payload.summary and a [Confirm]/[Cancel] pair; BOTH disable
// after the first click (the `submitted` gate — mirrors the pickers' `picked`
// gate) so a double click can't fire two writes (the controller single-winners
// anyway). It closes on actionResolved for THIS request_id, showing the one-line
// outcome first. Reuses the .drawer / .connect-card styles.
function ConfirmCard({ action, resolved, onDismiss }) {
  const requestId = action.request_id || "";
  const summary =
    (action.payload && action.payload.summary) || "Confirm this change?";
  const [submitted, setSubmitted] = useState(false);
  // resolved: null until this request resolves; then {ok?, cancelled?}.
  const done = !!resolved;
  const ok = resolved && resolved.ok;
  const cancelled = resolved && resolved.cancelled;

  function confirm() {
    if (submitted || done || !window.almaBridge) return;
    setSubmitted(true); // ONCE — the controller single-winner-claims anyway
    window.almaBridge.confirmWrite(requestId);
  }
  function cancel() {
    if (submitted || done || !window.almaBridge) return;
    setSubmitted(true); // ONCE — disables BOTH buttons
    window.almaBridge.cancelWrite(requestId);
  }

  return (
    <div className="drawer-overlay" onClick={onDismiss}>
      <aside
        className="drawer action-drawer confirm-write"
        data-action-type="confirm_write"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-hdr">
          <span className="drawer-title">Confirm a change</span>
          <button className="drawer-x" onClick={onDismiss} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          <div className="connect-card" data-state={done ? (cancelled ? "cancelled" : ok ? "ok" : "failed") : (submitted ? "submitted" : "pending")}>
            <p className="confirm-summary">{summary}</p>
            {done && cancelled && (
              <p className="connect-note">Cancelled — nothing was changed.</p>
            )}
            {done && !cancelled && ok && (
              <p className="connect-note connect-ok">Done — the change was saved.</p>
            )}
            {done && !cancelled && !ok && (
              <p className="connect-note connect-err">
                The change didn’t complete.
              </p>
            )}
            {!done && (
              <div className="confirm-actions">
                <button
                  className="connect-btn confirm-yes"
                  disabled={submitted}
                  onClick={confirm}
                >
                  {submitted ? "Working…" : "Confirm"}
                </button>
                <button
                  className="connect-btn confirm-no"
                  disabled={submitted}
                  onClick={cancel}
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}

// GuruPublishPicker (M5) — a TWO-LEVEL drawer for the `guru_publish_picker`
// action. Level 1 lists the Guru COLLECTIONS; clicking a collection loads + shows
// THAT collection's folders (level 2), where the operator picks a folder OR
// "publish at the collection level" (folder omitted). The final pick commits ONCE
// via guruTargetPicked(request_id, collectionId, folderId). Guru collection/folder
// names are operational KB metadata (the knowledge base's own structure), not
// patient PHI like Drive folder names — so the controller may echo the ids back;
// the picker shows the names. If a payload carries guru_not_connected, we show an
// inline "connect Guru in Settings" hint.
function GuruPublishPicker({ bridge, action, onResolved, onDismiss }) {
  const requestId = action.request_id || "";
  const [collections, setCollections] = useState(null); // null=loading; []=empty
  const [folders, setFolders] = useState(null); // null=not-loaded/loading; []=empty
  const [chosenCollection, setChosenCollection] = useState(null); // {id,name} once drilled in
  const [error, setError] = useState(null);
  const [notConnected, setNotConnected] = useState(false);
  const [picked, setPicked] = useState(false); // gate: commit ONCE

  // On open, ask for the collections (empty collection_id → level 1).
  useEffect(() => {
    if (!bridge) return;
    setCollections(null);
    setFolders(null);
    setChosenCollection(null);
    setError(null);
    setNotConnected(false);
    setPicked(false);
    if (bridge.guruListTargets) bridge.guruListTargets("", requestId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bridge, requestId]);

  // Wire the two-level listing result for THIS request.
  useEffect(() => {
    if (!bridge) return;
    const onTargets = (j) => {
      let p = {};
      try {
        p = JSON.parse(j) || {};
      } catch (e) {
        return;
      }
      if (p.request_id && requestId && p.request_id !== requestId) return; // not ours
      if (p.guru_not_connected) {
        setNotConnected(true);
        setCollections([]);
        return;
      }
      const items = Array.isArray(p.items) ? p.items : [];
      if (p.level === "folders") {
        setFolders(items);
        setError(p.error || null);
        window.__almaGuruFolders = items.length; // headless hook
      } else {
        setCollections(items);
        setError(p.error || null);
        window.__almaGuruCollections = items.length; // headless hook
      }
    };
    if (bridge.guruTargetsListed) bridge.guruTargetsListed.connect(onTargets);
  }, [bridge, requestId]);

  function openCollection(col) {
    if (!bridge) return;
    setChosenCollection(col);
    setFolders(null); // loading
    setError(null);
    if (bridge.guruListTargets) bridge.guruListTargets(col.id, requestId);
  }

  function back() {
    setChosenCollection(null);
    setFolders(null);
    setError(null);
  }

  function commit(collectionId, folderId) {
    if (picked || !bridge) return;
    setPicked(true); // ONCE — the controller single-winner-resolves anyway
    bridge.guruTargetPicked(requestId, collectionId, folderId || "");
  }

  const inFolders = !!chosenCollection;

  return (
    <div className="drawer-overlay" onClick={onDismiss}>
      <aside
        className="drawer action-drawer guru-picker"
        data-action-type="guru_publish_picker"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-hdr">
          {inFolders ? (
            <button className="drawer-back" onClick={back} title="Back to collections">
              ‹
            </button>
          ) : null}
          <span className="drawer-title">
            {inFolders
              ? `Folder in “${chosenCollection.name || chosenCollection.id}”`
              : "Choose a Guru publish target"}
          </span>
          <button className="drawer-x" onClick={onDismiss} title="Close">
            ×
          </button>
        </div>
        <div className="drawer-list">
          {notConnected ? (
            <div className="guru-connect-hint" data-not-connected="1">
              <p>
                Guru isn’t connected yet. Connect Guru in Settings to browse your
                collections and folders.
              </p>
            </div>
          ) : inFolders ? (
            <div className="guru-folder-list" data-level="folders">
              <button
                className="guru-collection-level-btn"
                disabled={picked}
                onClick={() => commit(chosenCollection.id, "")}
              >
                Publish at the collection level (no folder)
              </button>
              {folders === null && <div className="drive-loading">Loading folders…</div>}
              {error && <div className="drive-error">Could not load folders.</div>}
              {folders !== null && !error && folders.length === 0 && (
                <div className="drawer-empty">No folders — publish at the collection level.</div>
              )}
              {(folders || []).map((f) => (
                <div key={f.id} className="guru-folder-row drive-row">
                  <span className="drive-name">{f.name || f.id}</span>
                  <button
                    className="drive-pick"
                    disabled={picked}
                    onClick={() => commit(chosenCollection.id, f.id)}
                  >
                    Use this
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="guru-collection-list" data-level="collections">
              {collections === null && (
                <div className="drive-loading">Loading collections…</div>
              )}
              {error && <div className="drive-error">Could not load collections.</div>}
              {collections !== null && !error && collections.length === 0 && (
                <div className="drawer-empty">No collections found.</div>
              )}
              {(collections || []).map((c) => (
                <div key={c.id} className="guru-collection-row drive-row">
                  <span className="drive-name">{c.name || c.id}</span>
                  {c.read_only ? (
                    <span
                      className="dcheck warn guru-readonly-badge"
                      title="Guru-managed — folders can't be published here"
                    >
                      read-only
                    </span>
                  ) : null}
                  <button className="drive-pick" onClick={() => openCollection(c)}>
                    Open
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function ActionRouter({ bridge }) {
  const [action, setAction] = useState(null); // the active {type, request_id, …}
  const [googleState, setGoogleState] = useState("idle"); // idle|connecting|connected|failed
  const [writeResolved, setWriteResolved] = useState(null); // confirm_write outcome {request_id, ok?|cancelled?}

  useEffect(() => {
    if (!bridge) return;
    if (bridge.actionRequested) {
      bridge.actionRequested.connect((j) => {
        try {
          const a = JSON.parse(j);
          if (a && ACTION_LABELS[a.type]) {
            setAction(a);
            // googleAuthState is the SOLE driver of the connect-card state.
            // Show a fresh card for a new request, but never clobber an in-flight
            // 'connecting' state if the model re-requests mid-OAuth.
            if (a.type === "google_connect")
              setGoogleState((s) => (s === "connecting" ? s : "idle"));
            if (a.type === "confirm_write") setWriteResolved(null); // fresh card
            window.__almaAction = a.type; // headless hook
          }
        } catch (e) {}
      });
    }
    // The connect-card state machine is driven by the controller, NOT by a
    // click. We only mirror the signal into local state — we never call
    // connectGoogle from here (a real button click is required).
    if (bridge.googleAuthState) {
      bridge.googleAuthState.connect((j) => {
        let st = "idle";
        try {
          const p = JSON.parse(j);
          st = (p && p.state) || "idle";
        } catch (e) {
          st = j || "idle"; // tolerate a bare state string
        }
        setGoogleState(st);
        window.__almaGoogleState = st; // headless hook
      });
    }
    // actionResolved (M3/M7b): the controller resolved an action. For PICKERS we
    // close the drawer immediately. For confirm_write (M7b) the payload carries the
    // write outcome ({ok}|{cancelled}); we keep the card open and stash the outcome
    // so it shows a one-line result first — the operator dismisses it (or it's
    // replaced by the next action).
    if (bridge.actionResolved) {
      bridge.actionResolved.connect((j) => {
        let p = {};
        try {
          p = JSON.parse(j) || {};
        } catch (e) {}
        const rid = p.request_id || "";
        window.__almaActionResolved = rid; // headless hook
        setAction((a) => {
          if (!a || (rid && a.request_id !== rid)) return a; // not the open one
          if (a.type === "confirm_write") {
            setWriteResolved(p); // show the outcome; keep the card open
            return a;
          }
          return null; // pickers close immediately
        });
      });
    }
  }, [bridge]);

  if (!action) return null;
  if (action.type === "google_connect") {
    return (
      <ConnectGoogleCard
        action={action}
        state={googleState}
        onDismiss={() => setAction(null)}
      />
    );
  }
  if (action.type === "drive_folder_picker") {
    return (
      <DriveFolderPicker
        bridge={bridge}
        action={action}
        onResolved={() => setAction(null)}
        onDismiss={() => setAction(null)}
      />
    );
  }
  if (action.type === "asana_board_picker") {
    return (
      <AsanaBoardPicker
        bridge={bridge}
        action={action}
        onResolved={() => setAction(null)}
        onDismiss={() => setAction(null)}
      />
    );
  }
  if (action.type === "guru_publish_picker") {
    return (
      <GuruPublishPicker
        bridge={bridge}
        action={action}
        onResolved={() => setAction(null)}
        onDismiss={() => setAction(null)}
      />
    );
  }
  if (action.type === "confirm_write") {
    return (
      <ConfirmCard
        action={action}
        resolved={
          writeResolved && writeResolved.request_id === action.request_id
            ? writeResolved
            : null
        }
        onDismiss={() => {
          setAction(null);
          setWriteResolved(null);
        }}
      />
    );
  }
  return <ActionPlaceholder action={action} onDismiss={() => setAction(null)} />;
}

export default function ChatApp() {
  const bridge = useBridge();
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Renn is thinking…");
  const [tools, setTools] = useState([]);
  const [meter, setMeter] = useState(null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(""); // live assistant text, pre-finalize
  const [historyOpen, setHistoryOpen] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [drafts, setDrafts] = useState([]);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [resolvingId, setResolvingId] = useState(null);
  // Last approve/reject outcome that carried an operator-facing message —
  // in practice a REFUSAL (the draft moved after the review that bound it).
  const [resolveNote, setResolveNote] = useState(null);
  const [voiceAvail, setVoiceAvail] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voiceError, setVoiceError] = useState(false);
  const [stopping, setStopping] = useState(false); // Stop clicked, kill in flight
  const scrollRef = useRef(null);
  // The streamed buffer, mirrored into a ref so runStopped can finalize the
  // partial text without racing React state.
  const streamRef = useRef("");
  // Monotonic id for user bubbles, so a queueMessage callback can badge the
  // exact bubble it queued.
  const seqRef = useRef(0);
  // The drafts as last rendered, so an incoming poll can be COMPARED against
  // what is on screen instead of replacing it. The bridge callback below is
  // registered once and closes over this ref, not over the drafts state.
  const draftsRef = useRef([]);

  useEffect(() => {
    if (!bridge) return;
    bridge.responseReady.connect((t) => {
      document.title = "RT:" + t; // round-trip hook for headless verification
      setBusy(false);
      setTools([]);
      setStreaming(""); // finalized — the full text replaces the streamed buffer
      streamRef.current = "";
      setMessages((m) => [...m, { role: "assistant", text: t }]);
    });
    bridge.errorOccurred.connect((e) => {
      setBusy(false);
      setStreaming("");
      streamRef.current = "";
      setMessages((m) => [...m, { role: "error", text: e }]);
    });
    bridge.busyChanged.connect((b) => {
      setBusy(b);
      if (b) {
        setTools([]);
        setStreaming("");
        streamRef.current = "";
        setStatus("Renn is thinking…");
      } else {
        setStopping(false);
      }
    });
    // A queued bubble is un-badged only when Python names the drained text —
    // see unbadgeDispatched for why busyChanged(true) must not be used.
    if (bridge.queuedDispatched) {
      bridge.queuedDispatched.connect((t) => {
        setMessages((m) => unbadgeDispatched(m, t));
      });
    }
    // The user aborted the turn: finalize any partial text instead of
    // discarding it, or say so plainly when nothing had streamed yet.
    if (bridge.runStopped) {
      bridge.runStopped.connect(() => {
        const buf = streamRef.current;
        streamRef.current = "";
        setStreaming("");
        setTools([]);
        setStopping(false);
        setMessages((m) => [
          ...m,
          buf
            ? { role: "assistant", text: buf + "\n\n— stopped" }
            : { role: "system", text: "Run stopped." },
        ]);
        window.__almaRunStopped = (window.__almaRunStopped || 0) + 1; // headless hook
      });
    }
    // Keep the Agent's own neutral label — the shared engine hardcodes
    // "Gemini is thinking…", which is wrong on the Claude path. Real status
    // messages (e.g. bridge refresh) still come through.
    bridge.statusUpdate.connect((s) => {
      if (s && !/is thinking/i.test(s)) setStatus(s);
    });
    // Per-token streaming: append each delta into the in-flight assistant bubble.
    bridge.tokenStreamed.connect((d) => {
      setStreaming((s) => {
        const next = s + d;
        streamRef.current = next;
        window.__almaStreamBuf = next; // headless hook
        return next;
      });
    });
    bridge.telemetry.connect((j) => {
      try {
        setMeter(JSON.parse(j));
      } catch (e) {}
    });
    bridge.toolCall.connect((j) => {
      try {
        setTools((p) => [...p, JSON.parse(j)]);
      } catch (e) {}
    });
    // A loaded past chat (or a new-chat reset) replaces the live transcript.
    bridge.historyLoaded.connect((j) => {
      try {
        const p = JSON.parse(j);
        const msgs = (p.messages || []).map((m) => ({
          role: m.role === "user" ? "user" : "assistant",
          text: m.content || "",
        }));
        setMessages(msgs);
        setTools([]);
        setMeter(null);
        setBusy(false);
        setStreaming("");
        streamRef.current = "";
        setHistoryOpen(false);
        window.__almaLoaded = msgs.length; // headless hook
      } catch (e) {}
    });
    // Live job sidebar (M4): the bridge pushes the full job list on change.
    bridge.jobsListed.connect((j) => {
      try {
        const p = JSON.parse(j);
        const items = Array.isArray(p.items) ? p.items : [];
        setJobs(items);
        window.__almaJobs = items.length; // headless hook
      } catch (e) {}
    });
    if (bridge.listJobs) bridge.listJobs(); // initial populate
    // In-thread review / sign-off (M5): drafts a tool tried to push but that
    // need the operator's approval before they reach Guru.
    bridge.draftsPending.connect((j) => {
      try {
        const p = JSON.parse(j);
        const items = Array.isArray(p.items) ? p.items : [];
        // NEVER a bare setDrafts. This signal fires from a background poll at
        // the end of every Renn turn, so a revision made inside that turn used
        // to swap the bytes under the operator's cursor while the panel stayed
        // open and the approve button stayed armed. Anything whose content or
        // destination moved is MARKED, and its approve affordance is reset.
        const before = new Map(draftsRef.current.map((x) => [String(x.draft_id), x]));
        const moved = new Set();
        const merged = items.map((it) => {
          const old = before.get(String(it.draft_id));
          if (!old) return it;
          const changed =
            old.publish_body !== it.publish_body ||
            old.title !== it.title ||
            JSON.stringify(old.target || null) !== JSON.stringify(it.target || null);
          if (!changed) return it;
          moved.add(String(it.draft_id));
          return { ...it, changed_under_review: true };
        });
        draftsRef.current = merged;
        setDrafts(merged);
        // A card that moved mid-click is no longer the card being resolved.
        setResolvingId((cur) =>
          cur !== null && cur !== undefined && moved.has(String(cur)) ? null : cur);
        window.__almaDrafts = items.length; // headless hook
        window.__almaDraftsChanged = moved.size; // headless hook
      } catch (e) {}
    });
    bridge.draftResolved.connect((j) => {
      try {
        const p = JSON.parse(j);
        setResolvingId(null);
        // Surface a refusal instead of letting it look like a silent no-op or
        // a transient outage. Python owns the sentence.
        setResolveNote(p.message ? { message: String(p.message), refused: !!p.refused } : null);
        window.__almaResolved = p.draft_id; // headless hook
        window.__almaResolveRefused = !!p.refused; // headless hook
        window.__almaResolveMessage = p.message || ""; // headless hook
      } catch (e) {}
    });
    if (bridge.refreshDrafts) bridge.refreshDrafts();
    // On-device voice dictation (M6): push-to-talk → transcript fills the input.
    bridge.voiceTranscript.connect((t) => {
      if (t) setInput((cur) => (cur ? cur + " " : "") + t);
      window.__almaTranscript = t; // headless hook
    });
    bridge.voiceState.connect((s) => {
      setListening(s === "listening");
      setTranscribing(s === "transcribing");
      setVoiceError(s === "error"); // surface a failed dictation; cleared on next start
      window.__almaVoiceState = s; // headless hook
    });
    if (bridge.voiceAvailable) {
      bridge.voiceAvailable((avail) => {
        setVoiceAvail(!!avail);
        window.__almaVoiceAvail = !!avail; // headless hook
      });
    }
    window.__almaReady = true; // readiness hook for headless verification
  }, [bridge]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, tools, busy, streaming]);

  function send() {
    const text = input.trim();
    if (!text || !bridge) return;
    const id = ++seqRef.current;
    setMessages((m) => [...m, { role: "user", text, id }]);
    setInput("");
    if (bridge.queueMessage) {
      // Always route through the queue: it sends immediately when idle and
      // parks the text while a turn runs. The result arrives via the
      // QWebChannel callback (same consumption as voiceAvailable).
      bridge.queueMessage(text, (r) => {
        if (r === "queued") {
          setMessages((m) => m.map((x) => (x.id === id ? { ...x, queued: true } : x)));
        }
      });
    } else {
      bridge.send(text);
    }
  }

  function stop() {
    if (!bridge || !bridge.stopRun) return;
    setStopping(true);
    bridge.stopRun((ok) => {
      // Nothing was aborted (idle, or a client with no abort) — the run keeps
      // going, so give the button back rather than leaving it dead.
      if (!ok) setStopping(false);
    });
  }

  function newChat() {
    if (!bridge) return;
    bridge.newSession(); // emits historyLoaded with an empty transcript
  }

  // Opening the review panel is the ONE act that mints an approval binding
  // (ChatBridge.openReview). refreshDrafts only refreshes the list — it must
  // not be able to authorize anything, because a timer calls it too.
  function openReview() {
    if (!bridge) return;
    if (bridge.openReview) bridge.openReview();
    else if (bridge.refreshDrafts) bridge.refreshDrafts();
  }

  function approveDraft(id) {
    if (!bridge) return;
    setResolvingId(id);
    setResolveNote(null); // clear a previous refusal; this click gets its own answer
    bridge.approveDraft(String(id));
  }

  function rejectDraft(id) {
    if (!bridge) return;
    setResolvingId(id);
    setResolveNote(null);
    bridge.rejectDraft(String(id));
  }

  // WS-B: same click discipline as approve — the gate lives in Python.
  function sendToGuruDraft(id) {
    if (!bridge || !bridge.sendToGuruDraft) return;
    setResolvingId(id);
    setResolveNote(null);
    bridge.sendToGuruDraft(String(id));
  }

  // C1: navigation only; close the drawer so the Workbench is visible.
  function editInWorkbench(id) {
    if (!bridge || !bridge.editInWorkbench) return;
    bridge.editInWorkbench(String(id));
    setReviewOpen(false);
  }

  return (
    <div className="app">
      <header className="hdr">
        <span className="dot" aria-hidden="true" />
        <span className="name">Alma Agent</span>
        <span className="model">{meter && meter.model_used ? meter.model_used : "Claude"}</span>
        <button className="hbtn new" onClick={newChat} disabled={busy} title="New chat">
          New
        </button>
        <button
          className={"hbtn review" + (drafts.length ? " alert" : "")}
          onClick={() => {
            setReviewOpen(true);
            openReview();
          }}
          title="Review edits awaiting your sign-off"
        >
          Review{drafts.length ? ` (${drafts.length})` : ""}
        </button>
        <button
          className="hbtn jobs"
          onClick={() => {
            setJobsOpen(true);
            if (bridge) bridge.listJobs();
          }}
          title="Jobs"
        >
          Jobs{jobs.length ? ` (${jobs.length})` : ""}
        </button>
        <button className="hbtn hist" onClick={() => setHistoryOpen(true)} title="Chat history">
          History
        </button>
      </header>

      <div className="messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <Bubble key={i} m={m} />
        ))}
        {busy && (
          <div className={"msg assistant " + (streaming ? "streaming" : "thinking")}>
            {streaming ? (
              <div className="streamtext">
                {streaming}
                <span className="caret">▍</span>
              </div>
            ) : (
              <div className="tstatus">{status}</div>
            )}
            {tools.length > 0 && (
              <div className="tools">
                {tools.map((t, i) => (
                  <ToolRow key={i} t={t} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <Meter m={meter} />

      <Composer
        input={input}
        onInput={setInput}
        onSend={send}
        onStop={stop}
        busy={busy}
        stopping={stopping}
        disabled={!bridge}
        mic={
          <button
            className={
              "mic" +
              (listening || transcribing ? " listening" : voiceError ? " err" : "")
            }
            title={
              !voiceAvail
                ? "Dictation unavailable on this device"
                : listening
                ? "Listening — click to stop"
                : voiceError
                ? "Dictation failed — click to try again"
                : "Click to dictate (on-device)"
            }
            disabled={!voiceAvail || busy || transcribing}
            onClick={() => {
              if (!bridge) return;
              if (listening) bridge.stopVoice();
              else bridge.startVoice();
            }}
          >
            {transcribing ? "● transcribing…" : listening ? "● listening" : voiceError ? "mic ⚠" : "mic"}
          </button>
        }
      />

      <History
        bridge={bridge}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onLoaded={() => {}}
      />

      <JobsPanel jobs={jobs} open={jobsOpen} onClose={() => setJobsOpen(false)} />

      <ReviewPanel
        drafts={drafts}
        open={reviewOpen}
        onClose={() => setReviewOpen(false)}
        onApprove={approveDraft}
        onReject={rejectDraft}
        onReReview={openReview}
        busyId={resolvingId}
        note={resolveNote}
        onDismissNote={() => setResolveNote(null)}
      />

      <ActionRouter bridge={bridge} />
    </div>
  );
}
