import { test } from "node:test";
import assert from "node:assert/strict";
import {
  actionInput,
  nodeId,
  validateDigest,
  week,
} from "./reader-contract.ts";
const action = {
  kind: "coarse",
  date: "2026-10-29",
  paper: 1,
  requestId: "12345678-1234-1234-1234-123456789012",
};
test("same reading deduplicates but new explanation parent advances", async () => {
  assert.equal(
    await nodeId(action),
    await nodeId({
      ...action,
      requestId: "87654321-1234-1234-1234-123456789012",
    }),
  );
  assert.notEqual(
    await nodeId({ ...action, kind: "explain", parent: "a".repeat(32) }),
    await nodeId({ ...action, kind: "explain", parent: "b".repeat(32) }),
  );
});
test("feedback retry uses request id; a new explicit choice is separate", async () => {
  assert.equal(
    await nodeId({ ...action, kind: "neutral" }),
    await nodeId({ ...action, kind: "neutral" }),
  );
  assert.notEqual(
    await nodeId({ ...action, kind: "neutral" }),
    await nodeId({
      ...action,
      kind: "neutral",
      requestId: "different-request-123456789",
    }),
  );
});
test("fixed five and three-two split; invalid indices rejected", () => {
  for (const paper of [0, 6, 1.5])
    assert.throws(() => actionInput({ ...action, paper }));
  assert.throws(() => validateDigest({ date: "2026-10-29", cards: [] }));
  assert.equal(week, 604800000);
});
