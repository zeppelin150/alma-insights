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
  articleRow(105, "Importing client rosters", 10, { outdated: true, labels: ["clients", "import"], open_revisions: 1, updated: "Apr 12, 2026" }),
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
  // Render fidelity showcase: custom classes, an id, a data-attribute, an
  // inline style and a table all survive the PREVIEW profile and render the
  // way an end user would see them — they are inert inside sandbox="".
  110: '<div class="callout" id="cred-lead" data-hc-block="callout" ' +
    'style="border-left-color:#d4a017">' +
    "<p><b>Credentialing runs 60-90 days.</b> Start before the provider's " +
    "first session.</p></div><h2 id=\"timeline\">Timeline</h2>" +
    '<table class="timeline"><thead><tr><th>Week</th><th>Milestone</th></tr>' +
    "</thead><tbody><tr><td>0</td><td>Roster submitted</td></tr>" +
    "<tr><td>2</td><td>Payer acknowledgement</td></tr>" +
    "<tr><td>8-12</td><td>Effective date issued</td></tr></tbody></table>",
};

// The exact stored bytes, WHERE THEY DIVERGE from the preview. One fixture
// row diverges on purpose: the imported article carries a script tag and an
// event handler the preview strips, which is precisely when markup_notice
// fires. Everything else renders byte-for-byte, so the alert stays the rare
// signal the owner asked for rather than a permanent banner.
const SOURCE_OVERRIDES = {
  110: BODIES[110] +
    '<p onclick="track()">Questions? Ask the enablement team.</p>' +
    "<script>window.__x = 1;</script>",
};

// Verbatim wording of the controller's _MARKUP_NOTICE.
const MARKUP_NOTICE = "this content contains markup the preview does not " +
  "display - read the HTML source before pasting";

// Plain/markdown source text of the mirrored bodies — what js_save_body_edit
// seeds the specialist's textarea from (article_detail.body_text).
const BODY_TEXTS = {
  101: "## Overview\nProviders can self-serve SSO configuration from the " +
    "admin console.\n\n## Steps\n1. Open Admin Console, then Security, then " +
    "SSO\n2. Choose your identity provider (Okta, Azure AD, Google)\n3. " +
    "Upload the metadata XML and save\n4. Test with a pilot org before " +
    "enabling org-wide\n\nNote: current sessions stay active until SSO is " +
    "enabled org-wide.",
  105: "Rosters import from CSV under Clients > Import.\n\n## Columns\n" +
    "- first_name, last_name (required)\n- email, phone (optional)\n\n" +
    "This article predates the v2 importer and is flagged outdated.",
  106: "## Before you start\nConfirm the client's payer and plan are on " +
    "file.\n\n## Submitting\n1. Open the session note\n2. Click Submit " +
    "claim\n3. Watch the claim status column for payer acknowledgement",
  110: "Credentialing runs 60-90 days. Start before the provider's first " +
    "session.\n\n## Timeline\n- Week 0: roster submitted\n- Week 2: payer " +
    "acknowledgement\n- Weeks 8-12: effective date issued",
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
    // The RENDERED preview — the primary review surface. Python's preview
    // profile keeps presentational markup (classes, ids, data-attributes,
    // inline styles, tables) so this looks like the end user's article.
    body_srcdoc: BODIES[row.id] ||
      "<p>" + row.title + " — sample mirrored body for the demo fixture.</p>",
    // The exact stored bytes the clipboard would deliver, rendered as
    // escaped text inside the COLLAPSED source disclosure. Identical to the
    // preview for all but the one divergent fixture row, so markup_notice
    // fires exactly once across the fixture.
    body_source: SOURCE_OVERRIDES[row.id] || BODIES[row.id] ||
      "<p>" + row.title + " — sample mirrored body for the demo fixture.</p>",
    markup_notice: SOURCE_OVERRIDES[row.id] ? MARKUP_NOTICE : "",
    body_text: BODY_TEXTS[row.id] ||
      row.title + " — sample mirrored body for the demo fixture.",
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
    // Canonical source rendering of the same actions — the exact bytes
    // "Copy reply" would deliver (controller: _actions_source).
    actions_source: JSON.stringify(
      (MACRO_ACTIONS[row.id] || []).map(
        (a) => ({ field: a.field, value: String(a.value == null ? "" : a.value) })),
      null, 2),
    revisions: DRAFT_ROWS.filter((r) => r.kind === "macro" && r.target_id === row.id)
      .map((r) => ({ draft_id: r.draft_id, status: r.status, name: r.title })),
  };
}

