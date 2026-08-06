import { useEffect, useMemo, useRef, useState } from "react";
import { useBridge } from "../lib/bridge.js";
import ChatDrawer from "../chat/ChatDrawer.jsx";
import { makeDemoRenn } from "../chat/demoRenn.js";
import { buildDemoData, isDemoMode } from "./demo.js";
import {
  addDays, addMonths, agendaGroups, groupByDate, monthGrid, monthTitle,
  moveFocus, parseIso, shortDate, weekOf,
} from "./grid.js";

// Enablement Calendar route (#/calendar) — M1: read-only month/week/agenda
// views over the CalendarBridge viewmodel. Everything shown comes from Python
// (events, scope, today); every click routes back over the bridge (openTask →
// native TaskDetailPanel, setScope → host refilter). Briefs load lazily on
// hover, mirroring the Qt detail panel's lazy join.

const LEGEND = [
  ["drive", "Drive"], ["guru", "Guru"], ["asana", "Asana"], ["high", "Due / high"],
  ["subtask", "Subtask"],
];
const WEEK = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

function Seg({ options, value, onPick }) {
  return (
    <div className="cal-seg">
      {options.map(([key, label]) => (
        <button key={key}
                className={"cal-seg-btn" + (value === key ? " active" : "")}
                onClick={() => onPick(key)}>{label}</button>
      ))}
    </div>
  );
}

