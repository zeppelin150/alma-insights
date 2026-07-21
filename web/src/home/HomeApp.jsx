import { useEffect, useMemo, useState } from "react";
import { useBridge } from "../lib/bridge.js";
import ChatDrawer from "../chat/ChatDrawer.jsx";
import { makeDemoRenn } from "../chat/demoRenn.js";
import { buildDemoHome, isDemoMode } from "./demo.js";

// Home route (#/home) — the app's boot landing surface behind `ui.web_home`.
// Pure renderer: every string here (greeting, thousands separators, clamped
// titles, timestamps, tint hexes) arrives finished from HomeWebController, so
// this file computes no dates, formats no numbers and holds no mode logic.
//
// Clicks route back over the bridge, where the controller validates them
// against Python-held state. The mode-switch tile is the one authority-bearing
// action: it raises a NATIVE confirm dialog no script in this page can reach.

const EMPTY_VM = {
  mode: "",
  greeting: "",
  subtitle: "",
  empty_activity: "",
  tiles: [],
  stats: [],
  quick_actions: [],
  activity: [],
};

// Exported for the vitest suite (node env, react-dom/server — no DOM), which
// locks the escaping contract on every field that carries DB text.
export function Tile({ tile, onSwitch }) {
  return (
    <div className={"home-tile" + (tile.active ? " active" : "")}>
      <div className="home-tile-head">
        <span className="home-tile-title">{tile.title}</span>
        {tile.active && <span className="home-pill active">ACTIVE</span>}
      </div>
      <p className="home-tile-desc">{tile.desc}</p>
      {!tile.active && (
        <button className="home-switch" onClick={() => onSwitch(tile.key)}>
          Switch to this mode
        </button>
      )}
    </div>
  );
}

export function Stat({ stat }) {
  return (
    <div className="home-stat">
      <span className="home-stat-num">{stat.display}</span>
      <span className="home-stat-cap">{stat.caption}</span>
    </div>
  );
}

export function ActivityRow({ row, onOpen }) {
  return (
    <button className="home-activity-row" onClick={() => onOpen(row.kind)}>
      <span
        className="home-pill"
        style={{ background: row.tint_bg, color: row.tint_fg }}
      >
        {row.kind}
      </span>
      <span className="home-activity-title">{row.title}</span>
      <span className="home-activity-ts">{row.ts_display}</span>
    </button>
  );
}

export default function HomeApp() {
  const bridge = useBridge("homeBridge");
  const renn = useBridge("almaBridge"); // the shared in-page chat (M5.5)
  const [chatOpen, setChatOpen] = useState(false);
  const [vm, setVm] = useState(EMPTY_VM);

  useEffect(() => {
    window.__almaHomeMounted = true; // headless hook
  }, []);

  // Explicit demo mode (#/home?demo) — sample data, dev/preview only. Engages
  // ONLY when the flag is present AND no bridge exists, so in the app real
  // data always wins.
  const demo = isDemoMode() && !bridge;
  const rennBridge = useMemo(
    () => renn || (demo ? makeDemoRenn() : null),
    [renn, demo]
  );

  useEffect(() => {
    if (!demo) return;
    const fixture = buildDemoHome();
    setVm(fixture);
    window.__almaHomeVm = fixture; // headless hooks
    window.__almaHomeDemo = true;
  }, [demo]);

  useEffect(() => {
    if (!bridge) return;
    bridge.homeData.connect((j) => {
      try {
        const p = JSON.parse(j);
        const next = {
          mode: p.mode || "",
          greeting: p.greeting || "",
          subtitle: p.subtitle || "",
          empty_activity: p.empty_activity || "",
          tiles: Array.isArray(p.tiles) ? p.tiles : [],
          stats: Array.isArray(p.stats) ? p.stats : [],
          quick_actions: Array.isArray(p.quick_actions) ? p.quick_actions : [],
          activity: Array.isArray(p.activity) ? p.activity : [],
        };
        setVm(next);
        window.__almaHomeVm = next; // headless hook
      } catch (e) {}
    });
    bridge.refresh();
  }, [bridge]);

  function switchMode(key) {
    if (bridge) bridge.requestModeSwitch(key);
  }
  function runQuickAction(key) {
    if (bridge) bridge.quickAction(key);
  }
  function openActivity(kind) {
    if (bridge) bridge.activityActivated(kind);
  }

  if (!bridge && !demo) {
    return (
      <div className="home-root">
        <div className="route-empty">
          <p>Waiting for the home bridge…</p>
          <p className="route-note">
            This route renders the app's Home page (<code>ui.web_home</code>).
            Append <code>?demo</code> for sample data.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="home-root">
      <header className="home-head">
        <div>
          <h1 className="home-greeting">{vm.greeting}</h1>
          <p className="home-subtitle">{vm.subtitle}</p>
        </div>
        <span className="home-spacer" />
        {demo && <span className="home-pill demo">DEMO</span>}
        {rennBridge && (
          <button className="wb-btn primary" onClick={() => setChatOpen(true)}>
            Renn ›
          </button>
        )}
      </header>

      <div className="home-tiles">
        {vm.tiles.map((t) => (
          <Tile key={t.key} tile={t} onSwitch={switchMode} />
        ))}
      </div>

      {vm.stats.length > 0 && (
        <div className="home-stats">
          {vm.stats.map((s) => (
            <Stat key={s.label} stat={s} />
          ))}
        </div>
      )}

      <h2 className="home-heading">QUICK ACTIONS</h2>
      <div className="home-actions">
        {vm.quick_actions.map((a) => (
          <button
            key={a.key}
            className="home-action"
            onClick={() => runQuickAction(a.key)}
          >
            {a.label}
          </button>
        ))}
      </div>

      <h2 className="home-heading">RECENT ACTIVITY</h2>
      <div className="home-activity">
        {vm.activity.length === 0 ? (
          <p className="home-activity-empty">{vm.empty_activity}</p>
        ) : (
          vm.activity.map((row, i) => (
            <ActivityRow
              key={row.kind + i}
              row={row}
              onOpen={openActivity}
            />
          ))
        )}
      </div>

      <ChatDrawer
        bridge={rennBridge}
        open={chatOpen}
        onClose={() => setChatOpen(false)}
      />
    </div>
  );
}
