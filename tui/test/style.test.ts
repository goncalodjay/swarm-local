import { test } from "node:test";
import assert from "node:assert/strict";
import { ansi, statusColor, visibleLength, padVisible, truncateVisible } from "../src/style.ts";

test("ansi styles wrap text in SGR codes", () => {
  assert.equal(ansi.bold("x"), "\x1b[1mx\x1b[0m");
  assert.equal(ansi.dim("x"), "\x1b[2mx\x1b[0m");
  assert.equal(ansi.cyan("x"), "\x1b[36mx\x1b[0m");
});

test("statusColor maps each status to a style", () => {
  const plain = (text: string): string => text;
  assert.notEqual(statusColor("working"), plain);
  assert.notEqual(statusColor("needs-human"), plain);
  assert.notEqual(statusColor("finished-idle"), plain);
  assert.notEqual(statusColor("idle"), plain);
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
