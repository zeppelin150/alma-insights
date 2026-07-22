// Home renderer contract. Activity titles and tile copy are DB text that a
// ticket, chat title or report summary can carry, so the escaping contract is
// locked here: they must always render as escaped React children, never markup.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ActivityRow, CccBanner, Stat, Tile } from "./HomeApp.jsx";
import { buildDemoHome } from "./demo.js";

const noop = () => {};

describe("ActivityRow escaping", () => {
  it("renders a script-tag title as escaped text, not markup", () => {
    const row = {
      kind: "Chat",
      title: "<script>alert(1)</script>",
      ts_display: "2026-07-20  09:00",
      tint_bg: "#E8F0FE",
      tint_fg: "#1D4ED8",
    };
    const out = renderToStaticMarkup(<ActivityRow row={row} onOpen={noop} />);
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });

  it("renders an img-onerror payload inert", () => {
    const row = {
      kind: "Report",
      title: '<img src=x onerror="alert(1)">',
      ts_display: "2026-07-20  09:00",
      tint_bg: "#F3E8FF",
      tint_fg: "#6D28D9",
    };
    const out = renderToStaticMarkup(<ActivityRow row={row} onOpen={noop} />);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });

  it("carries the Python-supplied tint hexes through to inline style", () => {
    const row = {
      kind: "Task",
      title: "Draft: card",
      ts_display: "2026-07-19  11:05",
      tint_bg: "#E5F3EC",
      tint_fg: "#0D7D72",
    };
    const out = renderToStaticMarkup(<ActivityRow row={row} onOpen={noop} />);
    expect(out).toContain("#E5F3EC");
    expect(out).toContain("#0D7D72");
  });

  it("renders the pre-formatted timestamp verbatim (no JS date math)", () => {
    const row = {
      kind: "Chat",
      title: "t",
      ts_display: "2026-07-19  08:30",
      tint_bg: "#fff",
      tint_fg: "#000",
    };
    const out = renderToStaticMarkup(<ActivityRow row={row} onOpen={noop} />);
    expect(out).toContain("2026-07-19  08:30");
  });
});

describe("Tile", () => {
  const base = {
    key: "enablement",
    title: "Enablement",
    desc: "Lightweight workbench",
    icon: "pen",
    active: false,
  };

  it("shows the switch button only when NOT active", () => {
    const out = renderToStaticMarkup(<Tile tile={base} onSwitch={noop} />);
    expect(out).toContain("Switch to this mode");
    expect(out).not.toContain("ACTIVE");
  });

  it("shows the ACTIVE pill and no switch button when active", () => {
    const out = renderToStaticMarkup(
      <Tile tile={{ ...base, active: true }} onSwitch={noop} />
    );
    expect(out).toContain("ACTIVE");
    expect(out).not.toContain("Switch to this mode");
  });

  it("escapes tile copy", () => {
    const out = renderToStaticMarkup(
      <Tile tile={{ ...base, desc: "<b>x</b>" }} onSwitch={noop} />
    );
    expect(out).toContain("&lt;b&gt;");
  });
});

describe("CccBanner", () => {
  it("renders nothing at all when banner is null (product mode)", () => {
    const out = renderToStaticMarkup(<CccBanner banner={null} />);
    expect(out).toBe("");
  });

  it("renders the Python-supplied name and description verbatim", () => {
    const banner = {
      title: "Content Command Center",
      desc: "Content Command Center is an AI-powered workspace that connects Asana, Guru, and Zendesk.",
    };
    const out = renderToStaticMarkup(<CccBanner banner={banner} />);
    expect(out).toContain("Content Command Center");
    expect(out).toContain("connects Asana, Guru, and Zendesk");
  });

  it("escapes banner copy — markup renders as text, never as elements", () => {
    const banner = { title: "<b>x</b>", desc: "<script>alert(1)</script>" };
    const out = renderToStaticMarkup(<CccBanner banner={banner} />);
    expect(out).toContain("&lt;b&gt;");
    expect(out).toContain("&lt;script&gt;");
    expect(out).not.toContain("<script>");
  });
});

describe("Stat", () => {
  it("renders the Python-formatted display string verbatim", () => {
    const out = renderToStaticMarkup(
      <Stat stat={{ label: "Tickets", caption: "TICKETS", value: 48213,
                    display: "48,213", available: true }} />
    );
    // The thousands separator comes from Python — JS must not reformat it.
    expect(out).toContain("48,213");
    expect(out).toContain("TICKETS");
  });
});

describe("demo fixture", () => {
  it("matches the viewmodel contract the controller emits", () => {
    const vm = buildDemoHome();
    expect(Object.keys(vm).sort()).toEqual([
      "activity", "banner", "empty_activity", "greeting", "mode",
      "quick_actions", "stats", "subtitle", "tiles",
    ]);
    // The demo fixture is product mode, so the CCC banner is absent.
    expect(vm.banner).toBeNull();
    expect(vm.tiles).toHaveLength(2);
    expect(vm.tiles.filter((t) => t.active)).toHaveLength(1);
    expect(vm.stats).toHaveLength(3);
  });
});
