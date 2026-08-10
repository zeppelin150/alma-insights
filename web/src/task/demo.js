// CX-Requests-shaped sample data for the #/task route. Doubles as the vitest
// fixture set — the dev workspace has NO custom fields (premium lapsed), so
// this is the only way the field grid / automation feed render outside
// production. Shapes mirror src/services/task_web.py's contract EXACTLY;
// task.test.jsx locks key parity. All content is synthetic (no PHI).

export function isDemoMode() {
  if (typeof window === "undefined") return false;
  return /[?&/]demo/.test(window.location.hash) ||
         /[?&]demo/.test(window.location.search);
}

const P = {
  jordan: { name: "Jordan Avery", initials: "JA", color: "#4186e0" },
  priya: { name: "Priya Nair", initials: "PN", color: "#aa62e3" },
  dana: { name: "Dana Whitfield", initials: "DW", color: "#20aaea" },
  marcus: { name: "Marcus Lee", initials: "ML", color: "#62d26f" },
  renn: { name: "Renn Ops", initials: "RO", color: "#ea4e9d" },
};

function txt(v) {
  return [{ t: "text", v, href: "" }];
}

const COMMENT_LOG = [
  [P.jordan, "Jul 2, 9:14 AM", txt("Kicking this off — the BCBSMA copay change lands Oct 1, so member-facing macros need to be staged before then.")],
  [P.priya, "Jul 3, 11:02 AM", [
    { t: "text", v: "Draft outline is up: ", href: "" },
    { t: "link", v: "Copay update outline", href: "https://docs.example.com/copay-outline" },
    { t: "text", v: " — flagging ", href: "" },
    { t: "mention", v: "Dana Whitfield", href: "" },
    { t: "text", v: " for the billing review.", href: "" },
  ]],
  [P.dana, "Jul 7, 2:40 PM", txt("Billing review done. Two edge cases: secondary coverage and retro-terminated plans. Added notes inline.")],
  [P.jordan, "Jul 9, 8:55 AM", txt("Thanks — folding both into the FAQ section.")],
  [P.marcus, "Jul 14, 6:22 PM", txt("Care Navigator quiz questions drafted; five items, answer key attached.")],
  [P.priya, "Jul 16, 10:05 AM", txt("Quiz looks good. Swap Q3 — it references the old copay tier table.")],
  [P.marcus, "Jul 17, 9:31 AM", txt("Q3 swapped for the new tier table version.")],
  [P.jordan, "Jul 21, 3:18 PM", txt("Guru card draft is in the drafts lane; word-diff is clean against the live card.")],
  [P.dana, "Jul 24, 12:47 PM", [
    { t: "text", v: "Zendesk macro copy staged — see ", href: "" },
    { t: "link", v: "ZD-48213", href: "https://example.zendesk.com/agent/tickets/48213" },
    { t: "text", v: " for the pilot thread.", href: "" },
  ]],
  [P.priya, "Jul 28, 4:03 PM", txt("Legal confirmed no disclosure changes needed for this one.")],
  [P.jordan, "Jul 30, 9:26 AM", txt("Training deck updated; enablement session booked for Aug 12.")],
  [P.marcus, "Aug 1, 1:12 PM", txt("Navigator dry-run complete — two wording nits, both fixed.")],
  [P.dana, "Aug 4, 10:44 AM", txt("Billing spot-check on staging passed for both edge cases.")],
  [P.priya, "Aug 5, 5:09 PM", [
    { t: "mention", v: "Jordan Avery", href: "" },
    { t: "text", v: " ready for final sign-off from your side.", href: "" },
  ]],
  [P.jordan, "Aug 6, 8:37 AM", txt("Signed off. Publishing steps queued for the Oct 1 window.")],
  [P.renn, "Aug 7, 7:58 AM", txt("Reminder: mirror copies staged locally; paste into Zendesk by hand per the read-only policy.")],
];

