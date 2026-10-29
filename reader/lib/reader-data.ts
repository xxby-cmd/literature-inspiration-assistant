import { articleHtml } from "./article";
import { nodeBatch } from "./discovery";

export async function expireTranslations(db: any) {
  await db
    .prepare(
      "UPDATE reader_nodes SET content='',status='expired' WHERE kind='translation' AND expires_at IS NOT NULL AND expires_at<=? AND status='ready'",
    )
    .bind(Date.now())
    .run();
}
export async function digestFor(db: any, date?: string, batch?: string) {
  const row = await db
    .prepare("SELECT value FROM reader_settings WHERE key=?")
    .bind(batch ? "batch:" + batch : date ? "digest:" + date : "latest")
    .first();
  return row ? JSON.parse(row.value) : null;
}
export function renderedNode(node: any, render = true) {
  return {
    ...node,
    html: render && node.status === "ready" ? articleHtml(node.content) : "",
    content: "",
  };
}
export async function readingFor(db: any, id: string) {
  await expireTranslations(db);
  const node = await db
    .prepare("SELECT * FROM reader_nodes WHERE id=?")
    .bind(id)
    .first();
  if (!node) return null;
  const batch = await nodeBatch(db, id),
    digest = await digestFor(db, node.paper_date, batch);
  return {
    node: { ...renderedNode(node), batch },
    card: digest?.cards[node.paper_no - 1] || null,
  };
}
export async function libraryFor(db: any, date: string, batch = "") {
  await expireTranslations(db);
  const rows = await db
    .prepare(
      "SELECT n.* FROM reader_nodes n LEFT JOIN reader_settings s ON s.key='node_batch:'||n.id WHERE n.paper_date=? AND COALESCE(s.value,'\"\"')=? AND n.kind IN ('coarse','deep','translation','explain','practice') ORDER BY n.created_at",
    )
    .bind(date, JSON.stringify(batch))
    .all();
  // Prefetch only existing coarse readings. GET never creates model work.
  return rows.results.map((node: any) => ({
    ...renderedNode(node, node.kind === "coarse"),
    batch,
  }));
}
