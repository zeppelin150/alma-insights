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

export default function App() {
  const bridge = useBridge();
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Renn is thinking…");
  const [tools, setTools] = useState([]);
  const [meter, setMeter] = useState(null);
  const [input, setInput] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!bridge) return;
    bridge.responseReady.connect((t) => {
      document.title = "RT:" + t; // round-trip hook for headless verification
      setBusy(false);
      setTools([]);
      setMessages((m) => [...m, { role: "assistant", text: t }]);
    });
    bridge.errorOccurred.connect((e) => {
      setBusy(false);
      setMessages((m) => [...m, { role: "error", text: e }]);
    });
    bridge.busyChanged.connect((b) => {
      setBusy(b);
      if (b) {
        setTools([]);
        setStatus("Renn is thinking…");
      }
    });
    bridge.statusUpdate.connect((s) => s && setStatus(s));
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
    window.__almaReady = true; // readiness hook for headless verification
  }, [bridge]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, tools, busy]);

  function send() {
    const text = input.trim();
    if (!text || !bridge) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    bridge.send(text);
  }

  return (
    <div className="app">
      <header className="hdr">
        <span className="dot" aria-hidden="true" />
        <span className="name">Alma Agent</span>
        <span className="model">{meter && meter.model_used ? meter.model_used : "Claude"}</span>
        <span className="hist">History</span>
      </header>

      <div className="messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            {m.text}
          </div>
        ))}
        {busy && (
          <div className="msg assistant thinking">
            <div className="tstatus">{status}</div>
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
        <button className="mic" title="Dictate (coming soon)" disabled>
          mic
        </button>
        <button className="send" onClick={send} disabled={busy || !bridge}>
          Send
        </button>
      </div>
    </div>
  );
}
