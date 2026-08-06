import { useEffect, useRef, useState } from "react";
import { Markdown } from "../lib/markdown.jsx";
import { Bubble, unbadgeDispatched } from "./ChatApp.jsx";

// Renn as an in-page drawer (M5.5) — the updated chat surface the web tabs
// raise instead of the legacy Qt ChatPanel drilldown. A pure renderer over
// the SAME ChatBridge/ChatEngine page.py already owns (send_fn routes through
// _dispatch_chat), so it is the same assistant, session, and canvas coupling
// as before — streaming bubbles, markdown, live tool rows, and host notices
// (scan/publish results) included. History/jobs/review/voice deliberately
// stay on the dedicated Agent page.
//
// ``bridge`` is the resolved almaBridge object (or null → unavailable state);
// the parent owns open/close so it can fall back to the Qt drilldown when no
// web chat bridge is registered.
export default function ChatDrawer({ bridge, open, onClose }) {
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Renn is thinking…");
  const [streaming, setStreaming] = useState("");
  const [tools, setTools] = useState([]);
  const [input, setInput] = useState("");
  const [stopping, setStopping] = useState(false);
  const scrollRef = useRef(null);
  // Texts this drawer rendered optimistically and expects to see echoed back
  // as a chatNotice("u") from the host — so it can skip its own echo (a second
  // open drawer, which has no pending echo, renders the turn normally).
  const pendingEchoes = useRef([]);
  // The streamed buffer mirrored into a ref (runStopped finalizes it) and a
  // monotonic id so a queueMessage callback can badge the bubble it queued.
  const streamRef = useRef("");
  const seqRef = useRef(0);

  useEffect(() => {
    if (!bridge) return;
    bridge.responseReady.connect((t) => {
      setBusy(false);
      setTools([]);
      setStreaming("");
      streamRef.current = "";
      setMessages((m) => [...m, { role: "assistant", text: t }]);
      window.__almaDrawerMsgs = (window.__almaDrawerMsgs || 0) + 1; // headless hook
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
    // see unbadgeDispatched (ChatApp) for why busyChanged(true) must not be
    // used: the shared queue also drains items with no bubble in THIS drawer.
    if (bridge.queuedDispatched) {
      bridge.queuedDispatched.connect((t) => {
        setMessages((m) => unbadgeDispatched(m, t));
      });
    }
    // The user aborted the turn: keep any partial text instead of dropping it.
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
      });
    }
    // Keep the drawer's neutral label — the shared engine hardcodes a
    // provider-specific "… is thinking" we don't want to parrot.
    bridge.statusUpdate.connect((s) => {
      if (s && !/is thinking/i.test(s)) setStatus(s);
    });
    if (bridge.tokenStreamed) {
      bridge.tokenStreamed.connect((d) =>
        setStreaming((s) => {
          const next = s + d;
          streamRef.current = next;
          return next;
        })
      );
    }
    if (bridge.toolCall) {
      bridge.toolCall.connect((j) => {
        try {
          setTools((p) => [...p, JSON.parse(j)]);
        } catch (e) {}
      });
    }
    // Host notices arrive as bubbles too, so every surface shows the same
    // transcript: scan/publish outcomes ("a"), and USER turns ("u") mirrored
    // from the Qt panel or another drawer. A user turn THIS drawer originated
    // was already rendered optimistically (see send()) — we dedupe our own
    // echo by text so it isn't doubled here, while a turn from another surface
    // (no pending echo) is appended.
    if (bridge.chatNotice) {
      bridge.chatNotice.connect((j) => {
        try {
          const p = JSON.parse(j);
          if (p.role === "u") {
            const idx = pendingEchoes.current.indexOf(p.text || "");
            if (idx !== -1) {
              pendingEchoes.current.splice(idx, 1);   // our own echo — skip
              return;
            }
            setMessages((m) => [...m, { role: "user", text: p.text || "" }]);
          } else {
            setMessages((m) => [...m, { role: "assistant", text: p.text || "" }]);
          }
          window.__almaDrawerNotices = (window.__almaDrawerNotices || 0) + 1;
        } catch (e) {}
      });
    }
    window.__almaDrawerReady = true; // headless hook
  }, [bridge]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, tools, busy, streaming, open]);

  function send() {
    const text = input.trim();
    if (!text || !bridge) return;
    const id = ++seqRef.current;
    setMessages((m) => [...m, { role: "user", text, id }]);
    pendingEchoes.current.push(text);   // dedupe our own mirrored echo
    setInput("");
    if (bridge.queueMessage) {
      // Sends immediately when idle; parks the text while a turn runs.
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
      if (!ok) setStopping(false);   // nothing was aborted — re-arm the button
    });
  }

  if (!open) return null;

  return (
    <aside className="chatdock" data-testid="chat-drawer">
      <div className="chatdock-hdr">
        <span className="dot" aria-hidden="true" />
        <span className="chatdock-title">Renn</span>
        <span className="chatdock-sub">Enablement assistant</span>
        <span className="cal-spacer" />
        <button className="drawer-x" onClick={onClose} title="Close">×</button>
      </div>

      {!bridge ? (
        <div className="chatdock-empty">
          <p>Renn isn't wired to this surface yet.</p>
        </div>
      ) : (
        <>
          <div className="chatdock-msgs" ref={scrollRef}>
            {messages.length === 0 && !busy && (
              <div className="msg assistant">
                <Markdown text={"Hi, I'm Renn. Ask me to revise the draft, draft subtasks, or push a card — I can see what you're working on."} />
              </div>
            )}
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
                      <div key={i} className={"tool " + (t.ok ? "ok" : "err")}>
                        <span className="tick">{t.ok ? "✓" : "✗"}</span>
                        <span className="tname">{t.name}</span>
                        <span className="tmeta">
                          {t.ms ? ` · ${t.ms}ms` : ""}
                          {t.rows !== null && t.rows !== undefined ? ` · ${t.rows} rows` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="chatdock-composer">
            <input value={input}
                   onChange={(e) => setInput(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && send()}
                   placeholder="Message Renn…" />
            {busy ? (
              <button className="wb-btn primary stop" onClick={stop}
                      disabled={stopping} title="Stop this response">Stop</button>
            ) : (
              <button className="wb-btn primary" onClick={send}
                      disabled={!input.trim()}>Send</button>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
