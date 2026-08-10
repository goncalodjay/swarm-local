import { test } from "node:test";
import assert from "node:assert/strict";
import { moveSelection } from "../src/selection.ts";

test("down moves to the next agent", () => {
  assert.equal(moveSelection(0, "down", 4), 1);
});

test("down clamps at the last agent", () => {
  assert.equal(moveSelection(3, "down", 4), 3);
});

test("up moves to the previous agent", () => {
  assert.equal(moveSelection(2, "up", 4), 1);
});

test("up clamps at the first agent", () => {
  assert.equal(moveSelection(0, "up", 4), 0);
});

test("count of zero stays at zero", () => {
  assert.equal(moveSelection(0, "down", 0), 0);
});
