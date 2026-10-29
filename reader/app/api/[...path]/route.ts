import { env } from "cloudflare:workers";
import { validateDigest, week } from "@/lib/reader-contract";
import {
  expireTranslations,
  digestFor,
  readingFor,
  libraryFor,
} from "@/lib/reader-data";
import { queueReading, claimJob } from "@/lib/reader-queue";
import {
  queueDiscovery,
  completeDiscovery,
  discoveries,
  researchHistory,
  shelf,
  setPaperState,
  batchPattern,
} from "@/lib/discovery";
export const dynamic = "force-dynamic";
function json(data: any, status = 200) {
  return Response.json(data, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
async function body(r: Request) {
  const raw = await r.text();
  if (raw.length > 400000) throw Error("内容过长");
  return JSON.parse(raw);
}
// The loopback worker checks the per-instance header; the private gateway
// authenticates the reader and restricts which operations it forwards.
// There are no PushPlus credentials or model API keys in this application.
async function handle(request: Request) {
  try {
    const db = env.DB;
    if (!db) return json({ error: "阅读库还未就绪" }, 503);
    const path = new URL(request.url).pathname.replace(/^\/api\//, "");
    const now = Date.now();
    await expireTranslations(db);
    if (path === "today" && request.method === "GET") {
      const row = await db
        .prepare("SELECT value FROM reader_settings WHERE key='latest'")
        .first<{ value: string }>();
      return json({ digest: row ? JSON.parse(row.value) : null });
    }
    if (path === "library" && request.method === "GET") {
      const date = new URL(request.url).searchParams.get("date") || "";
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw Error("论文日期无效");
      const batch = new URL(request.url).searchParams.get("batch") || "";
      if (batch && !batchPattern.test(batch)) throw Error("文献分组无效");
      return json({ nodes: await libraryFor(db, date, batch) });
    }
    if (path === "batches" && request.method === "GET") {
      if (new URL(request.url).searchParams.get("kind") === "topic")
        return json({ batches: await researchHistory(db) });
      const rows = await db
        .prepare(
          "SELECT value FROM reader_settings WHERE key LIKE 'digest:%' ORDER BY key DESC LIMIT 30",
        )
        .all();
      const daily = rows.results
        .map((r: any) => JSON.parse(r.value))
        .map((d: any) => ({
          id: "",
          date: d.date,
          label: "每日推荐",
          mode: "daily",
          status: "ready",
          created_at: Date.parse(d.date + "T00:00:00+08:00"),
        }));
      return json({
        batches: [...(await discoveries(db)), ...daily].sort(
          (a: any, b: any) => b.created_at - a.created_at,
        ),
      });
    }
    if (path === "batch" && request.method === "GET") {
      const params = new URL(request.url).searchParams,
        id = params.get("id") || "",
        date = params.get("date") || "";
      if (id && !batchPattern.test(id)) throw Error("文献分组无效");
      if (!id && !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw Error("日期无效");
      const d = await digestFor(db, date, id);
      return d ? json({ digest: d }) : json({ error: "文献还在准备中" }, 404);
    }
    if (path === "shelf" && request.method === "GET")
      return json({ papers: await shelf(db) });
    if (path.startsWith("node/") && request.method === "GET") {
      const id = path.slice(5);
      if (!/^[a-f0-9]{32}$/.test(id))
        return json({ error: "没有找到阅读记录" }, 404);
      const reading = await readingFor(db, id);
      return reading ? json(reading) : json({ error: "没有找到阅读记录" }, 404);
    }
    if (request.method !== "POST") return json({ error: "没有这个入口" }, 404);
    const origin = request.headers.get("Origin");
    if (origin && origin !== new URL(request.url).origin)
      return json({ error: "请从阅读页选择操作" }, 403);
    const data = await body(request);
    if (path === "action") {
      const result = await queueReading(db, data);
      return result.status === "full"
        ? json({ error: "已有几项阅读任务在处理，请稍等再点" }, 429)
        : json(result);
    }
    if (path === "discover") return json(await queueDiscovery(db, data));
    if (path === "paper-state")
      return json({ paper: await setPaperState(db, data) });
    if (path === "sync/digest") {
      const d = validateDigest(data),
        value = JSON.stringify(d);
      await db.batch(
        ["latest", "digest:" + d.date].map((key) =>
          db
            .prepare(
              "INSERT INTO reader_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            )
            .bind(key, value),
        ),
      );
      return json({ ok: true });
    }
    if (path === "sync/prepare") {
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(data.date) ||
        !(await digestFor(db, data.date))
      )
        throw Error("预备论文日期无效");
      const nodes = [];
      for (let paper = 1; paper <= 5; paper++)
        nodes.push(
          await queueReading(
            db,
            {
              kind: "coarse",
              date: data.date,
              paper,
              requestId: "prepare-" + data.date + "-" + paper,
            },
            true,
          ),
        );
      return json({ nodes, complete: nodes.every((n) => n.status !== "full") });
    }
    if (path === "sync/claim") {
      return json({ job: await claimJob(db, now) });
    }
    if (path === "sync/heartbeat") {
      if (!Array.isArray(data.ids) || data.ids.length > 20)
        throw Error("任务列表无效");
      if (data.ids.length)
        await db.batch(
          data.ids.map((id: string) =>
            db
              .prepare(
                "UPDATE reader_jobs SET lease_until=? WHERE id=? AND status='running'",
              )
              .bind(now + 3600000, id),
          ),
        );
      await db
        .prepare(
          "INSERT INTO reader_settings(key,value) VALUES ('worker_heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        )
        .bind(String(now))
        .run();
      return json({ ok: true });
    }
    if (path === "sync/result") {
      const discoveryJob = await db
        .prepare("SELECT payload FROM reader_jobs WHERE id=? AND node_id=?")
        .bind(data.job_id, data.node_id)
        .first();
      if (discoveryJob) {
        const p = JSON.parse(String(discoveryJob.payload));
        if (p.kind === "discover")
          return json({
            ok: true,
            discovery: await completeDiscovery(db, p, data),
          });
      }
      if (
        typeof data.job_id !== "string" ||
        typeof data.node_id !== "string" ||
        typeof data.body !== "string" ||
        data.body.length > 150000
      )
        throw Error("阅读结果格式无效");
      const job: any = await db
        .prepare("SELECT * FROM reader_jobs WHERE id=? AND node_id=?")
        .bind(data.job_id, data.node_id)
        .first();
      if (!job) return json({ error: "任务编号不符" }, 409);
      const p = JSON.parse(job.payload),
        expires = p.kind === "translation" && data.ok ? now + week : null;
      await db.batch([
        db
          .prepare(
            "UPDATE reader_nodes SET status=?,title=?,content=?,scope=?,expires_at=?,error=? WHERE id=? AND generation=?",
          )
          .bind(
            data.ok ? "ready" : "error",
            String(data.title || ""),
            data.ok ? data.body : "",
            String(data.scope || ""),
            expires,
            data.ok ? "" : String(data.error || "本次未完成，请稍后重试"),
            data.node_id,
            p.generation,
          ),
        db
          .prepare(
            "UPDATE reader_jobs SET status='finished',lease_until=NULL WHERE id=?",
          )
          .bind(data.job_id),
      ]);
      return json({ ok: true, expires_at: expires });
    }
    return json({ error: "没有这个入口" }, 404);
  } catch (e) {
    return json(
      { error: e instanceof Error ? e.message : "本次操作未完成" },
      400,
    );
  }
}
export const GET = handle;
export const POST = handle;
