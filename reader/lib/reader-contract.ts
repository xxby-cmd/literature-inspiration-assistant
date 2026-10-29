export const stages = new Set([
  "coarse",
  "deep",
  "translation",
  "explain",
  "practice",
  "like",
  "neutral",
  "read",
]);
export const feedback = new Set(["like", "neutral", "read"]);
export const week = 7 * 24 * 60 * 60 * 1000;
export function validateDigest(v: any) {
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(v?.date) ||
    !Array.isArray(v.cards) ||
    v.cards.length !== 5
  )
    throw Error("需要完整的当天五篇");
  if (
    v.cards.filter((c: any) => c.group === "preference").length !== 3 ||
    v.cards.filter((c: any) => c.group === "exploration").length !== 2
  )
    throw Error("需要三篇兴趣和两篇探索");
  for (const c of v.cards)
    if (
      typeof c.title_cn !== "string" ||
      typeof c.plain_summary !== "string" ||
      typeof c.source_url !== "string" ||
      !c.source_url.startsWith("https://")
    )
      throw Error("论文信息不完整");
  return v;
}
export function actionInput(v: any) {
  if (
    !v ||
    !stages.has(v.kind) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(v.date) ||
    !Number.isInteger(v.paper) ||
    v.paper < 1 ||
    v.paper > 5 ||
    !/^[-a-zA-Z0-9]{16,80}$/.test(v.requestId)
  )
    throw Error("请从文献下方选择操作");
  if (v.parent && !/^[a-f0-9]{32}$/.test(v.parent))
    throw Error("上一级阅读记录无效");
  return v;
}
export async function nodeId(v: any) {
  const key = JSON.stringify([
    2,
    v.date,
    v.paper,
    v.kind,
    v.kind === "explain"
      ? v.parent || ""
      : feedback.has(v.kind)
        ? v.requestId
        : "",
    ...(v.batch ? [v.batch] : []),
  ]);
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key)),
    ),
  )
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
}