export function buildDemoTask() {
  const comments = COMMENT_LOG.map(([author, when, tokens], i) => ({
    gid: `c${i + 1}`, kind: "comment", author, when, tokens,
  }));
  const stories = [
    { gid: "s1", kind: "system", author: P.jordan, when: "Jul 2, 9:02 AM",
      tokens: txt("added this task to CX Requests") },
    ...comments,
    { gid: "s2", kind: "automation", author: null, when: "Aug 3, 6:00 AM",
      tokens: txt("When Task is overdue → Comment on Task: \"Past target — update the go-live checklist.\"") },
    { gid: "s3", kind: "system", author: P.jordan, when: "Aug 8, 9:12 AM",
      tokens: txt("completed this task") },
    { gid: "s4", kind: "automation", author: null, when: "Aug 8, 9:12 AM",
      tokens: txt("moved this task from New Requests to Complete · CX intake rule") },
  ];
  return {
    task_data: {
      connected: true,
      demo: true,
      task_id: "demo-1217331081535747",
      header: {
        completed: true,
        completed_on: "Aug 8, 2026",
        title: "BCBSMA copay update — member messaging + macros [[no value]]",
        status_pill: null,
        assignee: P.jordan,
        collaborators: [P.priya, P.dana, P.marcus, P.renn],
        due_display: "Sep 28, 2025 – Oct 17, 2025",
        due_iso: "2025-10-17",
        overdue: false,
        projects: [{ board: "CX Requests", section: "Complete" }],
        freshness: "Updated 5m ago",
        permalink: "https://app.asana.com/0/1215565058346588/1217331081535747",
        parent: null,
      },
      fields: [
        { gid: "f1", name: "Request Type", kind: "enum", value: "Guru: Update",
          pills: [{ text: "Guru: Update", color: "green" }], people: [], checked: false },
        { gid: "f2", name: "Urgent?", kind: "enum", value: "No",
          pills: [{ text: "No", color: "red" }], people: [], checked: false },
        { gid: "f3", name: "Approved by Ops", kind: "enum", value: "Yes",
          pills: [{ text: "Yes", color: "green" }], people: [], checked: false },
        { gid: "f4", name: "Requested By", kind: "people", value: "",
          pills: [], people: [P.dana], checked: false },
        { gid: "f5", name: "Team", kind: "enum", value: "Member Experience",
          pills: [{ text: "Member Experience", color: "blue" }], people: [], checked: false },
        { gid: "f6", name: "Audience", kind: "multi_enum", value: "",
          pills: [{ text: "Care Navigators", color: "aqua" },
                  { text: "Billing Specialists", color: "purple" }], people: [], checked: false },
        { gid: "f7", name: "Go-Live Date", kind: "date", value: "Oct 17, 2025",
          pills: [], people: [], checked: false },
        { gid: "f8", name: "Effort (hrs)", kind: "number", value: "12",
          pills: [], people: [], checked: false },
        { gid: "f9", name: "Ticket Link", kind: "text", value: "ZD-48213",
          pills: [], people: [], checked: false },
        { gid: "f10", name: "Impact", kind: "enum", value: "High",
          pills: [{ text: "High", color: "orange" }], people: [], checked: false },
        { gid: "f11", name: "Channel", kind: "enum", value: "Zendesk",
          pills: [{ text: "Zendesk", color: "yellow-green" }], people: [], checked: false },
        { gid: "f12", name: "Reviewed", kind: "checkbox", value: "",
          pills: [], people: [], checked: true },
        { gid: "f13", name: "Needs Legal Review", kind: "checkbox", value: "",
          pills: [], people: [], checked: false },
        { gid: "f14", name: "Content Owner", kind: "people", value: "",
          pills: [], people: [P.priya], checked: false },
        { gid: "f15", name: "Quarter", kind: "enum", value: "Q4 2025",
          pills: [{ text: "Q4 2025", color: "indigo" }], people: [], checked: false },
        { gid: "f16", name: "Source Board", kind: "text", value: "CX Requests",
          pills: [], people: [], checked: false },
        { gid: "f17", name: "Draft URL", kind: "text", value: "",
          pills: [], people: [], checked: false },
        { gid: "f18", name: "Training Required?", kind: "enum", value: "",
          pills: [], people: [], checked: false },
        { gid: "f19", name: "Stakeholder Sign-off", kind: "people", value: "",
          pills: [], people: [], checked: false },
        { gid: "f20", name: "Notes", kind: "text", value: "Rollout follows the BCBSMA cadence",
          pills: [], people: [], checked: false },
      ],
      description: {
        markdown:
          "**Name**\n\nDana Whitfield\n\n**Email**\n\ndana.w@example.com\n\n" +
          "**What audience is this for?**\n\n- Care Navigators\n- Billing Specialists\n\n" +
          "**Describe your request.**\n\nBCBSMA is changing specialist copays " +
          "effective Oct 1. We need the member-facing macro set, the Guru card, " +
          "and the Navigator quiz updated before the window opens. Outline: " +
          "[Copay update outline](https://docs.example.com/copay-outline)",
        srcdoc:
          "<p><strong>Name</strong></p><p>Dana Whitfield</p>" +
          "<p><strong>Email</strong></p><p>dana.w@example.com</p>" +
          "<p><strong>What audience is this for?</strong></p>" +
          "<ul><li>Care Navigators</li><li>Billing Specialists</li></ul>" +
          "<p><strong>Describe your request.</strong></p>" +
          "<p>BCBSMA is changing specialist copays effective Oct 1. We need the " +
          "member-facing macro set, the Guru card, and the Navigator quiz updated " +
          "before the window opens. Outline: " +
          '<a href="https://docs.example.com/copay-outline">Copay update outline</a></p>',
      },
      subtasks: [
        { gid: "st1", name: "Update Guru card draft", done: true, assignee: "Jordan Avery", due: "Aug 1", promoted: false },
        { gid: "st2", name: "Stage Zendesk macro copy", done: true, assignee: "Dana Whitfield", due: "Aug 4", promoted: false },
        { gid: "st3", name: "Navigator quiz refresh", done: true, assignee: "Marcus Lee", due: "Aug 5", promoted: true },
        { gid: "st4", name: "Enablement session deck", done: false, assignee: "Priya Nair", due: "Aug 12", promoted: true },
        { gid: "st5", name: "Post go-live spot check", done: false, assignee: "", due: "Oct 20", promoted: false },
      ],
      attachments: [
        { gid: "a1", name: "#cx-requests thread", host: "slack" },
        { gid: "a2", name: "Copay comms plan", host: "gdrive" },
        { gid: "a3", name: "ZD-48213 pilot ticket", host: "zendesk" },
        { gid: "a4", name: "tier-table-v2.png", host: "asana" },
      ],
      stories,
      capabilities: { complete: true, due: true, comment: true, subtask: true,
                      description: true, refresh: true },
    },
  };
}
