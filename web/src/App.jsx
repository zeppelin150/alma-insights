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
    </div>
  );
}
