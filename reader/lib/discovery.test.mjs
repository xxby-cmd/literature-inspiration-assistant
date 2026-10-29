import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import ts from "typescript";
const urls = new Map();
function moduleUrl(name) {
  if (urls.has(name)) return urls.get(name);
  const text = readFileSync(
    new URL("./" + name + ".ts", import.meta.url),
    "utf8",
  );
  const js = ts
    .transpileModule(text, {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
    })
    .outputText.replace(
      /from ['"]\.\/([^'"]+)['"]/g,
      (_, dependency) => "from " + JSON.stringify(moduleUrl(dependency)),
    );
  const url =
    "data:text/javascript;base64," + Buffer.from(js).toString("base64");
  urls.set(name, url);
  return url;
}
const {
  queueDiscovery,
  completeDiscovery,
  batchDigest,
  setPaperState,
  shelf,
  paperKey,
  researchHistory,
} = await import(moduleUrl("discovery"));
const { queueReading, claimJob } = await import(moduleUrl("reader-queue"));
const { nodeId } = await import(moduleUrl("reader-contract"));
function database() {
  const raw = new DatabaseSync(":memory:");
  raw.exec(`CREATE TABLE reader_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE reader_jobs(id TEXT PRIMARY KEY,node_id TEXT,payload TEXT,status TEXT,lease_until INTEGER,created_at INTEGER);
 CREATE TABLE reader_nodes(id TEXT PRIMARY KEY,paper_date TEXT,paper_no INTEGER,kind TEXT,parent_id TEXT,level INTEGER,generation INTEGER,status TEXT,title TEXT,content TEXT,scope TEXT,created_at INTEGER,expires_at INTEGER,error TEXT);`);
  return {
    raw,
    prepare(sql) {
      let values = [];
      const statement = raw.prepare(sql);
      const api = {
        bind(...args) {
          values = args;
          return api;
        },
        async first() {
          return statement.get(...values) || null;
        },
        async all() {
          return { results: statement.all(...values) };
        },
        async run() {
          return statement.run(...values);
        },
      };
      return api;
    },
    async batch(statements) {
      raw.exec("BEGIN");
      try {
        const result = [];
        for (const s of statements) result.push(await s.run());
        raw.exec("COMMIT");
        return result;
      } catch (e) {
        raw.exec("ROLLBACK");
        throw e;
      }
    },
  };
}
function digest(date, offset = 0) {
  return {
    date,
    cards: Array.from({ length: 5 }, (_, i) => ({
      group: i < 3 ? "preference" : "exploration",
      field: "field" + i,
      title_cn: "测试论文" + (offset + i),
      title_original: "Fixture paper " + (offset + i),
      plain_summary: "仅用于隔离测试",
      source_url: "https://papers.example/" + (offset + i),
    })),
    english: "English",
    practice: "Practice",
  };
}
async function daily(db) {
  const d = digest("2026-10-29");
  await db
    .prepare("INSERT INTO reader_settings VALUES (?,?)")
    .bind("digest:" + d.date, JSON.stringify(d))
    .run();
  return d;
}
test("explicit discovery is idempotent, one active request, failed retry is explicit", async () => {
  const db = database(),
    input = { mode: "more", requestId: "request-1234567890" };
  const a = await queueDiscovery(db, input),
    b = await queueDiscovery(db, input),
    c = await queueDiscovery(db, {
      ...input,
      requestId: "another-request-123456",
    });
  assert.equal(a.id, b.id);
  assert.equal(c.id, a.id);
  assert.equal(db.raw.prepare("SELECT COUNT(*) n FROM reader_jobs").get().n, 1);
  await assert.rejects(() =>
    queueDiscovery(db, {
      mode: "topic",
      topic: "different topic",
      requestId: "different-request-12345",
    }),
  );
  await completeDiscovery(db, { node_id: a.id }, { ok: false });
  const next = await queueDiscovery(db, {
    ...input,
    requestId: "explicit-retry-123456",
  });
  assert.notEqual(next.id, a.id);
  db.raw.close();
});

