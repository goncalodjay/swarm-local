import { test } from "node:test";
import assert from "node:assert/strict";
import { createStyle, visibleLength, padVisible, truncateVisible } from "../src/style.ts";

const colored = createStyle({ colors: true });
const plain = createStyle({ colors: false });

test("ansi styles wrap text in SGR codes when colors are enabled", () => {
  assert.equal(colored.bold("x"), "\x1b[1mx\x1b[0m");
  assert.equal(colored.dim("x"), "\x1b[2mx\x1b[0m");
  assert.equal(colored.cyan("x"), "\x1b[36mx\x1b[0m");
});

test("ansi styles pass through when colors are disabled", () => {
  assert.equal(plain.bold("x"), "x");
  assert.equal(plain.dim("x"), "x");
  assert.equal(plain.cyan("x"), "x");
});

test("statusColor maps each status to a style when colors are enabled", () => {
  const statuses = ["working", "needs-human", "finished-idle", "idle"] as const;
  const styled = statuses.map((status) => colored.statusColor(status)("x"));
  assert.deepEqual(styled, [
    "\x1b[33mx\x1b[0m",
    "\x1b[31mx\x1b[0m",
    "\x1b[32mx\x1b[0m",
    "\x1b[2mx\x1b[0m",
  ]);
});

test("statusColor passes through when colors are disabled", () => {
  const statuses = ["working", "needs-human", "finished-idle", "idle"] as const;
  const styled = statuses.map((status) => plain.statusColor(status)("x"));
  assert.deepEqual(styled, ["x", "x", "x", "x"]);
});

test("visibleLength ignores ANSI sequences", () => {
  assert.equal(visibleLength(colored.bold("abc")), 3);
  assert.equal(visibleLength("abc"), 3);
});

test("padVisible pads to the visible width", () => {
  const padded = padVisible(colored.bold("ab"), 4);
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
