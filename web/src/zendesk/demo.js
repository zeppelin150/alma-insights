// Sample-data fixture for the Zendesk clone's explicit demo mode
// (#/zendesk?demo) — dev/preview only. Engages ONLY when the demo flag is
// present AND no bridge exists (in the app the bridge connects immediately,
// so real data always wins); the header badges "SAMPLE DATA".
//
// Every object here carries EXACTLY the keys the ZendeskWebController pushes
// (plan section 3.4) — zendesk.test.jsx locks the key parity so the fixture
// can never drift from the paper contract Python is built against.
export function isDemoMode() {
  if (typeof window === "undefined") return false;
  return /[?&/]demo/.test(window.location.hash) ||
         /[?&]demo/.test(window.location.search);
}

const CATEGORIES = [
  { id: 1, name: "General", sections: [
    { id: 9, name: "FAQ", article_count: 3 },
    { id: 10, name: "Getting started", article_count: 2 },
  ] },
  { id: 2, name: "Billing & Claims", sections: [
    { id: 11, name: "Claims", article_count: 2 },
    { id: 12, name: "Payments", article_count: 1 },
  ] },
  { id: 3, name: "Provider Enablement", sections: [
    { id: 13, name: "Onboarding", article_count: 2 },
  ] },
];

// section_id -> [section name, category name]
const SECTION_NAMES = {
  9: ["FAQ", "General"], 10: ["Getting started", "General"],
  11: ["Claims", "Billing & Claims"], 12: ["Payments", "Billing & Claims"],
  13: ["Onboarding", "Provider Enablement"],
};

function articleRow(id, title, sectionId, opts = {}) {
  return {
    id, title,
    section_id: sectionId,
    section: SECTION_NAMES[sectionId][0],
    author: opts.author || "Dana Whitfield",
    updated_display: opts.updated || "Jul 1, 2026",
    draft: !!opts.draft,
    outdated: !!opts.outdated,
    labels: opts.labels || [],
    origin: opts.origin || "pull",
    open_revisions: opts.open_revisions || 0,
  };
}

const ARTICLES = [
  articleRow(101, "Setting up SSO", 9, { labels: ["sso", "security"], open_revisions: 1, updated: "Jul 1, 2026" }),
  articleRow(102, "Resetting your password", 9, { labels: ["account"], updated: "Jun 18, 2026" }),
  articleRow(103, "Supported browsers", 9, { draft: true, updated: "May 30, 2026" }),
  articleRow(104, "Creating your first client profile", 10, { labels: ["clients"], updated: "Jun 25, 2026" }),
  articleRow(105, "Importing client rosters", 10, { outdated: true, labels: ["clients", "import"], updated: "Apr 12, 2026" }),
  articleRow(106, "Submitting a claim", 11, { labels: ["claims"], open_revisions: 1, updated: "Jul 8, 2026" }),
  articleRow(107, "Claim denial codes", 11, { draft: true, labels: ["claims"], updated: "Jun 2, 2026" }),
  articleRow(108, "Payment schedules", 12, { labels: ["payments"], updated: "Jun 29, 2026" }),
  articleRow(109, "Provider onboarding checklist", 13, { labels: ["onboarding"], updated: "Jul 15, 2026" }),
  articleRow(110, "Credentialing timeline", 13, { draft: true, origin: "import", author: "Imported file", updated: "Jul 20, 2026" }),
];

const MACROS = [
  { id: 201, name: "Refund apology", description: "Standard apology + refund timeline reply.",
    active: true, updated_display: "Jun 12, 2026", open_revisions: 1 },
  { id: 202, name: "Assign to::Billing::Claim status", description: "",
    active: true, updated_display: "May 4, 2026", open_revisions: 0 },
  { id: 203, name: "Assign to::Billing::Payment question", description: "",
    active: true, updated_display: "May 4, 2026", open_revisions: 0 },
  { id: 204, name: "Escalate::Clinical", description: "Route clinical questions to the on-call queue.",
    active: true, updated_display: "Jun 27, 2026", open_revisions: 0 },
  { id: 205, name: "Close::Resolved - no response", description: "",
    active: false, updated_display: "Mar 9, 2026", open_revisions: 0 },
];