function Chip({ e, compact, onOpen, onHover, onLeave, draggable, onDragStart, onDragEnd }) {
  return (
    <button
      className={"cal-chip kind-" + e.kind + (e.status === "done" ? " done" : "")}
      onClick={() => onOpen(e.id)}
      onMouseEnter={(ev) => onHover(e, ev)}
      onMouseLeave={onLeave}
      draggable={draggable ? "true" : undefined}
      onDragStart={draggable ? (ev) => onDragStart(e, ev) : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      title=""
    >
      {e.priority === "high" && <span className="cal-chip-dot" aria-hidden="true" />}
      {e.is_card_due && <span className="cal-chip-glyph" aria-hidden="true">↻</span>}
      {e.is_subtask && <span className="cal-chip-sub" aria-hidden="true">↳</span>}
      <span className={"cal-chip-title" + (compact ? " compact" : "")}>{e.title}</span>
    </button>
  );
}

function BriefBlock({ brief }) {
  if (brief === "loading") return <div className="cal-hc-brief muted">Loading brief…</div>;
  if (!brief) return null;
  return (
    <div className="cal-hc-brief">
      {brief.ask && <div><span className="cal-hc-k">Ask</span>{brief.ask}</div>}
      {brief.deliverable && <div><span className="cal-hc-k">Deliverable</span>{brief.deliverable}</div>}
      {brief.effective_date && <div><span className="cal-hc-k">Effective</span>{brief.effective_date}</div>}
      {Array.isArray(brief.stakeholders) && brief.stakeholders.length > 0 && (
        <div><span className="cal-hc-k">Stakeholders</span>{brief.stakeholders.join(", ")}</div>
      )}
    </div>
  );
}

function HoverCard({ e, brief, pos }) {
  const left = Math.max(8, Math.min(pos.x, (window.innerWidth || 1200) - 340));
  return (
    <div className="cal-hover-card" style={{ left, top: pos.y + 6 }}>
      <div className="cal-hc-title">{e.title}</div>
      <div className="cal-hc-meta">
        <span className={"cal-hc-kind kind-" + e.kind}>{e.kind}</span>
        {e.status && <span>{e.status.replace("_", " ")}</span>}
        {e.priority === "high" && <span className="cal-hc-high">high priority</span>}
        <span>{shortDate(e.date)}</span>
      </div>
      {e.assignee && <div className="cal-hc-row">Assignee: {e.assignee}</div>}
      {e.is_subtask && e.parent_title && (
        <div className="cal-hc-row">Subtask of {e.parent_title}</div>
      )}
      {e.subs && <div className="cal-hc-row">Subtasks: {e.subs}</div>}
      {e.description && <div className="cal-hc-desc">{e.description}</div>}
      <BriefBlock brief={brief} />
    </div>
  );
}

function DayPopover({ iso, events, onClose, onOpen }) {
  return (
    <div className="drawer-overlay" onClick={onClose}>
      <aside className="drawer cal-day-drawer" onClick={(ev) => ev.stopPropagation()}>
        <div className="drawer-hdr">
          <span className="drawer-title">{shortDate(iso)} — {events.length} item{events.length === 1 ? "" : "s"}</span>
          <button className="drawer-x" onClick={onClose} title="Close">×</button>
        </div>
        <div className="drawer-list cal-day-list">
          {events.map((e) => (
            <button key={e.id} className={"cal-day-row kind-" + e.kind}
                    onClick={() => { onOpen(e.id); onClose(); }}>
              <span className="cal-day-row-title">{e.title}</span>
              <span className="cal-day-row-meta">
                {e.assignee || ""}{e.status ? " · " + e.status.replace("_", " ") : ""}
              </span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}

export default function CalendarApp() {
  const bridge = useBridge("calendarBridge");
  const renn = useBridge("almaBridge");   // the shared in-page chat (M5.5)
  const [chatOpen, setChatOpen] = useState(false);
  const [data, setData] = useState({ events: [], scope: "mine", today: null });
  const [view, setView] = useState("month");
  const [focus, setFocus] = useState(null);       // ISO day the keyboard owns
  const [popover, setPopover] = useState(null);   // ISO day expanded, or null
  const [briefs, setBriefs] = useState({});       // id -> brief | "loading" | null
  const [hover, setHover] = useState(null);       // {id, e, x, y} | null
  const [drag, setDrag] = useState(null);         // {id, date} while a chip drags
  const [dropHot, setDropHot] = useState(null);   // ISO day under the drag
  const [pendingMove, setPendingMove] = useState(null); // {id, date} awaiting resolution
  const [flash, setFlash] = useState(null);       // transient toast text
  const gridRef = useRef(null);
  const pendingTimer = useRef(null);
  const flashTimer = useRef(null);

  function showFlash(text) {
    setFlash(text);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 2500);
  }

  useEffect(() => {
    window.__almaCalendarMounted = true; // headless hook
  }, []);

  // Explicit demo mode (#/calendar?demo) — sample data, dev/preview only.
  // Engages ONLY when the flag is present AND no bridge exists (in the app
  // the bridge connects immediately, so real data always wins) and badges
  // the header so a screenshot can never pass as live state.
  const demo = isDemoMode() && !bridge;
  const rennBridge = useMemo(
    () => renn || (demo ? makeDemoRenn() : null), [renn, demo]);
  useEffect(() => {
    if (!demo) return;
    const todayIso = new Date().toISOString().slice(0, 10);
    const fixture = buildDemoData(todayIso);
    setData({ events: fixture.events, scope: "mine", today: todayIso });
    setFocus((f) => f || todayIso);
    setBriefs((b) => ({ ...fixture.briefs, ...b }));
    window.__almaCalEvents = fixture.events.length; // headless hooks
    window.__almaCalDemo = true;
  }, [demo]);

  useEffect(() => {
    if (!bridge) return;
    bridge.calendarData.connect((j) => {
      try {
        const p = JSON.parse(j);
        const events = Array.isArray(p.events) ? p.events : [];
        setData({ events, scope: p.scope || "mine", today: p.today || null });
        setFocus((f) => f || p.today || null);
        window.__almaCalEvents = events.length;   // headless hooks
        window.__almaCalScope = p.scope || "mine";
      } catch (e) {}
    });
    bridge.briefReady.connect((j) => {
      try {
        const p = JSON.parse(j);
        if (p.task_id) {
          setBriefs((b) => ({ ...b, [p.task_id]: p.brief || null }));
          window.__almaBriefFor = p.task_id;      // headless hook
        }
      } catch (e) {}
    });
    if (bridge.rescheduleResolved) {
      bridge.rescheduleResolved.connect((j) => {
        try {
          const p = JSON.parse(j);
          window.__almaRescheduled = p;           // headless hook
          clearTimeout(pendingTimer.current);
          setPendingMove(null);
          if (p.cancelled) showFlash("Move cancelled — nothing changed.");
          else if (p.dispatched) showFlash(`Moving to ${shortDate(p.date)} — syncing…`);
          else showFlash("Move didn't start — see the app status bar.");
        } catch (e) {}
      });
    }
    bridge.refresh(); // pull the current state on mount/reconnect
  }, [bridge]);

  useEffect(() => {
    window.__almaCalView = view; // headless hook
  }, [view]);

  const today = data.today;
  const anchor = focus || today;
  const byDate = groupByDate(data.events);

  function openTask(id) {
    if (bridge) bridge.openTask(id);
  }

  function pickScope(scope) {
    if (bridge && scope !== data.scope) bridge.setScope(scope);
  }

  function chipHover(e, ev) {
    const r = ev.currentTarget.getBoundingClientRect();
    setHover({ id: e.id, e, x: r.left, y: r.bottom });
    if (bridge && briefs[e.id] === undefined) {
      setBriefs((b) => ({ ...b, [e.id]: "loading" }));
      bridge.requestBrief(e.id);
    }
  }

  // ── drag-to-reschedule (M2) — the drop only ASKS; Python gates the write
  // behind a native confirm + the CAS'd write-back path. ─────────────────
  function chipDragStart(e, ev) {
    try { ev.dataTransfer.setData("text/plain", e.id); } catch (err) {}
    ev.dataTransfer.effectAllowed = "move";
    setDrag({ id: e.id, date: e.date });
    setHover(null);
  }

  function chipDragEnd() {
    setDrag(null);
    setDropHot(null);
  }

  function cellDrop(isoDay, ev) {
    ev.preventDefault();
    setDropHot(null);
    let id = drag && drag.id;
    let fromDate = drag && drag.date;
    if (!id) {
      try { id = ev.dataTransfer.getData("text/plain"); } catch (err) {}
      const known = (data.events || []).find((x) => x.id === id);
      if (!known) return;                 // only chips we rendered can move
      fromDate = known.date;
    }
    setDrag(null);
    if (!id || !bridge || pendingMove) return;
    if (isoDay === fromDate) return;      // same-day drop → nothing to ask
    setPendingMove({ id, date: isoDay });
    clearTimeout(pendingTimer.current);   // belt-and-braces unwedge: a silent
    pendingTimer.current = setTimeout(() => setPendingMove(null), 5000);
    bridge.requestReschedule(id, isoDay);
  }

  function nav(delta) {
    if (!anchor) return;
    if (view === "week") {
      setFocus(addDays(anchor, 7 * delta));
    } else {
      const { y, m } = parseIso(anchor);
      const next = addMonths(y, m, delta);
      setFocus(`${next.y}-${String(next.m).padStart(2, "0")}-01`);
    }
  }

  function onKeyDown(ev) {
    if (!anchor) return;
    const moved = moveFocus(anchor, ev.key);
    if (moved) {
      ev.preventDefault();
      setFocus(moved);
      return;
    }
    if (ev.key === "Enter") {
      const evs = byDate[anchor] || [];
      if (evs.length === 1) openTask(evs[0].id);
      else if (evs.length > 1) setPopover(anchor);
    } else if (ev.key === "PageUp") { ev.preventDefault(); nav(-1); }
    else if (ev.key === "PageDown") { ev.preventDefault(); nav(1); }
    else if (ev.key === "m" || ev.key === "M") setView("month");
    else if (ev.key === "w" || ev.key === "W") setView("week");
    else if (ev.key === "a" || ev.key === "A") setView("agenda");
    else if (ev.key === "t" || ev.key === "T" || ev.key === "Home") {
      if (today) setFocus(today);
    }
  }

  function cell(isoDay, inMonth, tall) {
    const evs = byDate[isoDay] || [];
    const visible = tall ? evs : (evs.length <= 3 ? evs : evs.slice(0, 2));
    const isToday = isoDay === today;
    const isFocus = isoDay === anchor;
    return (
      <div key={isoDay}
           className={"cal-cell" + (inMonth ? "" : " dim") + (isFocus ? " focus" : "")
                      + (tall ? " tall" : "") + (dropHot === isoDay ? " drop-hot" : "")}
           onClick={() => setFocus(isoDay)}
           onDragOver={(ev) => { ev.preventDefault(); setDropHot(isoDay); }}
           onDragLeave={() => setDropHot((d) => (d === isoDay ? null : d))}
           onDrop={(ev) => cellDrop(isoDay, ev)}>
        <div className="cal-cell-hd">
          <span className={"cal-daynum" + (isToday ? " today" : "")}>{parseIso(isoDay).d}</span>
        </div>
        <div className="cal-cell-body">
          {visible.map((e) => (
            <Chip key={e.id} e={e} compact={!tall} onOpen={openTask}
                  onHover={chipHover} onLeave={() => setHover(null)}
                  draggable={!e.is_card_due && !pendingMove}
                  onDragStart={chipDragStart} onDragEnd={chipDragEnd} />
          ))}
          {!tall && evs.length > 3 && (
            <button className="cal-more" onClick={(ev) => { ev.stopPropagation(); setPopover(isoDay); }}>
              +{evs.length - 2} more
            </button>
          )}
        </div>
      </div>
    );
  }

  function monthView() {
    const { y, m } = parseIso(anchor);
    const weeks = monthGrid(y, m);
    return (
      <div className="cal-grid" style={{ gridTemplateRows: `auto repeat(${weeks.length}, 1fr)` }}>
        {WEEK.map((d) => <div key={d} className="cal-dow">{d}</div>)}
        {weeks.flat().map((isoDay) => cell(isoDay, parseIso(isoDay).m === m, false))}
      </div>
    );
  }

  function weekView() {
    const days = weekOf(anchor);
    return (
      <div className="cal-grid week" style={{ gridTemplateRows: "auto 1fr" }}>
        {WEEK.map((d) => <div key={d} className="cal-dow">{d}</div>)}
        {days.map((isoDay) => cell(isoDay, true, true))}
      </div>
    );
  }

  function agendaView() {
    const { overdue, upcoming } = agendaGroups(data.events, today || "0000-00-00");
    const row = (e) => (
      <button key={e.id} className={"cal-agenda-row kind-" + e.kind} onClick={() => openTask(e.id)}
              onMouseEnter={(ev) => chipHover(e, ev)} onMouseLeave={() => setHover(null)}>
        <span className="cal-agenda-date">{shortDate(e.date)}</span>
        <span className="cal-agenda-title">{e.title}</span>
        <span className="cal-agenda-meta">{e.assignee || ""}{e.status ? " · " + e.status.replace("_", " ") : ""}</span>
      </button>
    );
    return (
      <div className="cal-agenda">
        {overdue.length > 0 && (
          <div className="cal-agenda-group overdue">
            <div className="cal-agenda-hd">Overdue</div>
            {overdue.map(row)}
          </div>
        )}
        {upcoming.map((g) => (
          <div key={g.date} className="cal-agenda-group">
            <div className={"cal-agenda-hd" + (g.date === today ? " today" : "")}>
              {g.date === today ? "Today — " : ""}{shortDate(g.date)}
            </div>
            {g.events.map(row)}
          </div>
        ))}
        {overdue.length === 0 && upcoming.length === 0 && (
          <div className="route-empty"><p>Nothing scheduled.</p></div>
        )}
      </div>
    );
  }

  const title = !anchor ? "Calendar"
    : view === "week" ? `Week of ${shortDate(weekOf(anchor)[0])}`
    : view === "agenda" ? "Agenda"
    : monthTitle(parseIso(anchor).y, parseIso(anchor).m);

  return (
    <div className="app route-calendar">
      <header className="hdr cal-hdr">
        <span className="dot" aria-hidden="true" />
        <span className="name">Calendar</span>
        {demo && <span className="cal-demo-badge">SAMPLE DATA</span>}
        <span className="cal-title">{title}</span>
        {view !== "agenda" && (
          <span className="cal-nav">
            <button className="cal-nav-btn" onClick={() => nav(-1)} title="Previous">‹</button>
            <button className="cal-nav-btn" onClick={() => nav(1)} title="Next">›</button>
          </span>
        )}
        <span className="cal-legend">
          {LEGEND.map(([kind, label]) => (
            <span key={kind} className="cal-legend-item">
              {kind === "subtask"
                ? <span className="cal-legend-glyph" aria-hidden="true">↳</span>
                : <span className={"cal-legend-dot kind-" + kind} aria-hidden="true" />}
              {label}
            </span>
          ))}
        </span>
        <span className="cal-spacer" />
        <Seg options={[["mine", "Mine"], ["all", "All"]]} value={data.scope} onPick={pickScope} />
        <Seg options={[["month", "Month"], ["week", "Week"], ["agenda", "Agenda"]]}
             value={view} onPick={setView} />
        {rennBridge && (
          <button className="wb-btn primary" onClick={() => setChatOpen(true)}>
            Renn ›
          </button>
        )}
      </header>

      {!bridge && !demo ? (
        <div className="route-empty">
          <p>Waiting for the calendar bridge…</p>
          <p className="route-note">This route runs inside the app's Calendar tab
            (<code>enablement.web_tabs</code>). Append <code>?demo</code> for sample data.</p>
        </div>
      ) : !anchor ? (
        <div className="route-empty"><p>Loading calendar…</p></div>
      ) : (
        <div className="cal-body" ref={gridRef} tabIndex={0} onKeyDown={onKeyDown}>
          {view === "month" && monthView()}
          {view === "week" && weekView()}
          {view === "agenda" && agendaView()}
        </div>
      )}

      {hover && <HoverCard e={hover.e} brief={briefs[hover.id]} pos={hover} />}
      {popover && (
        <DayPopover iso={popover} events={byDate[popover] || []}
                    onClose={() => setPopover(null)} onOpen={openTask} />
      )}
      {flash && <div className="cal-flash">{flash}</div>}
      <ChatDrawer bridge={rennBridge} open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  );
}
