// ── lightweight markdown → JSX (dependency-free, XSS-safe: all text enters as
// escaped React string children; we only ever emit known elements). Renders the
// subset Renn uses: **bold** / *italic* / `code`, [text](url) links, "-"/"1." lists,
// #/## headings, "---" rules, GitHub-style | tables |, and paragraphs. ───────────
export function mdOpen(url) {
  // links open in the SYSTEM browser via the bridge, http/https only — never let a
  // click navigate the QWebEngine app away.
  if (/^https?:\/\//i.test(url) && window.almaBridge && window.almaBridge.openExternal) {
    window.almaBridge.openExternal(url);
  }
}

function mdInline(text, keyBase) {
  const nodes = [];
  const re = /\*\*([^*]+)\*\*|__([^_]+)__|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*([^*\s][^*]*)\*/;
  let rest = String(text);
  let k = 0;
  while (rest) {
    const m = rest.match(re);
    if (!m) { nodes.push(rest); break; }
    if (m.index > 0) nodes.push(rest.slice(0, m.index));
    if (m[1] || m[2]) nodes.push(<strong key={keyBase + "s" + k++}>{m[1] || m[2]}</strong>);
    else if (m[3]) nodes.push(<code key={keyBase + "c" + k++}>{m[3]}</code>);
    else if (m[4] !== undefined && m[5] !== undefined) {
      const url = m[5];
      nodes.push(
        <a key={keyBase + "a" + k++} className="mdlink"
           onClick={(e) => { e.preventDefault(); mdOpen(url); }}>{m[4]}</a>
      );
    } else nodes.push(<em key={keyBase + "e" + k++}>{m[6]}</em>);
    rest = rest.slice(m.index + m[0].length);
  }
  return nodes;
}

function mdCells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}
function mdIsTableSep(line) {
  const s = (line || "").trim();
  return s.includes("-") && s.includes("|") && /^[\s|:-]+$/.test(s);
}

export function Markdown({ text }) {
  const lines = String(text || "").split("\n");
  const out = [];
  let list = null;   // { ordered, items: [] }
  let para = [];
  const flushPara = () => {
    if (para.length) { const key = "b" + out.length; out.push(<p key={key}>{mdInline(para.join(" "), key)}</p>); para = []; }
  };
  const flushList = () => {
    if (list) {
      const base = "b" + out.length;
      const items = list.items.map((it, i) => <li key={i}>{mdInline(it, base + "i" + i)}</li>);
      out.push(list.ordered ? <ol key={base}>{items}</ol> : <ul key={base}>{items}</ul>);
      list = null;
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/\s+$/, "");
    // table: a "| … |" header row immediately followed by a "|---|---|" separator
    if (/^\s*\|.*\|/.test(line) && i + 1 < lines.length && mdIsTableSep(lines[i + 1])) {
      flushPara(); flushList();
      const headers = mdCells(line);
      i += 2;   // consume header + separator
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(mdCells(lines[i]));
        i++;
      }
      i--;      // the for-loop will advance
      const key = "b" + out.length;
      out.push(
        <table key={key} className="mdtable">
          <thead><tr>{headers.map((h, hi) => <th key={hi}>{mdInline(h, key + "h" + hi)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => (
            <tr key={ri}>{headers.map((_, ci) => <td key={ci}>{mdInline(r[ci] || "", key + "r" + ri + "c" + ci)}</td>)}</tr>
          ))}</tbody>
        </table>
      );
      continue;
    }
    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) { flushPara(); flushList(); out.push(<hr key={"b" + out.length} />); continue; }
    if (!line.trim()) { flushPara(); flushList(); continue; }
    const head = line.match(/^(#{1,4})\s+(.*)$/);
    if (head) { flushPara(); flushList(); const key = "b" + out.length; out.push(<div key={key} className={"md-h md-h" + head[1].length}>{mdInline(head[2], key)}</div>); continue; }
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ol) { flushPara(); if (!list || !list.ordered) { flushList(); list = { ordered: true, items: [] }; } list.items.push(ol[1]); continue; }
    if (ul) { flushPara(); if (!list || list.ordered) { flushList(); list = { ordered: false, items: [] }; } list.items.push(ul[1]); continue; }
    flushList();
    para.push(line);
  }
  flushPara(); flushList();
  return <>{out}</>;
}
