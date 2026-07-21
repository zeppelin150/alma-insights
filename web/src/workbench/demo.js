// Sample-data fixture for the workbench's explicit demo mode
// (#/workbench?demo) — dev/preview only, mirrors the Qt page's _SAMPLE_DRAFTS.
// Engages ONLY when the demo flag is present AND no bridge exists; the header
// badges "SAMPLE DATA".
export function isDemoMode() {
  return /[?&/]demo/.test(window.location.hash) ||
         /[?&]demo/.test(window.location.search);
}

export const DEMO_CHIPS = [
  { id: 1, title: "SSO Setup", source: "drive" },
  { id: 2, title: "Returns Policy", source: "drive" },
  { id: 3, title: "Payments v2", source: "guru" },
];

export const DEMO_DRAFTS = {
  1: {
    id: 1,
    breadcrumb: "GURU › PROVIDER ENABLEMENT",
    title: "Setting up SSO for Providers",
    source: "From: SSO Setup.gdoc",
    markdown: "Providers can now self-serve SSO configuration…",
    preview_html:
      "<p>Providers can now self-serve SSO configuration from the admin " +
      "console. This guide covers setup and the <b>June 24</b> rollout.</p>" +
      "<blockquote><b>Rollout:</b> June 24, 2026 — enabled for all provider " +
      "orgs</blockquote><h2>Steps</h2><ol><li>Open Admin Console › Security › " +
      "SSO</li><li>Choose your identity provider (Okta, Azure AD, Google)</li>" +
      "<li>Upload the metadata XML and save</li><li>Test with a pilot org " +
      "before enabling org-wide</li></ol><h2>FAQ</h2><p><b>Does this affect " +
      "existing logins?</b> No — current sessions stay active until SSO is " +
      "enabled org-wide.</p><table><thead><tr><th>Plan</th><th>SSO</th></tr>" +
      "</thead><tbody><tr><td>Standard</td><td>Optional</td></tr><tr>" +
      "<td>Enterprise</td><td><span style=\"color:#0F6E56\">Required</span>" +
      "</td></tr></tbody></table>",
    baseline_present: true,
    checks: [
      { check: "broken_links", status: "ok", detail: "0 links flagged" },
      { check: "ai_readability", status: "ok", detail: "grade 8 reading level" },
      { check: "pii_scan", status: "ok", detail: "no PII patterns" },
      { check: "style_conformance", status: "warn", detail: "heading case differs from the style guide" },
    ],
  },
  2: {
    id: 2,
    breadcrumb: "GURU › SUPPORT OPS",
    title: "Returns & Refunds Policy",
    source: "From: Returns Policy.gdoc",
    markdown: "Returns are accepted within 30 days…",
    preview_html:
      "<p>Returns are accepted within <b>30 days</b> of delivery.</p>" +
      "<h2>Windows</h2><ul><li>Standard: 30 days</li><li>Holiday orders: " +
      "extended to Jan 31</li></ul>",
    baseline_present: false,
    checks: [
      { check: "broken_links", status: "ok", detail: "" },
      { check: "pii_scan", status: "ok", detail: "" },
    ],
  },
  3: {
    id: 3,
    breadcrumb: "GURU › PAYMENTS",
    title: "Payments v2 Overview",
    source: "Imported Guru card",
    markdown: "Payments v2 rolls out in phases…",
    preview_html:
      "<h2>Phases</h2><p>Payments v2 rolls out in <b>three phases</b> starting " +
      "July 21.</p><ul><li>Phase 1: internal</li><li>Phase 2: pilot orgs</li>" +
      "<li>Phase 3: GA</li></ul>",
    baseline_present: false,
    checks: [{ check: "ai_readability", status: "ok", detail: "" }],
  },
};

export const DEMO_DIFF = {
  baseline_present: true,
  change_count: 4,
  rows: [
    { tag: "equal", text: "## Steps" },
    { tag: "equal", text: "1. Open Admin Console › Security › SSO" },
    { tag: "del", text: "2. Choose your identity provider (Okta, Azure AD)",
      spans: [
        { tag: "equal", text: "2. Choose your identity provider (Okta, Azure AD" },
        { tag: "del", text: ")" },
      ] },
    { tag: "add", text: "2. Choose your identity provider (Okta, Azure AD, Google)",
      spans: [
        { tag: "equal", text: "2. Choose your identity provider (Okta, Azure AD" },
        { tag: "add", text: ", Google)" },
      ] },
    { tag: "equal", text: "3. Upload the metadata XML and save" },
    { tag: "del", text: "Rollout: June 10, 2026",
      spans: [
        { tag: "equal", text: "Rollout: June " },
        { tag: "del", text: "10," },
        { tag: "equal", text: " 2026" },
      ] },
    { tag: "add", text: "Rollout: June 24, 2026",
      spans: [
        { tag: "equal", text: "Rollout: June " },
        { tag: "add", text: "24," },
        { tag: "equal", text: " 2026" },
      ] },
  ],
};
