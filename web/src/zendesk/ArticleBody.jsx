// The ONLY HTML renderer on the Zendesk route: a fully sandboxed srcdoc
// iframe over the Python-sanitized body_srcdoc (PreviewFrame pattern).
// sandbox="" = no scripts, no same-origin, no forms, no popups — defense in
// depth behind src/data/html_sanitize.sanitize_html, which every srcdoc
// string already passed in the controller. Never innerHTML anywhere else.

// The sandboxed document carries its own Help-Center-ish stylesheet (it
// shares nothing with the app page — that's the point). Garden v8 values.
const FRAME_CSS = `
  body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         color: #2f3941; font-size: 14px; line-height: 1.6; margin: 20px 24px;
         background: #ffffff; }
  h1, h2, h3 { color: #2f3941; font-weight: 600; line-height: 1.3; }
  h2 { font-size: 18px; margin: 20px 0 8px; }
  h3 { font-size: 15px; margin: 16px 0 6px; }
  a { color: #1f73b7; }
  blockquote { border-left: 3px solid #d8dcde; color: #68737d;
               margin: 10px 0; padding: 6px 12px; }
  table { border-collapse: collapse; margin: 10px 0; }
  th, td { border: 1px solid #d8dcde; padding: 6px 10px; font-size: 13px; }
  th { background: #f8f9f9; text-align: left; font-weight: 600; }
  code, pre { background: #f8f9f9; border-radius: 4px;
              font-family: SFMono-Regular, Consolas, Menlo, monospace; }
  pre { padding: 8px 10px; overflow-x: auto; }
  code { padding: 1px 5px; }
  img { max-width: 100%; }
`;

export default function ArticleBody({ srcdoc }) {
  const doc = `<!doctype html><html><head><meta charset="utf-8">` +
    `<style>${FRAME_CSS}</style></head><body>${srcdoc || ""}</body></html>`;
  return (
    <iframe className="zd-frame" title="Article body (sandboxed)"
            sandbox="" srcDoc={doc} />
  );
}