const DRAFT_ROWS = [
  { draft_id: 7, kind: "article", target_id: 101, target_title: "Setting up SSO",
    title: "Setting up SSO (SAML)", status: "ready",
    rationale: "Steps 3-5 were stale after the July release renamed the Security menu.",
    sources: [{ ref: "doc:sso-runbook", label: "SSO runbook" }],
    source_ref: null,
    body: "## Overview\nProviders can self-serve SSO configuration from the " +
      "admin console.\n\n## Steps\n1. Open Admin Console, then Access, then " +
      "SSO\n2. Choose your identity provider (Okta, Azure AD, Google)\n3. " +
      "Upload the metadata XML and save\n4. Test with two pilot orgs before " +
      "enabling org-wide",
    created_display: "Jul 23, 2026", copied_display: null, is_new: false },
  { draft_id: 8, kind: "article", target_id: 106, target_title: "Submitting a claim",
    title: "Submitting a claim", status: "pending",
    rationale: "Payer acknowledgement now shows within minutes, not 24 hours.",
    sources: [{ ref: "doc:claims-v2", label: "Claims v2 release notes" }],
    source_ref: null,
    body: "## Before you start\nConfirm the client's payer and plan are on " +
      "file.\n\n## Submitting\n1. Open the session note\n2. Click Submit " +
      "claim\n3. Watch the claim status column — acknowledgement usually " +
      "arrives within a few minutes.",
    created_display: "Jul 22, 2026", copied_display: null, is_new: false },
  { draft_id: 9, kind: "article", target_id: null, target_title: null,
    title: "Telehealth billing FAQ", status: "pending",
    rationale: "Twelve tickets this month asked the same three telehealth billing questions.",
    sources: [{ ref: "trc:telehealth", label: "Telehealth ticket cluster" }],
    source_ref: null,
    body: "## Telehealth billing FAQ\n- Which CPT codes apply to telehealth " +
      "sessions?\n- Does place-of-service 10 vs 02 change reimbursement?\n" +
      "- How do modifier 95 claims get flagged?",
    created_display: "Jul 21, 2026", copied_display: null, is_new: true },
  { draft_id: 10, kind: "article", target_id: 105,
    target_title: "Importing client rosters",
    title: "Importing client rosters", status: "pending",
    rationale: "Edited in the workspace.",
    sources: [],
    source_ref: "specialist-edit",
    body: "Rosters import from CSV under Clients > Import.\n\n## Columns\n" +
      "- first_name, last_name (required)\n- email, phone (optional)\n\n" +
      "This article covers the v2 importer.",
    created_display: "Jul 25, 2026", copied_display: null, is_new: false },
  { draft_id: 3, kind: "macro", target_id: 201, target_title: "Refund apology",
    title: "Refund apology v2", status: "copied",
    rationale: "The old reply promised 7-10 days; finance now settles in 3-5.",
    sources: [{ ref: "doc:refund-sla", label: "Refund SLA update" }],
    source_ref: null,
    body: "Hi {{ticket.requester.first_name}},\n\nWe're sorry about the " +
      "billing mix-up. Your refund was issued today and lands in 3-5 " +
      "business days.",
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
  "article:10": {
    request_id: "demo-d10", kind: "article", draft_id: 10, baseline_present: true,
    change_count: 1,
    title: { changed: false, old: "Importing client rosters", new: "Importing client rosters" },
    rows: [
      { tag: "ctx", text: "Rosters import from CSV under Clients > Import." },
      { tag: "change", spans: [
        { tag: "equal", text: "This article " },
        { tag: "del", text: "predates the v2 importer and is flagged outdated" },
        { tag: "add", text: "covers the v2 importer" },
        { tag: "equal", text: "." },
      ] },
    ],
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

// Every diff_ready payload carries the full review contract: `rows` is the
// AUTHORITATIVE source diff and `text_rows` the SECONDARY readable
// projection. The sample rows above stand in for the source pane; the
// secondary pane is left empty rather than faking a second, differently
// worded diff of the same fixture. `warning` stays null because these
// fixtures are internally consistent (no changes ⇒ bytes equal).
Object.keys(DIFFS).forEach((key) => {
  const d = DIFFS[key];
  DIFFS[key] = {
    ...d,
    bytes_equal: d.change_count === 0,
    warning: null,
    markup_notice: "",
    text_rows: [],
    text_change_count: 0,
  };
});

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
      counts: { articles: ARTICLES.length, macros: MACROS.length, revisions_open: 4 },
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
