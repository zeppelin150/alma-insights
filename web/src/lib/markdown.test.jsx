// The escaping contract of the Markdown renderer, locked as a regression test:
// untrusted text NEVER becomes live markup — it enters React as string
// children and comes out entity-escaped. This is the property the whole
// bridge security model leans on (see tests/test_web_guardrails.py).
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown } from "./markdown.jsx";

const render = (text) => renderToStaticMarkup(<Markdown text={text} />);

describe("Markdown escaping contract", () => {
  it("script tags in text render as escaped entities, never elements", () => {
    const out = render("<script>alert(1)</script>");
    expect(out).not.toContain("<script");
    expect(out).toContain("&lt;script&gt;");
  });

  it("img/onerror payloads never become elements", () => {
    const out = render('<img src=x onerror="alert(1)">');
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });

  it("html inside emphasis/code spans stays escaped", () => {
    const out = render("**<b>bold</b>** and `<i>code</i>`");
    expect(out).not.toContain("<b>");
    expect(out).not.toContain("<i>");
    expect(out).toContain("<strong>");
    expect(out).toContain("<code>");
  });

  it("links carry NO href — navigation only via the bridge onClick", () => {
    const out = render("[docs](https://example.com/x)");
    expect(out).toContain('class="mdlink"');
    expect(out).not.toContain("href=");
    expect(out).not.toContain("https://example.com/x");
  });

  it("javascript: link targets never surface anywhere in the output", () => {
    const out = render("[x](javascript:alert(1))");
    expect(out).not.toContain("javascript:");
  });
});

describe("Markdown rendering subset", () => {
  it("renders bold, italic, and inline code", () => {
    const out = render("**b** *i* `c`");
    expect(out).toContain("<strong>b</strong>");
    expect(out).toContain("<em>i</em>");
    expect(out).toContain("<code>c</code>");
  });

  it("renders headings, rules, and lists", () => {
    const out = render("# Title\n\n---\n\n- one\n- two\n\n1. first");
    expect(out).toContain("md-h1");
    expect(out).toContain("<hr");
    expect(out).toContain("<ul><li>one</li><li>two</li></ul>");
    expect(out).toContain("<ol><li>first</li></ol>");
  });

  it("renders pipe tables with header and body", () => {
    const out = render("| A | B |\n|---|---|\n| 1 | 2 |");
    expect(out).toContain('<table class="mdtable">');
    expect(out).toContain("<th>A</th>");
    expect(out).toContain("<td>2</td>");
  });

  it("tolerates empty and null input", () => {
    expect(render("")).toBe("");
    expect(render(null)).toBe("");
  });
});
