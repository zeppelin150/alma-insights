// The ONLY HTML renderer on the Zendesk route: a fully sandboxed srcdoc
// iframe over the Python-sanitized body_srcdoc (PreviewFrame pattern).
// sandbox="" = no scripts, no same-origin, no forms, no popups, no top-level
// navigation — defense in depth behind the Python sanitizer, which every
// srcdoc string already passed in the controller. Never innerHTML anywhere.
//
// RENDER FIDELITY (owner correction, 2026-07-26). The rendered article is the
// PRIMARY review surface: a specialist reads it to see how the content lands
// for an end user, custom classes and all. Python's PREVIEW profile therefore
// keeps presentational markup (class / id / data-* / style, tables, embedded
// frames) and still strips scripts and event handlers; everything it keeps is
// inert in here because of sandbox="". This file NEVER strips, rewrites or
// re-escapes what Python served — it only supplies the Help-Center-like
// stylesheet that markup expects, so the preview shows what the markup notice
// says it shows and the notice stays a rare, meaningful signal.
//
// The sandboxed document shares nothing with the app page — that is the
// point. Values below approximate the stock Zendesk Help Center article
// template (Copenhagen) on Garden v8 colours.
const FRAME_CSS = `
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         color: #2f3941; font-size: 15px; line-height: 1.6; margin: 0;
         padding: 24px 28px 40px; background: #ffffff; }
  .article-body { max-width: 780px; overflow-wrap: break-word; }
  .article-body > *:first-child { margin-top: 0; }
  h1, h2, h3, h4, h5, h6 { color: #2f3941; font-weight: 600; line-height: 1.3; }
  h1 { font-size: 24px; margin: 24px 0 10px; }
  h2 { font-size: 19px; margin: 22px 0 8px; }
  h3 { font-size: 16px; margin: 18px 0 6px; }
  h4, h5, h6 { font-size: 14px; margin: 16px 0 6px; }
  p { margin: 0 0 12px; }
  a { color: #1f73b7; text-decoration: none; }
  a:hover { text-decoration: underline; }
  strong, b { font-weight: 600; }
  ul, ol { margin: 0 0 12px; padding-left: 24px; }
  li { margin: 4px 0; }
  li > ul, li > ol { margin: 4px 0; }
  dl { margin: 0 0 12px; }
  dt { font-weight: 600; margin-top: 8px; }
  dd { margin: 2px 0 0 20px; }
  blockquote { border-left: 3px solid #d8dcde; color: #68737d;
               margin: 12px 0; padding: 6px 14px; }
  hr { border: none; border-top: 1px solid #e9ebed; margin: 20px 0; }
  table { border-collapse: collapse; margin: 12px 0; max-width: 100%; }
  th, td { border: 1px solid #d8dcde; padding: 7px 10px; font-size: 14px;
           vertical-align: top; }
  th { background: #f8f9f9; text-align: left; font-weight: 600; }
  caption { caption-side: bottom; color: #68737d; font-size: 12px;
            padding-top: 6px; text-align: left; }
  code, pre, kbd, samp { font-family: SFMono-Regular, Consolas, Menlo, monospace; }
  code, pre { background: #f8f9f9; border-radius: 4px; }
  pre { padding: 10px 12px; overflow-x: auto; border: 1px solid #e9ebed;
        font-size: 13px; line-height: 1.5; }
  code { padding: 1px 5px; font-size: 13px; }
  pre code { padding: 0; background: transparent; }
  kbd { border: 1px solid #d8dcde; border-bottom-width: 2px; border-radius: 3px;
        padding: 1px 5px; font-size: 12px; background: #f8f9f9; }
  mark { background: #fff7d5; }
  sup, sub { font-size: 11px; }
  img, svg, video, canvas { max-width: 100%; height: auto; }
  figure { margin: 14px 0; }
  figcaption { color: #68737d; font-size: 12px; margin-top: 4px; }
  iframe, embed, object { max-width: 100%; border: 1px solid #e9ebed;
                          border-radius: 4px; background: #f8f9f9; }
  details { margin: 12px 0; }
  summary { cursor: default; font-weight: 600; }
  /* Help Center content blocks and the callout/notice classes article
     authors reach for — styled, never stripped. */
  .article-body .callout, .article-body .notice, .article-body .alert,
  .article-body [class*="callout"], .article-body [class*="notice"] {
    border-left: 4px solid #1f73b7; background: #f8f9f9;
    padding: 10px 14px; margin: 14px 0; border-radius: 0 4px 4px 0;
  }
  .article-body .callout > *:last-child,
  .article-body .notice > *:last-child { margin-bottom: 0; }
`;

export default function ArticleBody({ srcdoc }) {
  // .article-body is the stock Help Center article container: authored
  // markup lands inside it exactly as Python served it.
  const doc = `<!doctype html><html><head><meta charset="utf-8">` +
    `<meta name="color-scheme" content="light">` +
    `<style>${FRAME_CSS}</style></head><body>` +
    `<div class="article-body">${srcdoc || ""}</div></body></html>`;
  return (
    <iframe className="zd-frame" title="Article body (sandboxed preview)"
            sandbox="" srcDoc={doc} />
  );
}
