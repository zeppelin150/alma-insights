// Sample-data fixture for the calendar's explicit demo mode
// (#/calendar?demo) — the web twin of the Qt CalendarPage's _SAMPLE chips.
// Dev/preview only: CalendarApp uses this ONLY when the demo flag is present
// AND no QWebChannel bridge exists, and it badges the header "SAMPLE DATA".
// Events are generated RELATIVE to today so the demo never goes stale.
import { addDays } from "./grid.js";

export function isDemoMode() {
  return /[?&/]demo/.test(window.location.hash) ||
         /[?&]demo/.test(window.location.search);
}

export function buildDemoData(todayIso) {
  const d = (n) => addDays(todayIso, n);
  const events = [
    { id: "d1", title: "Triage: bulk SSO requests from providers", date: d(-4),
      kind: "asana", status: "open", priority: "high", assignee: "Chris",
      subs: "1 / 3", description: "Providers report SSO failures after the June rollout — needs a triage doc and a KB card update." },
    { id: "d2", title: "Draft: SSO setup card", date: d(-1),
      kind: "drive", status: "in_progress", assignee: "Chris", subs: "2 / 5",
      description: "Turn the SSO setup gdoc into a publish-ready Guru card." },
    { id: "d3", title: "Payments v2 rollout review", date: d(0),
      kind: "guru", status: "open", assignee: "Dana",
      description: "Review the Payments v2 card set before the rollout announcement." },
    { id: "d4", title: "Onboarding deck refresh", date: d(0),
      kind: "drive", status: "open", assignee: "Chris",
      description: "Q3 numbers + the two new intake flows." },
    { id: "d5", title: "Returns policy sync with Ops", date: d(1),
      kind: "asana", status: "open", assignee: "Priya",
      description: "Align the returns-window language across Zendesk macros and Guru." },
    { id: "d6", title: "Card due: Returns & Refunds Policy", date: d(2),
      kind: "guru", is_card_due: true,
      description: "Verification window closes — the card needs a re-verify pass." },
    { id: "d7", title: "Enablement newsletter draft", date: d(4),
      kind: "drive", status: "open", assignee: "Chris",
      description: "July issue: SSO self-serve, Payments v2, new quiz packs." },
    { id: "d8", title: "Quiz pack: claims denials 101", date: d(4),
      kind: "asana", status: "open", assignee: "Dana",
      description: "10-question knowledge check from the denials one-pager." },
    { id: "d9", title: "Macro cleanup: stale payer names", date: d(4),
      kind: "asana", status: "open", assignee: "Chris",
      description: "Retire macros referencing the pre-merge payer names." },
    { id: "d10", title: "Battle card: competitor pricing", date: d(4),
      kind: "drive", status: "open", assignee: "Priya",
      description: "One-pager for the sales enablement folder." },
    { id: "d11", title: "Office hours: new-hire KB tour", date: d(4),
      kind: "normal", status: "open", assignee: "Chris",
      description: "30-minute walkthrough of the enablement collection." },
    { id: "d12", title: "SSO rollout retro notes published", date: d(-6),
      kind: "asana", status: "done", assignee: "Chris",
      description: "Retro complete — notes archived to the EC folder." },
    { id: "d13", title: "Provider portal FAQ updates", date: d(7),
      kind: "guru", status: "open", assignee: "Dana",
      description: "Fold the top 6 portal tickets into the FAQ card." },
    { id: "d14", title: "Subtask sweep: onboarding checklist", date: d(9),
      kind: "high", status: "open", priority: "high", assignee: "Chris",
      description: "Five checklist items block the July cohort." },
  ];
  const briefs = {
    d1: { ask: "Stand up a triage doc for the SSO ticket spike and update the KB card.",
          deliverable: "Triage doc + refreshed 'Setting up SSO' Guru card",
          links: [], stakeholders: ["Support Ops", "Identity team"],
          effective_date: d(3) },
    d5: { ask: "Agree the single returns-window wording with Ops.",
          deliverable: "One approved sentence, applied to 3 macros + 1 card",
          links: [], stakeholders: ["Ops", "CX leads"], effective_date: null },
  };
  return { events, briefs };
}