test("the actual worker claim accepts discovery without a reading generation", async () => {
  const db = database(),
    record = await queueDiscovery(db, {
      mode: "more",
      requestId: "claim-request-1234567890",
    });
  const job = await claimJob(db);
  assert.equal(job.id, record.id + "-1");
  assert.equal(job.payload.kind, "discover");
  assert.equal(
    db.raw.prepare("SELECT COUNT(*) n FROM reader_nodes").get().n,
    0,
  );
  assert.equal(await claimJob(db), null);
  db.raw.prepare("UPDATE reader_jobs SET status='finished'").run();
  const d = await daily(db),
    reading = await queueReading(db, {
      kind: "coarse",
      date: d.date,
      paper: 1,
      requestId: "reading-1234567890",
    });
  assert.equal((await claimJob(db)).id, reading.id + "-1");
  assert.equal(
    db.raw.prepare("SELECT status FROM reader_nodes WHERE id=?").get(reading.id)
      .status,
    "running",
  );
  db.raw.close();
});
test("new batches never overwrite daily digest or collide with an old reading", async () => {
  const db = database(),
    old = await daily(db),
    job = await queueDiscovery(db, {
      mode: "more",
      requestId: "request-1234567890",
    });
  await completeDiscovery(
    db,
    { node_id: job.id },
    { ok: true, digest: digest(job.date, 10) },
  );
  assert.deepEqual(await batchDigest(db, old.date), old);
  assert.equal(
    (await batchDigest(db, job.date, job.id)).cards[0].source_url,
    "https://papers.example/10",
  );
  const action = {
    kind: "coarse",
    date: old.date,
    paper: 1,
    requestId: "reading-1234567890",
  };
  const legacy = await queueReading(db, action),
    newer = await queueReading(db, {
      ...action,
      date: job.date,
      batch: job.id,
    });
  assert.equal(legacy.id, await nodeId(action));
  assert.notEqual(legacy.id, newer.id);
  assert.equal(
    JSON.parse(
      db.raw
        .prepare("SELECT payload FROM reader_jobs WHERE node_id=?")
        .get(newer.id).payload,
    ).source_url,
    "https://papers.example/10",
  );
  db.raw.close();
});
test("cross-batch explanation cannot borrow another paper numbered one", async () => {
  const db = database(),
    d = await daily(db),
    old = await queueReading(db, {
      kind: "coarse",
      date: d.date,
      paper: 1,
      requestId: "reading-1234567890",
    });
  db.raw
    .prepare(
      "UPDATE reader_nodes SET status='ready',content='actual previous text' WHERE id=?",
    )
    .run(old.id);
  const job = await queueDiscovery(db, {
    mode: "more",
    requestId: "request-1234567890",
  });
  await completeDiscovery(
    db,
    { node_id: job.id },
    { ok: true, digest: digest(job.date, 10) },
  );
  await assert.rejects(() =>
    queueReading(db, {
      kind: "explain",
      date: job.date,
      paper: 1,
      batch: job.id,
      parent: old.id,
      requestId: "reading-1234567890",
    }),
  );
  db.raw.close();
});
test("saved and read are independent, persistent desired states, not toggle retries", async () => {
  const db = database(),
    d = await daily(db),
    input = { date: d.date, paper: 1, field: "saved", value: true };
  await setPaperState(db, input);
  await setPaperState(db, input);
  await setPaperState(db, { ...input, field: "read" });
  const result = await shelf(db);
  assert.equal(result.length, 1);
  assert.equal(result[0].saved, true);
  assert.equal(result[0].read, true);
  await setPaperState(db, { ...input, value: false });
  assert.equal((await shelf(db))[0].read, true);
  db.raw.close();
});
test("arxiv abstract, html, pdf and versions identify the same paper", () => {
  assert.equal(
    paperKey("https://arxiv.org/html/2401.12345v3"),
    paperKey("https://arxiv.org/pdf/2401.12345v1.pdf"),
  );
  assert.throws(() => paperKey("javascript:alert(1)"));
});

test("topic history keeps older searches, sorts newest first and never creates new jobs", async () => {
  const db = database();
  for (let i = 0; i < 65; i++)
    db.raw
      .prepare("INSERT INTO reader_settings VALUES (?,?)")
      .run(
        "discovery:" + String(i).padStart(32, "0"),
        JSON.stringify({
          id: String(i).padStart(32, "0"),
          mode: "topic",
          topic: "问题" + i,
          status: "ready",
          created_at: 1000 + i,
        }),
      );
  db.raw
    .prepare("INSERT INTO reader_settings VALUES (?,?)")
    .run(
      "discovery:non-topic",
      JSON.stringify({ mode: "more", created_at: 99999 }),
    );
  const first = await researchHistory(db),
    reopened = await researchHistory(db);
  assert.equal(first.length, 65);
  assert.equal(first[0].topic, "问题64");
  assert.equal(first.at(-1).topic, "问题0");
  assert.deepEqual(first, reopened);
  assert.equal(db.raw.prepare("SELECT COUNT(*) n FROM reader_jobs").get().n, 0);
  db.raw.close();
});
