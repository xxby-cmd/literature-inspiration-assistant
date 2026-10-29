import { test } from "node:test";
import assert from "node:assert/strict";
import {
  liveEntry,
  persistentEntries,
  routeFrom,
  routeUrl,
  cacheAge,
} from "./reader-session.ts";
const entry = (kind = "coarse", expires_at = null) => ({
  node: {
    id: "a".repeat(32),
    kind,
    expires_at,
    status: "ready",
    content: "raw text",
    html: "<p>formatted</p>",
  },
  savedAt: 10000000000,
});
test("reading and overview links preserve addressable history without external URLs", () => {
  for (const route of ["today", "read:" + "b".repeat(32), "paper:2026-10-29:3"])
    assert.equal(
      routeFrom(new URL(routeUrl(route), "https://reader.example")),
      route,
    );
  assert.equal(routeUrl("https://other.example"), "/");
  assert.equal(
    routeFrom(new URL("https://reader.example/?paper=8&date=2026-10-29")),
    "today",
  );
});
test("translation cache expires in memory and is never written to persistent cache", () => {
  const translated = entry("translation", 11000000000),
    regular = entry();
  assert.equal(liveEntry(translated, 11000000001).node.html, "");
  assert.equal(liveEntry(translated, 11000000001).node.status, "expired");
  assert.equal(
    liveEntry(
      {
        ...translated,
        node: { ...translated.node, status: "queued", expires_at: null },
      },
      10000000000,
    ).node.status,
    "queued",
  );
  const saved = persistentEntries({ a: regular, b: translated }, 10000000001);
  assert.equal(Object.keys(saved).length, 1);
  assert.equal(saved[regular.node.id].node.content, "");
});
test("cached articles are bounded, and old cache entries do not live forever", () => {
  assert.deepEqual(
    persistentEntries({ a: entry() }, 10000000000 + cacheAge + 1),
    {},
  );
  const many = {};
  for (let i = 0; i < 25; i++) {
    const e = entry();
    e.node.id = i.toString(16).padStart(32, "0");
    many[e.node.id] = e;
  }
  assert.equal(Object.keys(persistentEntries(many, 10000000001)).length, 15);
});
