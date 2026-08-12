import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { appendLogEntry, formatLogLine } from "../src/log.ts";

test("formatLogLine contains the event and key/value fields", () => {
  const line = formatLogLine("attach_start", { role: "coder", session: "swarmforge-coder", socket: "/s" });
  assert.ok(line.includes("attach_start"));
  assert.ok(line.includes("role=coder"));
  assert.ok(line.includes("session=swarmforge-coder"));
  assert.ok(line.includes("socket=/s"));
});

test("formatLogLine starts with an RFC3339 UTC timestamp", () => {
  const line = formatLogLine("tui_start", {});
  const timestamp = line.split(" ")[0];
  assert.match(timestamp, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
});

test("formatLogLine quotes values that contain spaces", () => {
  const line = formatLogLine("attach_end", { reason: "server disconnected unexpectedly" });
  assert.ok(line.includes('reason="server disconnected unexpectedly"'));
});

test("formatLogLine renders numbers, empty strings and null", () => {
  const line = formatLogLine("attach_end", { code: 1, reason: "", extra: null });
  assert.ok(line.includes("code=1"));
  assert.ok(line.includes("reason="));
  assert.ok(line.includes("extra=null"));
});

test("appendLogEntry creates the directory and appends one line per event", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "tui-log-"));
  const file = path.join(dir, "nested", "logs", "tui.log");
  assert.equal(appendLogEntry(file, "tui_start", {}), true);
  assert.equal(appendLogEntry(file, "attach_start", { session: "swarmforge-coder" }), true);
  assert.ok(existsSync(file));
  const lines = readFileSync(file, "utf8").trim().split("\n");
  assert.equal(lines.length, 2);
  assert.ok(lines[0].includes("tui_start"));
  assert.ok(lines[1].includes("attach_start"));
});

test("appendLogEntry returns false and does not throw on an invalid path", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "tui-log-"));
  const file = path.join(dir, "blocked", "tui.log");
  writeFileSync(path.join(dir, "blocked"), "not a directory");
  assert.equal(appendLogEntry(file, "tui_start", {}), false);
});
