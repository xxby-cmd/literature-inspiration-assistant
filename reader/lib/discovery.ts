import { validateDigest } from "./reader-contract";

export const batchPattern = /^[a-f0-9]{32}$/;
export function paperKey(value: string) {
  const u = new URL(value);
  if (u.protocol !== "https:") throw Error("原文链接无效");
  let host = u.hostname.toLowerCase().replace(/^www\./, ""),
    path = u.pathname.replace(/\/$/, "");
  if (host === "arxiv.org")
    path = path
      .replace(/^\/(pdf|html)\//, "/abs/")
      .replace(/\.pdf$/, "")
      .replace(/v\d+$/, "");
  return host + path.toLowerCase();
}
async function setting(db: any, key: string) {
  const r = await db
    .prepare("SELECT value FROM reader_settings WHERE key=?")
    .bind(key)
    .first();
  return r ? JSON.parse(r.value) : null;
}
export async function batchDigest(db: any, date: string, batch = "") {
  if (batch && !batchPattern.test(batch)) throw Error("文献分组无效");
  const d = await setting(db, batch ? "batch:" + batch : "digest:" + date);
  if (!d || d.date !== date) throw Error("没有找到这组文献");
  return d;
}
export async function nodeBatch(db: any, id: string) {
  return (await setting(db, "node_batch:" + id)) || "";
}
export async function discoveries(db: any) {
  const rows = await db
    .prepare(
      "SELECT value FROM reader_settings WHERE key LIKE 'discovery:%' ORDER BY rowid DESC LIMIT 60",
    )
    .all();
  return rows.results
    .map((r: any) => JSON.parse(r.value))
    .sort((a: any, b: any) => b.created_at - a.created_at);
}
export async function researchHistory(db: any) {
  const rows = await db
    .prepare(
      "SELECT value FROM reader_settings WHERE key LIKE 'discovery:%' AND json_extract(value,'$.mode')='topic' ORDER BY CAST(json_extract(value,'$.created_at') AS INTEGER) DESC,key DESC",
    )
    .all();
  return rows.results.map((r: any) => JSON.parse(r.value));
}
export async function queueDiscovery(db: any, input: any) {
  const mode = input?.mode,
    topic = String(input?.topic || "").trim(),
    request = input?.requestId;
  if (
    !["more", "topic", "similar"].includes(mode) ||
    !/^[-a-zA-Z0-9]{16,80}$/.test(request || "") ||
    topic.length > 500 ||
    (mode === "topic" && topic.length < 2)
  )
    throw Error("请填写想调研的问题");
  const id = Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode("discover:" + request),
      ),
    ),
  )
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
  const prior = await setting(db, "discovery:" + id);
  let seed = null;
  if (mode === "similar") {
    if (!Number.isInteger(input.paper) || input.paper < 1 || input.paper > 5)
      throw Error("先选择一篇论文");
    const d = await batchDigest(db, input.date, input.batch || "");
    seed = d.cards[input.paper - 1];
  }
  if (prior) {
    if (
      prior.mode !== mode ||
      prior.topic !== topic ||
      prior.seed_url !== (seed?.source_url || "")
    )
      throw Error("同一请求不能改成其他主题");
    return prior;
  }
  const active = (await discoveries(db)).find((d: any) =>
    ["queued", "running"].includes(d.status),
  );
  if (active) {
    if (
      active.mode === mode &&
      active.topic === topic &&
      active.seed_url === (seed?.source_url || "")
    )
      return active;
    throw Error("已有一组文献正在查找，请稍后再试");
  }
  const date = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10),
    created_at = Date.now();
  const time = new Date(created_at).toLocaleTimeString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
  });
  const record = {
    id,
    mode,
    topic,
    seed_url: seed?.source_url || "",
    label:
      mode === "topic"
        ? topic
        : mode === "similar"
          ? "相似：" + seed.title_cn
          : "继续发现 · " + time,
    date,
    status: "queued",
    created_at,
  };
  const payload = {
    node_id: id,
    kind: "discover",
    mode,
    topic,
    seed,
    date,
    background: false,
  };
  await db.batch([
    db
      .prepare(
        "INSERT OR IGNORE INTO reader_settings(key,value) VALUES ('discovery_active',?)",
      )
      .bind(JSON.stringify(id)),
    db
      .prepare(
        "INSERT OR IGNORE INTO reader_settings(key,value) SELECT ?,? WHERE (SELECT value FROM reader_settings WHERE key='discovery_active')=?",
      )
      .bind("discovery:" + id, JSON.stringify(record), JSON.stringify(id)),
    db
      .prepare(
        "INSERT OR IGNORE INTO reader_jobs(id,node_id,payload,status,created_at) SELECT ?,?,?,'queued',? WHERE (SELECT value FROM reader_settings WHERE key='discovery_active')=?",
      )
      .bind(
        id + "-1",
        id,
        JSON.stringify(payload),
        created_at,
        JSON.stringify(id),
      ),
  ]);
  const accepted = await setting(db, "discovery:" + id);
  if (!accepted) throw Error("已有一组文献正在查找，请稍后再试");
  return accepted;
}
export async function completeDiscovery(db: any, payload: any, data: any) {
  const record = await setting(db, "discovery:" + payload.node_id);
  if (!record) throw Error("调研记录不存在");
  if (data.ok) {
    const d = validateDigest(data.digest),
      keys = d.cards.map((c: any) => paperKey(c.source_url));
    if (d.date !== record.date || new Set(keys).size !== 5)
      throw Error("新文献分组不完整或重复");
    const digest = {
      ...d,
      batch_id: record.id,
      mode: record.mode,
      label: record.label,
    };
    await db
      .prepare("INSERT OR REPLACE INTO reader_settings(key,value) VALUES (?,?)")
      .bind("batch:" + record.id, JSON.stringify(digest))
      .run();
  }
  record.status = data.ok ? "ready" : "error";
  await db.batch([
    db
      .prepare("UPDATE reader_settings SET value=? WHERE key=?")
      .bind(JSON.stringify(record), "discovery:" + record.id),
    db
      .prepare(
        "UPDATE reader_jobs SET status='finished',lease_until=NULL WHERE node_id=?",
      )
      .bind(record.id),
    db
      .prepare(
        "DELETE FROM reader_settings WHERE key='discovery_active' AND value=?",
      )
      .bind(JSON.stringify(record.id)),
  ]);
  return record;
}
export async function shelf(db: any) {
  const rows = await db
    .prepare("SELECT value FROM reader_settings WHERE key LIKE 'paper_state:%'")
    .all();
  return rows.results
    .map((r: any) => JSON.parse(r.value))
    .sort((a: any, b: any) => b.updated_at - a.updated_at);
}
export async function setPaperState(db: any, input: any) {
  if (
    !Number.isInteger(input?.paper) ||
    input.paper < 1 ||
    input.paper > 5 ||
    !["saved", "read"].includes(input.field) ||
    typeof input.value !== "boolean"
  )
    throw Error("文献操作无效");
  const d = await batchDigest(db, input.date, input.batch || ""),
    card = d.cards[input.paper - 1],
    key = paperKey(card.source_url);
  const state = (await setting(db, "paper_state:" + key)) || {
    key,
    card,
    date: d.date,
    paper: input.paper,
    batch: d.batch_id || "",
    saved: false,
    read: false,
  };
  state[input.field] = input.value;
  state.updated_at = Date.now();
  await db
    .prepare("INSERT OR REPLACE INTO reader_settings(key,value) VALUES (?,?)")
    .bind("paper_state:" + key, JSON.stringify(state))
    .run();
  return state;
}
