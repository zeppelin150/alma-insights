// The workbench's two safety-critical renderers, locked as regression tests:
// the preview iframe must ALWAYS be fully sandboxed, and the diff renderer
// emits word spans as escaped text (never live markup).
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DiffBody, PreviewFrame } from "./WorkbenchApp.jsx";

describe("PreviewFrame sandbox contract", () => {
  it("renders an iframe with an EMPTY sandbox (no scripts, no origin)", () => {
    const out = renderToStaticMarkup(<PreviewFrame html="<p>hi</p>" />);
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
    expect(out).not.toContain("allow-same-origin");
  });

  it("carries the content via srcdoc with the frame stylesheet", () => {
    const out = renderToStaticMarkup(<PreviewFrame html="<h2>Steps</h2>" />);
    // React's server renderer emits the prop case-preserved; HTML attribute
    // names are case-insensitive, so match either.
    expect(out.toLowerCase()).toContain("srcdoc=");
    expect(out).toContain("Steps");          // attr-escaped content present
  });

  it("tolerates empty html", () => {
    const out = renderToStaticMarkup(<PreviewFrame html="" />);
    expect(out).toContain('sandbox=""');
  });
});

describe("DiffBody word-span renderer", () => {
  const diff = {
    baseline_present: true,
    change_count: 2,
    rows: [
      { tag: "equal", text: "## Steps" },
      { tag: "del", text: "Rollout: June 10",
        spans: [{ tag: "equal", text: "Rollout: June " }, { tag: "del", text: "10" }] },
      { tag: "add", text: "Rollout: June 24",
        spans: [{ tag: "equal", text: "Rollout: June " }, { tag: "add", text: "24" }] },
    ],
  };

  it("renders gutters, row classes, and word-level highlight spans", () => {
    const out = renderToStaticMarkup(<DiffBody diff={diff} />);
    expect(out).toContain("2 changed lines");
    expect(out).toContain('class="drow equal"');
    expect(out).toContain('class="wbspan del"');
    expect(out).toContain('class="wbspan add"');
    expect(out).toContain(">24</span>");
  });

  it("span text is escaped, never live markup", () => {
    const evil = {
      baseline_present: true, change_count: 1,
      rows: [{ tag: "add", text: "<img onerror=alert(1)>",
               spans: [{ tag: "add", text: "<img onerror=alert(1)>" }] }],
    };
    const out = renderToStaticMarkup(<DiffBody diff={evil} />);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });

  it("missing baseline shows the new-content notice", () => {
    const out = renderToStaticMarkup(<DiffBody diff={{ baseline_present: false, rows: [] }} />);
    expect(out).toContain("No linked card to compare against");
  });
});

// ── M4 chrome ──────────────────────────────────────────────────────
import { AiEditBar, ToolsMenu } from "./EditTools.jsx";

describe("M4 editing chrome", () => {
  it("AiEditBar disables everything while busy or bridgeless", () => {
    const busy = renderToStaticMarkup(<AiEditBar busy={true} disabled={false} onAsk={() => {}} />);
    expect(busy).toContain("Renn is revising…");
    expect((busy.match(/disabled/g) || []).length).toBeGreaterThanOrEqual(4);
    const off = renderToStaticMarkup(<AiEditBar busy={false} disabled={true} onAsk={() => {}} />);
    expect((off.match(/disabled/g) || []).length).toBeGreaterThanOrEqual(4);
  });

  it("ToolsMenu renders closed by default and disabled without a bridge", () => {
    const out = renderToStaticMarkup(
      <ToolsMenu disabled={true} existingCards={null}
                 onImport={() => {}} onPublish={() => {}} onListExisting={() => {}} />);
    expect(out).toContain("Tools");
    expect(out).toContain("disabled");
    expect(out).not.toContain("Push to Guru");   // menu closed
  });
});
