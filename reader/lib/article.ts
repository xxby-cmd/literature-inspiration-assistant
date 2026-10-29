import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkRehype from "remark-rehype";
import rehypeKatex from "rehype-katex";
import rehypeStringify from "rehype-stringify";

// Server-only rendering keeps Markdown/math packages off the phone's JS path.
export function normalizeMath(text: string) {
  return text
    .split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g)
    .map((part) =>
      part.startsWith("`") || part.startsWith("~~~")
        ? part
        : part
            // CJK punctuation next to a delimiter otherwise fails CommonMark's
            // flanking rule. Render spacing outside emphasis, without editing storage.
            .replace(
              /\*\*([^*\n]+)\*\*/g,
              (match, inner, offset, source) =>
                (offset > 0 && !/\s/.test(source[offset - 1]) ? " " : "") +
                "**" +
                inner +
                "**" +
                (offset + match.length < source.length &&
                !/\s/.test(source[offset + match.length])
                  ? " "
                  : ""),
            )
            .replace(
              /\\\[([\s\S]*?)\\\]/g,
              (_, math) => "\n$$\n" + math.trim() + "\n$$\n",
            )
            .replace(/\\\(([^\n]*?)\\\)/g, (_, math) => "$" + math + "$"),
    )
    .join("");
}
function presentation() {
  return (tree: any) => {
    function visit(parent: any) {
      if (!parent.children) return;
      parent.children = parent.children.map((node: any) => {
        if (node.type === "element") {
          if (node.tagName === "h1") node.tagName = "h2";
          if (node.tagName === "a") {
            if (!/^(https?:\/\/|#)/i.test(String(node.properties?.href || "")))
              delete node.properties.href;
            node.properties = {
              ...node.properties,
              target: "_blank",
              rel: ["noreferrer", "noopener"],
            };
          }
          if (node.tagName === "img")
            return {
              type: "element",
              tagName: "span",
              properties: { className: ["figure-note"] },
              children: [
                {
                  type: "text",
                  value:
                    "图片：" +
                    (node.properties?.alt || "论文插图") +
                    "（请在原文查看）",
                },
              ],
            };
          visit(node);
          if (node.tagName === "table")
            return {
              type: "element",
              tagName: "div",
              properties: {
                className: ["table-scroll"],
                tabIndex: 0,
                role: "region",
                ariaLabel: "论文表格，可横向滚动",
              },
              children: [node],
            };
        }
        return node;
      });
    }
    visit(tree);
  };
}
// Raw HTML is discarded by remark-rehype; no raw-HTML plugin is enabled.
// Emit sanitized server markup without React DOM's unsupported RSC renderer.
const renderer = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkMath)
  .use(remarkRehype)
  .use(rehypeKatex, {
    trust: false,
    throwOnError: false,
    maxExpand: 1000,
    maxSize: 20,
  })
  .use(presentation)
  .use(rehypeStringify);
export function articleHtml(text: string) {
  return (
    '<div class="article">' +
    String(renderer.processSync(normalizeMath(text))) +
    "</div>"
  );
}
