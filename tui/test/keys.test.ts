import { test } from "node:test";
import assert from "node:assert/strict";
import { parseKey } from "../src/keys.ts";

test("parseKey maps arrow up to up", () => {
  assert.equal(parseKey("\u001b[A"), "up");
});

test("parseKey maps arrow down to down", () => {
  assert.equal(parseKey("\u001b[B"), "down");
});

test("parseKey maps carriage return to enter", () => {
  assert.equal(parseKey("\r"), "enter");
});

test("parseKey maps line feed to enter", () => {
  assert.equal(parseKey("\n"), "enter");
});

test("parseKey maps q to quit", () => {
  assert.equal(parseKey("q"), "quit");
});

test("parseKey returns null for unhandled input", () => {
  assert.equal(parseKey("x"), null);
  assert.equal(parseKey("\u001b[C"), null);
  assert.equal(parseKey(""), null);
});