const BODIES = {
  101: "<h2>Overview</h2><p>Providers can self-serve SSO configuration from the " +
    "admin console.</p><h2>Steps</h2><ol><li>Open Admin Console, then Security, " +
    "then SSO</li><li>Choose your identity provider (Okta, Azure AD, Google)</li>" +
    "<li>Upload the metadata XML and save</li><li>Test with a pilot org before " +
    "enabling org-wide</li></ol><p><b>Note:</b> current sessions stay active " +
    "until SSO is enabled org-wide.</p>",
  105: "<p>Rosters import from CSV under <b>Clients &gt; Import</b>.</p>" +
    "<h2>Columns</h2><ul><li>first_name, last_name (required)</li>" +
    "<li>email, phone (optional)</li></ul><p>This article predates the v2 " +
    "importer and is flagged outdated.</p>",
  106: "<h2>Before you start</h2><p>Confirm the client's payer and plan are on " +
    "file.</p><h2>Submitting</h2><ol><li>Open the session note</li><li>Click " +
    "<b>Submit claim</b></li><li>Watch the claim status column for payer " +
    "acknowledgement</li></ol>",
};

function articleDetail(row) {
  return {
    id: row.id,
    title: row.title,
    section_id: row.section_id,
    section: SECTION_NAMES[row.section_id][0],
    category: SECTION_NAMES[row.section_id][1],
    labels: row.labels,
    author: row.author,
    draft: row.draft,
    outdated: row.outdated,
    position: (row.id % 7) + 1,
    html_url: row.origin === "pull"
      ? "https://covehealth.zendesk.com/hc/en-us/articles/" + row.id : null,
    origin: row.origin,
    source_file: row.origin === "import" ? "C:/exports/credentialing.html" : null,
    updated_display: row.updated_display,
    body_srcdoc: BODIES[row.id] ||
      "<p>" + row.title + " — sample mirrored body for the demo fixture.</p>",
    revisions: DRAFT_ROWS.filter((r) => r.kind === "article" && r.target_id === row.id)
      .map((r) => ({ draft_id: r.draft_id, status: r.status, title: r.title,
                     updated_display: r.created_display })),
  };
}

const MACRO_ACTIONS = {
  201: [
    { field: "comment_value", display: "Comment/Reply",
      value: "Hi {{ticket.requester.first_name}},\n\nWe're sorry about the " +
        "billing mix-up. Your refund was issued today and lands in 3-5 " +
        "business days.\n\n{{current_user.first_name}}" },
    { field: "status", display: "Status", value: "Pending" },
  ],
  202: [
    { field: "group_id", display: "Group", value: "Billing" },
    { field: "status", display: "Status", value: "Open" },
    { field: "comment_mode", display: "Comment mode", value: "Private" },
    { field: "comment_value", display: "Comment/Reply",
      value: "Routing to Billing for a claim status check: {{ticket.id}}." },
  ],
  203: [
    { field: "group_id", display: "Group", value: "Billing" },
    { field: "add_tags", display: "Add tags", value: "payments billing_question" },
  ],
  204: [
    { field: "priority", display: "Priority", value: "High" },
    { field: "group_id", display: "Group", value: "Clinical on-call" },
    { field: "add_tags", display: "Add tags", value: "clinical_escalation" },
  ],
  205: [
    { field: "status", display: "Status", value: "Solved" },
    { field: "comment_value_html", display: "Comment/Reply",
      value: "Closing this out since we haven't heard back - reply any time " +
        "to reopen, {{ticket.requester.first_name}}." },
  ],
};

function macroDetail(row) {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    active: row.active,
    updated_display: row.updated_display,
    actions: MACRO_ACTIONS[row.id] || [],
    revisions: DRAFT_ROWS.filter((r) => r.kind === "macro" && r.target_id === row.id)
      .map((r) => ({ draft_id: r.draft_id, status: r.status, name: r.title })),
  };
}

