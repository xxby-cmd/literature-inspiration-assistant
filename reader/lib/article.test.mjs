import { test } from "node:test";
import assert from "node:assert/strict";
import { articleHtml } from "./article.ts";
test("the same server renderer supplies tables, CJK emphasis, maths and code", () => {
  const body =
    "中文**“重点”**继续。\n\n| A | B |\n|---|---|\n|1|2|\n\n\\[x=\\frac{1}{2}\\]\n\n`**literal**`";
  const rendered = articleHtml(body);
  assert.match(rendered, /<strong>“重点”<\/strong>/);
  assert.match(rendered, /class="table-scroll"/);
  assert.match(rendered, /class="katex/);
  assert.match(rendered, /<code>\*\*literal\*\*<\/code>/);
});
test("source HTML, unsafe URLs and remote images are not executable content", () => {
  const rendered = articleHtml(
    "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n\n![alt](https://example.org/pixel.png)\n\n[good](https://example.org/paper)",
  );
  assert.doesNotMatch(rendered, /<script|<img|onerror=|href="javascript:/i);
  assert.match(rendered, /href="https:\/\/example.org\/paper"/);
  assert.match(rendered, /请在原文查看/);
});
