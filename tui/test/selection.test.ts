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

test("j moves to the next agent", () => {
  assert.equal(moveSelection(0, "j", 4), 1);
});

test("k moves to the previous agent", () => {
  assert.equal(moveSelection(2, "k", 4), 1);
});

test("home and g jump to the first agent", () => {
  assert.equal(moveSelection(3, "home", 4), 0);
  assert.equal(moveSelection(3, "g", 4), 0);
});

test("end and G jump to the last agent", () => {
  assert.equal(moveSelection(0, "end", 4), 3);
  assert.equal(moveSelection(0, "G", 4), 3);
});
