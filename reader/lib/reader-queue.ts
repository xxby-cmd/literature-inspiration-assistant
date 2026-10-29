import { actionInput, nodeId } from "./reader-contract";
import { batchDigest, nodeBatch } from "./discovery";

export async function claimJob(db: any, now = Date.now()) {
  await db
    .prepare(
      "UPDATE reader_jobs SET status='queued' WHERE status='running' AND lease_until<?",
    )
    .bind(now)
    .run();
  const job = await db
    .prepare(
      "UPDATE reader_jobs SET status='running',lease_until=? WHERE id=(SELECT id FROM reader_jobs WHERE status='queued' ORDER BY COALESCE(json_extract(payload,'$.background'),0),created_at LIMIT 1) RETURNING id,payload",
    )
    .bind(now + 3600000)
    .first();
  if (!job) return null;
  const payload = JSON.parse(String(job.payload));
  // A discovery creates a batch, so it has no reading node or generation yet.
  if (payload.kind !== "discover")
    await db
      .prepare(
        "UPDATE reader_nodes SET status='running' WHERE id=? AND generation=? AND status='queued'",
      )
      .bind(payload.node_id, payload.generation)
      .run();
  return { id: job.id, payload };
}

export async function queueReading(db: any, input: any, background = false) {
  const a = actionInput(input),
    now = Date.now();
  const digest = await batchDigest(db, a.date, a.batch || "");
  const id = await nodeId(a);
  let parent: any = null;
  if (a.kind === "explain") {
    parent = await db
      .prepare("SELECT * FROM reader_nodes WHERE id=? AND status='ready'")
      .bind(a.parent || "")
      .first();
    if (
      !parent ||
      parent.paper_date !== a.date ||
      parent.paper_no !== a.paper ||
      (await nodeBatch(db, parent.id)) !== (a.batch || "") ||
      !["coarse", "deep", "explain", "practice"].includes(parent.kind)
    )
      throw Error("先读完上一层，再选择通俗解释");
  }
  const existing: any = await db
    .prepare("SELECT * FROM reader_nodes WHERE id=?")
    .bind(id)
    .first();
  // Automatic preparation runs once. A failed model task requires an explicit
  // retry; a sync loop must never repeatedly spend the owner's allowance.
  if (
    existing &&
    (background || !["expired", "error"].includes(existing.status))
  ) {
    if (!background && existing.status === "queued")
      await db
        .prepare(
          "UPDATE reader_jobs SET payload=json_set(payload,'$.background',0) WHERE node_id=? AND status='queued'",
        )
        .bind(id)
        .run();
    return { id, status: existing.status, generation: existing.generation };
  }
  const pending = await db
    .prepare(
      "SELECT COUNT(*) AS n FROM reader_jobs WHERE status IN ('queued','running')",
    )
    .first();
  if ((pending?.n || 0) >= 8) return { id, status: "full" };
  const generation = (existing?.generation || 0) + 1,
    level = a.kind === "explain" ? (parent.level || 0) + 1 : 0,
    card = digest.cards[a.paper - 1];
  const payload = {
    node_id: id,
    generation,
    kind: a.kind,
    date: a.date,
    batch: a.batch || "",
    paper: a.paper,
    level,
    parent_id: parent?.id || null,
    parent_text: parent?.content || "",
    parent_scope: parent?.scope || "",
    source_url: card.source_url,
    background,
  };
  await db.batch([
    db
      .prepare(
        "INSERT INTO reader_nodes(id,paper_date,paper_no,kind,parent_id,level,generation,status,title,content,scope,created_at,expires_at,error) VALUES (?,?,?,?,?,?,?,'queued',?,'','',?,NULL,'') ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,status='queued',content='',scope='',expires_at=NULL,error='' WHERE reader_nodes.status IN ('expired','error')",
      )
      .bind(
        id,
        a.date,
        a.paper,
        a.kind,
        parent?.id || null,
        level,
        generation,
        card.title_cn,
        now,
      ),
    db
      .prepare(
        "INSERT OR IGNORE INTO reader_jobs(id,node_id,payload,status,created_at) VALUES (?,?,?,'queued',?)",
      )
      .bind(id + "-" + generation, id, JSON.stringify(payload), now),
    ...(a.batch
      ? [
          db
            .prepare(
              "INSERT OR IGNORE INTO reader_settings(key,value) VALUES (?,?)",
            )
            .bind("node_batch:" + id, JSON.stringify(a.batch)),
        ]
      : []),
  ]);
  return { id, status: "queued", generation };
}
