import { test } from "node:test";
import assert from "node:assert/strict";
import { parseKey } from "../src/keys.ts";

test("parseKey maps arrow keys to directions", () => {
  assert.equal(parseKey("\u001b[A"), "up");
  assert.equal(parseKey("\u001b[B"), "down");
  assert.equal(parseKey("\u001b[C"), "right");
  assert.equal(parseKey("\u001b[D"), "left");
});

test("parseKey maps home and end", () => {
  assert.equal(parseKey("\u001b[H"), "home");
  assert.equal(parseKey("\u001b[F"), "end");
});

test("parseKey maps escape to esc", () => {
  assert.equal(parseKey("\u001b"), "esc");
});

test("parseKey maps carriage return and line feed to enter", () => {
  assert.equal(parseKey("\r"), "enter");
  assert.equal(parseKey("\n"), "enter");
});

test("parseKey maps tab to tab", () => {
  assert.equal(parseKey("\t"), "tab");
});

test("parseKey maps ctrl+k to ctrl+k", () => {
  assert.equal(parseKey("\u000b"), "ctrl+k");
});

test("parseKey maps vim and jump keys", () => {
  assert.equal(parseKey("j"), "j");
  assert.equal(parseKey("k"), "k");
  assert.equal(parseKey("g"), "g");
  assert.equal(parseKey("G"), "G");
  assert.equal(parseKey("q"), "quit");
  assert.equal(parseKey("?"), "?");
});

test("parseKey returns null for unhandled input", () => {
  assert.equal(parseKey("x"), null);
  assert.equal(parseKey(""), null);
});
