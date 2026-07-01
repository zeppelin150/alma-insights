import { useEffect, useRef, useState } from "react";

// Connect to the Python ChatBridge over QWebChannel (in-process, no server).
// qwebchannel.js is loaded as a classic script in index.html, so QWebChannel +
// qt.webChannelTransport are globals on window.
function useBridge() {
  const [bridge, setBridge] = useState(null);
  useEffect(() => {
    const transport = window.qt && window.qt.webChannelTransport;
    if (typeof window.QWebChannel === "undefined" || !transport) return;
    new window.QWebChannel(transport, (channel) => {
      window.almaBridge = channel.objects.almaBridge; // expose under its registered name
      setBridge(channel.objects.almaBridge);
    });
  }, []);
  return bridge;
}

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

function DraftCard({ d, onApprove, onReject, busy }) {
  return (
    <div className="dcard">
      <div className="dcard-hdr">
        <span className="dtitle">{d.title}</span>
        <span className="dchg">
          {d.change_count} change{d.change_count === 1 ? "" : "s"}
        </span>
      </div>
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
      <div className="dactions">
        <button className="dreject" disabled={busy} onClick={() => onReject(d.draft_id)}>
          Reject
        </button>
        <button className="dapprove" disabled={busy} onClick={() => onApprove(d.draft_id)}>
          Approve &amp; publish
        </button>
      </div>
    </div>
  );
}

function ReviewPanel({ drafts, open, onClose, onApprove, onReject, busyId }) {
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
        <div className="drawer-list">
          {drafts.length === 0 && <div className="drawer-empty">Nothing awaiting approval.</div>}
          {drafts.map((d) => (
            <DraftCard
              key={d.draft_id}
              d={d}
              onApprove={onApprove}
              onReject={onReject}
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

export default function App() {
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
  const [voiceAvail, setVoiceAvail] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voiceError, setVoiceError] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!bridge) return;
    bridge.responseReady.connect((t) => {
      document.title = "RT:" + t; // round-trip hook for headless verification
      setBusy(false);
      setTools([]);
      setStreaming(""); // finalized — the full text replaces the streamed buffer
      setMessages((m) => [...m, { role: "assistant", text: t }]);
    });
    bridge.errorOccurred.connect((e) => {
      setBusy(false);
      setStreaming("");
      setMessages((m) => [...m, { role: "error", text: e }]);
    });
    bridge.busyChanged.connect((b) => {
      setBusy(b);
      if (b) {
        setTools([]);
        setStreaming("");
        setStatus("Renn is thinking…");
      }
    });
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
        setDrafts(items);
        window.__almaDrafts = items.length; // headless hook
      } catch (e) {}
    });
    bridge.draftResolved.connect((j) => {
      try {
        const p = JSON.parse(j);
        setResolvingId(null);
        window.__almaResolved = p.draft_id; // headless hook
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
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    bridge.send(text);
  }

  function newChat() {
    if (!bridge) return;
    bridge.newSession(); // emits historyLoaded with an empty transcript
  }

  function approveDraft(id) {
    if (!bridge) return;
    setResolvingId(id);
    bridge.approveDraft(String(id));
  }

  function rejectDraft(id) {
    if (!bridge) return;
    setResolvingId(id);
    bridge.rejectDraft(String(id));
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
            if (bridge) bridge.refreshDrafts();
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
          <div key={i} className={"msg " + m.role}>
            {m.text}
          </div>
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

      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Message the agent…"
          disabled={busy}
          autoFocus
        />
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
        <button className="send" onClick={send} disabled={busy || !bridge}>
          Send
        </button>
      </div>

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
        busyId={resolvingId}
      />

      <ActionRouter bridge={bridge} />
    </div>
  );
}