const DRAFT_ROWS = [
  { draft_id: 7, kind: "article", target_id: 101, target_title: "Setting up SSO",
    title: "Setting up SSO (SAML)", status: "ready",
    rationale: "Steps 3-5 were stale after the July release renamed the Security menu.",
    sources: [{ ref: "doc:sso-runbook", label: "SSO runbook" }],
    created_display: "Jul 23, 2026", copied_display: null, is_new: false },
  { draft_id: 8, kind: "article", target_id: 106, target_title: "Submitting a claim",
    title: "Submitting a claim", status: "pending",
    rationale: "Payer acknowledgement now shows within minutes, not 24 hours.",
    sources: [{ ref: "doc:claims-v2", label: "Claims v2 release notes" }],
    created_display: "Jul 22, 2026", copied_display: null, is_new: false },
  { draft_id: 9, kind: "article", target_id: null, target_title: null,
    title: "Telehealth billing FAQ", status: "pending",
    rationale: "Twelve tickets this month asked the same three telehealth billing questions.",
    sources: [{ ref: "trc:telehealth", label: "Telehealth ticket cluster" }],
    created_display: "Jul 21, 2026", copied_display: null, is_new: true },
  { draft_id: 3, kind: "macro", target_id: 201, target_title: "Refund apology",
    title: "Refund apology v2", status: "copied",
    rationale: "The old reply promised 7-10 days; finance now settles in 3-5.",
    sources: [{ ref: "doc:refund-sla", label: "Refund SLA update" }],
    created_display: "Jul 18, 2026", copied_display: "Jul 19, 2026", is_new: false },
];

const DIFFS = {
  "article:7": {
    request_id: "demo-d7",
    kind: "article",
    draft_id: 7,
    baseline_present: true,
    change_count: 3,
    title: { changed: true, old: "Setting up SSO", new: "Setting up SSO (SAML)" },
    rows: [
      { tag: "ctx", text: "Open Admin Console, then Security, then SSO" },
      { tag: "change", spans: [
        { tag: "equal", text: "Open Admin Console, then " },
        { tag: "del", text: "Security" },
        { tag: "add", text: "Access" },
        { tag: "equal", text: ", then SSO" },
      ] },
      { tag: "ctx", text: "Choose your identity provider (Okta, Azure AD, Google)" },
      { tag: "change", spans: [
        { tag: "equal", text: "Test with " },
        { tag: "del", text: "a pilot org" },
        { tag: "add", text: "two pilot orgs" },
        { tag: "equal", text: " before enabling org-wide" },
      ] },
    ],
  },
  "article:8": {
    request_id: "demo-d8", kind: "article", draft_id: 8, baseline_present: true,
    change_count: 1, title: { changed: false, old: "Submitting a claim", new: "Submitting a claim" },
    rows: [
      { tag: "ctx", text: "Watch the claim status column for payer acknowledgement" },
      { tag: "change", spans: [
        { tag: "equal", text: "Acknowledgement usually arrives within " },
        { tag: "del", text: "24 hours" },
        { tag: "add", text: "a few minutes" },
        { tag: "equal", text: "." },
      ] },
    ],
  },
  "article:9": {
    request_id: "demo-d9", kind: "article", draft_id: 9, baseline_present: false,
    change_count: 0, title: { changed: false, old: null, new: "Telehealth billing FAQ" },
    rows: [],
  },
  "macro:3": {
    request_id: "demo-d3", kind: "macro", draft_id: 3, baseline_present: true,
    change_count: 1, title: { changed: true, old: "Refund apology", new: "Refund apology v2" },
    rows: [
      { tag: "change", spans: [
        { tag: "equal", text: "lands in " },
        { tag: "del", text: "7-10" },
        { tag: "add", text: "3-5" },
        { tag: "equal", text: " business days" },
      ] },
    ],
  },
};

export function buildDemoZendesk() {
  const article_details = {};
  ARTICLES.forEach((a) => { article_details[a.id] = articleDetail(a); });
  const macro_details = {};
  MACROS.forEach((m) => { macro_details[m.id] = macroDetail(m); });
  return {
    zendesk_data: {
      view: "articles",
      connected: false,
      demo: true,
      counts: { articles: ARTICLES.length, macros: MACROS.length, revisions_open: 3 },
      categories: CATEGORIES,
      articles: ARTICLES,
      macros: MACROS,
      last_pull_display: "Jul 23, 2026 09:14",
      status: "",
    },
    article_details,
    macro_details,
    revisions_data: { filter: "all", revisions: DRAFT_ROWS },
    diffs: DIFFS,
  };
}
