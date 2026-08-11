import { test } from "node:test";
import assert from "node:assert/strict";
import { ansi, statusColor, visibleLength, padVisible, truncateVisible } from "../src/style.ts";

test("ansi styles wrap text in SGR codes", () => {
  const expected = process.env.NO_COLOR === undefined
    ? { bold: "\x1b[1mx\x1b[0m", dim: "\x1b[2mx\x1b[0m", cyan: "\x1b[36mx\x1b[0m" }
    : { bold: "x", dim: "x", cyan: "x" };
  assert.equal(ansi.bold("x"), expected.bold);
  assert.equal(ansi.dim("x"), expected.dim);
  assert.equal(ansi.cyan("x"), expected.cyan);
});

test("statusColor maps each status to a style", () => {
  const statuses = ["working", "needs-human", "finished-idle", "idle"] as const;
  const styled = statuses.map((status) => statusColor(status)("x"));
  assert.deepEqual(styled, process.env.NO_COLOR === undefined
    ? ["\x1b[33mx\x1b[0m", "\x1b[31mx\x1b[0m", "\x1b[32mx\x1b[0m", "\x1b[2mx\x1b[0m"]
    : ["x", "x", "x", "x"]);
});

test("visibleLength ignores ANSI sequences", () => {
  assert.equal(visibleLength(ansi.bold("abc")), 3);
  assert.equal(visibleLength("abc"), 3);
});

test("padVisible pads to the visible width", () => {
  const padded = padVisible(ansi.bold("ab"), 4);
  assert.equal(visibleLength(padded), 4);
  assert.ok(padded.endsWith("  "));
});

test("truncateVisible keeps the ellipsis within width", () => {
  const truncated = truncateVisible("a-really-long-line", 8);
  assert.equal(visibleLength(truncated), 8);
  assert.ok(truncated.endsWith("…"));
});

test("truncateVisible returns short text unchanged", () => {
  assert.equal(truncateVisible("short", 8), "short");
});
